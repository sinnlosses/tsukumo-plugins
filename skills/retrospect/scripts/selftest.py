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


STARTED = datetime.now(timezone.utc)


def after(days: float, seconds: int) -> str:
    """`days` 日前から `seconds` 秒あとの時刻。呼ぶたびに今を取り直さないので、秒の差が揺れない。"""
    return (STARTED - timedelta(days=days) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


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
            + flow_row(ago(2), "done", "GH-30", dropped=False, reflection="some")
            # 合流・反映の対（GH-40）と、end の無い反映（GH-41）。NOTHING は shipped に数えない
            + flow_row(after(8, 0), "claim", "GH-40", difficulty="sonnet")
            + flow_row(after(8, 10), "lap", "GH-40", difficulty="sonnet", stage="land", edge="start")
            + flow_row(after(8, 70), "lap", "GH-40", difficulty="sonnet", stage="land", edge="end")
            + flow_row(after(8, 80), "lap", "GH-40", difficulty="sonnet", stage="sync", edge="start")
            + flow_row(after(8, 120), "lap", "GH-40", difficulty="sonnet", stage="sync", edge="end")
            + flow_row(after(8, 130), "ship", "GH-40", difficulty="sonnet", result="NOTHING")
            + flow_row(after(8, 0), "claim", "GH-41", difficulty="sonnet")
            + flow_row(after(8, 20), "lap", "GH-41", difficulty="sonnet", stage="sync", edge="start")
            + flow_row(after(8, 50), "ship", "GH-41", difficulty="sonnet", result="NOTHING"),
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
            "節は期間・流れの数・段の所要時間・規則の棚卸し・期日を過ぎた介入・再発しなかった候補・ドラフトの行方の7つだけ",
            headings == ["===== 期間 =====", "===== 流れの数 =====", "===== 段の所要時間 =====", "===== 規則の棚卸し =====",
                         "===== 期日を過ぎた介入（期間の中に期日が来たもの） =====",
                         "===== 再発しなかった候補（28日より前） =====", "===== ドラフトの行方（直近28日） ====="],
            "\n".join(headings),
        )
        effect = lines_of("期日を過ぎた介入（期間の中に期日が来たもの）", out)
        check("Beads の無いリポジトリでは期日を過ぎた介入は - の1行", len(effect) == 1 and effect[0].startswith("-\t"), out)
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
            "段の所要時間に STAGE の行が出て、合流・反映の段と OPEN の行も落とさない",
            lines_of("段の所要時間", r.stdout)
            == [
                "STAGE\t計画\topus\tnormal\t1\t43200\t43200",
                "STAGE\t計画\tsonnet\tnormal\t2\t15\t20",
                "STAGE\t委譲\topus\tnormal\t1\t43200\t43200",
                "STAGE\t合流\tsonnet\tnormal\t1\t60\t60",
                "STAGE\t送り出し\topus\tnormal\t1\t432000\t432000",
                "STAGE\t反映\tsonnet\tnormal\t1\t40\t40",
                "OPEN\t反映\t1",
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


def test_transcript_binding() -> None:
    print("transcript.py の紐付け（台帳の agent の出来事。無い記録だけ先頭のタスクIDの探索）")
    sys.path.insert(0, HERE)
    import transcript

    base = datetime.now(timezone.utc) - timedelta(hours=1)

    def at(sec: int) -> str:
        return (base + timedelta(seconds=sec)).isoformat(timespec="seconds")

    def line(text: str) -> str:
        return json.dumps({"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": text}}, ensure_ascii=False) + "\n"

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "repo")
        os.makedirs(root)
        git(root, "init", "-q", "-b", "main")
        slug = "".join(c if c.isalnum() else "-" for c in os.path.abspath(root))
        sub = os.path.join(d, "cfg", "projects", slug)

        def put(session: str, name: str, text: str) -> str:
            return write(os.path.join(sub, session, "subagents", name), line(text))

        a1 = put("s1", "agent-aaa.jsonl", "GH-2 を先に書いた GH-1 の委譲")
        a2 = put("s1", "agent-bbb.jsonl", "IDを含まない依頼")
        put("s1", "agent-old.jsonl", "GH-4 の古い委譲")
        new = put("s2", "agent-new.jsonl", "GH-4 の新しい委譲")
        legacy = put("s3", "agent-legacy.jsonl", "GH-3 の古い記録")
        write(
            os.path.join(root, ".git", "task-workflow", "flow", "2026-10.jsonl"),
            flow_row(at(0), "claim", "GH-1") + flow_row(at(5), "agent", "GH-1", agent="aaa", session="s1")
            + flow_row(at(0), "claim", "GH-2") + flow_row(at(5), "agent", "GH-2", agent="bbb", session="s1")
            + flow_row(at(0), "claim", "GH-4") + flow_row(at(5), "agent", "GH-4", agent="old", session="s1")
            + flow_row(at(100), "claim", "GH-4") + flow_row(at(105), "agent", "GH-4", agent="new", session="s2")
            + flow_row(at(0), "claim", "GH-5") + flow_row(at(5), "agent", "GH-5", agent="missing", session="s9")
            + flow_row(at(0), "claim", "GH-6") + flow_row(at(5), "agent", "GH-6", agent="aaa", session="../s1")
            + flow_row(at(6), "agent", "GH-6", agent="../agent-aaa", session="s1")
            + flow_row(at(0), "claim", "GH-3"),
        )
        env = {**os.environ, "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg")}
        saved = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = env["CLAUDE_CONFIG_DIR"]
        try:
            found = lambda task: transcript.find_transcripts(root, task)  # noqa: E731
            check("先頭に別のタスクIDがあっても、agent の出来事を持つタスクに紐付き、先頭のIDのタスクには紐付かない",
                  found("GH-1") == [a1] and a1 not in found("GH-2"), repr((found("GH-1"), found("GH-2"))))
            check("先頭にタスクIDの無い transcript も出来事で紐付く", found("GH-2") == [a2], repr(found("GH-2")))
            check("agent の出来事が無いタスクは先頭3行のタスクID探索に戻る", found("GH-3") == [legacy], repr(found("GH-3")))
            check("最後の claim より前の agent の出来事は使わない", found("GH-4") == [new], repr(found("GH-4")))
            check("出来事があってファイルが無いときは文字列探索に戻らず空", found("GH-5") == [], repr(found("GH-5")))
            check("session・agent に ../ を入れた出来事は読み捨てる", found("GH-6") == [], repr(found("GH-6")))
            r = subprocess.run([sys.executable, os.path.join(HERE, "material.py"), root, "GH-1", "--signals"],
                               capture_output=True, text=True, env=env)
            check("material.py --signals の出典に agent-<id>.jsonl が出る", "出典\tagent-aaa.jsonl" in r.stdout, r.stdout + r.stderr)
        finally:
            if saved is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = saved


