"""着手の印の解放（`release --force`）と、同期が止まったあとの復元の自己テスト（`selftest_beads.py` の部品）。"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(HERE, "..", "core"))
sys.path.insert(1, os.path.join(HERE, "..", "tw"))

import beads  # noqa: E402
import ledger  # noqa: E402
import tracker  # noqa: E402
import tracker_github  # noqa: E402
from selftest_b_fakegithub import FakeGitHub, arm_kv_write, github, kv_written_during, status_labels  # noqa: E402
from selftest_b_support import bd, claim_snapshots, make_repo, new, num, work_and_done  # noqa: E402
from selftest_fixtures import run_handback_guard, run_task, start_task  # noqa: E402
from selftest_support import check, git, local, refuse_direct_run, say, write  # noqa: E402


def test_release_force_without_assignee() -> None:
    say("release --force: 担当者の消えた in_progress の着手の印を外せる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        a = new(main_path, "担当者が消えた")
        bd_id = beads.to_bd_id(a)
        bd(main_path, "update", bd_id, "--status", "in_progress", "--add-label", beads.SHIP_LABELS["done"])
        stuck = beads.show(main_path, bd_id)
        check("前提: assignee 無しの in_progress", stuck is not None and stuck.status == "in_progress"
              and not stuck.assignee, str(stuck and stuck.raw))
        r = run_task(wt1, "release", a)
        check("--force 無しは NOT_OWNER（挙動は変えない）", r.returncode == 4 and r.stdout.startswith("NOT_OWNER"), r.stdout)
        r = run_task(wt1, "release", a, "--force")
        freed = beads.show(main_path, bd_id)
        check("--force は RELEASED で status が open に戻る", r.returncode == 0 and r.stdout.startswith("RELEASED")
              and freed is not None and freed.status == "open" and not freed.assignee, r.stdout + r.stderr)
        check("ship の label も残らない", freed is not None
              and not any(l in beads.SHIP_LABELS.values() for l in freed.labels), str(freed and freed.labels))
        r = run_task(wt1, "release", a, "--force")
        check("もう一度は NOT_CLAIMED", r.returncode == 0 and r.stdout.startswith("NOT_CLAIMED"), r.stdout)


def test_release_force_clears_github_label() -> None:
    say("release --force: GitHub の label status::in_progress も残らず、取り込みで戻らない")
    with github() as (fake, _tmp, main_path, wt1):
        a = new(main_path, "label が残る")
        n, bd_id = num(a), beads.to_bd_id(a)
        time.sleep(1.1)
        bd(main_path, "update", bd_id, "--status", "in_progress")
        r = run_task(main_path, "sync")
        check("前提: GitHub に status::in_progress が付く", r.returncode == 0
              and "status::in_progress" in status_labels(fake, n), r.stdout + repr(fake.issues[n]["labels"]))
        r = run_task(wt1, "release", a, "--force")
        check("RELEASED だけで GitHub には書かない", r.returncode == 0 and r.stdout.startswith("RELEASED")
              and "TRACKER\tOK\tgithub" not in r.stdout
              and "status::in_progress" in status_labels(fake, n), r.stdout + r.stderr + repr(fake.issues[n]["labels"]))
        r = run_task(main_path, "sync")
        check("sync が GitHub の status::in_progress を外す", "status::in_progress" not in status_labels(fake, n),
              r.stdout + repr(fake.issues[n]["labels"]))
        again = beads.show(main_path, bd_id)
        check("sync のあとも open のまま", r.returncode == 0 and again is not None and again.status == "open",
              r.stdout + str(again and again.status))


def test_release_force_deadline_then_sync() -> None:
    say("release --force: 送りが上限切れでも、sync が label を外し open のまま")
    with github() as (fake, _tmp, main_path, wt1):
        a = new(main_path, "送りが切れる")
        n, bd_id = num(a), beads.to_bd_id(a)
        time.sleep(1.1)
        bd(main_path, "update", bd_id, "--status", "in_progress")
        r = run_task(main_path, "sync")
        check("前提: GitHub に status::in_progress が付く", r.returncode == 0
              and "status::in_progress" in status_labels(fake, n), r.stdout + repr(fake.issues[n]["labels"]))
        local.env[tracker.OP_TIMEOUT_ENV] = "2"
        fake.delay = 5
        r = run_task(wt1, "release", a, "--force")
        check("RELEASED と TRACKER FAILED（取り込みの読みが切れた行。送りは release にない）", r.returncode == 0 and r.stdout.startswith("RELEASED")
              and "TRACKER\tFAILED\tgithub" in r.stdout, r.stdout + r.stderr)
        fake.delay = 0
        r = run_task(main_path, "sync")
        again = beads.show(main_path, bd_id)
        check("sync が label を外す", r.returncode == 0 and "status::in_progress" not in status_labels(fake, n),
              r.stdout + repr(fake.issues[n]["labels"]))
        check("Beads は open のまま", again is not None and again.status == "open", str(again and again.status))


KILL_AFTER_PULL_ENV = "SELFTEST_KILL_AFTER_PULL"


def _arm_kill_after_pull(tmp: str) -> None:
    """PATH の先頭に、`bd github pull` が返った直後に呼び手（`task.py`）へ SIGTERM を打つ包みの `bd` を置く。
    打つのは `_killed_sync` の間（`KILL_AFTER_PULL_ENV` があるとき）だけ。"""
    real = shutil.which("bd", path=local.env["PATH"])
    bin_dir = os.path.join(tmp, "killbin")
    write(os.path.join(bin_dir, "bd"), f"""#!{sys.executable}
