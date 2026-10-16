from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

from selftest_support import check, copy_beads, git, refuse_direct_run, say, weight, write  # noqa: E402
import beads  # noqa: E402
import ledger  # noqa: E402
import metrics  # noqa: E402
import taskfile  # noqa: E402
import tw_base  # noqa: E402
from selftest_fixtures import BODY, PLANNED_BODY, commit_task, flow_rows, make_repo, run_task  # noqa: E402


@weight(14)
def test_flow_records_and_metrics() -> None:
    say("task.py: 着手・検証・送り出し・完了が台帳の flow/ に残り、metrics が数を出す")
    with tempfile.TemporaryDirectory() as tmp:
        flag = os.path.join(tmp, "verify-ok")
        verify_script = write(os.path.join(tmp, "verify.sh"), f'[ -f "{flag}" ] && echo ok || {{ echo fail; exit 1; }}\n')
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify=f"`sh {verify_script}`")
        commit_task(main_path, taskfile.Task("T-100", "流れ", "todo", "sonnet", "Y", (), PLANNED_BODY))
        commit_task(main_path, taskfile.Task("T-101", "やり直し", "todo", "haiku", "Y", (), PLANNED_BODY))

        r = run_task(wt1, "metrics")
        check("記録が無ければ EMPTY", r.returncode == 0 and r.stdout.strip() == "EMPTY", r.stdout + r.stderr)

        run_task(wt1, "claim", "T-101")
        run_task(wt1, "release", "T-101")
        run_task(wt1, "claim", "T-101")
        run_task(wt1, "release", "T-101")
        run_task(wt1, "claim", "T-100")
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(wt1, "verify")
        check("検証が落ちれば VERIFY_NOT_PASSED", r.returncode == 10, r.stdout + r.stderr)
        write(flag, "x")
        r = run_task(wt1, "verify")
        check("通れば VERIFIED", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)
        git(wt1, "add", "-A")
        result_path = write(os.path.join(tmp, "result.md"), "- 検証: x\n- 振り返り: 兆候なし\n")
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "T-100: 完了")
        write(os.path.join(main_path, "unrelated.txt"), "x")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "mainだけの変更")
        os.remove(flag)
        r = run_task(wt1, "ship")
        check("ship の検証が落ちれば VERIFY_FAILED", r.returncode == 8, r.stdout + r.stderr)
        write(flag, "x")
        r = run_task(wt1, "ship")
        check("打ち直せば SHIPPED", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)

        rows_ = flow_rows(wt1)
        steps = [(e["event"], e["task"], e.get("result")) for e in rows_]
        check(
            "出来事が起きた順に1行ずつ増える",
            steps
            == [
                ("claim", "T-101", None),
                ("release", "T-101", None),
                ("claim", "T-101", None),
                ("release", "T-101", None),
                ("claim", "T-100", None),
                ("verify", "T-100", "VERIFY_NOT_PASSED"),
                ("verify", "T-100", "VERIFIED"),
                ("done", "T-100", None),
                ("ship", "T-100", "VERIFY_FAILED"),
                ("lap", "T-100", None),
                ("lap", "T-100", None),
                ("ship", "T-100", "SHIPPED"),
            ],
            repr(steps),
        )
        check(
            "各行に difficulty と UTC の時刻があり、verify は所要秒を持つ",
            {e["difficulty"] for e in rows_ if e["task"] == "T-100"} == {"sonnet"}
            and all(e["t"].endswith("+00:00") for e in rows_)
            and all(isinstance(e["seconds"], int) for e in rows_ if e["event"] == "verify"),
            repr(rows_),
        )
        done = next(e for e in rows_ if e["event"] == "done")
        check("done は dropped と振り返りの有無だけを持つ", done["dropped"] is False and done["reflection"] == "none", repr(done))
        flow_text = json.dumps(rows_, ensure_ascii=False)
        check("パスもコマンドも記録に入らない", tmp not in flow_text and "verify.sh" not in flow_text, flow_text)

        # 前の期間の値（8日前に着手して7日前に送った1件）
        old = lambda t, ev, **kw: json.dumps({"t": t, "event": ev, "task": "T-050", "difficulty": "sonnet", **kw})
        stamp = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        d = ledger.flow_dir(ledger.ledger_root(cwd=wt1))
        write(os.path.join(d, "2000-01.jsonl"), "\n".join([old(stamp(9), "claim"), old(stamp(8), "ship", result="SHIPPED")]) + "\n")
        r = run_task(wt1, "metrics")
        table = {l.split("\t")[0]: l.split("\t")[1:] for l in r.stdout.splitlines()}
        say("  --- tw metrics の出力 ---")
        for line in r.stdout.splitlines():
            say(f"  | {line}")
        check(
            "metrics が今の期間と前の期間の数を並べる",
            r.returncode == 0
            and table["shipped"] == ["1", "1"]
            and table["lead_median_seconds"][1] == "86400"
            and table["lead_max_seconds"][0] != "-"
            and table["verify_per_task"] == ["2.0", "-"]
            and table["verify_failed"] == ["1", "0"]
            and table["ship_verify_failed"] == ["1", "0"]
            and table["reclaim"] == ["1", "0"]
            and table["reflection_none_ratio"] == ["100% (1/1)", "-"],
            r.stdout + r.stderr,
        )
        with open(os.path.join(d, "2000-01.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": stamp(0), "event": "done", "task": "T-051", "difficulty": "haiku",
                                "dropped": False, "reflection": "skipped"}) + "\n")
        r = run_task(wt1, "metrics")
        check("振り返りを近道で省いた done は reflection_none_ratio の分母に入らない",
              "reflection_none_ratio\t100% (1/1)\t-" in r.stdout.splitlines(), r.stdout)
        r = run_task(wt1, "metrics", "--days", "30")
        check("--days で期間が変わる（前の期間の1件が今の期間に入る）", r.stdout.splitlines()[1].split("\t")[1:] == ["2", "0"], r.stdout)

        with open(os.path.join(d, "2000-01.jsonl"), "a", encoding="utf-8") as f:
            f.write("壊れた行\n{\"t\": \"x\"}\n")
        r = run_task(wt1, "metrics")
        check("壊れた行は読み飛ばして件数を添える", r.returncode == 0 and r.stdout.splitlines()[-1] == "SKIPPED\t2", r.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "書けない", "todo", "sonnet", "Y", (), PLANNED_BODY))
        write(ledger.flow_dir(ledger.ledger_root(cwd=wt1)), "ファイルがディレクトリの場所を塞ぐ\n")
        r = run_task(wt1, "claim", "T-100")
        check(
            "記録を書けなくても claim の出力と終了コードは変わらず、標準エラーに1行だけ出る",
            r.returncode == 0 and r.stdout.startswith("CLAIMED\tT-100\t") and r.stderr.strip().startswith("flow:")
            and len(r.stderr.strip().splitlines()) == 1,
            r.stdout + r.stderr,
        )
        result_path = write(os.path.join(tmp, "result.md"), "x\n")
        r = run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done も変わらない", r.returncode == 0 and r.stdout.startswith("DONE\tT-100\t"), r.stdout + r.stderr)


