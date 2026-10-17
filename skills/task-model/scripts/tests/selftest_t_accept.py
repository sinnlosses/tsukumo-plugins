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
        stage_edges = lambda stage: [e.get("edge") for e in flow_rows(wt1) if e["event"] == "lap" and e.get("stage") == stage]
        check("tw accept が打つ accept・review は start として残る", stage_edges("accept") == ["start"] and stage_edges("review") == ["start"], repr(flow_rows(wt1)))
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
            check(f"{spelling}: start と end が T-100 に残る", r.returncode == 0 and edges == [("T-100", "start"), ("T-100", "end")], repr(edges) + r.stdout + r.stderr)


def test_accept_closes_open_delegate() -> None:
    say("task.py accept: 最初の呼び出しで、開いた delegate の end を accept の start の直後に1行だけ書く")
    laps = lambda wt: [(e.get("stage"), e.get("edge")) for e in flow_rows(wt) if e["event"] == "lap"]
    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp)
        run_task(wt1, "lap", "T-100", "delegate", "start")
        write(os.path.join(wt1, "code.py"), "x = 1\n")
        r = run_task(wt1, "accept", "T-100")
        check("開いた delegate は accept の start の直後に閉じる",
              laps(wt1) == [("delegate", "start"), ("accept", "start"), ("delegate", "end"), ("review", "start")],
              repr(laps(wt1)) + r.stdout + r.stderr)
        check("閉じた行は LAPPED として表に出さない", r.stdout.count("LAPPED") == 2, r.stdout)
        run_task(wt1, "accept", "T-100", "--after-review")
        check("--after-review は delegate を閉じ直さない", laps(wt1).count(("delegate", "end")) == 1, repr(laps(wt1)))

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp)
        run_task(wt1, "lap", "T-100", "delegate", "start")
        run_task(wt1, "lap", "T-100", "delegate", "end")
        write(os.path.join(wt1, "note.md"), "x\n")
        run_task(wt1, "verify")
        run_task(wt1, "accept", "T-100")
        check("手で閉じた delegate には end を足さない", laps(wt1).count(("delegate", "end")) == 1, repr(laps(wt1)))

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp)
        write(os.path.join(wt1, "note.md"), "x\n")
        run_task(wt1, "verify")
        run_task(wt1, "accept", "T-100")
        check("delegate の無い流れには何も書かない", all(stage != "delegate" for stage, _ in laps(wt1)), repr(laps(wt1)))

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp)
        run_task(wt1, "lap", "T-100", "delegate", "start")
        r = run_task(wt1, "lap", "T-100", "accept", "start")
        check("tw accept を使わない受け入れ（別のリポジトリ）で手で打つ accept の start も、開いた delegate を閉じる",
              laps(wt1) == [("delegate", "start"), ("accept", "start"), ("delegate", "end")]
              and r.stdout == "LAPPED\tT-100\taccept\tstart\n",
              repr(laps(wt1)) + r.stdout + r.stderr)


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


