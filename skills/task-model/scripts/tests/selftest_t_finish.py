from __future__ import annotations

import os
import tempfile

from selftest_support import RESULT_FULL, check, git, refuse_direct_run, say, write  # noqa: E402
import beads  # noqa: E402
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


def test_finish_reflection_flag() -> None:
    say("task.py finish --reflection: 渡した値が台帳に残り、不正な値は終了コード2で done もコミットも打たない")
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        r = run_task(wt1, "finish", "T-100", "--reflection", "some", "--result-file", "-", "--message", "x", stdin=RESULT)
        done = [e for e in flow_rows(wt1) if e["event"] == "done"]
        check("結果の行が兆候なしでも --reflection some なら reflection は some",
              r.returncode == 0 and len(done) == 1 and done[0]["reflection"] == "some", r.stdout + r.stderr + repr(done))
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(wt1, "finish", "T-100", "--reflection", "bogus", "--result-file", "-", "--message", "x",
                     "--add", "work.txt", stdin=RESULT)
        check("不正な値は終了コード2で DONE も COMMITTED も無く、done は記録されない",
              r.returncode == 2 and "DONE" not in r.stdout and "COMMITTED" not in r.stdout
              and not [e for e in flow_rows(wt1) if e["event"] == "done"], r.stdout + r.stderr)


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


def test_finish_prints_open_span() -> None:
    say("task.py finish: end の無い start の段を OPEN_SPAN で出し、終了コードは変えない。対が揃えば出さない")
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        run_task(wt1, "lap", "T-100", "delegate", "start")
        run_task(wt1, "lap", "T-100", "review", "start")
        run_task(wt1, "lap", "T-100", "review", "end")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        open_lines = [l for l in r.stdout.splitlines() if l.startswith("OPEN_SPAN")]
        check("閉じていない delegate だけ OPEN_SPAN に出て終了コード0", r.returncode == 0 and open_lines == ["OPEN_SPAN\tT-100\tdelegate"], r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        run_task(wt1, "lap", "T-100", "delegate", "start")
        run_task(wt1, "lap", "T-100", "delegate", "end")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("対が揃っていれば OPEN_SPAN は出ない", r.returncode == 0 and "OPEN_SPAN" not in r.stdout, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        stages = ("delegate", "accept", "review", "retro", "land")
        for stage in stages:
            run_task(wt1, "lap", "T-100", stage, "start")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        open_lines = [l for l in r.stdout.splitlines() if l.startswith("OPEN_SPAN")]
        check(
            "<段> start だけ打って end を打ち漏らすと、tw が閉じる delegate（accept の start）・retro（finish）のほかはどの段も OPEN_SPAN に出る",
            r.returncode == 0 and open_lines == [f"OPEN_SPAN\tT-100\t{s}" for s in stages if s not in ("delegate", "retro")],
            r.stdout + r.stderr,
        )


def test_finish_closes_open_retro() -> None:
    say("task.py finish: 開いた retro の end を done の前に1行だけ書き、開いていなければ書かない")
    retro_edges = lambda wt: [e.get("edge") for e in flow_rows(wt) if e["event"] == "lap" and e.get("stage") == "retro"]
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        run_task(wt1, "lap", "T-100", "retro", "start")
        run_task(wt1, "lap", "T-100", "retro", "start")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        kinds = [(e["event"], e.get("stage"), e.get("edge")) for e in flow_rows(wt1)]
        check("重ねた start に end が1つ続き、OPEN_SPAN は出ない",
              r.returncode == 0 and retro_edges(wt1) == ["start", "start", "end"] and "OPEN_SPAN" not in r.stdout,
              repr(kinds) + r.stdout + r.stderr)
        check("retro の end は done より前",
              kinds.index(("lap", "retro", "end")) < next(i for i, k in enumerate(kinds) if k[0] == "done"), repr(kinds))

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        run_task(wt1, "lap", "T-100", "retro", "start")
        run_task(wt1, "lap", "T-100", "retro", "end")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("手で閉じた retro には end を足さない", r.returncode == 0 and retro_edges(wt1) == ["start", "end"], r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "x", stdin=RESULT)
        check("retro の無い流れには何も書かない", r.returncode == 0 and retro_edges(wt1) == [], r.stdout + r.stderr)


def test_finish_open_span_for_any_id_spelling() -> None:
    say("task.py finish: ID の書き方（表示・小文字・Beads）によらず、終わりの無い段を T-100 の OPEN_SPAN で出す")
    for spelling in ("T-100", "t-100", beads.to_bd_id("T-100")):
        with tempfile.TemporaryDirectory() as tmp:
            _main, wt1 = _claimed(tmp)
            run_task(wt1, "lap", "T-100", "delegate", "start")
            r = run_task(wt1, "finish", spelling, "--result-file", "-", "--message", "x", stdin=RESULT)
            open_lines = [l for l in r.stdout.splitlines() if l.startswith("OPEN_SPAN")]
            check(f"{spelling}: OPEN_SPAN\\tT-100\\tdelegate が出る", r.returncode == 0 and open_lines == ["OPEN_SPAN\tT-100\tdelegate"], r.stdout + r.stderr)


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
            "symlink は SKIPPED_LINK と UNADDED で出て、残るので ship は DIRTY で止まる",
            "SKIPPED_LINK\tlinked" in r.stdout and "UNADDED\tlinked" in r.stdout and r.stdout.rstrip().endswith("DIRTY"),
            r.stdout,
        )
        shown = git(wt1, "show", "--stat", "--format=%s", "HEAD").stdout
        check(
            "件名は T-100: 完了 で、work.txt だけが入る",
            shown.startswith("T-100: 完了") and "work.txt" in shown and "linked" not in shown and ".tw/local" not in shown,
            shown,
        )


def test_finish_subject_prefix_once() -> None:
    say("task.py finish: 件名の T-xxx: は一度だけ付く。旧形式の --message 'T-100: …' と --add 明示も従来どおり通る")
    for message in ("完了", "T-100: 完了"):
        with tempfile.TemporaryDirectory() as tmp:
            main_path, wt1 = _claimed(tmp)
            write(os.path.join(wt1, "work.txt"), "x")
            r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", message, "--add", "work.txt", stdin=RESULT)
            subject = git(main_path, "log", "--format=%s", "-1", "main").stdout.strip()
            check(f"{message!r}: 件名は T-100: 完了", r.returncode == 0 and subject == "T-100: 完了", r.stdout + r.stderr + subject)


def test_finish_unadded_warning() -> None:
    say("task.py finish: --add で渡さなかった変更が残れば UNADDED で知らせる")
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = _claimed(tmp)
        write(os.path.join(wt1, "a.txt"), "x")
        write(os.path.join(wt1, "b.txt"), "x")
        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "完了", "--add", "a.txt", stdin=RESULT)
        check("UNADDED のあとに b.txt が出て a.txt は出ない", "UNADDED\tb.txt" in r.stdout and "UNADDED\ta.txt" not in r.stdout, r.stdout + r.stderr)


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
    test_finish_subject_prefix_once,
    test_finish_unadded_warning,
    test_finish_resume_after_commits_since_claim,
    test_finish_twice_without_resume_is_refused,
    test_done_twice_is_refused,
    test_finish_open_span_for_any_id_spelling,
    test_finish_prints_open_span,
    test_finish_closes_open_retro,
    test_finish_stops_on_commits_since_claim,
    test_finish_bundles_done_commit_ship,
    test_finish_reflection_flag,
    test_finish_stops_on_stop_lines,
    test_finish_refuses_missing_result_lines,
)


if __name__ == "__main__":
    refuse_direct_run()