def test_metrics_stages() -> None:
    say("task.py metrics --stages: lap を含む flow から段×difficulty×道の件数・中央値・最大が出る")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        d = ledger.flow_dir(ledger.ledger_root(cwd=wt1))
        r = run_task(wt1, "metrics", "--stages")
        check("記録が無ければ EMPTY", r.returncode == 0 and r.stdout.strip() == "EMPTY", r.stdout + r.stderr)
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        row = lambda task, sec, ev, **kw: json.dumps(
            {"t": (base + timedelta(seconds=sec)).isoformat(timespec="seconds"), "event": ev, "task": task, "difficulty": "sonnet", **kw}
        )
        lines = [
            row("T-060", 0, "claim"), row("T-060", 100, "lap", stage="delegate"), row("T-060", 400, "verify", result="VERIFIED", seconds=50),
            row("T-060", 500, "lap", stage="accept"), row("T-060", 560, "lap", stage="review"), row("T-060", 700, "lap", stage="retro"),
            row("T-060", 760, "done"), row("T-060", 790, "ship", result="VERIFY_FAILED"), row("T-060", 800, "ship", result="SHIPPED"),
            row("T-061", 0, "claim"), row("T-061", 300, "step", step=1, steps=2), row("T-061", 900, "done"), row("T-061", 930, "ship", result="SHIPPED"),
            row("T-062", 0, "claim"), row("T-062", 10, "done"),
        ]
        haiku = lambda task, sec, ev, **kw: json.dumps(
            {"t": (base + timedelta(seconds=sec)).isoformat(timespec="seconds"), "event": ev, "task": task, "difficulty": "haiku", **kw}
        )
        lines += [
            haiku("T-063", 0, "claim"), haiku("T-063", 20, "lap", stage="direct"),
            haiku("T-063", 100, "verify", result="VERIFIED", seconds=30), haiku("T-063", 200, "done"),
            haiku("T-063", 220, "ship", result="SHIPPED"),
            haiku("T-064", 0, "claim"), haiku("T-064", 10, "lap", stage="direct"), haiku("T-064", 50, "lap", stage="delegate"),
            haiku("T-064", 300, "done"), haiku("T-064", 310, "ship", result="SHIPPED"),
        ]
        opus = lambda task, sec, ev, **kw: json.dumps(
            {"t": (base + timedelta(seconds=sec)).isoformat(timespec="seconds"), "event": ev, "task": task, "difficulty": "opus", **kw}
        )
        lines += [
            opus("T-065", 0, "claim"), opus("T-065", 10, "lap", stage="delegate"), opus("T-065", 100, "lap", stage="review"),
            opus("T-065", 200, "lap", stage="retro"), opus("T-065", 340, "verify", result="VERIFIED", seconds=120),
            opus("T-065", 400, "done"), opus("T-065", 410, "ship", result="SHIPPED"),
        ]
        write(os.path.join(d, "2000-01.jsonl"), "\n".join(lines) + "\n")
        r = run_task(wt1, "metrics", "--stages")
        got = [tuple(l.split("\t")) for l in r.stdout.splitlines()]
        check(
            "段×difficulty×道ごとに件数・中央値・最大の STAGE の行が段の順に出る（送り出していないタスクは入らない。"
            "lap direct のあとに lap delegate があれば normal。検証でない段は次の検証でない出来事までから検証と重なった秒を引く）",
            r.returncode == 0
            and got
            == [
                ("STAGE", "計画", "haiku", "direct", "1", "20", "20"),
                ("STAGE", "計画", "haiku", "normal", "1", "10", "10"),
                ("STAGE", "計画", "opus", "normal", "1", "10", "10"),
                ("STAGE", "計画", "sonnet", "normal", "2", "200", "300"),
                ("STAGE", "直し", "haiku", "direct", "1", "150", "150"),
                ("STAGE", "直し", "haiku", "normal", "1", "40", "40"),
                ("STAGE", "委譲", "haiku", "normal", "1", "250", "250"),
                ("STAGE", "委譲", "opus", "normal", "1", "90", "90"),
                ("STAGE", "委譲", "sonnet", "normal", "2", "475", "600"),
                ("STAGE", "検証", "haiku", "direct", "1", "30", "30"),
                ("STAGE", "検証", "opus", "normal", "1", "120", "120"),
                ("STAGE", "検証", "sonnet", "normal", "1", "50", "50"),
                ("STAGE", "受け入れ", "sonnet", "normal", "1", "60", "60"),
                ("STAGE", "レビュー", "opus", "normal", "1", "100", "100"),
                ("STAGE", "レビュー", "sonnet", "normal", "1", "140", "140"),
                ("STAGE", "振り返り", "opus", "normal", "1", "80", "80"),
                ("STAGE", "振り返り", "sonnet", "normal", "1", "60", "60"),
                ("STAGE", "送り出し", "haiku", "direct", "1", "20", "20"),
                ("STAGE", "送り出し", "haiku", "normal", "1", "10", "10"),
                ("STAGE", "送り出し", "opus", "normal", "1", "10", "10"),
                ("STAGE", "送り出し", "sonnet", "normal", "2", "35", "40"),
            ],
            r.stdout + r.stderr,
        )
        say("  --- tw metrics --stages の出力 ---")
        for line in r.stdout.splitlines():
            say(f"  | {line}")


def test_metrics_stages_parallel_steps() -> None:
    say("task.py metrics --stages: 並列の段の返却が重なっても、委譲の時間を段ごとの和で数えない")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    row = lambda sec, ev, **kw: metrics.Event(base + timedelta(seconds=sec), ev, "T-070", {"difficulty": "opus", **kw})
    events = [
        row(0, "claim"), row(10, "lap", stage="delegate"),
        row(200, "step", step=1, steps=3), row(210, "step", step=2, steps=3),
        row(400, "verify", result="VERIFIED", seconds=50), row(410, "lap", stage="accept"),
        row(500, "done"), row(510, "ship", result="SHIPPED"),
    ]
    delegated = metrics.stage_durations(events, 1)[0][("委譲", "opus", "normal")]
    check("委譲の時間の和は lap delegate から受け入れまでの壁時計から検証の秒を引いたもので、段1（190秒）と段2（200秒）の和を超えない",
          sum(delegated) == 410 - 10 - 50, repr(delegated))


