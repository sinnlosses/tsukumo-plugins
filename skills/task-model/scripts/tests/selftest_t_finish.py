from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from selftest_support import RESULT_FULL, check, git, refuse_direct_run, say, write  # noqa: E402
import beads  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
import tw_accept  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_fixtures import BODY, claimed_work_tree, commit_task, flow_rows, make_repo, run_task  # noqa: E402

RESULT = RESULT_FULL


def _claimed(tmp: str, verify: str | None = None, extra: str = "") -> tuple[str, str]:
    main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify=verify, extra=extra)
    commit_task(main_path, taskfile.Task("T-100", "束ねる", "todo", "sonnet", "Y", (), BODY))
    run_task(wt1, "claim", "T-100")
    return main_path, wt1


def test_finish_without_task_id_in_subject() -> None:
    say("task.py finish: 設定 commit_task_id = \"付けない\" なら件名にタスクIDを付けず、--message の先頭の ID も外す")
    for message in ("完了", "T-100: 完了"):
        with tempfile.TemporaryDirectory() as tmp:
            main_path, wt1 = _claimed(tmp, extra='commit_task_id = "付けない"\n')
            write(os.path.join(wt1, "work.txt"), "x")
            r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", message, "--add", "work.txt", stdin=RESULT)
            subject = git(main_path, "log", "-1", "--format=%s", "main").stdout.strip()
            check(f"--message {message!r} の件名は 完了 だけ", r.returncode == 0 and subject == "完了",
                  repr(subject) + r.stdout + r.stderr)


def test_finish_bundles_done_commit_ship() -> None:
    say("task.py finish: done・コミット・ship が順に並び、ファイルが無ければコミットしない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _claimed(tmp)
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(
            wt1, "finish", "T-100", "--result-file", "-", "--message", "T-100: 完了", "--add", "work.txt", stdin=RESULT
        )
        kinds = [l.split("\t", 1)[0] for l in r.stdout.splitlines()]
        check(
            "DONE → COMMITTED → SHIPPED の順で終了コード0",
            r.returncode == 0 and [k for k in kinds if k in ("DONE", "COMMITTED", "SHIPPED")] == ["DONE", "COMMITTED", "SHIPPED"],
            r.stdout + r.stderr,
        )
        check("送った先の主ブランチにコミットがある", "T-100: 完了" in git(main_path, "log", "--oneline", "-3", "main").stdout)

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check(
            "ファイルを渡さなければ NO_COMMIT のあと ship まで進む",
            r.returncode == 0
            and "NO_COMMIT\t" in r.stdout
            and "COMMITTED" not in r.stdout
            and ("SHIPPED\t" in r.stdout or "NOTHING" in r.stdout),
            r.stdout + r.stderr,
        )


def _ship_rows(wt: str, **days_ago: int) -> None:
    """`{ID: 何日前}` の SHIPPED を台帳に足す（ID の `-` は `_` で渡す）。"""
    now = datetime.now(timezone.utc)
    rows = [
        json.dumps({"t": (now - timedelta(days=d)).isoformat(timespec="seconds"), "event": "ship",
                    "task": k.replace("_", "-"), "result": "SHIPPED"})
        for k, d in days_ago.items()
    ]
    write(os.path.join(ledger.flow_dir(ledger.ledger_root(cwd=wt)), "2000-01.jsonl"), "\n".join(rows) + "\n")


def _finish_lines(wt: str, message: str, *args: str) -> tuple[int, list[str], str]:
    r = run_task(wt, "finish", "T-100", "--result-file", "-", "--message", message, *args, stdin=RESULT)
    return r.returncode, [l for l in r.stdout.splitlines() if l.startswith("WARN_FIX_LINK")], r.stdout + r.stderr


def test_finish_refuses_missing_result_lines() -> None:
    say("task.py finish: ## 結果 の必須行が欠ければ終了コード2で、done もコミットも ship も打たない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _claimed(tmp)
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "T-100: 完了", "--add", "work.txt",
                     stdin="- 検証コマンド: 1 pass\n")
        check("欠けた行の名前を出して終了コード2",
              r.returncode == 2 and "- 規約:" in r.stderr, r.stdout + r.stderr)
        check("DONE・COMMITTED・SHIPPED のどれも出ない",
              not any(k in r.stdout for k in ("DONE", "COMMITTED", "SHIPPED")), r.stdout)
        check("主ブランチにコミットが無く、done の記録も無い",
              "T-100: 完了" not in git(main_path, "log", "--oneline", "-3", "main").stdout
              and not [e for e in flow_rows(wt1) if e["event"] == "done"])


def test_finish_stops_on_stop_lines() -> None:
    say("task.py finish: NOT_OWNER ではコミットも ship も打たず、検証が落ちれば VERIFY_FAILED で止まる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "束ねる", "todo", "sonnet", "Y", (), BODY))
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", "--add", "work.txt", stdin=RESULT)
        check(
            "着手していなければ NOT_OWNER で終了コード4、コミットも ship も無い",
            r.returncode == 4
            and r.stdout.startswith("NOT_OWNER\t")
            and "COMMITTED" not in r.stdout
            and "SHIPPED" not in r.stdout,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _claimed(tmp, verify="`sh -c 'exit 1'`")
        write(os.path.join(main_path, "unrelated.txt"), "x")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "mainだけの変更")
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "T-100: 完了", "--add", "work.txt", stdin=RESULT)
        check(
            "検証が落ちれば COMMITTED のあと VERIFY_FAILED で終了コード8",
            r.returncode == 8 and "COMMITTED\t" in r.stdout and "VERIFY_FAILED\t" in r.stdout,
            r.stdout + r.stderr,
        )


def test_finish_stops_on_commits_since_claim() -> None:
    say("task.py finish: claim のあとのコミットがあれば COMMITS_SINCE_CLAIM の行で止まり、コミットも ship も打たない")
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        write(os.path.join(wt1, "stray.txt"), "x")
        git(wt1, "add", "stray.txt")
        git(wt1, "commit", "-q", "-m", "委譲先のコミット")
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "T-100: 完了", "--add", "work.txt", stdin=RESULT)
        check(
            "DONE と COMMITS_SINCE_CLAIM を出して終了コード12、COMMITTED も SHIPPED も無い",
            r.returncode == 12
            and "DONE\t" in r.stdout
            and "COMMITS_SINCE_CLAIM\tT-100\t" in r.stdout
            and "COMMITTED" not in r.stdout
            and "SHIPPED" not in r.stdout,
            r.stdout + r.stderr,
        )