def test_gate() -> None:
    print("material.py --gate の判定（台帳が読めないときは引数に戻る）")
    quiet = {"--friction": "none", "--human": "0"}

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "repo")
        os.makedirs(root)
        env = {**os.environ, "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg")}
        kinds: set[str] = set()

        def gate(task_id: str, answers: dict[str, str]) -> subprocess.CompletedProcess:
            args = [a for kv in answers.items() for a in kv]
            r = subprocess.run(
                [sys.executable, os.path.join(HERE, "material.py"), root, task_id, "--gate", *args],
                capture_output=True,
                text=True,
                env=env,
            )
            kinds.update(l.split("\t")[1] for l in r.stdout.splitlines() if l.startswith("SIGNAL\t"))
            return r

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

    sys.path.insert(0, HERE)
    import material

    staged = material.stage_signals(material.StageTimes(100, {"委譲": 60}, ["受け入れ"], 0))
    kinds.update(name for name, _ in staged)
    check("判定の口が出す SIGNAL の種類は SIGNAL_KINDS の中だけ",
          len(kinds) == 6 and kinds <= set(material.SIGNAL_KINDS), repr(sorted(kinds)))


def test_hook_denials() -> None:
    print("material.py の手数で、そのトランスクリプトの委譲先（agent_id が一致）を hook が拒んだ回数だけをエラーから引く")
    quiet = ["--friction", "none", "--human", "0"]
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "repo")
        os.makedirs(root)
        git(root, "init", "-q", "-b", "main")
        env = {**os.environ, "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg")}

        def material(task_id: str, *args: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, os.path.join(HERE, "material.py"), root, task_id, *args],
                                  capture_output=True, text=True, env=env)

        slug = "".join(c if c.isalnum() else "-" for c in os.path.abspath(root))

        def transcript_with_errors(task_id: str, session: str, name: str) -> None:
            rows = [{"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": {"content": f"{task_id} の作り物"}}]
            for i in range(1, 4):
                at = f"2026-10-01T00:00:{i:02d}Z"
                rows += [
                    {"type": "assistant", "timestamp": at, "message": {"content": [{"type": "tool_use", "id": f"e{i}", "name": "Bash", "input": {}}]}},
                    {"type": "user", "timestamp": at, "message": {"content": [{"type": "tool_result", "tool_use_id": f"e{i}", "is_error": True}]}},
                ]
            write(os.path.join(d, "cfg", "projects", slug, session, "subagents", name),
                  "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))

        transcript_with_errors("GH-5", "s1", "agent-a1.jsonl")
        transcript_with_errors("GH-6", "s2", "b1.jsonl")

        r = material("GH-5", "--gate", *quiet)
        check("拒否の記録が無ければエラー3件で SIGNAL", "SIGNAL\tツールのエラー\t3件" in r.stdout.splitlines(), r.stdout + r.stderr)

        def denial(actor: str, agent: str | None = None) -> str:
            row = {"at": "2026-10-01T00:00:02+00:00", "hook": "tw-commit-guard", "rule": "SHELL_WRITE", "actor": actor}
            return json.dumps({**row, "agent": agent} if agent else row) + "\n"

        denials = os.path.join(root, ".git", "hook-denials", "2026-10.jsonl")
        write(denials,
              denial("subagent", "a2")  # 同じ時間に拒まれた別の委譲先
              + denial("subagent")  # agent の無い古い記録
              + denial("main")
              + denial("subagent", "b1")  # 名前が agent-<id>.jsonl でないトランスクリプトの委譲先
              + "壊れた行\n")
        r = material("GH-5", "--gate", *quiet)
        check("同じ時間の別の委譲先・agent の無い古い記録・main・壊れた行は引かず、エラー3件の SIGNAL が残る",
              "SIGNAL\tツールのエラー\t3件" in r.stdout.splitlines(), r.stdout + r.stderr)
        r = material("GH-6", "--gate", *quiet)
        check("名前が agent-<id>.jsonl でないトランスクリプトは引かない",
              "SIGNAL\tツールのエラー\t3件" in r.stdout.splitlines(), r.stdout + r.stderr)

        with open(denials, "a", encoding="utf-8") as f:
            f.write(denial("subagent", "a1"))
        r = material("GH-5", "--gate", *quiet)
        check("agent_id が一致する拒否1件を引くと2件で QUIET", r.stdout == "QUIET\n", r.stdout + r.stderr)
        r = material("GH-5", "--signals")
        check("手数の行に引いた後のエラーと除いた拒否の数",
              "ツール呼び出し\t3件\tエラー\t2件\thook の拒否（エラーから除いた）\t1件" in r.stdout.splitlines(), r.stdout + r.stderr)


def test_gate_ledger() -> None:
    print("material.py --gate が reverify・fixes・review を台帳から数える")
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    seq = iter(range(1, 10000))

    def row(event: str, task: str, **fields: object) -> str:
        at = (base + timedelta(seconds=next(seq))).isoformat(timespec="seconds")
        return flow_row(at, event, task, **fields)

    def lap(task: str, stage: str, edge: str = "start") -> str:
        return row("lap", task, stage=stage, edge=edge)

    def verify(task: str, result: str) -> str:
        return row("verify", task, result=result, seconds=1)

    rounds = {
        "GH-61": row("claim", "GH-61") + verify("GH-61", "VERIFY_NOT_PASSED") + verify("GH-61", "VERIFIED"),
        "GH-62": row("claim", "GH-62") + verify("GH-62", "FORMAT_FAILED") + verify("GH-62", "VERIFY_NOT_PASSED") + verify("GH-62", "VERIFIED"),
        # delegate の lap が無い記録（メインの流れが実装の段を delegate で囲む前の旧い記録）で2回差し戻した
        "GH-63": row("claim", "GH-63") + lap("GH-63", "accept") + lap("GH-63", "review") + verify("GH-63", "VERIFIED")
        + lap("GH-63", "review") + verify("GH-63", "VERIFIED") + lap("GH-63", "review"),
        # delegate の lap を打つ流れで1回差し戻した（lap の有無で結果が変わらない）
        "GH-64": row("claim", "GH-64") + lap("GH-64", "accept") + lap("GH-64", "review") + lap("GH-64", "delegate")
        + verify("GH-64", "VERIFIED") + lap("GH-64", "review"),
        "GH-65": row("claim", "GH-65") + lap("GH-65", "accept") + lap("GH-65", "review"),
        # 直しの無いままレビューだけが続けて打たれた
        "GH-68": row("claim", "GH-68") + lap("GH-68", "accept") + lap("GH-68", "review") + lap("GH-68", "review"),
        "GH-66": row("claim", "GH-66") + verify("GH-66", "VERIFY_NOT_PASSED") + row("release", "GH-66")
        + row("claim", "GH-66") + verify("GH-66", "VERIFIED"),
        "GH-67": row("claim", "GH-67") + lap("GH-67", "delegate") + lap("GH-67", "accept"),
    }

    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "repo")
        os.makedirs(root)
        git(root, "init", "-q", "-b", "main")
        env = {**os.environ, "CLAUDE_CONFIG_DIR": os.path.join(d, "cfg")}
        write(os.path.join(root, ".git", "task-workflow", "flow", "2026-10.jsonl"), "".join(rounds.values()))

        def gate(task_id: str, **given: str) -> subprocess.CompletedProcess:
            args = ["--friction", "none", "--human", "0"] + [a for k, v in given.items() for a in (f"--{k}", v)]
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "material.py"), root, task_id, "--gate", *args],
                capture_output=True, text=True, env=env,
            )

        r = gate("GH-61")
        check("失敗の検証が1回なら検証の打ち直しは1回", "SIGNAL\t検証の打ち直し\t1回" in r.stdout.splitlines(), r.stdout + r.stderr)
        r = gate("GH-62")
        check("FORMAT_FAILED と VERIFY_NOT_PASSED を合わせて2回", "SIGNAL\t検証の打ち直し\t2回" in r.stdout.splitlines(), r.stdout + r.stderr)
        r = gate("GH-63")
        check("delegate の lap が無くても、間に verify が挟まる review start の組の数が差し戻しの回数で、指摘ありになる",
              "SIGNAL\t受け入れでの直し\t2回" in r.stdout.splitlines() and "SIGNAL\t受け入れでの直し\tレビューの指摘あり" in r.stdout.splitlines(),
              r.stdout + r.stderr)
        r = gate("GH-64")
        check("delegate の lap があっても同じ数え方で1回",
              "SIGNAL\t受け入れでの直し\t1回" in r.stdout.splitlines() and "SIGNAL\t受け入れでの直し\tレビューの指摘あり" in r.stdout.splitlines(),
              r.stdout + r.stderr)
        r = gate("GH-68")
        check("verify の挟まらない連続した review start は直しに数えない", "受け入れでの直し" not in r.stdout, r.stdout + r.stderr)
        r = gate("GH-65", review="found")
        check("review start が1回だけなら clean で、引数が違えば台帳が勝って MISMATCH を標準エラーへ",
              "レビューの指摘あり" not in r.stdout and "MISMATCH\t--review\t引数=found\t台帳=clean" in r.stderr, r.stdout + r.stderr)
        r = gate("GH-67", review="found")
        check("review start が無い回は skip で、差し戻しは数えず、引数が違えば MISMATCH",
              "受け入れでの直し" not in r.stdout and "MISMATCH\t--review\t引数=found\t台帳=skip" in r.stderr, r.stdout + r.stderr)
        r = gate("GH-67", reverify="3")
        check("全部 0 の台帳に --reverify 3 を渡しても SIGNAL は出ず、MISMATCH を出す",
              "検証の打ち直し" not in r.stdout and "MISMATCH\t--reverify\t引数=3\t台帳=0" in r.stderr, r.stdout + r.stderr)
        r = gate("GH-66")
        check("最後の claim 以降だけ数える", "検証の打ち直し" not in r.stdout and "UNCOUNTED" not in r.stderr, r.stdout + r.stderr)
        r = gate("GH-70")
        check("claim が無ければ QUIET で、3つとも UNCOUNTED を標準エラーへ",
              r.stdout == "QUIET\n" and all(f"UNCOUNTED\t{o}" in r.stderr for o in ("--reverify", "--fixes", "--review")), r.stdout + r.stderr)
        r = gate("GH-70", reverify="1")
        check("claim が無ければ引数に戻る", "SIGNAL\t検証の打ち直し\t1回" in r.stdout.splitlines() and "MISMATCH" not in r.stderr, r.stdout + r.stderr)

        def gate_rows(task_id: str) -> list[dict]:
            flow = os.path.join(root, ".git", "task-workflow", "flow")
            rows = [json.loads(l) for n in sorted(os.listdir(flow)) for l in open(os.path.join(flow, n), encoding="utf-8") if l.strip()]
            return [x for x in rows if x["event"] == "gate" and x["task"] == task_id]

        signal_kinds = lambda out: "・".join(dict.fromkeys(l.split("\t")[1] for l in out.splitlines() if l.startswith("SIGNAL\t")))
        first = gate("GH-63")
        rows = gate_rows("GH-63")
        check("--gate は台帳に gate を1行足し、signals は出した SIGNAL の種類を重ねずに出た順でつないだもの",
              len(rows) == 2 and rows[-1]["signals"] == signal_kinds(first.stdout)
              and rows[-1]["signals"].startswith("受け入れでの直し") and rows[-1]["difficulty"] == "opus",
              repr(rows))
        check("QUIET のときの gate の signals は空で、claim が無ければ difficulty は -",
              [(x["signals"], x["difficulty"]) for x in gate_rows("GH-70")] == [("", "-"), ("検証の打ち直し", "-")], repr(gate_rows("GH-70")))
        second = gate("GH-63")
        check("前の gate の行は段の所要時間に入らず、2回目も同じ節を出す",
              lines_of("段の所要時間（flow/ から取った秒）", first.stdout) == lines_of("段の所要時間（flow/ から取った秒）", second.stdout),
              first.stdout + second.stdout)


