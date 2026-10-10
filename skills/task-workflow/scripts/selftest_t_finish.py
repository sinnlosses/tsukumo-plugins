from __future__ import annotations

import os
import tempfile

from selftest_support import check, git, say, write  # noqa: E402
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


TESTS = (
    test_finish_bundles_done_commit_ship,
    test_finish_stops_on_stop_lines,
)