def test_done_twice_is_refused() -> None:
    say("task.py done: 済んだタスクの2回目は ALREADY_DONE で終了コード4、done は1件のまま。dropped で閉じたあとも同じ")
    for flags, kind in (((), "done"), (("--dropped",), "dropped")):
        with tempfile.TemporaryDirectory() as tmp:
            _main, wt1 = _claimed(tmp)
            first = run_task(wt1, "done", "T-100", *flags, "--result-file", "-", stdin=RESULT)
            second = run_task(wt1, "done", "T-100", *flags, "--result-file", "-", stdin=RESULT)
            check(f"{kind}: 1回目は DONE で終了コード0", first.returncode == 0 and first.stdout.startswith("DONE\t"), first.stdout + first.stderr)
            check(
                f"{kind}: 2回目は ALREADY_DONE で終了コード4、DONE は出ない",
                second.returncode == 4
                and second.stdout.startswith(f"ALREADY_DONE\tT-100\t{kind}")
                and "DONE\t" not in second.stdout.replace("ALREADY_DONE", ""),
                second.stdout + second.stderr,
            )
            check(
                "台帳の done は1件のまま",
                len([e for e in flow_rows(wt1) if e["event"] == "done"]) == 1,
            )


def test_finish_without_add_commits_changes() -> None:
    say("task.py finish: --add を省くと通常ファイルの変更を個別に1コミットにし、symlink と .tw/local は入れない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _claimed(tmp)
        write(os.path.join(wt1, "work.txt"), "x")
        os.symlink(tmp, os.path.join(wt1, "linked"))
        write(os.path.join(wt1, ".tw", "local", "note"), "x")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "完了", stdin=RESULT)
        check("COMMITTED を出す", "COMMITTED\t" in r.stdout, r.stdout + r.stderr)
        check(
            "symlink は SKIPPED と UNADDED で出て、残るので ship は DIRTY で止まる",
            "SKIPPED\tlinked\tsymlink" in r.stdout and "UNADDED\tlinked" in r.stdout and r.stdout.rstrip().endswith("DIRTY"),
            r.stdout,
        )
        shown = git(wt1, "show", "--stat", "--format=%s", "HEAD").stdout
        check(
            "件名は T-100: 完了 で、work.txt だけが入る",
            shown.startswith("T-100: 完了") and "work.txt" in shown and "linked" not in shown and ".tw/local" not in shown,
            shown,
        )