def test_metrics_stages_edge() -> None:
    say("task.py metrics --stages: edge 付きの lap は end − start、旧い行は今まで通り、end の無い start は OPEN、NOTHING のタスクも出る")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        d = ledger.flow_dir(ledger.ledger_root(cwd=wt1))
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        row = lambda task, sec, ev, **kw: json.dumps(
            {"t": (base + timedelta(seconds=sec)).isoformat(timespec="seconds"), "event": ev, "task": task, "difficulty": "sonnet", **kw}
        )
        lines = [
            row("T-080", 0, "claim"),
            row("T-080", 10, "lap", stage="delegate", edge="start"),
            row("T-080", 100, "verify", result="VERIFIED", seconds=30),
            row("T-080", 310, "lap", stage="delegate", edge="end"),
            row("T-080", 320, "lap", stage="review", edge="start"),
            row("T-080", 400, "done"),
            row("T-080", 410, "ship", result="NOTHING"),
            row("T-081", 0, "claim"), row("T-081", 50, "lap", stage="delegate"), row("T-081", 150, "lap", stage="accept"),
            row("T-081", 200, "done"), row("T-081", 210, "ship", result="SHIPPED"),
            row("T-082", 0, "claim"), row("T-082", 20, "lap", stage="land", edge="start"),
            row("T-082", 80, "lap", stage="land", edge="end"), row("T-082", 90, "done"),
            row("T-082", 100, "lap", stage="sync", edge="start"), row("T-082", 160, "lap", stage="sync", edge="end"),
            row("T-082", 170, "ship", result="NOTHING"),
        ]
        write(os.path.join(d, "2000-01.jsonl"), "\n".join(lines) + "\n")
        r = run_task(wt1, "metrics", "--stages")
        got = {tuple(l.split("\t")) for l in r.stdout.splitlines()}
        check("委譲は edge 付き（T-080: end − start の300秒。検証の秒は引かない。NOTHING のタスク）と旧い行（T-081: 100秒）の2件",
              ("STAGE", "委譲", "sonnet", "normal", "2", "200", "300") in got, r.stdout + r.stderr)
        check("edge の無い旧い lap は今の規則で出る（accept のみ 受け入れ 50秒）",
              ("STAGE", "受け入れ", "sonnet", "normal", "1", "50", "50") in got, r.stdout)
        check("合流・反映の段が出る（NOTHING のタスク）",
              ("STAGE", "合流", "sonnet", "normal", "1", "60", "60") in got and ("STAGE", "反映", "sonnet", "normal", "1", "60", "60") in got,
              r.stdout)
        check("end の無い start は STAGE に入れず OPEN の行で数え、STAGE の行のあとに出る",
              ("OPEN", "レビュー", "1") in got and not any(t[:2] == ("STAGE", "レビュー") for t in got)
              and r.stdout.splitlines()[-1] == "OPEN\tレビュー\t1", r.stdout)
        r = run_task(wt1, "metrics")
        check("shipped の数は SHIPPED だけ（NOTHING を入れない）",
              any(l.split("\t")[:2] == ["shipped", "1"] for l in r.stdout.splitlines()), r.stdout)


def test_lap_edge() -> None:
    say("task.py lap: 第3引数 start|end は edge 欄に残り、省くと欄を書かず、不正なら終了コード2")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "段", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        r = run_task(wt1, "lap", "T-100", "land", "end")
        check("land end は LAPPED", r.returncode == 0 and r.stdout.strip() == "LAPPED\tT-100\tland\tend", r.stdout + r.stderr)
        run_task(wt1, "lap", "T-100", "sync")
        r = run_task(wt1, "lap", "T-100", "land", "bogus")
        check("不正な第3引数は終了コード2", r.returncode == 2, r.stdout + r.stderr)
        laps = [e for e in flow_rows(wt1) if e["event"] == "lap"]
        check("edge は与えた行だけに残り、不正な呼び出しは記録しない",
              [(e["stage"], e.get("edge")) for e in laps] == [("land", "end"), ("sync", None)], repr(laps))