def test_gate_work_repo() -> None:
    print("material.py --gate が別のリポジトリのタスクの差し戻しを作業先のコミットから数える")
    if shutil.which("bd") is None:
        check("bd が PATH にある（material.py は作業先を Beads の課題から読む）", False)
        return
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)

    def at(seconds: int) -> str:
        return (base + timedelta(seconds=seconds)).isoformat(timespec="seconds")

    def lap(seconds: int, task: str, stage: str, edge: str = "start") -> str:
        return flow_row(at(seconds), "lap", task, stage=stage, edge=edge)

    def review(start: int, task: str) -> str:
        return lap(start, task, "review") + lap(start + 1, task, "review", "end")

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
        work = os.path.join(d, "work")
        os.makedirs(root)
        os.makedirs(work)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "beads.role", "maintainer")
        git(work, "init", "-q", "-b", "main")
        git(work, "config", "user.email", "t@example.com")
        git(work, "config", "user.name", "t")

        def bd(*args: str, stdin: str | None = None) -> None:
            subprocess.run(["bd", *args], cwd=root, env=env, input=stdin, check=True, capture_output=True, text=True)

        def commit(seconds: int, subject: str) -> None:
            write(os.path.join(work, "f.txt"), subject)
            git(work, "add", "f.txt")
            stamp = at(seconds)
            subprocess.run(["git", "commit", "-q", "-m", subject], cwd=work, check=True, capture_output=True,
                           env={**os.environ, "GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp})

        bd("init", "--stealth", "-p", "t", "--non-interactive", "--skip-hooks", "--quiet")
        for n, repo in ((31, work), (32, work), (33, os.path.join(d, "missing")), (34, work)):
            bd("create", "--id", f"t-0{n}", "--title", f"課題{n}", "--body-file", "-", "--silent",
               "--metadata", '{"difficulty": "sonnet", "loopable": "Y"}', stdin="## 目的・背景\n\nx\n")
            bd("update", f"t-0{n}", "--notes", f"### 1. 直す\n- 満たす完了条件: なし\n### 作業先\n- `{repo}`")

        commit(1, "初期")
        commit(2, "T-031: 実装")
        commit(15, "T-031: 受け入れの差し戻しを直す")
        commit(102, "T-032: 実装")
        commit(112, "T-099: 別のタスクの変更")
        git(work, "checkout", "-q", "-b", "t-034")
        commit(315, "T-034: 主ブランチに入る前の直し")
        git(work, "checkout", "-q", "main")

        ledger = (
            # verify の無い台帳で、差し戻し1回の直しが作業先のコミットにだけ現れる
            flow_row(at(0), "claim", "T-031") + lap(5, "T-031", "accept") + review(10, "T-031") + review(20, "T-031")
            # 差し戻しでない連続: 窓にあるのは最初のレビューより前の実装と、別のタスクIDのコミットだけ
            + flow_row(at(100), "claim", "T-032") + review(110, "T-032") + review(113, "T-032")
            # 作業先が読めない: verify だけで数える
            + flow_row(at(200), "claim", "T-033") + review(210, "T-033")
            + flow_row(at(212), "verify", "T-033", result="VERIFIED", seconds=1) + review(220, "T-033")
            # 合流していない枝のコミットも数える
            + flow_row(at(300), "claim", "T-034") + review(310, "T-034") + review(320, "T-034")
        )
        write(os.path.join(root, ".git", "task-workflow", "flow", "2026-10.jsonl"), ledger)

        def gate(task_id: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "material.py"), root, task_id, "--gate", "--friction", "none", "--human", "0"],
                capture_output=True, text=True, env=env,
            )

        r = gate("T-031")
        check("verify が無くても、続く review start の組の間の作業先のコミットを差し戻し1回と数え、指摘ありになる",
              "SIGNAL\t受け入れでの直し\t1回" in r.stdout.splitlines()
              and "SIGNAL\t受け入れでの直し\tレビューの指摘あり" in r.stdout.splitlines(),
              r.stdout + r.stderr)
        r = gate("T-032")
        check("窓に verify も自分のIDのコミットも無い連続は数えない（最初のレビューより前の実装・別のIDのコミットは数えない）",
              "受け入れでの直し" not in r.stdout and r.returncode == 0, r.stdout + r.stderr)
        r = gate("T-033")
        check("作業先が読めなければ UNREAD を標準エラーへ出し、verify だけで数える",
              "SIGNAL\t受け入れでの直し\t1回" in r.stdout.splitlines()
              and any(l.startswith("UNREAD\t作業先のコミット\t") for l in r.stderr.splitlines()),
              r.stdout + r.stderr)
        r = gate("T-034")
        check("主ブランチに入る前のタスクIDの枝のコミットも数える",
              "SIGNAL\t受け入れでの直し\t1回" in r.stdout.splitlines(), r.stdout + r.stderr)


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
            + lap("GH-54", 130, "retro", "start") + lap("GH-54", 190, "retro", "end") + flow_row(at(200), "done", "GH-54")
            # edge の対の中に step と verify が入る
            + flow_row(at(0), "claim", "GH-55") + lap("GH-55", 10, "delegate", "start") + flow_row(at(80), "step", "GH-55")
            + flow_row(at(90), "verify", "GH-55", result="VERIFIED", seconds=30) + lap("GH-55", 100, "delegate", "end")
            + lap("GH-55", 100, "accept", "start") + lap("GH-55", 190, "accept", "end") + flow_row(at(200), "done", "GH-55")
            # step が続き、verify が step の区間に入る
            + flow_row(at(0), "claim", "GH-56") + flow_row(at(10), "step", "GH-56") + flow_row(at(30), "step", "GH-56")
            + flow_row(at(50), "verify", "GH-56", result="VERIFIED", seconds=10) + lap("GH-56", 80, "accept", "start")
            + lap("GH-56", 100, "accept", "end") + flow_row(at(110), "done", "GH-56")
            # 同じ段の edge の対が重なる
            + flow_row(at(0), "claim", "GH-57") + lap("GH-57", 10, "delegate", "start") + lap("GH-57", 30, "delegate", "start")
            + lap("GH-57", 70, "delegate", "end") + lap("GH-57", 100, "delegate", "end") + flow_row(at(110), "done", "GH-57"),
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
            "当たったときの節の並びは 手数 → 段の所要時間 → 同じ兆候の候補 → ドラフトの行方",
            len(heads) == 4 and heads[0].startswith("===== 手数") and heads[1] == title
            and heads[2].startswith("===== 同じ兆候の候補") and heads[3].startswith("===== ドラフトの行方"),
            "\n".join(heads),
        )
        stages = lines_of("段の所要時間（flow/ から取った秒）", r.stdout)
        check("節に TOTAL・STAGE・GAP の行が出る", stages[0] == "TOTAL\t200" and "STAGE\t委譲\t100\t50%" in stages and "GAP\t10\t5%" in stages, "\n".join(stages))

        r = gate("GH-52")
        lines = r.stdout.splitlines()
        check("end の無い start は OPEN の SIGNAL と節の OPEN の行", "SIGNAL\t段の所要時間\tOPEN レビュー" in lines and "OPEN\tレビュー" in lines, r.stdout + r.stderr)
        check(
            "OPEN があれば空きが50%でも段の空きの SIGNAL は出ず、節の GAP の行は出る",
            not any(l.startswith("SIGNAL\t段の所要時間\t段の空き") for l in lines) and "GAP\t100\t50%" in lines_of("段の所要時間（flow/ から取った秒）", r.stdout),
            r.stdout + r.stderr,
        )

        r = gate("GH-53")
        check("段のあいだの空きが2割以上なら SIGNAL", "SIGNAL\t段の所要時間\t段の空き 45%" in r.stdout.splitlines(), r.stdout + r.stderr)

        r = gate("GH-54")
        check("どれにも当たらなければ QUIET", r.stdout == "QUIET\n", r.stdout + r.stderr)

        for task, expected, label in (
            ("GH-55", ["TOTAL\t200", "STAGE\t委譲\t90\t45%", "STAGE\t検証\t30\t15%"],
             "edge の対の中の step と verify は委譲を重ねて数えない（実際の委譲は90秒）"),
            ("GH-56", ["TOTAL\t110", "STAGE\t委譲\t60\t55%", "STAGE\t検証\t10\t9%"],
             "続く step の区間から verify の秒を引く"),
            ("GH-57", ["TOTAL\t110", "STAGE\t委譲\t60\t55%"],
             "重なった同じ段の edge の対は和集合の秒で数える"),
        ):
            r = material(task)
            got = lines_of("段の所要時間（flow/ から取った秒）", r.stdout)
            check(label, all(l in got for l in expected), "\n".join(got))

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