_ALLOW_FILE = ("-c", "protocol.file.allow=always")


def _with_submodule(tmp: str) -> tuple[str, str, str]:
    """サブモジュール `vendor/sub`（2コミット）を持つ主ブランチから着手した作業ツリー。`(wt1, 元のサブモジュールの先, 別の元リポジトリ)`。"""
    main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
    for name in ("sub-origin", "other-origin"):
        origin = os.path.join(tmp, name)
        os.makedirs(origin)
        git(origin, "init", "-q", "-b", "main")
        git(origin, "config", "user.email", "t@example.com")
        git(origin, "config", "user.name", "t")
        for n in (1, 2):
            write(os.path.join(origin, "f.txt"), str(n))
            git(origin, "add", "f.txt")
            git(origin, "commit", "-q", "-m", f"c{n}")
    git(main_path, *_ALLOW_FILE, "submodule", "add", "-q", os.path.join(tmp, "sub-origin"), "vendor/sub")
    git(main_path, "commit", "-q", "-m", "サブモジュールを足す")
    commit_task(main_path, taskfile.Task("T-100", "束ねる", "todo", "sonnet", "Y", (), BODY))
    run_task(wt1, "claim", "T-100")
    git(wt1, *_ALLOW_FILE, "submodule", "update", "--init", "-q")
    return wt1, git(wt1, "rev-parse", "HEAD:vendor/sub").stdout.strip(), os.path.join(tmp, "other-origin")


def _finish_without_add(wt1: str, kind_path: str, kind: str = "submodule", gitmodules: bool = False) -> str:
    """`--add` を省いて `finish` を打ち、`SKIPPED` の行と、コミットに通常ファイルだけが入ったことを確かめて出力を返す。

    `gitmodules` が真なら `.gitmodules` も `SKIPPED` で出て、`HEAD` の `.gitmodules` が変わらないことを見る。
    """
    gitmodules_before = git(wt1, "rev-parse", "HEAD:.gitmodules").stdout.strip()
    write(os.path.join(wt1, "work.txt"), "x")
    r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "完了", stdin=RESULT)
    check(f"SKIPPED\\t{kind_path}\\t{kind} が出て COMMITTED になる", f"SKIPPED\t{kind_path}\t{kind}" in r.stdout and "COMMITTED\t" in r.stdout, r.stdout + r.stderr)
    changed = git(wt1, "show", "--name-only", "--format=", "HEAD").stdout.split()
    check("コミットに work.txt が入り、サブモジュールのパスは入らない", "work.txt" in changed and kind_path not in changed, repr(changed))
    check(
        ".gitmodules はコミットに入らず、HEAD のものが変わらない",
        ".gitmodules" not in changed and git(wt1, "rev-parse", "HEAD:.gitmodules").stdout.strip() == gitmodules_before,
        repr(changed),
    )
    if gitmodules:
        check("SKIPPED\\t.gitmodules\\tsubmodule が出る", "SKIPPED\t.gitmodules\tsubmodule" in r.stdout, r.stdout)
    return r.stdout


def test_finish_resume_after_commits_since_claim() -> None:
    say("task.py finish --resume: COMMITS_SINCE_CLAIM で止まったあと、1回でコミットと ship まで進み done は二重にならない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _claimed(tmp)
        write(os.path.join(wt1, "stray.txt"), "x")
        git(wt1, "add", "stray.txt")
        git(wt1, "commit", "-q", "-m", "委譲先のコミット")
        write(os.path.join(wt1, "work.txt"), "x")
        first = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "完了", stdin=RESULT)
        check("1回目は終了コード12", first.returncode == 12, first.stdout + first.stderr)
        r = run_task(wt1, "finish", "T-100", "--resume", "--message", "完了")
        kinds = [l.split("\t", 1)[0] for l in r.stdout.splitlines()]
        check(
            "RESUMED → COMMITTED → SHIPPED の順で終了コード0",
            r.returncode == 0 and [k for k in kinds if k in ("RESUMED", "COMMITTED", "SHIPPED")] == ["RESUMED", "COMMITTED", "SHIPPED"],
            r.stdout + r.stderr,
        )
        check("DONE は出ず、台帳の done は1件のまま", "DONE" not in kinds and len([e for e in flow_rows(wt1) if e["event"] == "done"]) == 1)
        check("主ブランチに T-100: 完了 がある", "T-100: 完了" in git(main_path, "log", "--format=%s", "-3", "main").stdout)