def test_accept_other_repo() -> None:
    say("task.py accept: 作業先が別のリポジトリなら、作業先の作業ツリーで範囲つきの判定・木の控え・verify-check を打つ")
    with tempfile.TemporaryDirectory() as tmp:
        wt1, _work_main, work_tree = _other_repo_claimed(tmp)
        _commit(work_tree, "note.md", "x\n")
        run_task(work_tree, "verify")
        r = run_task(wt1, "accept", "T-100")
        lines = r.stdout.splitlines()
        check(
            "文書だけなら NO_RANGE を出さず SKIP のあと作業先の控えで VERIFIED_SAME、整形は打たない",
            r.returncode == 0
            and "SKIP\tdocs-only" in lines
            and "NO_RANGE" not in r.stdout
            and "FORMATTED" not in lines
            and lines[-1].startswith("VERIFIED_SAME\t"),
            r.stdout + r.stderr,
        )

        _commit(work_tree, "code.py", "x = 1\n")
        r = run_task(wt1, "accept", "T-100")
        snapshot = subprocess.run([sys.executable, REVIEW_SNAPSHOT_PY], cwd=work_tree, capture_output=True, text=True).stdout.strip()
        check(
            "コードのコミットなら REVIEW と、作業先の作業ツリーの木の SNAPSHOT",
            r.returncode == 0 and "REVIEW\tcode\t1" in r.stdout.splitlines() and r.stdout.splitlines()[-1] == f"SNAPSHOT\t{snapshot}",
            r.stdout + r.stderr,
        )

        r = run_task(wt1, "accept", "T-100", "--after-review")
        check(
            "--after-review も作業先の控えを見る（コミットのあと verify していないので NOT_VERIFIED）、整形は打たない",
            r.returncode == 0 and r.stdout.splitlines()[-1].startswith("NOT_VERIFIED\t") and "FORMATTED" not in r.stdout,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        wt1, _work_main, work_tree = _other_repo_claimed(tmp, difficulty="opus")
        _commit(work_tree, "note.md", "x\n")
        r = run_task(wt1, "accept", "T-100")
        check("difficulty が opus なら REVIEW\topus", r.returncode == 0 and "REVIEW\topus" in r.stdout.splitlines(), r.stdout + r.stderr)


def test_accept_other_repo_missing_worktree() -> None:
    say("task.py accept: 作業先の作業ツリー（枝 t-100）が無ければ MISSING で止まる")
    with tempfile.TemporaryDirectory() as tmp:
        wt1, _work_main, _work_tree = _other_repo_claimed(tmp, worktree=False)
        r = run_task(wt1, "accept", "T-100")
        check(
            "MISSING で終了コード6、plan-check を打たない",
            r.returncode == 6 and any(l.startswith("MISSING\t") and "t-100" in l for l in r.stdout.splitlines()) and "PLAN_" not in r.stdout,
            r.stdout + r.stderr,
        )


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


def test_lap_end_not_doubled() -> None:
    say("task.py lap・accept: end は開いている段にだけ書き、手打ちと tw の end が重ならない")
    edges = lambda wt, stage: [e.get("edge") for e in flow_rows(wt) if e["event"] == "lap" and e.get("stage") == stage]
    with tempfile.TemporaryDirectory() as tmp:
        wt1 = _claimed_repo(tmp)
        run_task(wt1, "lap", "T-100", "delegate", "start")
        write(os.path.join(wt1, "note.md"), "x\n")
        run_task(wt1, "verify")
        run_task(wt1, "accept", "T-100")
        r = run_task(wt1, "lap", "T-100", "delegate", "end")
        check("accept が閉じた delegate へ手で end を打っても LAPPED を出し、end は1つのまま",
              r.stdout == "LAPPED\tT-100\tdelegate\tend\n" and edges(wt1, "delegate") == ["start", "end"],
              repr(edges(wt1, "delegate")) + r.stdout + r.stderr)
        run_task(wt1, "accept", "T-100", "--after-review")
        run_task(wt1, "accept", "T-100", "--after-review")
        check("--after-review を重ねても accept の end は1つ", edges(wt1, "accept") == ["start", "end"], repr(edges(wt1, "accept")))
        r = run_task(wt1, "lap", "T-100", "land", "end")
        check("開いていない段への end は何も書かない",
              r.stdout == "LAPPED\tT-100\tland\tend\n" and edges(wt1, "land") == [], repr(edges(wt1, "land")) + r.stdout)
        run_task(wt1, "lap", "T-100", "land", "start")
        run_task(wt1, "lap", "T-100", "land", "end")
        check("開いた段への手打ちの end は書く", edges(wt1, "land") == ["start", "end"], repr(edges(wt1, "land")))


TESTS = (
    test_accept_not_last_step,
    test_lap_end_not_doubled,
    test_accept_closes_open_delegate,
    test_accept_end_pairs_with_start_for_any_id_spelling,
    test_accept_records_accept_end,
    test_accept_bundles_acceptance_steps,
    test_accept_stops_on_stop_lines,
    test_accept_other_repo,
    test_accept_other_repo_missing_worktree,
    test_accept_other_repo_main_dirty,
)


if __name__ == "__main__":
    refuse_direct_run()
