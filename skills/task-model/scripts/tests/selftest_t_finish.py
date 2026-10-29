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
from selftest_fixtures import BODY, commit_task, flow_rows, make_repo, run_task  # noqa: E402

RESULT = RESULT_FULL


def _claimed(tmp: str, verify: str | None = None) -> tuple[str, str]:
    main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify=verify)
    commit_task(main_path, taskfile.Task("T-100", "束ねる", "todo", "sonnet", "Y", (), BODY))
    run_task(wt1, "claim", "T-100")
    return main_path, wt1


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


TESTS = (
    test_finish_without_add_commits_changes,
    test_finish_resume_after_commits_since_claim,
    test_finish_twice_without_resume_is_refused,
    test_done_twice_is_refused,
    test_finish_stops_on_commits_since_claim,
    test_finish_bundles_done_commit_ship,
    test_finish_stops_on_stop_lines,
    test_finish_refuses_missing_result_lines,
)


if __name__ == "__main__":
    refuse_direct_run()