def test_finish_twice_without_resume_is_refused() -> None:
    say("task.py finish: done 済みに --resume なしで打てば ALREADY_DONE で終了コード4。未 done の --resume は --result-file が無ければ終了コード2")
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        run_task(wt1, "done", "T-100", "--result-file", "-", stdin=RESULT)
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "完了", stdin=RESULT)
        check(
            "ALREADY_DONE で終了コード4、COMMITTED も SHIPPED も無い",
            r.returncode == 4 and "ALREADY_DONE\t" in r.stdout and "COMMITTED" not in r.stdout and "SHIPPED" not in r.stdout,
            r.stdout + r.stderr,
        )
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        r = run_task(wt1, "finish", "T-100", "--resume", "--message", "完了")
        check("done 前の --resume は --result-file が無ければ終了コード2", r.returncode == 2, r.stdout + r.stderr)


def _accept_edges(wt: str) -> list[str]:
    return [r.get("edge") for r in flow_rows(wt) if r["event"] == "lap" and r.get("stage") == "accept"]


def test_finish_closes_open_accept() -> None:
    say("task.py finish: accept が開いたままなら end を1つ書き、閉じ済み・accept が無いときは書かない")
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        run_task(wt1, "lap", "T-100", "accept", "start")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("開いた accept に end が1つ書かれる", r.returncode == 0 and _accept_edges(wt1) == ["start", "end"], r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        run_task(wt1, "lap", "T-100", "accept", "start")
        run_task(wt1, "lap", "T-100", "accept", "end")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("閉じ済みなら二重に書かない", r.returncode == 0 and _accept_edges(wt1) == ["start", "end"], r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        r = run_task(wt1, "finish", "T-100", "--dropped", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("accept が無ければ何も書かない", _accept_edges(wt1) == [], r.stdout + r.stderr)


def _other_repo_claimed(tmp: str, unmerged_branch: bool) -> tuple[str, str, str]:
    """着手側の作業ツリー・作業先の本体。`unmerged_branch` なら作業先に主ブランチへ未合流の枝 `t-100` があり、
    偽なら `claim` が作った枝 `t-100` とその作業ツリーを消して枝の無い作業先にする。"""
    own_main, wt1, _ = make_repo(os.path.join(tmp, "own"), branch="切らない", verify="`true`")
    work_main, _, _ = make_repo(os.path.join(tmp, "work"), verify="`true`")
    commit_task(own_main, taskfile.Task("T-100", "作業先で直す", "todo", "sonnet", "Y", (), task_body((("書く", "x"),), ["a.txt"], work_main)))
    run_task(wt1, "claim", "T-100")
    work_tree = claimed_work_tree(work_main, "t-100")
    if not unmerged_branch:
        git(work_main, "worktree", "remove", work_tree)
        git(work_main, "branch", "-D", "t-100")
    else:
        write(os.path.join(work_tree, "a.txt"), "x")
        git(work_tree, "add", "a.txt")
        git(work_tree, "commit", "-q", "-m", "a")
    return wt1, work_main, own_main


def _status_of(wt: str) -> str:
    return next((l for l in run_task(wt, "show", "T-100").stdout.splitlines() if l.startswith("status:")), "")


def test_finish_stops_on_unlanded_work_branch() -> None:
    say("task.py finish: 作業先の枝が主ブランチへ未合流なら WORK_NOT_LANDED で何も書かずに止まる")
    with tempfile.TemporaryDirectory() as tmp:
        wt1, work_main, _own = _other_repo_claimed(tmp, True)
        before = _status_of(wt1)
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check(
            "未合流の枝は WORK_NOT_LANDED（終了コード4）で、DONE を打たず status も変えない",
            r.returncode == 4 and r.stdout == f"WORK_NOT_LANDED\tT-100\t{work_main}\tt-100\n" and _status_of(wt1) == before,
            r.stdout + r.stderr,
        )
        r = run_task(wt1, "finish", "T-100", "--dropped", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("--dropped は枝を見ない", "WORK_NOT_LANDED" not in r.stdout and "DONE\t" in r.stdout, r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        wt1, work_main, _own = _other_repo_claimed(tmp, True)
        git(work_main, "merge", "-q", "--ff-only", "t-100")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("ff で合流したあとは通る", "WORK_NOT_LANDED" not in r.stdout and "DONE\t" in r.stdout, r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        wt1, _work_main, _own = _other_repo_claimed(tmp, False)
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("作業先に枝が無ければ通る", "WORK_NOT_LANDED" not in r.stdout and "DONE\t" in r.stdout, r.stdout + r.stderr)


def _named_claimed(tmp: str, land: str, absolute: bool = False) -> tuple[str, str, str, dict[str, str]]:
    """着手側の作業ツリー・作業先の本体・作業先の作業ツリー・env。`### 作業先` は登録簿の名前 plugins（`land` は引数）。
    `absolute` なら絶対パスで書く（登録簿の名前に解ける）。"""
    registry = f'\n[repos.plugins]\nland = "{land}"\n'
    _own_main, wt1, _ = make_repo(os.path.join(tmp, "own"), branch="切らない", verify="`true`", extra=registry)
    work_main, _, _ = make_repo(os.path.join(tmp, "work"), verify="`true`")
    xdg = os.path.join(tmp, "xdg")
    if os.environ.get("XDG_CONFIG_HOME") and os.path.isdir(os.environ["XDG_CONFIG_HOME"]):
        shutil.copytree(os.environ["XDG_CONFIG_HOME"], xdg)
    write(os.path.join(xdg, "tw", "repos.toml"), f'plugins = "{work_main}"\n')
    env = {"XDG_CONFIG_HOME": xdg}
    commit_task(_own_main, taskfile.Task("T-100", "作業先で直す", "todo", "sonnet", "Y", (), task_body((("書く", "x"),), ["a.txt"], work_main if absolute else "plugins")))
    claimed =run_task(wt1, "claim", "T-100", env=env)
    if claimed.returncode != 0:
        raise RuntimeError(claimed.stdout + claimed.stderr)
    work_tree = claimed_work_tree(work_main, "t-100")
    write(os.path.join(work_tree, "a.txt"), "x")
    git(work_tree, "add", "a.txt")
    git(work_tree, "commit", "-q", "-m", "a")
    return wt1, work_main, work_tree, env


def test_finish_work_landed_sha() -> None:
    say("task.py finish: 登録簿の land が branch・pr なら、tw land のあと枝の先端が work_landed の SHA と同じときだけ通る。ff は今のまま")
    for land in ("branch", "pr"):
        with tempfile.TemporaryDirectory() as tmp:
            wt1, work_main, work_tree, env = _named_claimed(tmp, land)
            r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT, env=env)
            check(f"land={land}: tw land を打っていなければ WORK_NOT_LANDED（終了コード4）",
                  r.returncode == 4 and r.stdout == f"WORK_NOT_LANDED\tT-100\t{work_main}\tt-100\n", r.stdout + r.stderr)
            landed = run_task(work_tree, "land", "t-100", env=env)
            check(f"land={land}: tw land は KEPT で終わる", landed.returncode == 0 and landed.stdout.startswith("KEPT\t"),
                  landed.stdout + landed.stderr)
            git(work_tree, "commit", "-q", "--allow-empty", "-m", "land のあとの足し")
            r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT, env=env)
            check(f"land={land}: tw land のあとにコミットを足すと WORK_NOT_LANDED",
                  r.returncode == 4 and r.stdout == f"WORK_NOT_LANDED\tT-100\t{work_main}\tt-100\n", r.stdout + r.stderr)
            landed = run_task(work_tree, "land", "t-100", env=env)
            r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT, env=env)
            check(f"land={land}: 打ち直した tw land のあとは通る", "WORK_NOT_LANDED" not in r.stdout and "DONE\t" in r.stdout,
                  landed.stdout + r.stdout + r.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            wt1, work_main, work_tree, env = _named_claimed(tmp, land)
            git(work_main, "worktree", "remove", "--force", work_tree)
            git(work_main, "branch", "-D", "t-100")
            r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT, env=env)
            check(f"land={land}: 枝が無ければ通る", "WORK_NOT_LANDED" not in r.stdout and "DONE\t" in r.stdout, r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        wt1, work_main, _work_tree, env = _named_claimed(tmp, "ff")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT, env=env)
        check("land=ff（名前で書いた作業先）: 未合流なら WORK_NOT_LANDED",
              r.returncode == 4 and r.stdout == f"WORK_NOT_LANDED\tT-100\t{work_main}\tt-100\n", r.stdout + r.stderr)
        git(work_main, "merge", "-q", "--ff-only", "t-100")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT, env=env)
        check("land=ff: ff で合流したあとは通る", "WORK_NOT_LANDED" not in r.stdout and "DONE\t" in r.stdout, r.stdout + r.stderr)


def test_finish_work_landed_absolute_path() -> None:
    say("task.py finish: `### 作業先` を絶対パスで書いても、登録簿の名前に解けて land が branch・pr なら名前で書いたときと同じに通る")
    for land in ("branch", "pr"):
        with tempfile.TemporaryDirectory() as tmp:
            wt1, work_main, work_tree, env = _named_claimed(tmp, land, absolute=True)
            r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT, env=env)
            check(f"land={land}（絶対パス）: tw land を打っていなければ WORK_NOT_LANDED",
                  r.returncode == 4 and r.stdout == f"WORK_NOT_LANDED\tT-100\t{work_main}\tt-100\n", r.stdout + r.stderr)
            landed = run_task(work_tree, "land", "t-100", env=env)
            check(f"land={land}（絶対パス）: tw land は KEPT で終わる", landed.stdout.startswith("KEPT\t"), landed.stdout + landed.stderr)
            r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT, env=env)
            check(f"land={land}（絶対パス）: tw land のあとは通る", "WORK_NOT_LANDED" not in r.stdout and "DONE\t" in r.stdout,
                  r.stdout + r.stderr)


