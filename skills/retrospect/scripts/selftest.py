#!/usr/bin/env python3
"""`weekly.py`（横断の振り返りの材料）と `material.py --signals` の `長く待った呼び出し`・`--gate` の判定の自己テスト。

使い方: python3 selftest.py

標準ライブラリだけで動く。落ちたら非0で終わる。
"""

from __future__ import annotations

import json
import os
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
        write(os.path.join(d, "develop", "direction.md"), "# 未対応の指示メモ\n\n## ユーザーから\n")
        write(
            os.path.join(d, "CLAUDE.md"),
            "# x\n\n## タスク運用\n\n- 検証コマンド: なし\n- 規則の発火の集計: `sh hook.sh`（直近30日）\n",
        )
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
            "節は期間・流れの数・段の所要時間・規則の棚卸しの4つだけ",
            headings == ["===== 期間 =====", "===== 流れの数 =====", "===== 段の所要時間 =====", "===== 規則の棚卸し ====="],
            "\n".join(headings),
        )
        flow = lines_of("流れの数", out)
        check("悪くなった数に WORSE", "WORSE\tshipped\t0\t2" in flow, "\n".join(flow))
        check("WORSE があれば期間内のやり方の変更を CHANGE で並べる", any(l.startswith("CHANGE\tproject\t") and l.endswith("\tやり方を変える") for l in flow), "\n".join(flow))
        check("旧い節の規則の発火の集計のコマンドの出力をそのまま出す", lines_of("規則の棚卸し", out) == ["直近 30 日の拒否の回数", "deny-a: 0", "deny-b: 12"], out)

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


def main() -> None:
    test_weekly()
    test_slow_calls()
    test_gate()
    print()
    if failures:
        print(f"FAILED {len(failures)}件: " + ", ".join(failures))
        raise SystemExit(1)
    print("すべて通った")


if __name__ == "__main__":
    main()