def test_relay() -> None:
    say("task.py relay: 鍵ごとに最後の申し送りを読み、空は消した扱い、書けるのは着手の持ち主だけで、release・done をまたいで残る")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "申し送り", "todo", "sonnet", "Y", (), BODY))
        relays = lambda: [c for c in beads.comments(wt1, "t-100") if str(c.get("text") or "").startswith(beads.RELAY_HEADING_PREFIX)]

        r = run_task(wt1, "relay", "T-100", "実装", "--body-file", "-", stdin="- 規約: なし\n")
        check("着手の印が無ければ NOT_OWNER で書かない",
              r.returncode == 4 and r.stdout.startswith("NOT_OWNER\tT-100") and not relays(), r.stdout + r.stderr)
        r = run_task(wt1, "relay", "T-100", "実装")
        check("書いていない鍵は NONE で終了コード0", r.returncode == 0 and r.stdout.strip() == "NONE\tT-100\t実装", r.stdout + r.stderr)

        run_task(wt1, "claim", "T-100")
        r = run_task(wt2, "relay", "T-100", "実装", "--body-file", "-", stdin="- 規約: なし\n")
        check("別の作業ツリーの印では NOT_OWNER で書かない", r.returncode == 4 and not relays(), r.stdout + r.stderr)
        for body in ("- 規約: 古い\n", "- 規約: 新しい\n- ドラフト: x\n"):
            r = run_task(wt1, "relay", "T-100", "実装", "--body-file", "-", stdin=body)
            check("書けば RELAYED", r.returncode == 0 and r.stdout.strip() == "RELAYED\tT-100\t実装", r.stdout + r.stderr)
        run_task(wt1, "relay", "T-100", "受け入れ", "--body-file", "-", stdin="- 材料: friction=0\n")
        r = run_task(wt2, "relay", "T-100", "実装")
        check("同じ鍵は最後の本文で、別の作業ツリーからも読める",
              r.returncode == 0 and r.stdout == "RELAY\tT-100\t実装\n- 規約: 新しい\n- ドラフト: x\n", r.stdout + r.stderr)
        r = run_task(wt1, "relay", "T-100", "受け入れ")
        check("鍵ごとに別の本文", r.stdout == "RELAY\tT-100\t受け入れ\n- 材料: friction=0\n", r.stdout)

        run_task(wt1, "relay", "T-100", "受け入れ", "--body-file", "-", stdin="")
        r = run_task(wt1, "relay", "T-100", "受け入れ")
        check("空の本文で書いたら NONE", r.stdout.strip() == "NONE\tT-100\t受け入れ", r.stdout)

        run_task(wt1, "release", "T-100")
        run_task(wt1, "claim", "T-100")
        r = run_task(wt1, "relay", "T-100", "実装")
        check("release → claim のあとも残る", r.stdout.startswith("RELAY\tT-100\t実装\n- 規約: 新しい"), r.stdout + r.stderr)

        run_task(wt1, "done", "T-100", "--result-file", "-", stdin="- 検証: x\n- 振り返り: 兆候なし\n")
        shown = run_task(wt1, "show", "T-100").stdout
        result = shown.split("## 結果\n", 1)[-1]
        check("done のあとの ## 結果 は done の本文のままで申し送りが混ざらない",
              "- 振り返り: 兆候なし" in result and "申し送り" not in result and "規約: 新しい" not in shown, shown)
        r = run_task(wt1, "relay", "T-100", "実装")
        check("done のあとも読める", r.stdout.startswith("RELAY\tT-100\t実装\n"), r.stdout)

        for key in ("", " ", "a\tb", "a\nb"):
            r = run_task(wt1, "relay", "T-100", key)
            check(f"鍵 {key!r} は終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = run_task(wt1, "relay", "T-999", "実装")
        check("無い ID は NOT_READY で終了コード4", r.returncode == 4 and r.stdout.startswith("NOT_READY\tT-999"), r.stdout + r.stderr)


def test_record_normalizes_task_id() -> None:
    say("tw_base.record: どの書き方のタスクIDを渡しても、flow/ の task は表示の形の1つになる")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        for spelling in ("T-100", "t-100", beads.to_bd_id("T-100")):
            tw_base.record(wt1, "lap", spelling, stage="sync")
        tasks = [e["task"] for e in flow_rows(wt1) if e["event"] == "lap"]
        check("3通りとも T-100 で残る", tasks == ["T-100"] * 3, repr(tasks))


def test_retrospect_due() -> None:
    say("task.py status: 横断の振り返りの時期に retrospect_due の行を出す")
    due_line = lambda out: next((l for l in out.splitlines() if l.startswith("retrospect_due\t")), None)
    record = lambda day: f"# 横断の振り返りの記録\n\n## {day}（x〜y）\n\n- ドラフト: 0件（なし）\n"
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)

        r = run_task(wt1, "status")
        check("記録も flow/ も無い初回は出さない", r.returncode == 0 and due_line(r.stdout) is None, r.stdout + r.stderr)

        d = ledger.flow_dir(ledger.ledger_root(cwd=wt1))
        claim = lambda days: json.dumps({"t": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds"), "event": "claim", "task": "T-001", "difficulty": "haiku"}) + "\n"
        write(os.path.join(d, "2000-01.jsonl"), claim(3))
        r = run_task(wt1, "status")
        check("記録が無く flow/ の最古が7日未満なら出さない", due_line(r.stdout) is None, r.stdout)

        write(os.path.join(d, "2000-01.jsonl"), claim(8) + claim(3))
        r = run_task(wt1, "status")
        check("記録が無く flow/ の最古が8日前なら -\\t8d で出る", due_line(r.stdout) == "retrospect_due\t-\t8d", r.stdout)

        write(os.path.join(main_path, "docs", "history", "retrospect.md"), record("2000-01-01"))
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "古い記録")
        r = run_task(wt1, "status")
        line = due_line(r.stdout) or ""
        check("主ブランチの記録が7日以上前なら日付つきで出る", line.startswith("retrospect_due\t2000-01-01\t"), r.stdout)

        today = datetime.now().date().isoformat()
        write(os.path.join(wt1, "docs", "history", "retrospect.md"), record(today))
        r = run_task(wt1, "status")
        check("作業ツリーに今日の記録を書くと出なくなる", due_line(r.stdout) is None, r.stdout)
        r = run_task(wt2, "status")
        check("主ブランチへ入れるまでは、ほかの作業ツリーには出る", (due_line(r.stdout) or "").startswith("retrospect_due\t2000-01-01\t"), r.stdout)

        write(os.path.join(main_path, "docs", "history", "retrospect.md"), record(today) + record("2000-01-01").split("\n", 2)[2])
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "今日の記録")
        r = run_task(wt2, "status")
        check("主ブランチへ入れれば、ほかの作業ツリーでも出なくなる", due_line(r.stdout) is None, r.stdout)


