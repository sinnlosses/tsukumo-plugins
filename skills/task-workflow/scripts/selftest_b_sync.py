"""トラッカー github との同期（`sync`・送り・取り込み・錠）の自己テスト（`selftest_beads.py` の部品）。"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import beads  # noqa: E402
import ledger  # noqa: E402
import tracker  # noqa: E402
from selftest_b_fakegithub import github  # noqa: E402
from selftest_b_support import bd, new, num, rows  # noqa: E402
from selftest_fixtures import run_task, start_task  # noqa: E402
from selftest_support import check, local, refuse_direct_run, say  # noqa: E402


def test_tracker_github_bidirectional() -> None:
    say("トラッカー github・issue_prefix gh（Issue 番号の ID・送りと取り込みの往復）")
    with github() as (fake, _tmp, main_path, wt1):
        fake.open_issue("先にある PR 以外の Issue", [])
        a = new(main_path, "Issue を先に立てる")
        check("new は Issue を立てて GH-<番号> を返す", a == "GH-2" and beads.show(main_path, "gh-2") is not None, a)
        check("仮の ID は残らない", not any(i.bd_id.startswith("gh-new-") for i in beads.list_issues(main_path)))
        check("Status 欄も書く", fake.status_of(2) == "Todo", repr(fake.items))

        n, bd_id = num(a), beads.to_bd_id(a)
        r = run_task(wt1, "claim", a)
        check("claim", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
        fake.clock_offset = 60
        fake.edit(n, body="GitHub で直した本文")
        r = run_task(main_path, "sync")
        check("sync が GitHub で立てた Issue を取り込み、番号の ID へ付け替えて振り分け前にする", r.returncode == 0
              and "triage\t1\tGH-1" in run_task(main_path, "status").stdout, r.stdout)
        issue = beads.show(main_path, bd_id)
        check("取り込みで GitHub の本文が入り、錠の持ち主（assignee）は戻る", issue is not None
              and "GitHub で直した本文" in str(issue.raw.get("description"))
              and issue.assignee == "wt1" and issue.status == "in_progress", r.stdout + str(issue and issue.raw))


def test_tracker_timeout() -> None:
    say("トラッカー github・1件の操作は上限で打ち切って TRACKER FAILED にし、sync が打ち直す")
    with github() as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "遅れる反映")
        local.env[tracker.OP_TIMEOUT_ENV] = "2"
        fake.delay = 5
        started = time.monotonic()
        r = run_task(main_path, "edit", a, "--difficulty", "haiku")
        elapsed = time.monotonic() - started
        edited = beads.show(main_path, beads.to_bd_id(a))
        meta = edited.raw.get("metadata") if edited is not None else {}
        check("上限を超えたら TRACKER FAILED の行を出し、終了コードは0のまま", r.returncode == 0
              and "TRACKER\tFAILED\tgithub" in r.stdout, r.stdout + r.stderr)
        check("Beads の書き込みは残る", meta.get("difficulty") == "haiku", str(edited))
        check("待ちは上限の数倍に延びない", elapsed < 12, f"{elapsed:.1f}秒")
        fake.delay = 0
        r = run_task(main_path, "sync")
        check("sync が打ち直して TRACKER OK", r.returncode == 0 and "TRACKER\tOK\tgithub" in r.stdout
              and "TRACKER\tFAILED" not in r.stdout, r.stdout + r.stderr)


def test_tracker_register_timeout_no_duplicate() -> None:
    say("トラッカー github・Issue を立てた直後に上限で落ちた仮の ID を sync が送り直しても Issue は1つ")
    with github() as (fake, _tmp, main_path, _wt1):
        fake.clock_offset = -3600
        old = fake.open_issue("ずっと前からある Issue", [])
        fake.clock_offset = 0
        local.env[tracker.OP_TIMEOUT_ENV] = "2"
        fake.delay = 5
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
        # 取り込みの一覧と拾い探しの一覧は並んで走るので、順は決まらない。古い Issue を含まないのは拾い探しの一覧。
        check("sync の拾い探しも古い Issue を取りに行かない", any(old not in got for got in fake.listed), repr(fake.listed))
        check("仮の ID は gh-2 へ付け替わる", beads.show(main_path, "gh-2") is not None
              and not any(beads.is_provisional(i.bd_id) for i in beads.list_issues(main_path)), r.stdout)


def test_tracker_pull_reports_duplicate() -> None:
    say("トラッカー github・取り込んだ新しい Issue が既存の課題と題・本文が同じなら TRACKER INVALID")
    with github() as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "同じ題")
        issue = beads.show(main_path, beads.to_bd_id(a))
        body = str(issue.raw.get("description"))
        fake.open_issue("同じ題", [], body=body + "\n")
        r = run_task(main_path, "sync")
        check("題・本文が同じなら INVALID の行", f"TRACKER\tINVALID\tGH-2\t{a} と重複している" in r.stdout,
              r.stdout + r.stderr)
        fake.open_issue("同じ題（別件）", [], body=body)
        fake.open_issue("同じ題", [], body=body + "違う")
        r = run_task(main_path, "sync")
        check("題か本文が違うだけの Issue は重複とみなさない", "と重複している" not in r.stdout, r.stdout + r.stderr)


def _kv(cwd: str) -> dict[str, str]:
    return json.loads(bd(cwd, "kv", "list", "--json").stdout or "{}")


def test_tracker_project_items_per_issue() -> None:
    say("トラッカー github・Project の控えは課題ごとのキー（旧い1つの塊は最初の読み込みで移して消す）")
    prefix, legacy_key = tracker.ITEMS_KEY_PREFIX, tracker.LEGACY_ITEMS_KEY
    with github() as (fake, _tmp, main_path, _wt1):
        a, b = new(main_path, "控えを移す1"), new(main_path, "控えを移す2")
        na, nb = num(a), num(b)
        check("new のあと控えは課題ごとのキーにある", {f"{prefix}{na}", f"{prefix}{nb}"} <= set(_kv(main_path)), str(_kv(main_path)))
        old = {k: json.loads(v) for k, v in _kv(main_path).items() if k.startswith(prefix)}
        for k in old:
            bd(main_path, "kv", "clear", k)
        legacy = {f"https://github.com/o/r/issues/{int(k[len(prefix):])}": v for k, v in old.items()}
        legacy.update({f"https://github.com/o/r/issues/{1000 + i}": {"item": f"PVTI_{1000 + i}", "status": "Todo"}
                       for i in range(40)})
        bd(main_path, "kv", "set", legacy_key, json.dumps(legacy))
        r = run_task(main_path, "sync")
        kv = _kv(main_path)
        items = {k: v for k, v in kv.items() if k.startswith(prefix)}
        check("旧い塊は課題ごとのキーへ移って消える", r.returncode == 0 and legacy_key not in kv
              and len(items) == len(legacy), r.stdout + r.stderr + str(sorted(kv)))
        check("移した値は課題の数に依らず小さい", max(len(v) for v in items.values()) < 100, str(max(items.values(), key=len)))
        check("移した値は元のまま", json.loads(items[f"{prefix}{na}"]) == legacy[f"https://github.com/o/r/issues/{na}"])

        fake.status_writes = 0
        r = run_task(main_path, "sync")
        check("移したあとの sync は同じ Status の課題へ触らない", r.returncode == 0 and fake.status_writes == 0,
              f"{fake.status_writes}\n{r.stdout}{r.stderr}")

        before = _kv(main_path)
        # 直前の sync が控える時刻は秒に丸まるので、同じ秒に更新すると送る課題に選ばれない。
        time.sleep(1.1)
        bd(main_path, "update", beads.to_bd_id(a), "--status", "deferred")
        r = run_task(main_path, "sync")
        after = _kv(main_path)
        changed = sorted(k for k in after if k.startswith(prefix) and before.get(k) != after[k])
        check("Status が変わった課題だけ1キー書き換わる", fake.status_writes == 1 and fake.status_of(na) == "Pending"
              and fake.status_of(nb) == "Todo" and changed == [f"{prefix}{na}"],
              f"status_writes={fake.status_writes} 課題{na}={fake.status_of(na)} 課題{nb}={fake.status_of(nb)} changed={changed}\n"
              f"sync:\n{r.stdout}{r.stderr}\nkv の控え: {after}")


def test_tracker_sync_pushes_only_changed() -> None:
    say("トラッカー github・sync は変えた課題だけを送り、変わりが無ければ送らない")
    with github() as (fake, _tmp, main_path, _wt1):
        a, b = new(main_path, "変えない"), new(main_path, "変える")
        na, nb = num(a), num(b)
        r = run_task(main_path, "sync")
        check("最初の sync は成功する", r.returncode == 0 and "TRACKER\tOK\tgithub" in r.stdout, r.stdout + r.stderr)
        fake.issue_patches.clear()
        r = run_task(main_path, "sync")
        check("変わりが無ければ Issue を送らず pushed=0", r.returncode == 0 and "pushed=0\t" in r.stdout
              and fake.issue_patches == [], f"{fake.issue_patches}\n{r.stdout}{r.stderr}")
        time.sleep(1.1)
        u = bd(main_path, "update", beads.to_bd_id(b), "--title", "Beads で直した題")
        check("bd update", u.returncode == 0, u.stdout + u.stderr)
        r = run_task(main_path, "sync")
        check("変えた1件だけを送る", r.returncode == 0 and "pushed=1\t" in r.stdout
              and set(fake.issue_patches) == {nb} and fake.issues[nb]["title"] == "Beads で直した題"
              and fake.issues[na]["title"] == "変えない",
              f"{fake.issue_patches}\n{r.stdout}{r.stderr}")


def test_tracker_sync_bd_calls() -> None:
    say("トラッカー github・1回の sync の bd の起動は、課題4件で17回以下")
    with github() as (fake, tmp, main_path, _wt1):
        ids = [new(main_path, f"課題{i}") for i in range(4)]
        r = run_task(_wt1, "claim", ids[0])
        check("claim", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
        counted = os.path.join(tmp, "bd-count.txt")
        local.env = {**local.env, "BD_COUNT_FILE": counted}
        # 全件取り込んで着手の印を戻す sync は、1件ごとの history と update が本物の仕事なので17回に収まらない。
        for label, limit in (("変わりの無い sync", 17), ("4件とも GitHub で直した sync", 21)):
            if label.startswith("4件"):
                fake.clock_offset = 60
                for task_id in ids:
                    fake.edit(num(task_id), body="GitHub で直した本文")
            open(counted, "w").close()
            r = run_task(main_path, "sync")
            calls = open(counted).read().splitlines()
            say(f"  {label}: bd {len(calls)} 回")
            check(f"{label}は成功する", r.returncode == 0 and "TRACKER\tOK\tgithub" in r.stdout, r.stdout + r.stderr)
            check(f"{label}の bd の起動は{limit}回以下（{len(calls)}回）", len(calls) <= limit, "\n".join(calls))


def test_tracker_sync_lock_busy() -> None:
    say("トラッカー github・重ねて打った sync は錠で1本にし、2本目は待たず BUSY で返る")
    with github() as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "錠を確かめる")
        n = num(a)
        fake.clock_offset = 60
        fake.edit(n, body="GitHub で直した本文")
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
        check("1本目の結果は変わらない（取り込みが済み、TRACKER OK）", first.returncode == 0
              and "TRACKER\tOK\tgithub" in out, out + err)
        issue = beads.show(main_path, beads.to_bd_id(a))
        check("1本目が GitHub の本文を取り込んだ", issue is not None
              and "GitHub で直した本文" in str(issue.raw.get("description")), str(issue and issue.raw))
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


def test_tracker_sync_skips_echo() -> None:
    say("トラッカー github・自分の送りの反響は取り込みに渡さず、あとから GitHub で直したものは渡す")
    with github(prefix=None) as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "反響を確かめる")
        n = num(a)
        r = run_task(main_path, "sync")
        check("最初の sync は成功する", r.returncode == 0, r.stdout + r.stderr)
        fake.fetched.clear()
        r = run_task(main_path, "sync")
        check("自分の送りで更新時刻が進んだだけの Issue は取りに行かない", r.returncode == 0 and n not in fake.fetched,
              f"{fake.fetched}\n{r.stdout}{r.stderr}")
        fake.clock_offset = 60
        fake.edit(n, body="あとから GitHub で直した")
        fake.fetched.clear()
        r = run_task(main_path, "sync")
        issue = beads.show(main_path, beads.to_bd_id(a))
        check("送った時刻より後に GitHub で直した Issue は取り込む", n in fake.fetched and issue is not None
              and "あとから GitHub で直した" in str(issue.raw.get("description")), f"{fake.fetched}\n{r.stdout}{r.stderr}")


def test_tracker_github_conflict() -> None:
    say("トラッカー github・issue_prefix gh（init.py が gh で作る・両側で変えたら Beads が勝つ）")
    with github(prefix=None) as (fake, _tmp, main_path, _wt1):
        check("トラッカーが github なら init.py は issue_prefix gh で .beads を作る",
              beads.read_prefix(main_path) == beads.PREFIX_GITHUB)
        a = new(main_path, "両側で直す")
        n, bd_id = num(a), beads.to_bd_id(a)
        time.sleep(1.1)
        bd(main_path, "update", bd_id, "--title", "Beads で直した題")
        fake.clock_offset = 60
        fake.edit(n, body="GitHub でも直した")
        r = run_task(main_path, "sync")
        check("両側で変えたら CONFLICT の行を出し、Beads が勝つ", f"TRACKER\tCONFLICT\t{a}" in r.stdout
              and fake.issues[n]["title"] == "Beads で直した題", r.stdout + repr(fake.issues[n]))


def test_tracker_github_draft() -> None:
    say("トラッカー github・ドラフトは Status が Pending で、GitHub で直したあとの取り込みでもドラフトのまま")
    with github() as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "ドラフト", "--draft", body="## 目的・背景\n\n直したい改善\n")
        n, bd_id = num(a), beads.to_bd_id(a)
        check("ドラフトの Status は Pending", fake.status_of(n) == "Pending", repr(fake.items))
        check("GitHub では label status::deferred", "status::deferred" in fake.issues[n]["labels"], repr(fake.issues[n]))
        time.sleep(1.1)
        fake.clock_offset = 60
        fake.edit(n, body="GitHub で直した本文")
        r = run_task(main_path, "sync")
        issue = beads.show(main_path, bd_id)
        meta = issue.raw.get("metadata") if issue is not None else None
        check("取り込みのあとも deferred で metadata draft が戻る", issue is not None and issue.status == "deferred"
              and isinstance(meta, dict) and meta.get(beads.DRAFT_KEY) == beads.DRAFT_ON
              and "GitHub で直した本文" in str(issue.raw.get("description")), r.stdout + r.stderr + str(issue and issue.raw))
        check("status は DRAFT のまま", rows(run_task(main_path, "status").stdout).get(a, [""] * 8)[5] == "DRAFT")
        check("Status は Pending のまま", fake.status_of(n) == "Pending", repr(fake.items))


# 長いものから並列に乗せる（後ろに残ると全体がその分延びる）。
TESTS = (
    test_tracker_github_bidirectional,
    test_tracker_sync_bd_calls,
    test_tracker_sync_lock_busy,
    test_tracker_sync_skips_echo,
    test_tracker_github_conflict,
    test_tracker_github_draft,
    test_tracker_sync_pushes_only_changed,
    test_tracker_timeout,
    test_tracker_register_timeout_no_duplicate,
    test_tracker_pull_reports_duplicate,
    test_tracker_project_items_per_issue,
)


if __name__ == "__main__":
    refuse_direct_run("selftest_beads.py")
