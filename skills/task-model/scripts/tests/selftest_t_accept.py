from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from selftest_support import check, git, refuse_direct_run, say, write  # noqa: E402
import beads  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402
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


REVIEW_SNAPSHOT_PY = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core", "review_snapshot.py")
)


def _other_repo_claimed(
    tmp: str,
    difficulty: str = "sonnet",
    before_claim: str | None = None,
    worktree: bool = True,
    steps: tuple[tuple[str, str], ...] = (("書く", "x"),),
) -> tuple[str, str, str]:
    """着手側の作業ツリー・作業先の本体・作業先の作業ツリー（枝 `t-100`）。`before_claim` は着手の前に本体へ置く未追跡のファイル。"""
    own_main, wt1, _ = make_repo(os.path.join(tmp, "own"), branch="切らない", verify="`true`", format_command="`true`")
    work_main, _, _ = make_repo(os.path.join(tmp, "work"), verify="`true`")
    body = task_body(steps, ["shared.txt"], work_main)
    commit_task(own_main, taskfile.Task("T-100", "作業先で直す", "todo", difficulty, "Y", (), body))
    if before_claim is not None:
        write(os.path.join(work_main, before_claim), "before\n")
    run_task(wt1, "claim", "T-100")
    work_tree = os.path.join(tmp, "work-t-100")
    if worktree:
        git(work_main, "worktree", "add", "-q", "-b", "t-100", work_tree, "main")
    return wt1, work_main, work_tree


def _commit(tree: str, name: str, text: str) -> None:
    write(os.path.join(tree, name), text)
    git(tree, "add", name)
    git(tree, "commit", "-q", "-m", name)


def test_accept_other_repo_main_dirty() -> None:
    say("task.py accept: 作業先の本体の汚れを、着手の控え・委譲先の枝と比べて3種類の行で出す")
    with tempfile.TemporaryDirectory() as tmp:
        wt1, work_main, work_tree = _other_repo_claimed(tmp, before_claim="pre.txt")
        _commit(work_tree, "note.md", "x\n")
        run_task(work_tree, "verify")
        r = run_task(wt1, "accept", "T-100")
        check(
            "着手の前からの汚れは MAIN_DIRTY_BEFORE を出して続ける",
            r.returncode == 0 and "MAIN_DIRTY_BEFORE\tpre.txt" in r.stdout.splitlines() and r.stdout.splitlines()[-1].startswith("VERIFIED_SAME\t"),
            r.stdout + r.stderr,
        )

        write(os.path.join(work_main, "note.md"), "x\n")
        r = run_task(wt1, "accept", "T-100")
        check(
            "枝と同じ中身を本体に置くと MAIN_DIRTY_FROM_DELEGATE で終了コード4、plan-check を打たない",
            r.returncode == 4 and "MAIN_DIRTY_FROM_DELEGATE\tnote.md" in r.stdout.splitlines() and "PLAN_" not in r.stdout,
            r.stdout + r.stderr,
        )

        write(os.path.join(work_main, "note.md"), "other\n")
        r = run_task(wt1, "accept", "T-100", "--after-review")
        check(
            "中身が違えば MAIN_DIRTY_UNKNOWN で終了コード4（--after-review でも見る）、verify-check を打たない",
            r.returncode == 4 and "MAIN_DIRTY_UNKNOWN\tnote.md" in r.stdout.splitlines() and "VERIFIED" not in r.stdout,
            r.stdout + r.stderr,
        )

        os.remove(os.path.join(work_main, "note.md"))
        os.remove(os.path.join(wt1, ".tw", "local", "task-main-dirty", "T-100"))
        r = run_task(wt1, "accept", "T-100", "--after-review")
        check(
            "控えの無い着手では着手の前からの汚れも MAIN_DIRTY_UNKNOWN で止まる",
            r.returncode == 4 and r.stdout.splitlines() == ["MAIN_DIRTY_UNKNOWN\tpre.txt"],
            r.stdout + r.stderr,
        )


THREE_STEPS = (("書く", "x"), ("試す", "x"), ("直す", "x"))


def test_accept_not_last_step() -> None:
    say("task.py accept: 段が2つ以上の計画で 1..N-1 の段の控えが欠ければ NOT_LAST_STEP で止まり、lap も書かない")
    accept_laps = lambda wt: [e for e in flow_rows(wt) if e["event"] == "lap" and e.get("stage") == "accept"]
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "段が3つ", "todo", "sonnet", "Y", (), task_body(THREE_STEPS, ["shared.txt"])))
        run_task(wt1, "claim", "T-100")
        r = run_task(wt1, "accept", "T-100")
        check("控えが無ければ欠けた段を並べて終了コード4、accept の lap を書かない",
              r.returncode == 4 and r.stdout == "NOT_LAST_STEP\tT-100\t1,2/3\n" and accept_laps(wt1) == [],
              r.stdout + r.stderr)
        run_task(wt1, "step", "T-100", "1")
        r = run_task(wt1, "accept", "T-100")
        check("段1の控えのあとは段2だけが欠ける", r.returncode == 4 and r.stdout == "NOT_LAST_STEP\tT-100\t2/3\n", r.stdout + r.stderr)
        run_task(wt1, "pause", "T-100", "2")
        r = run_task(wt1, "accept", "T-100")
        check("pause の段の控えでも揃い、従来の並びに進む",
              r.returncode == 0 and r.stdout.startswith("LAPPED\t") and "NOT_LAST_STEP" not in r.stdout, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "メインで行う", "todo", "sonnet", "N", (), task_body(THREE_STEPS, ["shared.txt"])))
        run_task(wt1, "claim", "T-100")
        r = run_task(wt1, "accept", "T-100")
        check("loopable: N（メインで行う流れ）は控えが無くても進む",
              r.returncode == 0 and "NOT_LAST_STEP" not in r.stdout, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        wt1, _work_main, work_tree = _other_repo_claimed(tmp, steps=THREE_STEPS[:2])
        _commit(work_tree, "note.md", "x\n")
        r = run_task(wt1, "accept", "T-100")
        check("別のリポジトリの作業先でも着手側の控えで判定する", r.returncode == 4 and r.stdout == "NOT_LAST_STEP\tT-100\t1/2\n", r.stdout + r.stderr)
        run_task(wt1, "step", "T-100", "1")
        run_task(work_tree, "verify")
        r = run_task(wt1, "accept", "T-100")
        check("段1の控えのあとは作業先の verify-check まで進む",
              r.returncode == 0 and r.stdout.splitlines()[-1].startswith("VERIFIED_SAME\t"), r.stdout + r.stderr)


TESTS = (
    test_accept_not_last_step,
    test_accept_bundles_acceptance_steps,
    test_accept_stops_on_stop_lines,
    test_accept_other_repo_main_dirty,
)


if __name__ == "__main__":
    refuse_direct_run()

