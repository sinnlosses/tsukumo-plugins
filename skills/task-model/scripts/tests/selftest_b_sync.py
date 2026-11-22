"""トラッカー github との同期（採番・`sync` の送り・錠）の自己テスト（`selftest_beads.py` の部品）。"""

from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(HERE, "..", "core"))
sys.path.insert(1, os.path.join(HERE, "..", "tw"))

import beads  # noqa: E402
import ledger  # noqa: E402
import tracker  # noqa: E402
from selftest_b_fakegithub import github  # noqa: E402
from selftest_b_support import HELD_PLANNED_BODY, bd, new, num, work_and_done  # noqa: E402
from selftest_fixtures import child_env, run_task, start_task  # noqa: E402
from selftest_support import check, local, refuse_direct_run, say  # noqa: E402


def _sends(counted: str) -> list[str]:
    """控えた `bd` の起動のうち、`bd github sync --push-only` の行。"""
    return [l for l in open(counted).read().splitlines() if l.startswith("github sync --push-only")]


def test_tracker_github_numbered() -> None:
    say("トラッカー github・issue_prefix gh（Issue 番号の ID・変えた課題だけを送り、GitHub からは取り込まない）")
    with github(prefix=None) as (fake, tmp, main_path, wt1):
        check("トラッカーが github なら init.py は issue_prefix gh で .beads を作る",
              beads.read_prefix(main_path) == beads.PREFIX_GITHUB)
        fake.open_issue("先にある PR 以外の Issue", [])
        a = new(main_path, "Issue を先に立てる")
        check("new は Issue を立てて GH-<番号> を返す", a == "GH-2" and beads.show(main_path, "gh-2") is not None, a)
        check("仮の ID は残らない", not any(i.bd_id.startswith("gh-new-") for i in beads.list_issues(main_path)))
        check("Status 欄も書く", fake.status_of(2) == "Todo", repr(fake.items))

        n, bd_id = num(a), beads.to_bd_id(a)
        counted = os.path.join(tmp, "bd-count.txt")
        local.env = {**local.env, "BD_COUNT_FILE": counted}
        open(counted, "w").close()
        fake.fetched.clear()
        fake.listed.clear()
        r = run_task(main_path, "sync")
        check("変えた課題が無ければ sync は送らず、GitHub の Issue を読まない", r.returncode == 0 and "pushed=0" in r.stdout
              and not _sends(counted) and not fake.fetched and not fake.listed,
              r.stdout + r.stderr + repr(_sends(counted)) + f" {fake.fetched} {fake.listed}")

        time.sleep(1.1)
        b = new(main_path, "変えない課題")
        r = run_task(wt1, "claim", a)
        check("claim", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
        fake.clock_offset = 60
        fake.edit(n, body="GitHub で直した本文")
        fake.open_issue("あとから GitHub で立てた Issue", [])
        fake.fetched.clear()
        fake.listed.clear()
        open(counted, "w").close()
        r = run_task(main_path, "sync")
        sends = _sends(counted)
        check("sync は前回の送りより後に変えた課題だけを --issues で送る", r.returncode == 0 and "pushed=1" in r.stdout
              and len(sends) == 1 and sends[0].split("--issues ", 1)[-1].split()[0] == bd_id,
              r.stdout + r.stderr + repr(sends) + f" 変えない課題 {b}")
        check("sync が Status 欄を In progress にする", fake.status_of(n) == "In progress", repr(fake.items))
        # `bd github sync --push-only` は送る Issue を書く前に1件ずつ読む。それ以外の Issue は読まない。
        check("sync は Issue の一覧を読まず、1件ずつ読むのは送った Issue だけ", not fake.listed
              and set(fake.fetched) <= {n}, f"{fake.fetched} {fake.listed}")
        issue = beads.show(main_path, bd_id)
        check("GitHub で直した本文は取り込まず、錠の持ち主も変わらない", issue is not None
              and "GitHub で直した本文" not in str(issue.raw.get("description"))
              and issue.assignee == "wt1" and issue.status == "in_progress", str(issue and issue.raw))
        status = run_task(main_path, "status").stdout
        check("GitHub で立てた Issue は Beads に入らない", beads.show(main_path, "gh-4") is None
              and "triage\t0\t-" in status, status)


def test_tracker_register_timeout_no_duplicate() -> None:
    say("トラッカー github・Issue を立てた直後に上限で落ちた仮の ID を sync が送り直しても Issue は1つ")
    with github() as (fake, _tmp, main_path, _wt1):
        fake.clock_offset = -3600
        old = fake.open_issue("ずっと前からある Issue", [])
        fake.clock_offset = 0
        local.env[tracker.OP_TIMEOUT_ENV] = "12"
        fake.delay = 120
        fake.delay_post_only = True
        fake.listed.clear()
        a = new(main_path, "立てた直後に落ちる")
        fake.delay = 0
        check("前提: 落ちた register は仮の ID のまま返り、Issue は古いものと合わせて2つ",
              beads.is_provisional(beads.to_bd_id(a)) and len(fake.issues) == 2,
              f"{a} {len(fake.issues)}")
        check("register の拾い探しは古い Issue を取りに行かない",
              bool(fake.listed) and all(old not in got for got in fake.listed), repr(fake.listed))
        fake.listed.clear()
        r = run_task(main_path, "sync")
        check("sync のあとも立てた Issue は1つ（合わせて2つ）", len(fake.issues) == 2, f"{len(fake.issues)} {r.stdout}{r.stderr}")
        check("sync の拾い探しも古い Issue を取りに行かない",
              bool(fake.listed) and all(old not in got for got in fake.listed), repr(fake.listed))
        check("仮の ID は gh-2 へ付け替わる", beads.show(main_path, "gh-2") is not None
              and not any(beads.is_provisional(i.bd_id) for i in beads.list_issues(main_path)), r.stdout)


def test_edit_deps_after_body_edit_keeps_body() -> None:
    say("トラッカー github・本文を直したあとの --add-deps・--remove-deps・依存の打ち直しが本文を古い版に戻さない")
    for case in ("add", "remove", "again"):
        with github() as (fake, _tmp, main_path, wt1):
            dep = new(main_path, "依存先")
            a = new(main_path, f"{case} の本文", *(("--deps", dep) if case != "add" else ()))
            shown = run_task(wt1, "show", a).stdout
            body = shown.split("---\n", 2)[2].replace("## 目的・背景\n", "## 目的・背景\n\n直したあとの文\n", 1)
            time.sleep(1.1)
            r = run_task(wt1, "edit", a, "--change-frame", "--body-file", "-", stdin=body)
            check(f"{case}: 本文を直せた", r.returncode == 0 and r.stdout.startswith("EDITED"), r.stdout + r.stderr)
            check(f"{case}: 直した直後は本文が変わっている","直したあとの文" in run_task(wt1, "show", a).stdout, "")
            flag = {"add": ("--add-deps", dep), "remove": ("--remove-deps", dep), "again": ("--add-deps", dep)}[case]
            r = run_task(wt1, "edit", a, *flag)
            check(f"{case}: 依存を直せた", r.returncode == 0, r.stdout + r.stderr)
            after =run_task(wt1, "show", a).stdout
            check(f"{case}: 本文が直したあとのまま", "直したあとの文" in after, after)


def _github_calls(fake, counted: str) -> int:
    """偽の GitHub への要求（読みも書きも）と Status 欄の書き込み、`bd github` の起動の合計。"""
    http = sum(fake.requests.values())
    runs = sum(1 for l in open(counted).read().splitlines() if l.startswith("github "))
    return http + fake.status_writes + runs


def _in_process(toplevel: str, code: str, *args: str) -> None:
    """`code`（`sys.argv[1]` が core のパス、`sys.argv[2]` が `toplevel`）を、足場の環境のまま別のプロセスで打つ
    （並列のテストの環境変数を変えない）。"""
    subprocess.run([sys.executable, "-c", code, os.path.join(HERE, "..", "core"), toplevel, *args],
                   cwd=toplevel, env=child_env(), capture_output=True, check=False)


def test_tracker_writes_only_at_new_and_ship() -> None:
    say("トラッカー github・GitHub に触るのは new と ship・sync の送りだけ（claim・edit・hold・release・done は読みも書きもしない）")
    with github() as (fake, tmp, main_path, wt1):
        a, b, c = new(main_path, "書き込みを数える"), new(main_path, "hold する", body=HELD_PLANNED_BODY), new(main_path, "release する")
        counted = os.path.join(tmp, "bd-count.txt")
        local.env = {**local.env, "BD_COUNT_FILE": counted}
        open(counted, "w").close()

        def calls_during(fn) -> int:
            before = _github_calls(fake, counted)
            fn()
            return _github_calls(fake, counted) - before

        def edit(*args: str):
            return run_task(wt1, "edit", a, *args, stdin="メモ\n")

        results = {}

        def record(name: str, *args: str, stdin: str | None = None) -> None:
            results[name] = run_task(wt1, *args, stdin=stdin)

        steps = (
            ("claim", lambda: record("claim", "claim", a)),
            ("edit 節", lambda: results.update(e1=edit("--section", "参考情報", "--body-file", "-"))),
            ("edit --summary", lambda: results.update(e2=edit("--summary", "題を直した"))),
            ("edit --difficulty", lambda: results.update(e3=edit("--difficulty", "haiku"))),
            ("hold", lambda: record("hold", "hold", b, "--reason-file", "-", stdin="理由\n")),
            ("claim と release", lambda: (record("c1", "claim", c), record("c2", "release", c))),
        )
        for label, fn in steps:
            n = calls_during(fn)
            check(f"{label} は GitHub を読みも書きもしない", n == 0, f"要求 {n} 件")
        check("claim・edit・hold・release は成功する", results["claim"].stdout.startswith("CLAIMED")
              and all(results[k].returncode == 0 for k in ("e1", "e2", "e3", "hold", "c1", "c2")),
              repr({k: v.stdout + v.stderr for k, v in results.items()}))
        check("claim・edit のあとも GitHub の Status 欄は Todo のまま", fake.status_of(num(a)) == "Todo", repr(fake.items))

        push = "import sys; sys.path.insert(0, sys.argv[1]); import tracker; tracker.session(sys.argv[2]).push_created([sys.argv[3]])"
        weak = calls_during(lambda: (edit("--summary", "また直した"), _in_process(wt1, push, beads.to_bd_id(a))))
        check("弱めた実装（edit のあとに旧 after の送りを足す）では書き込みが数えられる", weak >= 1, f"要求 {weak} 件")
        read = (
            "import sys; sys.path.insert(0, sys.argv[1]); import tracker, tracker_github as gh; "
            "s = tracker.session(sys.argv[2]); gh.rest_issues(sys.argv[2], s._repository(), 'state=open', s._remaining)"
        )
        weak = calls_during(lambda: _in_process(wt1, read))
        check("弱めた実装（Issue の一覧を読む）では読みが数えられる", weak >= 1, f"要求 {weak} 件")

        n = calls_during(lambda: work_and_done(wt1, a))
        check("done は GitHub を読みも書きもしない", n == 0, f"要求 {n} 件")
        open(counted, "w").close()
        statuses = fake.status_writes
        r = run_task(wt1, "ship")
        sends = _sends(counted)
        check("ship の最後に github sync --push-only が1回、TRACKER OK が1回", r.returncode == 0 and len(sends) == 1
              and r.stdout.count("TRACKER\tOK\tgithub") == 1, r.stdout + r.stderr + "\n".join(sends))
        check("ship は変えた課題だけを --issues で送る", len(sends) == 1 and beads.to_bd_id(a) in sends[0].split("--issues ", 1)[-1],
              "\n".join(sends))
        check("Issue は closed で、Status 欄は Done になる",
              fake.issues[num(a)]["state"] == "closed" and fake.status_of(num(a)) == "Done"
              and fake.status_writes - statuses >= 1, repr(fake.issues[num(a)]) + repr(fake.items))

    with github(prefix=beads.PREFIX_LOCAL) as (fake, tmp, main_path, wt1):
        counted = os.path.join(tmp, "bd-count.txt")
        local.env = {**local.env, "BD_COUNT_FILE": counted}
        open(counted, "w").close()
        made = _github_calls(fake, counted)
        t = new(main_path, "t 接頭辞の課題")
        check("t 接頭辞の new は送る（Beads から送るだけ）", _github_calls(fake, counted) > made and len(fake.issues) == 1,
              repr(fake.requests))
        made = _github_calls(fake, counted)
        r1 = run_task(wt1, "claim", t)
        r2 = run_task(wt1, "edit", t, "--summary", "題を直した")
        check("t 接頭辞の claim・edit は GitHub に触らない", r1.stdout.startswith("CLAIMED") and r2.returncode == 0
              and _github_calls(fake, counted) == made, r1.stdout + r1.stderr + r2.stdout + r2.stderr)


def test_tracker_sync_lock_busy() -> None:
    say("トラッカー github・重ねて打った sync は錠で1本にし、2本目は待たず BUSY で返る")
    with github() as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "錠を確かめる")
        n, bd_id = num(a), beads.to_bd_id(a)
        time.sleep(1.1)
        bd(main_path, "update", bd_id, "--title", "Beads で直した題")
        lock = os.path.join(ledger.ledger_root(main_path), ledger.SYNC_LOCK_NAME)
        fake.delay = 1.5
        first = start_task(main_path, "sync")
        deadline = time.monotonic() + 30
        while not os.path.exists(lock) and time.monotonic() < deadline:
            time.sleep(0.05)
        second = run_task(main_path, "sync")
        check("1本目が走っている間の2本目は BUSY の行と終了コード5で返る", second.returncode == 5
              and second.stdout.startswith("BUSY\tsync\t") and "TRACKER" not in second.stdout,
              second.stdout + second.stderr)
        out, err = first.communicate(timeout=120)
        check("1本目の結果は変わらない（変えた課題を送り、TRACKER OK）", first.returncode == 0
              and "TRACKER\tOK\tgithub\tpushed=1" in out and fake.issues[n]["title"] == "Beads で直した題",
              out + err + repr(fake.issues[n]))
        check("終わったら錠は残らない", not os.path.exists(lock))
        fake.delay = 0
        third = run_task(main_path, "sync")
        check("1本目が終わったあとの sync は通る", third.returncode == 0 and "TRACKER\tOK\tgithub" in third.stdout,
              third.stdout + third.stderr)
        with open(lock, "w", encoding="utf-8") as f:
            f.write("999999\tx\ty")
        stale = run_task(main_path, "sync")
        check("持ち主のいない古い錠は壊さず、行に添えて BUSY で返る", stale.returncode == 5
              and "古い錠" in stale.stdout and os.path.exists(lock), stale.stdout + stale.stderr)


# 長いものから並列に乗せる（後ろに残ると全体がその分延びる）。
TESTS = (
    test_tracker_writes_only_at_new_and_ship,
    test_tracker_github_numbered,
    test_tracker_sync_lock_busy,
    test_tracker_register_timeout_no_duplicate,
    test_edit_deps_after_body_edit_keeps_body,
)


if __name__ == "__main__":
    refuse_direct_run("selftest_beads.py")