def _accepted_then(tmp: str, steps: str, change: bool) -> tuple[str, object]:
    """`steps` の並びで着手し、独自の段 `x` のスキルを置いて受け入れの終わりの控えを書いたあと、`change` なら作業ツリーを変えて `finish` を打つ。"""
    _main, wt1 = _claimed(tmp, extra=f"steps = {steps}\n")
    write(os.path.join(wt1, ".claude", "skills", "x", "SKILL.md"),
          '---\nname: x\ndescription: 段\nmetadata:\n  workflow-step: "true"\n---\n本文\n')
    write(os.path.join(wt1, "work.txt"), "受け入れた中身")
    head, tree = tw_accept.snapshot(wt1)
    ledger.record_event(wt1, tw_accept.ACCEPTED_EVENT, "T-100", "sonnet", head=head, tree=tree)
    if change:
        write(os.path.join(wt1, "work.txt"), "受け入れのあとに変えた中身")
    return wt1, run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)


def test_finish_stops_on_change_after_accept() -> None:
    say("task.py finish: 並びの accept より後ろに独自の段があり、受け入れの終わりの控えのあとに作業ツリーが変わっていれば CHANGED_AFTER_ACCEPT で何も書かずに止まる")
    with tempfile.TemporaryDirectory() as tmp:
        wt1, r = _accepted_then(tmp, '["implement", "accept", "x"]', change=True)
        issue = beads.show(wt1, beads.to_bd_id("T-100"))
        row = r.stdout.splitlines()[0].split("\t") if r.stdout else []
        check("変えたら CHANGED_AFTER_ACCEPT（content）で終了コード4、done を書かない",
              r.returncode == 4 and len(row) == 4 and row[:2] == ["CHANGED_AFTER_ACCEPT", "T-100"]
              and os.path.realpath(row[2]) == os.path.realpath(wt1) and row[3] == "content"
              and "DONE\t" not in r.stdout and issue is not None and issue.status == "in_progress", r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        _wt1, r = _accepted_then(tmp, '["implement", "accept", "x"]', change=False)
        check("変えなければ通る", r.returncode == 0 and "CHANGED_AFTER_ACCEPT" not in r.stdout and "DONE\t" in r.stdout,
              r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        _wt1, r = _accepted_then(tmp, '["implement", "x", "accept"]', change=True)
        check("独自の段が accept の後ろに無い並びは変えても通る",
              r.returncode == 0 and "CHANGED_AFTER_ACCEPT" not in r.stdout and "DONE\t" in r.stdout, r.stdout + r.stderr)


TESTS = (
    test_finish_stops_on_change_after_accept,
    test_finish_stops_on_unlanded_work_branch,
    test_finish_work_landed_sha,
    test_finish_work_landed_absolute_path,
    test_finish_closes_open_accept,
    test_finish_without_add_commits_changes,
    test_finish_resume_after_commits_since_claim,
    test_finish_twice_without_resume_is_refused,
    test_done_twice_is_refused,
    test_finish_stops_on_commits_since_claim,
    test_finish_bundles_done_commit_ship,
    test_finish_without_task_id_in_subject,
    test_finish_stops_on_stop_lines,
    test_finish_refuses_missing_result_lines,
)


if __name__ == "__main__":
    refuse_direct_run()

