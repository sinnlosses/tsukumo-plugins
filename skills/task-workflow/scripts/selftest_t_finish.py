from __future__ import annotations

import os
import tempfile

from selftest_support import check, git, refuse_direct_run, say, write  # noqa: E402
import beads  # noqa: E402
import taskfile  # noqa: E402
from selftest_fixtures import BODY, commit_task, make_repo, run_task  # noqa: E402

RESULT = "- 検証: x\n- 振り返り: 兆候なし\n"


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
            "手順書の形（<段> start だけ打って end を打ち漏らす）は、どの段も OPEN_SPAN に出る",
            r.returncode == 0 and open_lines == [f"OPEN_SPAN\tT-100\t{s}" for s in stages],
            r.stdout + r.stderr,
        )


def test_finish_open_span_for_any_id_spelling() -> None:
    say("task.py finish: ID の書き方（表示・小文字・Beads）によらず、終わりの無い段を T-100 の OPEN_SPAN で出す")
    for spelling in ("T-100", "t-100", beads.to_bd_id("T-100")):
        with tempfile.TemporaryDirectory() as tmp:
            _main, wt1 = _claimed(tmp)
            run_task(wt1, "lap", "T-100", "delegate", "start")
            r = run_task(wt1, "finish", spelling, "--result-file", "-", "--message", "x", stdin=RESULT)
            open_lines = [l for l in r.stdout.splitlines() if l.startswith("OPEN_SPAN")]
            check(f"{spelling}: OPEN_SPAN\\tT-100\\tdelegate が出る", r.returncode == 0 and open_lines == ["OPEN_SPAN\tT-100\tdelegate"], r.stdout + r.stderr)


TESTS = (
    test_finish_open_span_for_any_id_spelling,
    test_finish_prints_open_span,
    test_finish_stops_on_commits_since_claim,
    test_finish_bundles_done_commit_ship,
    test_finish_stops_on_stop_lines,
)


if __name__ == "__main__":
    refuse_direct_run()