def test_material_commits() -> None:
    print("material.py のコミット照合（送った範囲の記録・件名・記録が壊れたとき）")
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
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "t")

        def bd(*args: str, stdin: str | None = None) -> None:
            subprocess.run(["bd", *args], cwd=root, env=env, input=stdin, check=True, capture_output=True, text=True)

        def commit(subject: str) -> str:
            write(os.path.join(root, "f.txt"), subject)
            git(root, "add", "f.txt")
            git(root, "commit", "-q", "-m", subject)
            return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()

        def material(task_id: str) -> str:
            r = subprocess.run([sys.executable, os.path.join(HERE, "material.py"), root, task_id], capture_output=True, text=True, env=env)
            return "\n".join(lines_of("コミット", r.stdout)) + "\n" + r.stdout.rsplit("=====", 1)[-1]

        bd("init", "--stealth", "-p", "t", "--non-interactive", "--skip-hooks", "--quiet")
        for n in (10, 11, 12, 13, 14, 15):
            bd("create", "--id", f"t-0{n}", "--title", f"課題{n}", "--body-file", "-", "--silent",
               "--metadata", '{"difficulty": "sonnet", "loopable": "Y"}', stdin="## 目的・背景\n\nx\n")

        base = commit("初期")
        first = commit("件名にIDが無い変更")
        last = commit("もう一つ規約外の変更")
        outside = commit("T-010 に言及しただけの別の変更")
        t11 = commit("T-011: 件名に規約どおりID")
        bd("update", "t-010", "--set-metadata", f"task_shipped={base}..{last}")
        t14 = commit("T-014: 記録の形が壊れた課題")
        bd("update", "t-013", "--set-metadata", f"task_shipped={'a' * 40}..{'b' * 40}")
        bd("update", "t-014", "--set-metadata", "task_shipped=壊れた値")
        git(root, "checkout", "-q", "-b", "side")
        side = commit("T-015: 主ブランチに入っていない枝のコミット")
        git(root, "checkout", "-q", "main")
        bd("update", "t-015", "--set-metadata", f"task_shipped={base}..{side}")

        out = material("T-010")
        check("記録あり・件名が規約外: 範囲のコミットを出す", first[:7] in out and last[:7] in out and "該当するコミットが無い" not in out, out)
        check("記録あり: 範囲の外の言及だけのコミットは出さない", outside[:7] not in out, out)
        out = material("T-011")
        check("記録なし・件名にID: 件名で引く（従来どおり）", t11[:7] in out, out)
        out = material("T-012")
        check("記録なし・件名にも無い: 該当なしと注意", "該当するコミットが無い" in out and "見つからない" in out, out)
        out = material("T-013")
        check("記録が存在しない SHA: 件名へ戻り、件名にも無ければ該当なし", "該当するコミットが無い" in out, out)
        out = material("T-014")
        check("記録の形が壊れている: 件名へ戻る", t14[:7] in out, out)
        out = material("T-015")
        check("実在するが主ブランチの祖先でない先端: 範囲を使わず件名へ戻り、主ブランチに無いコミットを出さない",
              side[:7] not in out and "該当するコミットが無い" in out, out)


