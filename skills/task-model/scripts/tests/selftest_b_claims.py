"""着手の印の控えと、同時の sync で着手の印が消えないことの自己テスト（`selftest_beads.py` の部品）。"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(HERE, "..", "core"))
sys.path.insert(1, os.path.join(HERE, "..", "tw"))

import beads  # noqa: E402
from selftest_b_fakegithub import FakeGitHub, github  # noqa: E402
from selftest_b_support import claim_snapshots, new, num, work_and_done  # noqa: E402
from selftest_fixtures import run_task, start_task  # noqa: E402
from selftest_support import check, refuse_direct_run, say  # noqa: E402


def _claim_and_push(fake: "FakeGitHub", main_path: str, wt: str, summary: str, *extra: str) -> tuple[str, str, int]:
    """`wt` が着手して本体の sync で送り、GitHub の側の更新時刻を先へずらす。`(タスクID, Beads ID, Issue 番号)`。"""
    a = new(main_path, summary, *extra)
    r = run_task(wt, "claim", a)
    if r.returncode != 0:
        raise RuntimeError(f"claim 失敗: {r.stdout}{r.stderr}")
    run_task(main_path, "sync")
    fake.clock_offset = 60
    return a, beads.to_bd_id(a), num(a)


def test_claim_snapshot_cleared_on_release_done_ship() -> None:
    say("着手の印の控え: release・done・ship で手放した課題の控えは消える")
    with github() as (fake, tmp, main_path, wt1):
        wt2 = os.path.join(tmp, "wt2")
        a, a_id, _na = _claim_and_push(fake, main_path, wt1, "release する")
        r = run_task(wt1, "release", a)
        check("前提: release", r.stdout.startswith("RELEASED"), r.stdout + r.stderr)
        b, b_id, _nb = _claim_and_push(fake, main_path, wt1, "done する")
        work_and_done(wt1, b)
        c, c_id, _nc = _claim_and_push(fake, main_path, wt2, "ship する")
        work_and_done(wt2, c)
        r = run_task(wt2, "ship")
        check("前提: ship", r.returncode == 0 and r.stdout.startswith("SHIPPED"), r.stdout + r.stderr)
        check("控えは3つとも無い", not {a_id, b_id, c_id} & set(claim_snapshots(main_path)), str(claim_snapshots(main_path)))


def test_claim_kept_through_concurrent_syncs() -> None:
    say("着手の印: 2本・3本を同時に打つ sync で消えない")
    with github() as (fake, tmp, main_path, wt1):
        wt2 = os.path.join(tmp, "wt2")
        a, bd_id, n = _claim_and_push(fake, main_path, wt1, "同時の sync")
        marks = beads.mark_values(beads.show(main_path, bd_id))
        for trees in ((main_path, wt2), (main_path, wt1, wt2)):
            fake.edit(n, body=f"{len(trees)}本の前に GitHub で直した")
            procs = [start_task(w, "sync") for w in trees]
            outs = [p.communicate()[0] for p in procs]
            got = beads.show(main_path, bd_id)
            check(f"{len(trees)}本の同時の sync のあとも assignee と MARK_KEYS が残る", got is not None
                  and got.status == "in_progress" and got.assignee == "wt1" and beads.mark_values(got) == marks,
                  repr(outs) + str(got and got.raw))


# 長いものから並列に乗せる（後ろに残ると全体がその分延びる）。
TESTS = (
    test_claim_snapshot_cleared_on_release_done_ship,
    test_claim_kept_through_concurrent_syncs,
)


if __name__ == "__main__":
    refuse_direct_run("selftest_beads.py")