def test_config_file_agents_md_and_conflict() -> None:
    say("設定の読み: .tw/config.toml だけを読み、AGENTS.md・CLAUDE.md の節は読まない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, verify="`echo verified`")
        commit_task(main_path, taskfile.Task("T-100", "config.toml", "todo", "sonnet", "Y", (), BODY))
        r = run_task(wt1, "claim", "T-100")
        check("config.toml だけで claim できる", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
        result_path = write(os.path.join(tmp, "result.md"), "- 検証: x\n")
        r = run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done できる", r.returncode == 0, r.stdout + r.stderr)
        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "T-100: config.toml")
        r = run_task(wt1, "ship")
        check(
            "ship も config.toml の検証コマンドを読む（verify=none にならない）",
            r.returncode == 0 and r.stdout.startswith("SHIPPED") and "verify=none" not in r.stdout,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp)
        write(os.path.join(main_path, "AGENTS.md"), "# a\n\n## タスク運用\n\n- 検証コマンド: `false`\n")
        write(os.path.join(main_path, "CLAUDE.md"), "# y\n\n## タスク運用\n\n- ブランチ: 既定\n")
        r = run_task(main_path, "status")
        check(".tw/config.toml があれば両方に節があっても読まず、通る",
              r.returncode == 0 and "MISSING" not in r.stdout and "INVALID" not in r.stdout, r.stdout + r.stderr)
        r = run_task(main_path, "config")
        check("config の1行目は .tw/config.toml で、節の値は混ざらない",
              r.stdout.splitlines()[:1] == ["CONFIG\t.tw/config.toml"] and "verify\tなし\tconfig" in r.stdout.splitlines(),
              r.stdout + r.stderr)


def _make_config_doctor_repo(tmp: str, name: str, with_config: bool = True) -> str:
    """`task config-doctor`・`task config` のフィクスチャ用の最小リポジトリ（`.beads` 付き）。
    `with_config` が偽なら `.tw/config.toml` を置かず、CLAUDE.md の旧い節だけを置く。"""
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "beads.role", "maintainer")
    copy_beads(repo, beads.PREFIX_LOCAL)
    write(os.path.join(repo, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
    if with_config:
        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "なし"\nbranch = "既定"\n')
    else:
        write(
            os.path.join(repo, "CLAUDE.md"),
            "# x\n\n## タスク運用\n\n- 検証コマンド: なし\n- 整形コマンド: なし\n- ブランチ: 既定\n",
        )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def test_config_doctor() -> None:
    say("task.py config-doctor（設定と置き場のズレの点検。読むだけ）")

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "ok")
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "OKのリポジトリは終了コード0で検査がOK、old_section・legacy の行は無い",
            r.returncode == 0
            and any(l.startswith("base_branch\tOK\tmain\t順3") for l in lines)
            and any(l.startswith("config\tOK\t.tw/config.toml") for l in lines)
            and any(l.startswith("beads\tOK\t") for l in lines)
            and not any(l.startswith(("old_section", "legacy")) for l in lines),
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "leftover")
        write(os.path.join(repo, "develop", "tasks.json"), "[]\n")
        write(os.path.join(repo, "develop", "progress.md"), "## 未解決\n\n- x\n")
        r = run_task(repo, "config-doctor")
        check(
            "develop/ の旧い形式のファイルは見ない（終了コード0で legacy の行も無い）",
            r.returncode == 0 and "legacy" not in r.stdout and "develop/" not in r.stdout,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "no-config", with_config=False)
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            ".tw/config.toml が無ければ終了コード1で config が MISSING、CLAUDE.md の節は読まない",
            r.returncode == 1
            and any(l.startswith("config\tMISSING\t.tw/config.toml") for l in lines)
            and not any(l.startswith(("old_section", "legacy")) for l in lines),
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "no-beads")
        shutil.rmtree(os.path.join(repo, ".beads"))
        r = run_task(repo, "config-doctor")
        check(
            ".beads が無ければ終了コード1で beads が MISSING",
            r.returncode == 1 and any(l.startswith("beads\tMISSING\t") for l in r.stdout.splitlines()),
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "bad-branch")
        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "なし"\nbranch = "自分で切らない"\n')
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "branch が語彙の外なら config INVALID（終了コード3）",
            r.returncode == 3 and any(l.startswith("config\tINVALID\t.tw/config.toml") for l in lines),
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "toml")
        write(os.path.join(repo, "CLAUDE.md"), "# x\n")
        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "なし"\n')
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            ".tw/config.toml なら config は OK で old_section の行は出ない",
            r.returncode == 0
            and any(l.startswith("config\tOK\t.tw/config.toml") for l in lines)
            and not any(l.startswith("old_section") for l in lines),
            r.stdout + r.stderr,
        )
        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "なし"\nfoo = "x"\n')
        r = run_task(repo, "config-doctor")
        check(
            "知らないキーは行番号付きで config INVALID（終了コード3）",
            r.returncode == 3 and any(l.startswith("config\tINVALID\t.tw/config.toml\t") and ":2:" in l
                                      for l in r.stdout.splitlines()),
            r.stdout + r.stderr,
        )
        for body, lineno, what in (
            ('verify = "なし"\nflow = "main"\n', 2, "廃止した flow"),
            ('verify = "なし"\nflow = "direct"\n', 2, "語彙の外の値の flow も廃止の知らせ"),
            ('verify = "なし"\n[parts]\nacceptor = "my-plugin:acceptor"\n', 2, "廃止した [parts]"),
            ('verify = "なし"\nbranch = "切らない"\n\n[parts]\nship = "x"\n[parts]\n', 4, "最初の [parts]"),
            ('verify = "なし"\n[foo]\n', 2, "知らない表"),
        ):
            write(os.path.join(repo, ".tw", "config.toml"), body)
            r = run_task(repo, "config")
            check(f"{what} は config が行番号付きの INVALID（終了コード3）",
                  r.returncode == 3 and r.stdout.startswith(f"INVALID\t.tw/config.toml:{lineno}:"), r.stdout + r.stderr)
            if "廃止" in what:
                check(f"{what} は廃止と steps への案内を添える", "廃止した" in r.stdout and "steps" in r.stdout, r.stdout)


def test_config_doctor_prerequisites() -> None:
    say("task.py config-doctor: 前提のコマンドの行（必須・任意）")
    import tw_maint

    rows = tw_maint.prerequisite_rows((3, 8, 10), {"git": "2.40", "bd": "1.3.1", "dolt": "1.0", "gh": None, "tw": None}, False)
    check(
        "Python 3.9 未満は python MISSING、gh（トラッカーが github でない）と tw は OPTIONAL",
        rows[0].startswith("python\tMISSING\t必須\t3.8.10")
        and "gh\tOPTIONAL\t任意\tPATH に無い" in rows and "tw\tOPTIONAL\t任意\tPATH に無い" in rows,
        repr(rows),
    )
    rows = tw_maint.prerequisite_rows((3, 9, 6), {"git": "2.40", "bd": "1.2.9", "dolt": "1.0", "gh": None, "tw": "x"}, True)
    check(
        "bd が最低版未満なら bd MISSING、トラッカーが github なら gh は必須で MISSING、Python 3.9 は OK",
        rows[0].startswith("python\tOK\t") and any(l.startswith("bd\tMISSING\t必須\t1.2.9") for l in rows)
        and "gh\tMISSING\t必須\tPATH に無い" in rows,
        repr(rows),
    )

    with tempfile.TemporaryDirectory() as tmp:
        only_git = os.path.join(tmp, "bin")
        os.makedirs(only_git)
        os.symlink(shutil.which("git"), os.path.join(only_git, "git"))
        repo = _make_config_doctor_repo(tmp, "no-bd")
        r = run_task(repo, "config-doctor", env={"PATH": only_git})
        lines = r.stdout.splitlines()
        check(
            "bd・dolt が PATH に無ければ MISSING 必須で終了コード1、gh は OPTIONAL",
            r.returncode == 1
            and "bd\tMISSING\t必須\tPATH に無い" in lines
            and "dolt\tMISSING\t必須\tPATH に無い" in lines
            and "gh\tOPTIONAL\t任意\tPATH に無い" in lines
            and any(l.startswith("git\tOK\t必須\t") for l in lines),
            r.stdout + r.stderr,
        )

        write(os.path.join(repo, ".tw", "config.toml"),
              'verify = "なし"\nbranch = "既定"\ntracker = "github"\ngithub_project = "o/1"\n')
        r = run_task(repo, "config-doctor", env={"PATH": only_git})
        check(
            "tracker = github で gh が無ければ gh MISSING 必須",
            "gh\tMISSING\t必須\tPATH に無い" in r.stdout.splitlines(),
            r.stdout + r.stderr,
        )

        words = {l.split("\t")[1] for l in r.stdout.splitlines() if "\t" in l}
        import task

        help_body = task.SUBCOMMAND_HELP["config-doctor"][1].splitlines()[-1]
        with open(os.path.join(os.path.dirname(os.path.abspath(task.__file__)), "..", "..", "tw-reference.md"), encoding="utf-8") as f:
            row = next(l for l in f if l.startswith("| `config-doctor` |"))
        check(
            "config-doctor が実際に出す2語目は、help の最後の一文と tw-reference の行の両方に書いてある",
            "OPTIONAL" in words and all(w in help_body and w in row for w in words | {"INVALID"}),
            f"{words} / {help_body} / {row}",
        )