def test_draft_fate() -> None:
    print("draft_fate（ドラフトの行方の表と絞り）")
    sys.path.insert(0, HERE)
    import draft_fate
    from draft_fate import beads

    now = datetime(2026, 10, 12, tzinfo=timezone.utc)

    def issue(status: str, metadata: dict, days: float = 1, labels: tuple[str, ...] = (), description: str = "",
              bd_id: str = "t-001") -> beads.Issue:
        created = (now - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
        return beads.Issue(bd_id, "題", status, labels, (), None, None, None,
                           {"metadata": metadata, "created_at": created, "description": description})

    pointed = "## 目的・背景\n\n- 観点: 黄 道案内\n"
    candidate_meta = {"draft": "Y", "candidate_signal": "friction log", "candidate_place": "docs/a.md"}
    rows = draft_fate.tally([
        issue("deferred", candidate_meta, labels=("candidate",), description=pointed),
        issue("closed", candidate_meta, days=29, labels=("candidate", "cancelled"), description=pointed),
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
        "観点ごとに 積んだ・採用・却下・未決 を数え、期間の外・ドラフトでない課題・候補（label candidate）は数えない",
        [(r.viewpoint, r.stacked, r.adopted, r.dropped, r.pending) for r in rows]
        == [("不明", 1, 0, 0, 1), ("機械の検査", 1, 0, 0, 1), ("道案内", 6, 2, 2, 2)],
        repr(rows),
    )
    for adopted, dropped, expected in ((2, 3, True), (3, 3, False), (0, 2, False)):
        got = draft_fate.Row("道案内", adopted + dropped, adopted, dropped, 0).throttled
        check(f"採用{adopted}・却下{dropped} の絞り中は {expected}", got == expected)

    red = "## 目的・背景\n\n- 観点: 赤 道案内\n"
    pool = [
        issue("deferred", candidate_meta, days=27, labels=("candidate",), description=pointed, bd_id="t-011"),
        issue("deferred", candidate_meta, days=29, labels=("candidate",), description=pointed, bd_id="t-012"),
        issue("deferred", {**candidate_meta, "candidate_signal": "ツールのエラー"}, days=3, labels=("candidate",),
              description=pointed, bd_id="t-013"),
        issue("deferred", candidate_meta, days=40, description=pointed, bd_id="t-014"),
        issue("deferred", {"draft": "Y"}, days=40, description=red, bd_id="t-015"),
        issue("closed", candidate_meta, days=40, labels=("candidate", "cancelled"), description=pointed, bd_id="t-016"),
    ]
    key = ("道案内", "friction log", "docs/a.md")
    check(
        "昇格の材料: 28日以内で SIGNAL の種類が同じ候補だけを RECUR に、積んだ順で",
        draft_fate.recurring_candidates(pool, {"friction log"}, now) == [("T-011", key, 27)],
        repr(draft_fate.recurring_candidates(pool, {"friction log"}, now)),
    )
    check(
        "失効: 28日を過ぎても候補のままのものだけ（上がった候補・赤・閉じた候補は出ない）",
        draft_fate.expired_candidates(pool, now) == [("T-012", key, 29)],
        repr(draft_fate.expired_candidates(pool, now)),
    )
    check(
        "同じ候補も、日付を進めると昇格の材料から失効へ移る",
        draft_fate.recurring_candidates(pool, {"friction log"}, now + timedelta(days=2)) == []
        and [r[0] for r in draft_fate.expired_candidates(pool, now + timedelta(days=2))] == ["T-012", "T-011"],
    )
    check(
        "赤のドラフトと上がった候補は従来どおり行方に数える",
        [(r.viewpoint, r.stacked, r.pending) for r in draft_fate.tally(pool, now + timedelta(days=-20))] == [("道案内", 2, 2)],
        repr(draft_fate.tally(pool, now + timedelta(days=-20))),
    )

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

        quiet = ["--friction", "none", "--human", "0"]
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


def test_viewpoint_vocabulary() -> None:
    print("tw new --draft の観点・色の語彙が detail.md の正典と一致する")
    import re

    sys.path.insert(0, os.path.join(HERE, "..", "..", "task-model", "scripts", "core"))
    import taskfile

    with open(os.path.join(HERE, "..", "detail.md"), encoding="utf-8") as f:
        sections = {block.split("\n", 1)[0].strip(): block for block in f.read().split("\n## ")}

    def section(prefix: str) -> str:
        return next((text for title, text in sections.items() if title.startswith(prefix)), "")

    def column(text: str, pattern: str) -> tuple[str, ...]:
        return tuple(m.group(1) for line in text.split("\n") if (m := re.match(pattern, line)))

    check("7観点が改善の7観点の節の太字の項目と同じ順",
          column(section("改善の7観点"), r"^- \*\*(.+?)\*\*:") == taskfile.VIEWPOINTS,
          repr(column(section("改善の7観点"), r"^- \*\*(.+?)\*\*:")))
    check("色が色と1行の書式の表と同じ",
          column(section("色と1行の書式"), r"^\| `(.+?)` \|") == taskfile.VIEWPOINT_COLORS,
          repr(column(section("色と1行の書式"), r"^\| `(.+?)` \|")))
    weekly = tuple(v for v in column(section("週ごとに振り返る"), r"^\s*\| (\S+) \|") if v not in ("観点", "---"))
    check("横断の3観点が週ごとに振り返るの表の1列目と同じ", weekly == taskfile.WEEKLY_VIEWPOINTS, repr(weekly))
    ladder = next((line for text in sections.values() for line in text.split("\n") if line.startswith("**出し先の梯子**:")), "")
    check("出し先の梯子が taskfile の出し先の種類の語彙と同じ順",
          tuple(re.findall(r"`([^`]+)`", ladder)) == taskfile.INTERVENTION_TARGETS, repr(ladder))


def test_intervention_effect() -> None:
    print("intervention_effect（期日を過ぎた介入の判定）")
    sys.path.insert(0, HERE)
    import intervention_effect as ie
    from intervention_effect import beads, metrics

    now = datetime(2026, 12, 1, tzinfo=timezone.utc)
    shipped_at = now - timedelta(days=15)  # 期日は now − 1日
    base_at = shipped_at - timedelta(days=20)  # 基準線の窓だけに入る
    now_at = shipped_at + timedelta(days=5)  # いまの窓だけに入る
    serial = iter(range(1, 100000))

    def stamp(t: datetime) -> str:
        return t.isoformat(timespec="seconds").replace("+00:00", "Z")

    def issue(bd_id: str, signal: str, *, shipped: datetime = shipped_at, labels: tuple[str, ...] = (),
              metadata: dict | None = None) -> beads.Issue:
        description = (f"## 目的・背景\n\nx\n\n## 介入\n\n- 狙う兆候: {signal}\n- 基準線: 直近28日で12回\n"
                       "- 出し先の種類: スクリプト\n- 判定の期日: 送り出しから14日、または比較できるタスク20件\n- やめる条件: 減らなければ外す\n")
        raw = {"description": description, "created_at": stamp(shipped - timedelta(days=1)), "closed_at": stamp(shipped),
               "metadata": metadata or {}}
        return beads.Issue(bd_id, "題", "closed", labels, (), None, None, None, raw)

    def event(at: datetime, kind: str, **fields: object) -> metrics.Event:
        task = str(fields.pop("task", f"GH-{next(serial)}"))
        return metrics.Event(at, kind, task, {"event": kind, "task": task, **fields})

    def ships(at: datetime, n: int) -> list[metrics.Event]:
        return [event(at, "ship", result="SHIPPED") for _ in range(n)]

    def fails(at: datetime, n: int) -> list[metrics.Event]:
        return [event(at, "verify", result="VERIFY_NOT_PASSED") for _ in range(n)]

    def verdicts(issues: list[beads.Issue], events: list[metrics.Event], denials: list = (), days: int = 7) -> list[tuple]:
        found = ie.judge(issues, sorted(events, key=lambda e: e.at), list(denials), now, days)
        return [(v.task_id, v.name, v.verdict, v.reason) for v in found]

    one = [issue("gh-1", "`verify_failed`（検証の落ち）")]
    check("tw metrics の数が20%以上減れば helped",
          verdicts(one, ships(base_at, 10) + fails(base_at, 10) + ships(now_at, 10) + fails(now_at, 2))
          == [("GH-1", "verify_failed", ie.HELPED, "-")])
    check("20%以上増えれば hurt",
          verdicts(one, ships(base_at, 10) + fails(base_at, 2) + ships(now_at, 10) + fails(now_at, 10))
          == [("GH-1", "verify_failed", ie.HURT, "-")])
    check("変化が20%未満なら no observed effect",
          verdicts(one, ships(base_at, 10) + fails(base_at, 10) + ships(now_at, 10) + fails(now_at, 11))
          == [("GH-1", "verify_failed", ie.NO_EFFECT, "-")])
    check("どちらかの分母が10未満なら判定保留",
          verdicts(one, ships(base_at, 10) + fails(base_at, 10) + ships(now_at, 9) + fails(now_at, 1))
          == [("GH-1", "verify_failed", ie.PENDING, ie.TOO_FEW)])

    def flaky(at: datetime, n: int) -> list[metrics.Event]:
        rows: list[metrics.Event] = []
        for i in range(n):
            task = f"GH-{next(serial)}"
            rows += [event(at, "verify", task=task, result="VERIFY_NOT_PASSED", key=f"k{i}"),
                     event(at + timedelta(minutes=1), "verify", task=task, result="VERIFIED", key=f"k{i}")]
        return rows

    def reclaims(at: datetime, n: int) -> list[metrics.Event]:
        rows: list[metrics.Event] = []
        for _ in range(n):
            task = f"GH-{next(serial)}"
            rows += [event(at - timedelta(minutes=1), "release", task=task), event(at, "claim", task=task)]
        return rows

    makers = {
        "verify_failed": fails,
        "verify_real": fails,
        "verify_flaky": flaky,
        "verify_infra": lambda at, n: [event(at, "verify", result="VERIFY_NOT_PASSED", cause="infra") for _ in range(n)],
        "ship_verify_failed": lambda at, n: [event(at, "ship", result="VERIFY_FAILED") for _ in range(n)],
        "reclaim": reclaims,
    }
    check("件数の数の表は、tw metrics の数のうち割合・秒・1件あたりでも shipped でもないものと同じ",
          set(makers) == set(ie.COUNT_METRICS) and set(ie.COUNT_METRICS) < set(ie.METRIC_NAMES) and "shipped" not in ie.COUNT_METRICS)
    for name, make in makers.items():
        target = [issue("gh-10", f"`{name}`")]
        check(f"{name} は送り出しの数で割り、送り出しが倍になっても率が同じなら no observed effect",
              verdicts(target, ships(base_at, 10) + make(base_at, 4) + ships(now_at, 20) + make(now_at, 8))
              == [("GH-10", name, ie.NO_EFFECT, "-")])
        check(f"{name} は件数が増えても率が下がれば helped（送り出し10件で4件 → 20件で6件）",
              verdicts(target, ships(base_at, 10) + make(base_at, 4) + ships(now_at, 20) + make(now_at, 6))
              == [("GH-10", name, ie.HELPED, "-")])

    per_task = ships(base_at, 10) + [event(base_at, "verify", task=f"GH-{800 + i // 2}", result="VERIFIED") for i in range(10)]
    measured = ie.measure("verify_per_task", sorted(per_task, key=lambda e: e.at), [], base_at - timedelta(days=1), base_at + timedelta(days=1))
    check("1件あたりの数（verify_per_task）は送り出しの数で割らない", measured == ie.Measure(2.0, 10), repr(measured))
    check("行頭が表の名前でもバッククォートの語でもなければ判定保留（測れない兆候）",
          verdicts([issue("gh-2", "描画のタスクで `material.py` の数")], ships(base_at, 10) + ships(now_at, 10))
          == [("GH-2", None, ie.PENDING, ie.UNMEASURABLE)])

    pair = [issue("gh-3", "friction log"), issue("gh-4", "`friction log`", shipped=shipped_at - timedelta(days=3))]
    check("同じ名前を測る介入が比較の範囲の中で送り出されれば両方 confounded",
          verdicts(pair, ships(base_at, 10) + ships(now_at, 10), days=10)
          == [("GH-4", "friction log", ie.CONFOUNDED, "重なり:GH-3"), ("GH-3", "friction log", ie.CONFOUNDED, "重なり:GH-4")])

    def gates(at: datetime, n: int, hit: int) -> list[metrics.Event]:
        return [event(at, "gate", signals="friction log・時間の偏り" if i < hit else "") for i in range(n)]

    again = event(now_at + timedelta(hours=1), "gate", task="GH-900", signals="")
    first = event(now_at, "gate", task="GH-900", signals="friction log")
    check("SIGNAL の種類は gate を持つタスクの割合で、同じタスクは最後の1件",
          verdicts([issue("gh-5", "friction log（委譲先）")], gates(base_at, 10, 5) + gates(now_at, 9, 1) + [first, again])
          == [("GH-5", "friction log", ie.HELPED, "-")])
    check("gate の記録が無い窓は分母0で判定保留",
          verdicts([issue("gh-5", "friction log")], gates(now_at, 10, 1)) == [("GH-5", "friction log", ie.PENDING, ie.TOO_FEW)])

    denials = [ie.Denial(base_at, "worktree-write") for _ in range(10)] + [ie.Denial(now_at, "worktree-write") for _ in range(30)]
    check("hook の規則のキーは拒否の数を送り出しの数で割り、増えれば hurt",
          verdicts([issue("gh-6", "`worktree-write` の拒否")], ships(base_at, 10) + ships(now_at, 10), denials)
          == [("GH-6", "worktree-write", ie.HURT, "-")])

    check("期日が期間の外の介入と、label cancelled の介入は出さない",
          verdicts([issue("gh-7", "`reclaim`", shipped=now - timedelta(days=30)),
                    issue("gh-8", "`reclaim`", labels=("cancelled",))], ships(base_at, 10) + ships(now_at, 10)) == [])

    lines = ie.section_lines(ie.judge(
        [issue("gh-9", "`verify_failed`", metadata={"from_draft": "機械の検査"})],
        sorted(ships(base_at, 10) + fails(base_at, 2) + ships(now_at, 10) + fails(now_at, 10), key=lambda e: e.at), [], now, 7))
    check("hurt の介入は INTERVENTION の行と、観点・出し先の種類つきの REVERT の行を出す",
          lines == ["INTERVENTION\tGH-9\tverify_failed\t0.2(n=10)\t1(n=10)\thurt\t-", "REVERT\tGH-9\t機械の検査\thurt\tスクリプト"],
          repr(lines))
    check("該当が無ければ EMPTY", ie.section_lines([]) == ["EMPTY"])

    with tempfile.TemporaryDirectory() as d:
        git(d, "init", "-q", "-b", "main")
        write(os.path.join(d, ".git", "hook-denials", "2026-10.jsonl"),
              "".join(json.dumps({"at": at, "hook": "h", "rule": "r", "actor": "main"}) + "\n"
                      for at in ("2026-10-03T19:53:14.58175Z", "2026-10-03T20:33:38.942169922Z", "2026-10-03T20:33:38+09:00"))
              + json.dumps({"at": "壊れた時刻", "rule": "r"}) + "\n" + "壊れた行\n")
        got = ie.read_denials(d)
        check("拒否の記録の時刻は小数の桁によらず読み、読めない行は飛ばす", [x.rule for x in got] == ["r", "r", "r"], repr(got))


def main() -> None:
    test_viewpoint_vocabulary()
    test_weekly()
    test_slow_calls()
    test_transcript_binding()
    test_gate()
    test_hook_denials()
    test_gate_ledger()
    test_gate_work_repo()
    test_stage_signals()
    test_task_material()
    test_material_commits()
    test_draft_fate()
    test_intervention_effect()
    print()
    if failures:
        print(f"FAILED {len(failures)}件: " + ", ".join(failures))
        raise SystemExit(1)
    print("すべて通った")


if __name__ == "__main__":
    main()
