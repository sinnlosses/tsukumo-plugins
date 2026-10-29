"""トラッカー github との同期（`sync`・送り・取り込み・錠）の自己テスト（`selftest_beads.py` の部品）。"""

from __future__ import annotations

import json
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
import tracker_claims  # noqa: E402
import tracker_github  # noqa: E402
from selftest_b_fakegithub import arm_kv_write, github, kv_written_during  # noqa: E402
from selftest_b_support import HELD_PLANNED_BODY, PLANNED_BODY as NEW_DEFAULT_BODY, bd, new, num, rows, work_and_done  # noqa: E402
from selftest_fixtures import PLANNED_BODY, child_env, run_task, start_task  # noqa: E402
from selftest_support import check, local, refuse_direct_run, say, write  # noqa: E402


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
        run_task(main_path, "sync")  # claim は送らないので、GitHub の編集を取り込む前に着手を送っておく
        fake.clock_offset = 60
        fake.edit(n, body="GitHub で直した本文")
        r = run_task(main_path, "sync")
        check("sync が GitHub で立てた Issue を取り込み、番号の ID へ付け替えて振り分け前にする", r.returncode == 0
              and "triage\t1\tGH-1" in run_task(main_path, "status").stdout, r.stdout)
        issue = beads.show(main_path, bd_id)
        check("取り込みで GitHub の本文が入り、錠の持ち主（assignee）は戻る", issue is not None
              and "GitHub で直した本文" in str(issue.raw.get("description"))
              and issue.assignee == "wt1" and issue.status == "in_progress", r.stdout + str(issue and issue.raw))


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