def test_bin_tw_python_floor() -> None:
    say("bin/tw: Python の下限を検査し、hooks は前提の欠落だけ1行警告する")
    import subprocess

    from selftest_fixtures import TASK_PY

    plugin_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(TASK_PY)))))
    bin_tw = os.path.join(plugin_root, "bin", "tw")
    with tempfile.TemporaryDirectory() as tmp:
        old = os.path.join(tmp, "old")
        os.makedirs(old)
        write(os.path.join(old, "python3"), '#!/bin/sh\n[ "$1" = "-c" ] && exit 1\necho "Python 3.8.10"\n')
        os.chmod(os.path.join(old, "python3"), 0o755)
        base_path = "/usr/bin:/bin"
        r = subprocess.run([bin_tw, "--help"], capture_output=True, text=True, env={"PATH": f"{old}:{base_path}"})
        check(
            "Python 3.9 未満なら日本語で案内して終了コード78",
            r.returncode == 78 and "Python 3.9 以上が要る" in r.stderr and "3.8.10" in r.stderr,
            r.stdout + r.stderr,
        )

        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        r = subprocess.run(["/bin/sh", bin_tw, "--help"], capture_output=True, text=True, env={"PATH": empty})
        check("python3 が無ければ案内して終了コード78", r.returncode == 78 and "python3 が見つからない" in r.stderr,
              r.stdout + r.stderr)

        import json

        with open(os.path.join(plugin_root, "hooks", "hooks.json"), encoding="utf-8") as f:
            commands = [h["command"] for gs in json.load(f)["hooks"].values() for g in gs for h in g["hooks"]]
        r = subprocess.run(["/bin/sh", "-c", commands[0]], capture_output=True, text=True,
                           env={"PATH": f"{old}:{base_path}", "CLAUDE_PLUGIN_ROOT": plugin_root})
        check(
            "hook は前提の欠落で1行警告し、終了コードは0（コミットや委譲を塞がない）",
            r.returncode == 0 and r.stdout == "" and r.stderr.count("\n") == 1 and "前提が欠けている" in r.stderr,
            r.stdout + r.stderr,
        )
        r = subprocess.run(["/bin/sh", "-c", commands[0]], capture_output=True, text=True,
                           env={"PATH": os.environ["PATH"], "CLAUDE_PLUGIN_ROOT": plugin_root}, input="{}")
        check("前提が揃っていれば hook は警告を出さず終了コード0", r.returncode == 0 and r.stderr == "", r.stdout + r.stderr)


def test_config_command() -> None:
    say("task.py config: 解けた設定を出す")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "no-config", with_config=False)
        r = run_task(repo, "config")
        check("設定ファイルが無ければ config は MISSING（終了コード6）",
              r.returncode == 6 and r.stdout == "MISSING\n", r.stdout + r.stderr)
        r = run_task(repo, "status")
        check("設定ファイルが無ければ status も MISSING（終了コード6）",
              r.returncode == 6 and r.stdout == "MISSING\n", r.stdout + r.stderr)

        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "./check.sh"  # 説明\nbranch = "切らない"\n')
        r = run_task(repo, "config")
        lines = r.stdout.splitlines()
        check(
            ".tw/config.toml の値を出す",
            r.returncode == 0
            and lines[:1] == ["CONFIG\t.tw/config.toml"]
            and "verify\t./check.sh\tconfig" in lines
            and "branch\t切らない\tconfig" in lines
            and "format\tなし\tdefault" in lines,
            r.stdout + r.stderr,
        )
        check(
            "出力に flow・parts.* の行は無く、steps の行は出る",
            not any(l.startswith(("flow\t", "parts.")) for l in lines) and any(l.startswith("steps\t") for l in lines),
            r.stdout,
        )
        r = run_task(repo, "status")
        check("config.toml があれば status は old_layout の行を出さない",
              r.returncode == 0 and "old_layout" not in r.stdout, r.stdout + r.stderr)

        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "x"\nbranch = 1\n')
        r = run_task(repo, "config")
        check("読めなければ INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID\t.tw/config.toml:2:"),
              r.stdout + r.stderr)


def test_config_schema() -> None:
    say("task.py config --schema: 欄の形を1行の JSON で出す（設定にも git にもよらない）")
    with tempfile.TemporaryDirectory() as tmp:
        r = run_task(tmp, "config", "--schema")
        check("git の外でも終了コード0で1行", r.returncode == 0 and len(r.stdout.splitlines()) == 1, r.stdout + r.stderr)
        schema = json.loads(r.stdout)
        keys = schema["keys"]
        check("形の版は2で、既定・なしの語を持たない",
              schema["schema"] == 2 and "default_word" not in schema and "none_word" not in schema, repr(schema))
        check("欄は steps の1つだけ（flow・parts.<枠> は無い）", [k["key"] for k in keys] == ["steps"],
              repr([k["key"] for k in keys]))
        check("どの欄も label と description を持つ", all(k["label"] and k["description"] for k in keys), repr(keys))


def _skill(root: str, name: str, mark: str | None, description: str = "説明", order: str | None = None) -> str:
    """`root/<name>/SKILL.md` を書く。`mark` は `metadata` に置く `workflow-step` の値（None なら metadata を置かない）。
    `order` は `workflow-step-order` の値（None なら置かない）。"""
    meta = f'metadata:\n  workflow-step: "{mark}"\n' if mark is not None else ""
    if mark is not None and order is not None:
        meta += f'  workflow-step-order: "{order}"\n'
    return write(
        os.path.join(root, name, "SKILL.md"),
        f"---\nname: {name}\ndescription: >\n  {description}\n  続き\n{meta}---\n本文\n",
    )


def test_default_steps_order() -> None:
    say("layout.default_steps: plugin の候補を workflow-step-order の順に、位置の無いもの・読めないものは後ろに走査順で並べる")
    import layout

    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        plugin_root = os.path.join(tmp, "plugin")
        plugin_skills = os.path.join(plugin_root, "skills")
        _skill(plugin_skills, "a-last", "true")
        _skill(plugin_skills, "b-second", "true", order="2")
        _skill(plugin_skills, "c-first", "true", order="1")
        _skill(plugin_skills, "d-bad", "true", order="x")
        _skill(plugin_skills, "e-tenth", "true", order="10")
        _skill(os.path.join(home, ".claude", "skills"), "home-step", "true", order="0")
        schema = layout.config_schema(None, home, (plugin_root, "p"))
        steps = schema["keys"][0]
        check("default は位置の数の順、位置の無いもの・整数でないものは後ろに走査順、plugin の外は入らない",
              steps["default"] == ["p:c-first", "p:b-second", "p:e-tenth", "p:a-last", "p:d-bad"], repr(steps["default"]))
        check("values は走査順のまま",
              [v["value"] for v in steps["values"]] == ["home-step", "p:a-last", "p:b-second", "p:c-first", "p:d-bad", "p:e-tenth"],
              repr(steps["values"]))

    plugin = layout.own_plugin_root()
    if plugin is not None:
        got = layout.config_schema(None, None, plugin)["keys"][0]["default"]
        check("同梱の既定の並びは 実装・受け入れ・振り返り・送り出し の段の順",
              got == [f"{plugin[1]}:{n}" for n in ("implement-step", "accept-step", "retrospect-step", "ship-step")], repr(got))


