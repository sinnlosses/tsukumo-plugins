from __future__ import annotations

import os
import tempfile

from selftest_support import check, refuse_direct_run, say, write  # noqa: E402
import beads  # noqa: E402
import taskfile  # noqa: E402
from selftest_fixtures import PLANNED_BODY, commit_task, flow_rows, make_repo, run_task  # noqa: E402


def _claimed_repo(tmp: str, format_command: str = "`true`") -> str:
    main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`true`", format_command=format_command)
    commit_task(main_path, taskfile.Task("T-100", "受け入れ", "todo", "sonnet", "Y", (), PLANNED_BODY))
    run_task(wt1, "claim", "T-100")
    return wt1


def test_accept_bundles_acceptance_steps() -> None:
    say("task.py accept: 文書だけの差分は判定 → 整形 → verify-check まで通り、コード差分は REVIEW と SNAPSHOT で返る")
    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp)
        write(os.path.join(wt1, "note.md"), "x\n")
        run_task(wt1, "verify")
        r = run_task(wt1, "accept", "T-100")
        lines = r.stdout.splitlines()
        check(
            "文書だけなら lap・plan-check・SKIP・FORMATTED のあと VERIFIED_SAME で終わる",
            r.returncode == 0
            and lines[0].startswith("LAPPED\t")
            and any(l.startswith("PLAN_") for l in lines)
            and "SKIP\tdocs-only" in lines
            and "FORMATTED" in lines
            and lines[-1].startswith("VERIFIED_SAME\t"),
            r.stdout + r.stderr,
        )

        write(os.path.join(wt1, "code.py"), "x = 1\n")
        r = run_task(wt1, "accept", "T-100")
        lines = r.stdout.splitlines()
        check(
            "コード差分なら REVIEW と SNAPSHOT で返り、整形・verify-check は打たない",
            r.returncode == 0
            and "REVIEW\tcode\t1" in lines
            and lines[-1].startswith("SNAPSHOT\t")
            and "FORMATTED" not in lines
            and not any(l.startswith(("VERIFIED_SAME", "NOT_VERIFIED")) for l in lines),
            r.stdout + r.stderr,
        )

        r = run_task(wt1, "accept", "T-100", "--after-review")
        check(
            "--after-review は整形と verify-check だけ",
            r.returncode == 0
            and r.stdout.splitlines()[0] == "FORMATTED"
            and r.stdout.splitlines()[-1].startswith("NOT_VERIFIED\t")
            and "LAPPED" not in r.stdout,
            r.stdout + r.stderr,
        )


def test_accept_stops_on_stop_lines() -> None:
    say("task.py accept: 整形が落ちれば FORMAT_FAILED、着手していなければ NOT_CLAIMED で止まる")
    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp, format_command="`echo broken; exit 3`")
        r = run_task(wt1, "accept", "T-100", "--after-review")
        check(
            "整形が落ちれば FORMAT_FAILED で終了コード10、verify-check は打たない",
            r.returncode == 10 and "FORMAT_FAILED\t" in r.stdout and "NOT_VERIFIED" not in r.stdout,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "受け入れ", "todo", "sonnet", "Y", (), PLANNED_BODY))
        r = run_task(wt1, "accept", "T-100")
        check(
            "着手していなければ NOT_CLAIMED のあとを打たない",
            r.returncode != 0 and r.stdout.startswith("NOT_CLAIMED\t") and "PLAN_" not in r.stdout,
            r.stdout + r.stderr,
        )


def test_accept_records_accept_end() -> None:
    say("task.py accept: 整形と verify-check まで通った回だけ accept の end を flow に残し、REVIEW で返る回は残さない")
    accept_ends = lambda wt: [
        e for e in flow_rows(wt) if e["event"] == "lap" and e.get("stage") == "accept" and e.get("edge") == "end"
    ]
    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp)
        write(os.path.join(wt1, "note.md"), "x\n")
        run_task(wt1, "verify")
        r = run_task(wt1, "accept", "T-100")
        check("文書だけ（レビュー不要）の回は end が1つ残る", r.returncode == 0 and len(accept_ends(wt1)) == 1, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp)
        write(os.path.join(wt1, "code.py"), "x = 1\n")
        r = run_task(wt1, "accept", "T-100")
        check("REVIEW で返る回は end を残さない", "REVIEW\tcode\t1" in r.stdout and accept_ends(wt1) == [], r.stdout + r.stderr)
        r = run_task(wt1, "accept", "T-100", "--after-review")
        check("--after-review の終わりで end が1つ残る", r.returncode == 0 and len(accept_ends(wt1)) == 1, r.stdout + r.stderr)
        check("end の行は LAPPED として表に出さない", "LAPPED" not in r.stdout, r.stdout)


def test_accept_end_pairs_with_start_for_any_id_spelling() -> None:
    say("task.py accept: ID の書き方（表示・小文字・Beads）によらず、accept の start と end が同じ task で残る")
    for spelling in ("T-100", "t-100", beads.to_bd_id("T-100")):
        with tempfile.TemporaryDirectory() as tmp:
            wt1 = _claimed_repo(tmp)
            write(os.path.join(wt1, "note.md"), "x\n")
            run_task(wt1, "verify")
            r = run_task(wt1, "accept", spelling)
            edges = [(e["task"], e.get("edge")) for e in flow_rows(wt1) if e["event"] == "lap" and e.get("stage") == "accept"]
            check(f"{spelling}: start と end が T-100 に残る", r.returncode == 0 and edges == [("T-100", None), ("T-100", "end")], repr(edges) + r.stdout + r.stderr)


TESTS = (
    test_accept_end_pairs_with_start_for_any_id_spelling,
    test_accept_records_accept_end,
    test_accept_bundles_acceptance_steps,
    test_accept_stops_on_stop_lines,
)


if __name__ == "__main__":
    refuse_direct_run()
