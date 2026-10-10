from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from selftest_support import check, git, say, weight, write  # noqa: E402
import layout  # noqa: E402
import ledger  # noqa: E402
import metrics  # noqa: E402
import taskfile  # noqa: E402
from selftest_fixtures import BODY, PLANNED_BODY, TASK_REL, commit_task, flow_rows, make_repo, run_task, task_rel  # noqa: E402


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
    delegated = metrics.stage_durations(events, 1)[("委譲", "opus", "normal")]
    check("委譲の時間の和は lap delegate から受け入れまでの壁時計から検証の秒を引いたもので、段1（190秒）と段2（200秒）の和を超えない",
          sum(delegated) == 410 - 10 - 50, repr(delegated))


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


def test_prune() -> None:
    say("task.py prune: 振り返り済みの done/dropped だけを git rm して stage する")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp, branch="切らない", store=layout.STORE_FILES)
        reviewed_body =BODY + "\n## 結果\n\n- 検証: x\n- 振り返り: 兆候なし\n"
        plain_body = BODY + "\n## 結果\n\n- 検証: x\n"
        commit_task(main_path, taskfile.Task("T-102", "振り返りの印が無い dropped", "dropped", "sonnet", "Y", (), plain_body))
        commit_task(main_path, taskfile.Task("T-101", "1件ごとに振り返り済み", "done", "sonnet", "Y", (), reviewed_body))
        commit_task(main_path, taskfile.Task("T-103", "振り返りの印が無い done", "done", "sonnet", "Y", (), plain_body))
        commit_task(main_path, taskfile.Task("T-104", "T-101 を待つ", "todo", "sonnet", "Y", ("T-101",), BODY))
        commit_task(main_path, taskfile.Task("T-105", "印が立っている", "done", "sonnet", "Y", (), reviewed_body))
        ledger.try_claim(ledger.ledger_root(cwd=main_path), "T-105", main_path, "main")

        r = run_task(main_path, "prune")
        check("既定のしきい値（10件）に届かなければ NOTHING", r.returncode == 0 and r.stdout.startswith("NOTHING\t"), r.stdout + r.stderr)
        check("しきい値未満では消さない", os.path.exists(os.path.join(main_path, task_rel(main_path), "T-101.md")))
        r = run_task(main_path, "prune", "--min", "2", "--dry-run")
        check("--min 2 でも1件なら --dry-run も NOTHING", r.returncode == 0 and r.stdout.startswith("NOTHING\t"), r.stdout + r.stderr)

        r = run_task(main_path, "prune", "--min", "1", "--dry-run")
        check("--dry-run は PLAN で1件", r.returncode == 0 and r.stdout.splitlines()[-1] == "PLAN\t1", r.stdout + r.stderr)
        check(
            "対象と理由（振り返りの印が無い T-102・T-103 は対象にならない）",
            r.stdout.splitlines()[:1] == ["PRUNE\tT-101\treviewed"],
            r.stdout,
        )
        check("--dry-run は消さない", os.path.exists(os.path.join(main_path, task_rel(main_path), "T-101.md")))

        write(os.path.join(main_path, "scratch.txt"), "x\n")
        r = run_task(main_path, "prune", "--min", "1")
        check("汚れていれば DIRTY(4)", r.returncode == 4 and r.stdout.strip() == "DIRTY", r.stdout + r.stderr)
        r = run_task(main_path, "prune", "--min", "1", "--dry-run")
        check("--dry-run は汚れていても打てる", r.returncode == 0, r.stdout + r.stderr)
        os.remove(os.path.join(main_path, "scratch.txt"))

        r = run_task(main_path, "prune", "--min", "1")
        check("PRUNED で1件", r.returncode == 0 and r.stdout.splitlines()[-1] == "PRUNED\t1", r.stdout + r.stderr)
        staged = git(main_path, "diff", "--cached", "--name-status").stdout.split()
        check(
            "1件の削除だけが stage される（振り返りの印が無いものは残る）",
            staged == ["D", f"{TASK_REL}/T-101.md"],
            str(staged),
        )
        git(main_path, "commit", "-q", "-m", "振り返り済みのタスクファイルを消す（1件）")
        status = run_task(main_path, "status").stdout
        check("消した依存は解決済みのまま", any(l.startswith("T-104\ttodo") and "\tREADY\t" in l for l in status.splitlines()), status)
        check("status --check が通る", run_task(main_path, "status", "--check").returncode == 0)
        r = run_task(main_path, "prune", "--min", "1")
        check("2回目は NOTHING", r.returncode == 0 and r.stdout.startswith("NOTHING"), r.stdout + r.stderr)


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
              r.returncode == 0 and "OLD_LAYOUT" not in r.stdout and "INVALID" not in r.stdout, r.stdout + r.stderr)
        r = run_task(main_path, "config")
        check("config の1行目は .tw/config.toml で、節の値は混ざらない",
              r.stdout.splitlines()[:1] == ["CONFIG\t.tw/config.toml"] and "verify\tなし\tconfig" in r.stdout.splitlines(),
              r.stdout + r.stderr)


