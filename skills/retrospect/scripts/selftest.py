#!/usr/bin/env python3
"""`weekly.py`（横断の振り返りの材料）と `material.py` のタスクの材料・`--signals` の `長く待った呼び出し`・`--gate` の判定の自己テスト。

使い方: python3 selftest.py

標準ライブラリと `bd`（タスクの材料を Beads から読む）で動く。`bd` は一時の HOME で打つ。落ちたら非0で終わる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# 足場は共有の git dir の台帳に記録を置くので、利用者の値で置き場を移さない。
os.environ.pop("TW_STATE_DIR", None)

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def write(path: str, body: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def weekly(*args: str) -> subprocess.CompletedProcess:
    root = args[0]
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "weekly.py"), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": os.path.dirname(os.path.realpath(root))},
    )


def ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def flow_row(at: str, event: str, task: str, **fields: object) -> str:
    return json.dumps({"t": at, "event": event, "task": task, "difficulty": "opus", **fields}) + "\n"


def git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def lines_of(section_name: str, out: str) -> list[str]:
    body = out.split(f"===== {section_name} =====", 1)[-1]
    return [l for l in body.split("=====", 1)[0].splitlines() if l]


def test_weekly() -> None:
    print("weekly.py")
    today = date.today()
    with tempfile.TemporaryDirectory() as d:
        git(d, "init", "-q", "-b", "main")
        git(d, "config", "user.email", "test@example.com")
        git(d, "config", "user.name", "test")
        write(os.path.join(d, "hook.sh"), 'echo "直近 30 日の拒否の回数"\necho "deny-a: 0"\necho "deny-b: 12"\n')
        write(os.path.join(d, "CLAUDE.md"), "# x\n")
        write(os.path.join(d, ".tw", "config.toml"), 'verify = "なし"\nhook_tally = "sh hook.sh"  # 直近30日\n')
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", "やり方を変える")
        write(
            os.path.join(d, ".git", "task-workflow", "flow", "2026-10.jsonl"),
            flow_row(ago(20), "done", "GH-10", dropped=False, reflection="some")
            + flow_row(ago(16), "claim", "GH-20")
            + flow_row(ago(15.5), "lap", "GH-20", stage="delegate")
            + flow_row(ago(15), "done", "GH-20", dropped=False, reflection="none")
            + flow_row(ago(10), "ship", "GH-20", result="SHIPPED")
            + flow_row(ago(9), "ship", "GH-21", result="SHIPPED")
            + flow_row(ago(2), "done", "GH-30", dropped=False, reflection="some"),
        )

        r = weekly(d)
        out = r.stdout
        print("  --- weekly.py の出力（材料が全部ある） ---")
        for line in out.splitlines():
            print(f"  | {line}")
        check("材料が全部あれば終了コード0で traceback が無い", r.returncode == 0 and "Traceback" not in r.stderr, r.stderr)
        check("記録が無ければ期間は7日で last=-", lines_of("期間", out) == [f"PERIOD\t7d\t{(today - timedelta(days=7)).isoformat()}\t{today.isoformat()}\tlast=-"], out)
        headings = [l for l in out.splitlines() if l.startswith("===== ")]
        check(
            "節は期間・流れの数・段の所要時間・規則の棚卸し・ドラフトの行方の5つだけ",
            headings == ["===== 期間 =====", "===== 流れの数 =====", "===== 段の所要時間 =====", "===== 規則の棚卸し =====",
                         "===== ドラフトの行方（直近28日） ====="],
            "\n".join(headings),
        )
        fate = lines_of("ドラフトの行方（直近28日）", out)
        check("Beads の無いリポジトリではドラフトの行方は - の1行", len(fate) == 1 and fate[0].startswith("-\t"), out)
        flow = lines_of("流れの数", out)
        check("悪くなった数に WORSE", "WORSE\tshipped\t0\t2" in flow, "\n".join(flow))
        check("WORSE があれば期間内のやり方の変更を CHANGE で並べる", any(l.startswith("CHANGE\tproject\t") and l.endswith("\tやり方を変える") for l in flow), "\n".join(flow))
        check("設定の規則の発火の集計のコマンドの出力をそのまま出す", lines_of("規則の棚卸し", out) == ["直近 30 日の拒否の回数", "deny-a: 0", "deny-b: 12"], out)

        check("期間内に送り出した段が無ければ段の所要時間は EMPTY", lines_of("段の所要時間", out) == ["EMPTY"], out)
        r = weekly(d, "--days", "30")
        check("--days で期間を変えられる", lines_of("期間", r.stdout)[0].startswith("PERIOD\t30d\t"), r.stdout)
        check(
            "段の所要時間に STAGE の行が出る",
            lines_of("段の所要時間", r.stdout)
            == [
                "STAGE\t計画\topus\tnormal\t1\t43200\t43200",
                "STAGE\t委譲\topus\tnormal\t1\t43200\t43200",
                "STAGE\t送り出し\topus\tnormal\t1\t432000\t432000",
            ],
            r.stdout,
        )
        r = weekly(d, "--days", "0")
        check("--days が1未満なら終了コード2", r.returncode == 2, r.stdout + r.stderr)

        write(os.path.join(d, ".tw", "config.toml"), 'verify = "なし"\nhook_tally = "exit 3"\n')
        r = weekly(d)
        check("config.toml の hook_tally が落ちたら - と終了コード", lines_of("規則の棚卸し", r.stdout) == ["-\t終了コード 3"] and r.returncode == 0, r.stdout)

        write(os.path.join(d, ".tw", "config.toml"), 'verify = "なし"\n')
        r = weekly(d)
        check(
            "規則の発火の集計の行が無ければ - と理由で、traceback を出さない",
            r.returncode == 0
            and "Traceback" not in r.stderr
            and lines_of("規則の棚卸し", r.stdout) == ["-\t設定に hook_tally が無い（この観点は飛ばす）"],
            r.stdout + r.stderr,
        )

        write(os.path.join(d, "docs", "history", "retrospect.md"), f"# 横断の振り返りの記録\n\n## {(today - timedelta(days=10)).isoformat()}（x〜y）\n\n## 2000-01-01（x〜y）\n")
        r = weekly(d)
        check("最後の記録が10日前なら期間は10日", lines_of("期間", r.stdout)[0].startswith("PERIOD\t10d\t") and r.stdout.count(f"last={(today - timedelta(days=10)).isoformat()}") == 1, r.stdout)
        write(os.path.join(d, "docs", "history", "retrospect.md"), "# 横断の振り返りの記録\n\n## 2000-01-01（x〜y）\n")
        r = weekly(d)
        check("期間は28日で打ち切る", lines_of("期間", r.stdout)[0].startswith("PERIOD\t28d\t"), r.stdout)

    with tempfile.TemporaryDirectory() as d:
        r = weekly(d)
        print("  --- weekly.py の出力（記録の無い初回・材料なし） ---")
        for line in r.stdout.splitlines():
            print(f"  | {line}")
        check("git でも台帳でもない空のディレクトリでも終了コード0で traceback が無い", r.returncode == 0 and "Traceback" not in r.stderr, r.stderr)
        check("記録の無い初回は期間7日・last=-", lines_of("期間", r.stdout)[0].startswith("PERIOD\t7d\t") and r.stdout.count("last=-") == 1, r.stdout)
        check(
            "どの節も欠席の理由を出す",
            lines_of("流れの数", r.stdout) == ["-\t台帳が読めない（git のリポジトリでない）"]
            and lines_of("段の所要時間", r.stdout) == ["-\t台帳が読めない（git のリポジトリでない）"]
            and lines_of("規則の棚卸し", r.stdout)[0].startswith("-\t"),
            r.stdout,
        )


def test_slow_calls() -> None:
    print("material.py --signals の長く待った呼び出し")

    def use(at: str, call_id: str, name: str, **inp: object) -> dict:
        block = {"type": "tool_use", "id": call_id, "name": name, "input": inp}
        return {"type": "assistant", "timestamp": at, "message": {"content": [block]}}

    def result(at: str, call_id: str) -> dict:
        block = {"type": "tool_result", "tool_use_id": call_id}
        return {"type": "user", "timestamp": at, "message": {"content": [block]}}

    rows = [
        {"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": "GH-1 の作り物"}},
        use("2026-10-01T00:00:01Z", "a", "Bash", command="SECRET-COMMAND-BODY", description="検証を流す"),
        result("2026-10-01T00:05:01Z", "a"),
        use("2026-10-01T00:05:02Z", "b", "Read"),
        result("2026-10-01T00:05:04Z", "b"),
        use("2026-10-01T00:05:05Z", "c", "Bash", command="x", description="送る"),
        result("2026-10-01T00:06:05Z", "c"),
        use("2026-10-01T00:06:06Z", "d", "Edit", file_path="/x"),
        result("2026-10-01T00:06:26Z", "d"),
        use("2026-10-01T00:06:27Z", "e", "Grep"),
    ]
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "repo")
        slug = "".join(c if c.isalnum() else "-" for c in os.path.abspath(root))
        sub = os.path.join(d, "cfg", "projects", slug, "s1", "subagents")
        os.makedirs(sub)
        os.makedirs(root)
        write(os.path.join(sub, "a.jsonl"), "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "material.py"), root, "GH-1", "--signals"],
            capture_output=True,
            text=True,
            env={**os.environ, "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg")},
        )
        line = [l for l in r.stdout.splitlines() if l.startswith("長く待った呼び出し\t")]
        check(
            "長い順に3件まで、道具名・Bash の説明・秒数で並べ、結果の来ない呼び出しは数えない",
            line == ["長く待った呼び出し\tBash 検証を流す 300秒, Bash 送る 60秒, Edit 20秒"],
            r.stdout + r.stderr,
        )
        check("コマンドの本文を出さない", "SECRET-COMMAND-BODY" not in r.stdout, r.stdout)


def test_gate() -> None:
    print("material.py --gate の判定")
    quiet = {"--friction": "none", "--reverify": "0", "--fixes": "0", "--human": "0", "--review": "skip"}

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "repo")
        os.makedirs(root)
        env = {**os.environ, "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg")}

        def gate(task_id: str, answers: dict[str, str]) -> subprocess.CompletedProcess:
            args = [a for kv in answers.items() for a in kv]
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "material.py"), root, task_id, "--gate", *args],
                capture_output=True,
                text=True,
                env=env,
            )

        r = gate("GH-1", quiet)
        check("5つとも当たらずトランスクリプトも無ければ QUIET の1行だけ", r.returncode == 0 and r.stdout == "QUIET\n", r.stdout + r.stderr)
        r = gate("GH-1", {**quiet, "--review": "clean"})
        check("レビューが指摘なしでも QUIET", r.stdout == "QUIET\n", r.stdout + r.stderr)

        for opt, value, expected in (
            ("--friction", "some", "SIGNAL\tfriction log\tsome"),
            ("--friction", "missing", "SIGNAL\tfriction log\tmissing"),
            ("--reverify", "1", "SIGNAL\t検証の打ち直し\t1回"),
            ("--fixes", "2", "SIGNAL\t受け入れでの直し\t2回"),
            ("--human", "1", "SIGNAL\t人の差し戻し\t1回"),
            ("--review", "found", "SIGNAL\t受け入れでの直し\tレビューの指摘あり"),
        ):
            r = gate("GH-1", {**quiet, opt: value})
            lines = r.stdout.splitlines()
            check(f"{opt} {value} が当たれば SIGNAL を出し QUIET を出さない", expected in lines and "QUIET" not in lines, r.stdout + r.stderr)

        def error_call(i: int) -> list[dict]:
            at = f"2026-10-01T00:00:{i:02d}Z"
            return [
                {"type": "assistant", "timestamp": at, "message": {"content": [{"type": "tool_use", "id": f"e{i}", "name": "Read", "input": {}}]}},
                {"type": "user", "timestamp": at, "message": {"content": [{"type": "tool_result", "tool_use_id": f"e{i}", "is_error": True}]}},
            ]

        rows = [{"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": "GH-2 の作り物"}}]
        for i in range(1, 4):
            rows += error_call(i)
        slug = "".join(c if c.isalnum() else "-" for c in os.path.abspath(root))
        write(
            os.path.join(d, "cfg", "projects", slug, "s1", "subagents", "a.jsonl"),
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
        )
        r = gate("GH-2", quiet)
        lines = r.stdout.splitlines()
        check(
            "5つが当たらなくてもトランスクリプトのエラー3件で SIGNAL と手数の節を出す",
            "SIGNAL\tツールのエラー\t3件" in lines and "QUIET" not in lines and "===== 手数（トランスクリプトから取った数だけ） =====" in lines,
            r.stdout + r.stderr,
        )

        def bash_call(i: int, command: str) -> list[dict]:
            at = f"2026-10-01T00:01:{i:02d}Z"
            return [
                {"type": "assistant", "timestamp": at, "message": {"content": [{"type": "tool_use", "id": f"b{i}", "name": "Bash", "input": {"command": command}}]}},
                {"type": "user", "timestamp": at, "message": {"content": [{"type": "tool_result", "tool_use_id": f"b{i}"}]}},
            ]

        rows = [{"type": "user", "timestamp": "2026-10-01T00:01:00Z", "message": {"content": "GH-3 の作り物"}}]
        for i, command in enumerate(("./check.sh", "./check.sh --full", "./check.sh --full"), 1):
            rows += bash_call(i, command)
        write(
            os.path.join(d, "cfg", "projects", slug, "s2", "subagents", "b.jsonl"),
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows),
        )
        r = gate("GH-3", quiet)
        check("設定に検証コマンドが無ければ、それを3回打っても QUIET", r.stdout == "QUIET\n", r.stdout + r.stderr)
        write(os.path.join(root, ".tw", "config.toml"), 'verify = "./check.sh"\n')
        r = gate("GH-3", quiet)
        check(
            "設定の検証コマンドを引数違いも合わせて3回打てば SIGNAL",
            "SIGNAL\t検証の打ち直し\t./check.sh×3" in r.stdout.splitlines(),
            r.stdout + r.stderr,
        )

        r = gate("GH-1", {k: v for k, v in quiet.items() if k != "--human"})
        check("5つのうち1つでも欠ければ終了コード2", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)
        r = gate("GH-1", {**quiet, "--review": "maybe"})
        check("値が違えば終了コード2", r.returncode == 2, r.stdout + r.stderr)


def test_stage_signals() -> None:
    print("material.py の段の所要時間（SIGNAL と節）")
    quiet = {"--friction": "none", "--reverify": "0", "--fixes": "0", "--human": "0", "--review": "skip"}
    base = datetime.now(timezone.utc) - timedelta(hours=1)

    def at(sec: int) -> str:
        return (base + timedelta(seconds=sec)).isoformat(timespec="seconds")

    def lap(task: str, sec: int, stage: str, edge: str) -> str:
        return flow_row(at(sec), "lap", task, stage=stage, edge=edge)

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "repo")
        os.makedirs(root)
        git(root, "init", "-q", "-b", "main")
        env = {**os.environ, "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg")}
        write(
            os.path.join(root, ".git", "task-workflow", "flow", "2026-10.jsonl"),
            # 半分ちょうど（委譲 100/200）
            flow_row(at(0), "claim", "GH-51") + lap("GH-51", 10, "delegate", "start") + lap("GH-51", 110, "delegate", "end")
            + lap("GH-51", 110, "accept", "start") + lap("GH-51", 190, "accept", "end") + flow_row(at(200), "done", "GH-51")
            # end の無い start
            + flow_row(at(0), "claim", "GH-52") + lap("GH-52", 10, "delegate", "start") + lap("GH-52", 100, "delegate", "end")
            + lap("GH-52", 110, "review", "start") + flow_row(at(200), "done", "GH-52")
            # 段のあいだの空き（45%）
            + flow_row(at(0), "claim", "GH-53") + lap("GH-53", 10, "delegate", "start") + lap("GH-53", 60, "delegate", "end")
            + lap("GH-53", 100, "accept", "start") + lap("GH-53", 150, "accept", "end") + flow_row(at(200), "done", "GH-53")
            # どれにも当たらない
            + flow_row(at(0), "claim", "GH-54") + lap("GH-54", 10, "delegate", "start") + lap("GH-54", 70, "delegate", "end")
            + lap("GH-54", 70, "accept", "start") + lap("GH-54", 130, "accept", "end")
            + lap("GH-54", 130, "retro", "start") + lap("GH-54", 190, "retro", "end") + flow_row(at(200), "done", "GH-54"),
        )

        def material(task_id: str, *args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "material.py"), root, task_id, *args],
                capture_output=True, text=True, env=env,
            )

        def gate(task_id: str) -> subprocess.CompletedProcess:
            return material(task_id, "--gate", *[a for kv in quiet.items() for a in kv])

        title = "===== 段の所要時間（flow/ から取った秒） ====="
        r = gate("GH-50")
        check("flow にそのタスクの claim が無ければ QUIET", r.stdout == "QUIET\n", r.stdout + r.stderr)
        r = material("GH-50")
        check("claim が無ければ全体の出力で段の所要時間の節は EMPTY", lines_of("段の所要時間（flow/ から取った秒）", r.stdout) == ["EMPTY"], r.stdout + r.stderr)

        r = gate("GH-51")
        lines = r.stdout.splitlines()
        check("段が半分ちょうどなら SIGNAL を出す", "SIGNAL\t段の所要時間\t委譲 50%" in lines and "QUIET" not in lines, r.stdout + r.stderr)
        check("受け入れ 40% は SIGNAL にしない", not any(l.startswith("SIGNAL\t段の所要時間\t受け入れ") for l in lines), r.stdout)
        heads = [l for l in lines if l.startswith("===== ")]
        check(
            "当たったときの節の並びは 手数 → 段の所要時間 → ドラフトの行方",
            len(heads) == 3 and heads[0].startswith("===== 手数") and heads[1] == title and heads[2].startswith("===== ドラフトの行方"),
            "\n".join(heads),
        )
        stages = lines_of("段の所要時間（flow/ から取った秒）", r.stdout)
        check("節に TOTAL・STAGE・GAP の行が出る", stages[0] == "TOTAL\t200" and "STAGE\t委譲\t100\t50%" in stages and "GAP\t10\t5%" in stages, "\n".join(stages))

        r = gate("GH-52")
        lines = r.stdout.splitlines()
        check("end の無い start は OPEN の SIGNAL と節の OPEN の行", "SIGNAL\t段の所要時間\tOPEN レビュー" in lines and "OPEN\tレビュー" in lines, r.stdout + r.stderr)

        r = gate("GH-53")
        check("段のあいだの空きが2割以上なら SIGNAL", "SIGNAL\t段の所要時間\t段の空き 45%" in r.stdout.splitlines(), r.stdout + r.stderr)

        r = gate("GH-54")
        check("どれにも当たらなければ QUIET", r.stdout == "QUIET\n", r.stdout + r.stderr)

        r = material("GH-51", "--signals")
        check("--signals は手数だけで段の所要時間の節を出さない", title not in r.stdout, r.stdout)


def test_task_material() -> None:
    print("material.py のタスクと登録からの差分（Beads の課題・Beads に無い古いタスク）")
    if shutil.which("bd") is None:
        check("bd が PATH にある（material.py はタスクを Beads から読む）", False)
        return
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "home")
        write(os.path.join(home, ".config", "bd", "config.yaml"), "metrics:\n    disabled: true\n    notice_shown: true\nno-git-ops: true\n")
        env = {
            **os.environ,
            "HOME": home,
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
            "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg"),
        }
        env.pop("BEADS_ACTOR", None)
        env.pop("GITHUB_TOKEN", None)
        root = os.path.join(d, "repo")
        os.makedirs(root)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "beads.role", "maintainer")

        def bd(*args: str, stdin: str | None = None) -> None:
            subprocess.run(["bd", *args], cwd=root, env=env, input=stdin, check=True, capture_output=True, text=True)

        bd("init", "--stealth", "-p", "t", "--non-interactive", "--skip-hooks", "--quiet")
        bd("create", "--id", "t-001", "--title", "材料", "--body-file", "-", "--metadata", '{"difficulty": "sonnet", "loopable": "Y"}', "--silent",
           stdin="## 目的・背景\n\n登録時の目的\n")
        bd("update", "t-001", "--description", "## 目的・背景\n\n書き直した目的\n")
        write(os.path.join(root, "docs", "history", "tasks.md"), "# 履歴\n\n## T-002 古いもの\n\n古い本文\n")
        write(os.path.join(root, "docs", "history", "progress.md"), "## 2026-01-01\n\n### T-002 古いもの\n\n進めた\n")

        def material(task_id: str, path: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "material.py"), root, task_id],
                capture_output=True, text=True, env={**env, "PATH": path},
            )

        r = material("T-001", env["PATH"])
        task = lines_of("タスク", r.stdout)
        diff = lines_of("登録から完了までの差分（Beads の版）", r.stdout)
        check("Beads の課題を出典つきで出す", r.returncode == 0 and task[:1] == ["出典\tBeads t-001"] and "書き直した目的" in task, r.stdout + r.stderr)
        check("bd history の最初と最後の版の差を出す", "-登録時の目的" in diff and "+書き直した目的" in diff, r.stdout)

        r = material("T-002", env["PATH"])
        check("Beads に無い古いタスクはアーカイブから出す", "古い本文" in lines_of("タスク", r.stdout), r.stdout + r.stderr)
        check("Beads に無い古いタスクは progress の小節を出す",
              "進めた" in lines_of("progress の小節（Beads に無い古いタスク）", r.stdout), r.stdout)

        r = material("T-002", "/usr/bin:/bin")
        check("bd が無くても落ちずにアーカイブから出す", r.returncode == 0 and "古い本文" in lines_of("タスク", r.stdout), r.stdout + r.stderr)


def test_draft_fate() -> None:
    print("draft_fate（ドラフトの行方の表と絞り）")
    sys.path.insert(0, HERE)
    import draft_fate
    from draft_fate import beads

    now = datetime(2026, 10, 12, tzinfo=timezone.utc)

    def issue(status: str, metadata: dict, days: float = 1, labels: tuple[str, ...] = (), description: str = "") -> beads.Issue:
        created = (now - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        return beads.Issue("t-001", "題", status, labels, (), None, None, None,
                           {"metadata": metadata, "created_at": created, "description": description})

    pointed = "## 目的・背景\n\n- 観点: 黄 道案内\n"
    rows = draft_fate.tally([
        issue("closed", {"from_draft": "道案内"}),
        issue("closed", {"from_draft": "道案内"}),
        issue("closed", {"draft": "Y"}, labels=("cancelled",), description=pointed),
        issue("closed", {"from_draft": "道案内"}, labels=("cancelled",)),
        issue("deferred", {"draft": "Y"}, description=pointed),
        issue("open", {"from_draft": "道案内"}),
        issue("closed", {"from_draft": "道案内"}, days=29),
        issue("closed", {"difficulty": "opus"}),
        issue("deferred", {"draft": "Y"}, description="## 目的・背景\n\n直したい改善\n"),
        issue("in_progress", {"from_draft": "機械の検査"}),
    ], now)
    check(
        "観点ごとに 積んだ・採用・却下・未決 を数え、期間の外とドラフトでない課題は数えない",
        [(r.viewpoint, r.stacked, r.adopted, r.dropped, r.pending) for r in rows]
        == [("不明", 1, 0, 0, 1), ("機械の検査", 1, 0, 0, 1), ("道案内", 6, 2, 2, 2)],
        repr(rows),
    )
    for adopted, dropped, expected in ((2, 3, True), (3, 3, False), (0, 2, False)):
        got = draft_fate.Row("道案内", adopted + dropped, adopted, dropped, 0).throttled
        check(f"採用{adopted}・却下{dropped} の絞り中は {expected}", got == expected)

    if shutil.which("bd") is None:
        check("bd が PATH にある（ドラフトの行方は Beads から読む）", False)
        return
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "home")
        write(os.path.join(home, ".config", "bd", "config.yaml"), "metrics:\n    disabled: true\n    notice_shown: true\nno-git-ops: true\n")
        env = {
            **os.environ,
            "HOME": home,
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
            "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg"),
        }
        env.pop("BEADS_ACTOR", None)
        env.pop("GITHUB_TOKEN", None)
        root = os.path.join(d, "repo")
        os.makedirs(root)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "beads.role", "maintainer")

        def bd(*args: str, stdin: str | None = None) -> None:
            subprocess.run(["bd", *args], cwd=root, env=env, input=stdin, check=True, capture_output=True, text=True)

        bd("init", "--stealth", "-p", "t", "--non-interactive", "--skip-hooks", "--quiet")
        for n in range(1, 4):
            bd("create", "--id", f"t-00{n}", "--title", "却下", "--body-file", "-", "--silent",
               "--metadata", '{"difficulty": "sonnet", "loopable": "Y", "draft": "Y"}', stdin=pointed)
            bd("update", f"t-00{n}", "--add-label", "cancelled")
            bd("close", f"t-00{n}")
        bd("create", "--id", "t-004", "--title", "採用", "--body-file", "-", "--silent",
           "--metadata", '{"difficulty": "sonnet", "loopable": "Y", "from_draft": "機械の検査"}', stdin="## 目的・背景\n\nx\n")
        bd("close", "t-004")

        def run(script: str, *args: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, os.path.join(HERE, script), root, *args], capture_output=True, text=True, env=env)

        quiet = ["--friction", "none", "--reverify", "0", "--fixes", "0", "--human", "0", "--review", "skip"]
        r = run("material.py", "T-004", "--gate", *quiet)
        check("QUIET のときはドラフトの行方を出さない", r.stdout == "QUIET\n", r.stdout + r.stderr)
        r = run("material.py", "T-004", "--gate", *["some" if a == "none" else a for a in quiet])
        fate = lines_of("ドラフトの行方（直近28日）", r.stdout)
        check("SIGNAL のときは行方の表を出し、却下3・採用0の観点は絞り中",
              fate[:1] and fate[0].startswith("WINDOW\t28d\t")
              and "FATE\t道案内\t3\t0\t3\t0\t絞り中" in fate and "FATE\t機械の検査\t1\t1\t0\t0\t-" in fate,
              r.stdout + r.stderr)
        r = run("weekly.py")
        check("weekly の行方の節に合計と絞り中の観点の TOTAL の行",
              "TOTAL\t4\t1\t3\t0\t道案内" in lines_of("ドラフトの行方（直近28日）", r.stdout), r.stdout + r.stderr)


def main() -> None:
    test_weekly()
    test_slow_calls()
    test_gate()
    test_stage_signals()
    test_task_material()
    test_draft_fate()
    print()
    if failures:
        print(f"FAILED {len(failures)}件: " + ", ".join(failures))
        raise SystemExit(1)
    print("すべて通った")


if __name__ == "__main__":
    main()