import os, signal, subprocess, sys
r = subprocess.run([{real!r}, *sys.argv[1:]])
if os.environ.get({KILL_AFTER_PULL_ENV!r}) and "github" in sys.argv and "pull" in sys.argv:
    os.kill(os.getppid(), signal.SIGTERM)
sys.exit(r.returncode)
""")
    os.chmod(os.path.join(bin_dir, "bd"), 0o755)
    local.env["PATH"] = bin_dir + os.pathsep + local.env["PATH"]


def _killed_sync(cwd: str) -> subprocess.CompletedProcess:
    """止めた sync。止めた sync が残す古い sync の錠は、`BUSY` の行の案内どおり人が消したことにする。"""
    local.env[KILL_AFTER_PULL_ENV] = "1"
    try:
        r = run_task(cwd, "sync")
    finally:
        del local.env[KILL_AFTER_PULL_ENV]
    with contextlib.suppress(FileNotFoundError):
        os.unlink(os.path.join(ledger.ledger_root(cwd), ledger.SYNC_LOCK_NAME))
    return r


def _claim_and_push(fake: "FakeGitHub", main_path: str, wt: str, summary: str) -> tuple[str, str, int]:
    """`wt` が着手して本体の sync で送り、GitHub の側の更新時刻を先へずらす。`(タスクID, Beads ID, Issue 番号)`。"""
    a = new(main_path, summary)
    r = run_task(wt, "claim", a)
    if r.returncode != 0:
        raise RuntimeError(f"claim 失敗: {r.stdout}{r.stderr}")
    run_task(main_path, "sync")
    fake.clock_offset = 60
    return a, beads.to_bd_id(a), num(a)


def test_claim_restored_after_killed_sync() -> None:
    say("着手の印の控え: sync が bd github pull の直後に止まっても、次の plan-check・sync・edit が控えから戻す")
    with github() as (fake, tmp, main_path, wt1):
        _arm_kill_after_pull(tmp)
        a, bd_id, n = _claim_and_push(fake, main_path, wt1, "取り込みで消える")
        marks = beads.mark_values(beads.show(main_path, bd_id))
        nexts = (("plan-check", wt1, ("plan-check", a)), ("sync", main_path, ("sync",)),
                 ("edit の before", wt1, ("edit", a, "--loopable", "Y")))
        for label, where, args in nexts:
            # 前の回に戻した版は Beads の側が新しく、送るまでは取り込みが飛ばすので、先に送っておく。
            run_task(main_path, "sync")
            fake.edit(n, body=f"{label} の前に GitHub で直した")
            r = _killed_sync(main_path)
            lost = beads.show(main_path, bd_id)
            check(f"前提（{label}）: 止めた sync が担当者と印を空にしたまま残す", r.returncode != 0 and lost is not None
                  and not lost.assignee and not beads.mark_values(lost), r.stdout + str(lost and lost.raw))
            r = run_task(where, *args)
            got = beads.show(main_path, bd_id)
            check(f"{label} が assignee と MARK_KEYS を戻し、TRACKER RESTORED を出す", r.returncode == 0
                  and f"TRACKER\tRESTORED\t{a}" in r.stdout + r.stderr and got is not None
                  and got.status == "in_progress" and got.assignee == "wt1" and beads.mark_values(got) == marks,
                  r.stdout + r.stderr + str(got and got.raw))
        r = run_task(wt1, "plan-check", a)
        check("戻したあとは plan-check が戻さずに通る", r.returncode == 0 and "RESTORED" not in r.stdout + r.stderr,
              r.stdout + r.stderr)


def test_handback_guard_after_killed_sync() -> None:
    say("着手の印の控え: 取り込みが印を消したまま handback-guard が最初に走っても、戻した印で判定し、検証の欠けた返却を拒む")
    with github(verify="echo verified") as (fake, tmp, main_path, wt1):
        _arm_kill_after_pull(tmp)
        a, bd_id, n = _claim_and_push(fake, main_path, wt1, "返却の前に消える")
        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "work.txt")
        git(wt1, "commit", "-q", "-m", f"{a}: 作業")
        fake.edit(n, body="返却の前に GitHub で直した")
        _killed_sync(main_path)
        lost = beads.show(main_path, bd_id)
        check("前提: 止めた sync が担当者と印を空にしたまま残す", lost is not None and not lost.assignee
              and not beads.mark_values(lost), str(lost and lost.raw))
        reason = (run_handback_guard(tmp, wt1) or {}).get("reason", "")
        got = beads.show(main_path, bd_id)
        check("コミット済みで作業ツリーがきれいでも、検証が無ければ block（NOT_VERIFIED）", "NOT_VERIFIED" in reason, reason)
        check("handback-guard が印を戻す", got is not None and got.assignee == "wt1"
              and beads.CLAIM_HEAD_KEY in beads.mark_values(got), str(got and got.raw))


def test_claim_not_restored_when_released_on_github() -> None:
    say("着手の印の控え: GitHub で手放された着手（担当者が付いた・label が外れた）は控えがあっても戻さない")
    with github() as (fake, tmp, main_path, wt1):
        _arm_kill_after_pull(tmp)
        a, a_id, na = _claim_and_push(fake, main_path, wt1, "label を外される")
        b, b_id, nb = _claim_and_push(fake, main_path, wt1, "担当者を付けられる")
        fake.edit(na, labels=[l for l in fake.issues[na]["labels"] if l != "status::in_progress"])
        fake.edit(nb, assignee="someone")
        _killed_sync(main_path)
        for task_id in (a, b):
            r = run_task(wt1, "plan-check", task_id)
            check(f"{task_id}: plan-check は戻さず NOT_OWNER", r.returncode == 4 and r.stdout.startswith("NOT_OWNER")
                  and "RESTORED" not in r.stdout + r.stderr, r.stdout + r.stderr)
        check("手放された課題の控えは消える", not {a_id, b_id} & set(claim_snapshots(main_path)), str(claim_snapshots(main_path)))
        r = run_task(main_path, "sync")
        got_a, got_b = beads.show(main_path, a_id), beads.show(main_path, b_id)
        check("sync も戻さない（label を外された課題は open・担当者を付けられた課題は wt1 でない）", "RESTORED" not in r.stdout
              and got_a is not None and got_a.status == "open" and got_b is not None and got_b.assignee != "wt1",
              r.stdout + str(got_a and got_a.raw) + str(got_b and got_b.raw))


def test_claim_not_restored_by_plain_sync_after_release_on_github() -> None:
    say("着手の印の控え: GitHub で status::in_progress を外された着手は、止めない plain な sync でも戻さない")
    with github() as (fake, tmp, main_path, wt1):
        a, bd_id, n = _claim_and_push(fake, main_path, wt1, "label を外される")
        fake.edit(n, labels=[l for l in fake.issues[n]["labels"] if l != "status::in_progress"])
        r = run_task(main_path, "sync")
        got = beads.show(main_path, bd_id)
        check("plain な sync が RESTORED を出さず、課題は open のまま担当者も wt1 に戻らない", "RESTORED" not in r.stdout + r.stderr
              and got is not None and got.status == "open" and got.assignee != "wt1",
              r.stdout + r.stderr + str(got and got.raw))


def test_claim_not_restored_after_release_done_ship() -> None:
    say("着手の印の控え: release・done・ship で手放した課題は、取り込みが止まっても控えから戻らない")
    with github() as (fake, tmp, main_path, wt1):
        _arm_kill_after_pull(tmp)
        wt2 = os.path.join(tmp, "wt2")
        a, a_id, na = _claim_and_push(fake, main_path, wt1, "release する")
        r = run_task(wt1, "release", a)
        check("前提: release", r.stdout.startswith("RELEASED"), r.stdout + r.stderr)
        b, b_id, nb = _claim_and_push(fake, main_path, wt1, "done する")
        work_and_done(wt1, b)
        c, c_id, nc = _claim_and_push(fake, main_path, wt2, "ship する")
        work_and_done(wt2, c)
        r = run_task(wt2, "ship")
        check("前提: ship", r.returncode == 0 and r.stdout.startswith("SHIPPED"), r.stdout + r.stderr)
        check("控えは3つとも無い", not {a_id, b_id, c_id} & set(claim_snapshots(main_path)), str(claim_snapshots(main_path)))
        for n in (na, nb, nc):
            fake.edit(n, body="手放したあとに GitHub で直した")
        _killed_sync(main_path)
        for task_id, where in ((a, wt1), (b, wt1), (c, wt2)):
            r = run_task(where, "plan-check", task_id)
            check(f"{task_id}: plan-check は控えから戻さない", r.returncode == 4 and "RESTORED" not in r.stdout + r.stderr,
                  r.stdout + r.stderr)
        r = run_task(main_path, "sync")
        got_a, got_c = beads.show(main_path, a_id), beads.show(main_path, c_id)
        check("sync も戻さない（release は open・ship は closed のまま）", "RESTORED" not in r.stdout
              and got_a is not None and got_a.status == "open" and got_c is not None and got_c.status == "closed",
              r.stdout + str(got_a and got_a.raw) + str(got_c and got_c.raw))


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


def test_pull_sees_other_worktree_kv() -> None:
    say("着手の戻し: 取り込みの途中に別の作業ツリーが書いた送りの印を見て、戻すかを決める")
    with github() as (fake, tmp, main_path, wt1):
        arm_kv_write(tmp)
        a, a_id, na = _claim_and_push(fake, main_path, wt1, "取り込みの間に送りの印を空にされる")
        b, b_id, nb = _claim_and_push(fake, main_path, wt1, "送りの印はそのまま")
        for n in (na, nb):
            fake.edit(n, labels=[l for l in fake.issues[n]["labels"] if l != "status::in_progress"])

        def edit(task_id: str) -> subprocess.CompletedProcess:
            return run_task(wt1, "edit", task_id, "--section", "参考情報", "--body-file", "-", stdin="メモ\n")

        with kv_written_during(tmp, "github pull", f"{tracker.PUSHED_KEY_PREFIX}{na}", "") as fired:
            r = edit(a)
        rb = edit(b)
        got_a, got_b = beads.show(main_path, a_id), beads.show(main_path, b_id)
        check("前提: 取り込みの直前に送りの印を書いた", os.path.exists(fired), r.stdout + r.stderr)
        check("取り込みの間に印が空にされた課題は、送っていない着手として in_progress と assignee へ戻す",
              got_a is not None and got_a.status == "in_progress" and got_a.assignee == "wt1",
              r.stdout + r.stderr + str(got_a and got_a.raw))
        check("印がそのままの課題は、GitHub で手放されたとして open のまま", got_b is not None and got_b.status == "open",
              rb.stdout + rb.stderr + str(got_b and got_b.raw))


def test_after_sees_other_worktree_kv() -> None:
    say("Status 欄: claim は書かず、sync の途中に別の作業ツリーが書いた項目の控えを、Status 欄の書き込みが読み直して見る")
    with github() as (fake, tmp, main_path, wt1):
        arm_kv_write(tmp)
        a = new(main_path, "送りの間に控えを書かれる")
        writes = fake.status_writes
        r = run_task(wt1, "claim", a)
        check("claim は Status 欄を書かない", r.returncode == 0 and fake.status_writes == writes
              and fake.status_of(num(a)) == "Todo", r.stdout + repr(fake.items))
        key = f"{tracker_github.ITEMS_KEY_PREFIX}{num(a)}"
        item = json.loads(bd(main_path, "kv", "get", key).stdout)
        written = json.dumps({"item": item["item"], "status": "In progress"}, ensure_ascii=False)
        with kv_written_during(tmp, f"kv set {tracker.SYNCED_ITEM_PREFIX}", key, written) as fired:
            r = run_task(main_path, "sync")
        check("前提: sync の途中（同期の時刻を書く前）に項目の控えを書いた", r.returncode == 0 and os.path.exists(fired),
              r.stdout + r.stderr)
        check("書かれた控え（もう書く値）を見て Status 欄を書かない",
              fake.status_writes == writes and fake.status_of(num(a)) == "Todo", r.stdout + repr(fake.items))


# 長いものから並列に乗せる（後ろに残ると全体がその分延びる）。
TESTS = (
    test_claim_not_restored_after_release_done_ship,
    test_claim_restored_after_killed_sync,
    test_handback_guard_after_killed_sync,
    test_claim_not_restored_when_released_on_github,
    test_claim_not_restored_by_plain_sync_after_release_on_github,
    test_pull_sees_other_worktree_kv,
    test_after_sees_other_worktree_kv,
    test_claim_kept_through_concurrent_syncs,
    test_release_force_without_assignee,
    test_release_force_clears_github_label,
    test_release_force_deadline_then_sync,
)


if __name__ == "__main__":
    refuse_direct_run("selftest_beads.py")