def test_step_candidates() -> None:
    say("layout.step_candidates: 印のあるスキルだけを3つの走査先から拾い、実体が同じものは1つにする")
    import layout

    with tempfile.TemporaryDirectory() as tmp:
        project = os.path.join(tmp, "project")
        home = os.path.join(tmp, "home")
        plugin_root = os.path.join(tmp, "plugin")
        _skill(os.path.join(project, ".claude", "skills"), "proj-step", "true")
        _skill(os.path.join(project, ".claude", "skills"), "proj-plain", None)
        _skill(os.path.join(project, ".claude", "skills"), "proj-false", "false")
        _skill(os.path.join(home, ".claude", "skills"), "home-step", "true")
        plugin_skills = os.path.join(plugin_root, "skills")
        _skill(plugin_skills, "plug-a", "true", "プラグインの説明")
        _skill(plugin_skills, "plug-b", "true")
        _skill(plugin_skills, "plug-plain", None)
        os.symlink(os.path.join(plugin_skills, "plug-b"), os.path.join(home, ".claude", "skills", "plug-b"))
        got = layout.step_candidates(project, home, (plugin_root, "my-plugin"))
        check("印のあるスキルだけが、作業ツリー・ホーム・plugin の順に並ぶ",
              [c.value for c in got] == ["proj-step", "home-step", "plug-b", "my-plugin:plug-a"],
              repr([c.value for c in got]))
        check("リンクで入れた plugin のスキルは重ねず、リンクの名前（<名前>）で1つになる",
              [c.value for c in got].count("plug-b") == 1 and "my-plugin:plug-b" not in [c.value for c in got])
        plug_a = next(c for c in got if c.value == "my-plugin:plug-a")
        check("label は frontmatter の name、description は複数行を1行に畳んだもの",
              plug_a.label == "plug-a" and plug_a.description == "プラグインの説明 続き", repr(plug_a))
        check("plugin の実体のスキルは own（リンク経由も）、ほかは違う",
              {c.value: c.own for c in got} == {"proj-step": False, "home-step": False, "plug-b": True, "my-plugin:plug-a": True},
              repr(got))
        check("走査先が無ければ空", layout.step_candidates(None, None, None) == [])
        check("走査先のディレクトリが無くても空", layout.step_candidates(os.path.join(tmp, "none"), None, None) == [])

        quoted = os.path.join(tmp, "quoted", "skills")
        write(os.path.join(quoted, "q", "SKILL.md"),
              '---\nname: "q-name"\ndescription: "引用: 説明"\nmetadata:\n  workflow-step: \'true\'\n  other: x\n---\n')
        write(os.path.join(quoted, "broken", "SKILL.md"), "frontmatter が無い\n")
        got = layout.step_candidates(None, None, (os.path.join(tmp, "quoted"), "p"))
        check("引用符つきの値を読み、frontmatter の無いスキルは飛ばす",
              [(c.value, c.label, c.description) for c in got] == [("p:q-name", "q-name", "引用: 説明")], repr(got))


def test_config_schema_version() -> None:
    say("task.py config --schema: 並びの欄だけの schema 2。--version は知らない引数として終了コード2")
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q")
        _skill(os.path.join(repo, ".claude", "skills"), "my-step", "true", "作業ツリーのスキル")
        _skill(os.path.join(home, ".claude", "skills"), "my-step", "true", "ホームの同名")
        _skill(os.path.join(home, ".claude", "skills"), "other", "true")
        env = {"HOME": home}
        r = run_task(repo, "config", "--schema", env=env)
        check("引数なしは終了コード0で1行", r.returncode == 0 and len(r.stdout.splitlines()) == 1, r.stdout + r.stderr)
        schema = json.loads(r.stdout)
        steps = schema["keys"][0]
        values = [v["value"] for v in steps["values"]]
        check("schema 2 は steps の1欄だけ", schema["schema"] == 2 and [k["key"] for k in schema["keys"]] == ["steps"]
              and steps["kind"] == "steps" and steps["label"] and steps["description"], repr(schema))
        check("作業ツリーとホームの印つきスキルが候補になり、全名が同じものは先の1つ",
              values[:2] == ["my-step", "other"]
              and steps["values"][0]["description"] == "作業ツリーのスキル 続き" and values.count("my-step") == 1, repr(values))
        check("default は全名の配列で、候補の中の名前だけ",
              isinstance(steps["default"], list) and all(d in values for d in steps["default"]), repr(steps))
        for version in ("1", "2", "3"):
            r = run_task(repo, "config", "--schema", "--version", version, env=env)
            check(f"--version {version} は知らない引数で終了コード2、stdout は空",
                  r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)
        r = run_task(repo, "config", "--version", "2", env=env)
        check("--schema の無い --version も終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = run_task(tmp, "config", "--schema", env=env)
        check("git の外でも打てる（ホームの候補だけ）",
              r.returncode == 0 and [v["value"] for v in json.loads(r.stdout)["keys"][0]["values"]][:1] == ["my-step"],
              r.stdout + r.stderr)


def test_config_set() -> None:
    say("task.py config set: 検査してから .tw/config.toml に書き、ほかの行・並び・コメントを変えない")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "set")
        path = os.path.join(repo, ".tw", "config.toml")

        def read_config_file() -> str:
            with open(path, encoding="utf-8") as f:
                return f.read()

        original = '# 設定\nverify = "なし"  # 検証\n\nbranch = "切らない"\n'
        write(path, original)
        before = read_config_file()
        for args in (
            ("verify", "x"),
            ("flow", "main"),
            ("flow", "delegate"),
            ("parts.acceptor", "my-plugin:acceptor"),
            ("parts.retrospect", "なし"),
            ("parts.foo", "x"),
        ):
            r = run_task(repo, "config", "set", *args)
            check(f"set {' '.join(args)} は終了コード2で理由を stderr に出し、ファイルを変えない",
                  r.returncode == 2 and r.stdout == "" and r.stderr.strip() != "" and read_config_file() == before,
                  r.stdout + r.stderr)
        r = run_task(repo, "config", "set", "steps")
        check("値の無い set は終了コード2", r.returncode == 2, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "no-config", with_config=False)
        r = run_task(repo, "config", "set", "steps", '["a"]')
        check("設定が無ければ MISSING（終了コード6）", r.returncode == 6 and r.stdout.startswith("MISSING"), r.stdout + r.stderr)