def _make_config_doctor_repo(tmp: str, name: str, old_layout: bool = False) -> str:
    """`task config-doctor`・`task config` のフィクスチャ用の最小リポジトリ。
    既定は `.tw/config.toml` の配置で、`old_layout` なら旧配置（`develop/direction.md` と CLAUDE.md の旧い節。`.tw/config.toml` 無し）。"""
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    write(
        os.path.join(repo, "develop" if old_layout else ".tw", "direction.md"),
        "# 未対応の指示メモ\n\n## ユーザーから\n\n## エージェントのドラフト\n",
    )
    write(os.path.join(repo, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
    if old_layout:
        write(
            os.path.join(repo, "CLAUDE.md"),
            "# x\n\n## タスク運用\n\n- 検証コマンド: なし\n- 整形コマンド: なし\n- ブランチ: 既定\n",
        )
    else:
        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "なし"\nbranch = "既定"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def test_config_doctor() -> None:
    say("task.py config-doctor（T-021: 設定と形式のズレの点検。読むだけ）")

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "ok")
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "OKのリポジトリは終了コード0で検査がOK、old_section の行は無い",
            r.returncode == 0
            and any(l.startswith("base_branch\tOK\tmain\t順3") for l in lines)
            and any(l.startswith("config\tOK\t.tw/config.toml") for l in lines)
            and not any(l.startswith("old_section") for l in lines)
            and "legacy\tOK" in lines,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        # 旧形式の残り（develop/tasks.json・develop/progress.md）だけがあるケース。
        # 他の3検査はOKでも、残りがあれば全体は「直すものがある」＝終了コード1。
        repo = _make_config_doctor_repo(tmp, "leftover")
        write(os.path.join(repo, "develop", "tasks.json"), "[]\n")
        write(os.path.join(repo, "develop", "progress.md"), "## 未解決\n\n- x\n")
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "旧形式の残りがあれば終了コード1で案内する",
            r.returncode == 1
            and any(
                l.startswith("legacy\tFOUND\t") and "develop/tasks.json" in l and "develop/progress.md" in l
                and l.endswith("tw migrate --dry-run")
                for l in lines
            )
            and any(l.startswith("base_branch\tOK") for l in lines)
            and any(l.startswith("config\tOK") for l in lines),
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        # 旧配置（.tw/config.toml 無し）: 節は読まず、config は MISSING で直す案内になる。
        repo = _make_config_doctor_repo(tmp, "old", old_layout=True)
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "旧配置は終了コード1で config が MISSING、old_section の行は出さない",
            r.returncode == 1
            and any(l.startswith("config\tMISSING\t.tw/config.toml") for l in lines)
            and not any(l.startswith("old_section") for l in lines)
            and "legacy\tOK" in lines,
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


def test_config_command() -> None:
    say("task.py config: 解けた設定を出す")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "legacy", old_layout=True)
        r = run_task(repo, "config")
        lines = r.stdout.splitlines()
        check(
            "旧配置だけなら config も OLD_LAYOUT で止まる（終了コード5）",
            r.returncode == 5 and lines == ["OLD_LAYOUT\ttw migrate-layout --dry-run"],
            r.stdout + r.stderr,
        )
        r = run_task(repo, "status")
        check("旧配置だけなら status も OLD_LAYOUT で止まる（終了コード5）",
              r.returncode == 5 and r.stdout == "OLD_LAYOUT\ttw migrate-layout --dry-run\n", r.stdout + r.stderr)

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
        r = run_task(repo, "status")
        check("config.toml があれば status は old_layout の行を出さない",
              r.returncode == 0 and "old_layout" not in r.stdout, r.stdout + r.stderr)

        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "x"\nbranch = 1\n')
        r = run_task(repo, "config")
        check("読めなければ INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID\t.tw/config.toml:2:"),
              r.stdout + r.stderr)


TESTS = (
    test_flow_records_and_metrics,
    test_prune,
    test_config_file_agents_md_and_conflict,
    test_retrospect_due,
    test_config_doctor,
    test_config_command,
    test_metrics_stages,
    test_metrics_stages_parallel_steps,
)