def test_release_then_claim_without_sync() -> None:
    say("トラッカー github・release か hold で外した直後の claim・edit が GitHub の古い in_progress に戻されない")
    with github() as (fake, _tmp, main_path, wt1):
        for verb in ("release", "hold"):
            a = new(main_path, f"{verb} のあとに取り直す", body=HELD_PLANNED_BODY if verb == "hold" else NEW_DEFAULT_BODY)
            n = num(a)
            r = run_task(wt1, "claim", a)
            check(f"{verb}: claim", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
            run_task(wt1, "sync")
            check(f"{verb}: 着手は GitHub へ in_progress で届いた", "status::in_progress" in fake.issues[n]["labels"]
                  and fake.status_of(n) == "In progress", repr(fake.issues[n]) + repr(fake.items))
            time.sleep(1.1)
            requests_before = sum(fake.requests.values())
            if verb == "release":
                r = run_task(wt1, "release", a)
            else:
                r = run_task(wt1, "hold", a, "--reason-file", "-", stdin="手放す")
            check(f"{verb}: 外せた", r.returncode == 0, r.stdout + r.stderr)
            if verb == "hold":
                r = run_task(wt1, "edit", a, "--status", "todo")
                check("hold: sync を挟まない edit --status todo が通る", r.returncode == 0 and "NOT_READY" not in r.stdout,
                      r.stdout + r.stderr)
            r = run_task(wt1, "claim", a)
            check(f"{verb}: sync を挟まない claim が通る", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
            say(f"  {verb}: 外してから取り直すまでの GitHub への要求 {sum(fake.requests.values()) - requests_before} 回")
            issue = beads.show(main_path, beads.to_bd_id(a))
            check(f"{verb}: 持ち主は wt1 のまま", issue is not None and issue.status == "in_progress" and issue.assignee == "wt1",
                  str(issue and issue.raw))

        b = new(main_path, "別の作業ツリーが着手中")
        r = run_task(main_path, "claim", b)
        check("claim", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
        run_task(main_path, "sync")
        r = run_task(wt1, "claim", b)
        check("担当者のある着手は引き込まれず TAKEN", r.returncode != 0 and "TAKEN" in r.stdout, r.stdout + r.stderr)


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


def test_release_sync_then_github_change_is_pulled() -> None:
    say("トラッカー github・外した版を sync で送ったあとは、GitHub 側の変更をこれまでどおり取り込む")
    with github() as (fake, _tmp, main_path, wt1):
        def taken_and_dropped(title: str, verb: str) -> tuple[str, int]:
            a = new(main_path, title, body=HELD_PLANNED_BODY if verb == "hold" else NEW_DEFAULT_BODY)
            run_task(wt1, "claim", a)
            run_task(wt1, "sync")
            time.sleep(1.1)
            if verb == "release":
                run_task(wt1, "release", a)
            else:
                run_task(wt1, "hold", a, "--reason-file", "-", stdin="手放す")
            r = run_task(wt1, "sync")
            check(f"{verb}: 外した版を sync が送った", r.returncode == 0 and "TRACKER\tOK\tgithub" in r.stdout, r.stdout + r.stderr)
            fake.clock_offset += 60
            return a, num(a)

        a, n = taken_and_dropped("release して送ったあとに閉じられる", "release")
        fake.edit(n, state="closed")
        r = run_task(wt1, "claim", a)
        check("release→sync→GitHub で閉じる→claim は着手できない", r.returncode != 0 and "CLAIMED" not in r.stdout, r.stdout + r.stderr)

        b, nb = taken_and_dropped("hold して送ったあとに担当がつく", "hold")
        fake.edit(nb, assignee="someone")
        run_task(wt1, "edit", b, "--status", "todo")
        issue = beads.show(main_path, beads.to_bd_id(b))
        check("hold→sync→GitHub で担当がつく→次の操作で取り込む", issue is not None and issue.assignee == "someone",
              str(issue and issue.raw))

        c, nc = taken_and_dropped("hold して送ったあとに本文が直される", "hold")
        fake.edit(nc, body="GitHub で直した本文")
        run_task(wt1, "edit", c, "--status", "todo")
        issue = beads.show(main_path, beads.to_bd_id(c))
        check("hold→sync→GitHub で本文が直される→次の操作で取り込む",
              issue is not None and "GitHub で直した本文" in str(issue.raw.get("description")), str(issue and issue.raw))


def _github_writes(fake, counted: str) -> int:
    """偽の GitHub への書き込み（POST・PATCH・PUT・DELETE と Status 欄）と、`bd github push`・`sync` の起動の合計。"""
    http = sum(c for k, c in fake.requests.items() if not k.startswith("GET "))
    pushes = sum(1 for l in open(counted).read().splitlines() if l.startswith(("github push", "github sync")))
    return http + fake.status_writes + pushes


def _push_created_in_process(toplevel: str, bd_id: str) -> None:
    """旧 `after` の送り（今は `push_created`）を、足場の環境のまま別のプロセスで打つ（並列のテストの環境変数を変えない）。"""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import tracker; "
        "tracker.session(sys.argv[2]).push_created([sys.argv[3]])"
    )
    subprocess.run([sys.executable, "-c", code, os.path.join(HERE, "..", "core"), toplevel, bd_id],
                   cwd=toplevel, env=child_env(), capture_output=True, check=False)


def test_tracker_writes_only_at_new_and_ship() -> None:
    say("トラッカー github・GitHub へ書くのは new と ship・sync の送りだけ（claim・edit・hold・release は書かない）")
    with github() as (fake, tmp, main_path, wt1):
        a, b, c = new(main_path, "書き込みを数える"), new(main_path, "hold する", body=HELD_PLANNED_BODY), new(main_path, "release する")
        counted = os.path.join(tmp, "bd-count.txt")
        local.env = {**local.env, "BD_COUNT_FILE": counted}
        open(counted, "w").close()

        def writes_during(fn) -> int:
            before = _github_writes(fake, counted)
            fn()
            return _github_writes(fake, counted) - before

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
            n = writes_during(fn)
            check(f"{label} は GitHub に書かない", n == 0, f"書き込み {n} 件")
        check("claim・edit・hold・release は成功する", results["claim"].stdout.startswith("CLAIMED")
              and all(results[k].returncode == 0 for k in ("e1", "e2", "e3", "hold", "c1", "c2")),
              repr({k: v.stdout + v.stderr for k, v in results.items()}))
        check("claim・edit のあとも GitHub の Status 欄は Todo のまま", fake.status_of(num(a)) == "Todo", repr(fake.items))

        weak = writes_during(lambda: (edit("--summary", "また直した"), _push_created_in_process(wt1, beads.to_bd_id(a))))
        check("弱めた実装（edit のあとに旧 after の送りを足す）では書き込みが数えられる", weak >= 1, f"書き込み {weak} 件")

        work_and_done(wt1, a)
        open(counted, "w").close()
        statuses = fake.status_writes
        r = run_task(wt1, "ship")
        sends = [l for l in open(counted).read().splitlines() if l.startswith("github sync --push-only")]
        check("ship の最後に github sync --push-only が1回、TRACKER OK が1回", r.returncode == 0 and len(sends) == 1
              and r.stdout.count("TRACKER\tOK\tgithub") == 1, r.stdout + r.stderr + "\n".join(sends))
        check("Issue は closed で、Status 欄は Done になる",
              fake.issues[num(a)]["state"] == "closed" and fake.status_of(num(a)) == "Done"
              and fake.status_writes - statuses >= 1, repr(fake.issues[num(a)]) + repr(fake.items))

    with github(prefix=beads.PREFIX_LOCAL) as (fake, tmp, main_path, wt1):
        counted = os.path.join(tmp, "bd-count.txt")
        local.env = {**local.env, "BD_COUNT_FILE": counted}
        open(counted, "w").close()
        made = _github_writes(fake, counted)
        t = new(main_path, "t 接頭辞の課題")
        check("t 接頭辞の new は送る（Beads から送るだけ）", _github_writes(fake, counted) > made and len(fake.issues) == 1,
              repr(fake.requests))
        made = _github_writes(fake, counted)
        r1 = run_task(wt1, "claim", t)
        r2 = run_task(wt1, "edit", t, "--summary", "題を直した")
        check("t 接頭辞の claim・edit は送らない", r1.stdout.startswith("CLAIMED") and r2.returncode == 0
              and _github_writes(fake, counted) == made, r1.stdout + r1.stderr + r2.stdout + r2.stderr)


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
    with github(prefix=None) as (fake, tmp, main_path, _wt1):
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

        arm_kv_write(tmp)
        fake.clock_offset = 0
        b = new(main_path, "取り込みの間に同期の時刻を書かれる")
        nb, b_id = num(b), beads.to_bd_id(b)
        time.sleep(1.1)
        bd(main_path, "update", b_id, "--title", "Beads で直した題2")
        fake.clock_offset = 60
        fake.edit(nb, body="GitHub でも直した2")
        later = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        with kv_written_during(tmp, "github pull", f"{tracker_claims.SYNCED_ITEM_PREFIX}{nb}", later) as fired:
            r = run_task(main_path, "sync")
        check("取り込みの間に別の作業ツリーが同期の時刻を進めても、取り込みの前に読んだ時刻で CONFLICT を出す",
              os.path.exists(fired) and f"TRACKER\tCONFLICT\t{b}" in r.stdout, r.stdout + r.stderr)


# 長いものから並列に乗せる（後ろに残ると全体がその分延びる）。
TESTS = (
    test_tracker_github_bidirectional,
    test_tracker_writes_only_at_new_and_ship,
    test_tracker_sync_lock_busy,
    test_tracker_sync_skips_echo,
    test_tracker_github_conflict,
    test_tracker_register_timeout_no_duplicate,
    test_tracker_pull_reports_duplicate,
    test_release_then_claim_without_sync,
    test_release_sync_then_github_change_is_pulled,
    test_edit_deps_after_body_edit_keeps_body,
)


if __name__ == "__main__":
    refuse_direct_run("selftest_beads.py")