def test_config_steps() -> None:
    say("task.py config set steps: 1行の JSON の配列を検査して steps = [...] に書き、tw config が同じ形で出す")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "steps")
        home = os.path.join(tmp, "home")
        _skill(os.path.join(home, ".claude", "skills"), "step-a", "true")
        _skill(os.path.join(home, ".claude", "skills"), "step-b", "true")
        _skill(os.path.join(home, ".claude", "skills"), "step-plain", None)
        env = {"HOME": home}
        path = os.path.join(repo, ".tw", "config.toml")

        def read_config_file() -> str:
            with open(path, encoding="utf-8") as f:
                return f.read()

        original = '# 設定\nverify = "なし"  # 検証\n\nbranch = "切らない"\n'
        write(path, original)
        lines = run_task(repo, "config", env=env).stdout.splitlines()
        check("書いていなければ steps の行は default で、値は JSON の配列",
              any(l.startswith("steps\t[") and l.endswith("\tdefault") for l in lines), repr(lines))
        r = run_task(repo, "config", "set", "steps", '["step-b","step-a"]', env=env)
        check("候補の並びを書くと SET と同じ形の値を返す",
              r.returncode == 0 and r.stdout == 'SET\tsteps\t["step-b","step-a"]\n', r.stdout + r.stderr)
        check("最上位の最後のキーの次に steps = [...] の1行が増え、ほかの行はそのまま",
              read_config_file() == original.replace('branch = "切らない"\n', 'branch = "切らない"\nsteps = ["step-b", "step-a"]\n'),
              read_config_file())
        lines = run_task(repo, "config", env=env).stdout.splitlines()
        check("tw config が書いた並びを config で出す", 'steps\t["step-b","step-a"]\tconfig' in lines, repr(lines))
        write(path, read_config_file().replace('["step-b", "step-a"]', '["step-b", "step-a"]  # 順'))
        r = run_task(repo, "config", "set", "steps", '["step-a"]', env=env)
        check("同じキーは値だけ変わり、行末のコメントが残る",
              r.returncode == 0 and 'steps = ["step-a"]  # 順\n' in read_config_file()
              and read_config_file().count("steps =") == 1, read_config_file())

        write(path, read_config_file().replace('["step-a"]', '["gone", "step-a"]'))
        r = run_task(repo, "config", "set", "steps", '["step-a", "gone"]', env=env)
        check("候補に無くても今の値にある名前は並べ替えて書ける",
              r.returncode == 0 and 'steps = ["step-a", "gone"]' in read_config_file(), r.stdout + r.stderr + read_config_file())

        before = read_config_file()
        for label, arg in (
            ("空", "[]"),
            ("重複", '["step-a","step-a"]'),
            ("候補にも今の値にも無い名前", '["step-a","nope"]'),
            ("印の無いスキル", '["step-plain"]'),
            ("JSON でない", "step-a"),
            ("配列でない", '"step-a"'),
            ("文字列でない要素", "[1]"),
            ("空の名前", '[""]'),
        ):
            r = run_task(repo, "config", "set", "steps", arg, env=env)
            check(f"{label}は終了コード2で理由を stderr に出し、ファイルを変えない",
                  r.returncode == 2 and r.stdout == "" and r.stderr.strip() != "" and read_config_file() == before,
                  r.stdout + r.stderr)

        write(path, 'verify = "なし"\nsteps = []\n')
        r = run_task(repo, "config", env=env)
        check("ファイルに空の steps があれば INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID\t"), r.stdout + r.stderr)
        write(path, 'verify = "なし"\n[parts]\nsteps = ["a"]\n')
        r = run_task(repo, "config", env=env)
        check("[parts] の中の steps は INVALID", r.returncode == 3, r.stdout + r.stderr)


def test_open_stages() -> None:
    say("metrics.open_stages: start だけの段を返し、対の揃った段と前の claim の run は数えない")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = lambda sec, kind, **f: metrics.Event(t0 + timedelta(seconds=sec), kind, "T-100", {"event": kind, **f})
    events = [
        row(0, "claim"),
        row(1, "lap", stage="review", edge="start"),
        row(2, "claim"),
        row(3, "lap", stage="delegate", edge="start"),
        row(4, "lap", stage="accept", edge="start"),
        row(5, "lap", stage="accept", edge="end"),
        row(6, "lap", stage="delegate", edge="start"),
        row(7, "lap", stage="sync"),
    ]
    check("delegate だけが1回返る", metrics.open_stages(events, "T-100") == ["delegate"], repr(metrics.open_stages(events, "T-100")))
    check("別のタスクは空", metrics.open_stages(events, "T-999") == [])


def test_task_stage_spans() -> None:
    say("metrics.task_stage_spans: ship 前でも最後の claim 以降の区間・終わりの無い段・着手と最後の出来事の時刻を返す")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    at = lambda sec: t0 + timedelta(seconds=sec)
    row = lambda sec, kind, **f: metrics.Event(at(sec), kind, "T-100", {"event": kind, **f})
    events = [
        row(0, "claim"),
        row(1, "lap", stage="review", edge="start"),
        row(2, "claim"),
        row(3, "lap", stage="delegate", edge="start"),
        row(4, "lap", stage="accept", edge="start"),
        row(5, "lap", stage="accept", edge="end"),
        row(6, "lap", stage="delegate", edge="end"),
        row(7, "lap", stage="sync", edge="start"),
        row(8, "done"),
    ]
    got = metrics.task_stage_spans(events, "T-100")
    check("claim の無いタスクは None", metrics.task_stage_spans(events, "T-999") is None)
    check("最後の claim 以降の区間（edge の対と claim から次の出来事まで）が返る",
          got is not None and sorted(got[0], key=lambda s: (s[1], s[0])) == [
              ("計画", at(2), at(3)), ("委譲", at(3), at(6)), ("受け入れ", at(4), at(5))
          ], repr(got))
    check("終わりの無い段名・着手の時刻・最後の出来事の時刻が返る",
          got is not None and got[1:] == (["反映"], at(2), at(8)), repr(got))


TESTS = (
    test_open_stages,
    test_task_stage_spans,
    test_flow_records_and_metrics,
    test_config_file_agents_md_and_conflict,
    test_retrospect_due,
    test_config_doctor,
    test_config_doctor_prerequisites,
    test_bin_tw_python_floor,
    test_config_command,
    test_config_schema,
    test_step_candidates,
    test_default_steps_order,
    test_config_schema_version,
    test_config_set,
    test_config_steps,
    test_metrics_stages,
    test_metrics_stages_parallel_steps,
    test_metrics_stages_edge,
    test_lap_edge,
    test_relay,
    test_record_normalizes_task_id,
)


if __name__ == "__main__":
    refuse_direct_run()
