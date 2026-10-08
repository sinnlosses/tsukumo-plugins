#!/usr/bin/env python3
"""`context_size.py` の自己テスト。

使い方: python3 selftest_context_size.py

標準ライブラリだけで動く。落ちたら非0で終わる。
transcript は作り物で、会話の文面は入れない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "context_size.py")
SESSION = "11111111-2222-3333-4444-555555555555"

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def assistant(input_tokens: int, cache_creation: int, cache_read: int, sidechain: bool = False) -> dict:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "output_tokens": 999,
            }
        },
    }


def write_transcript(config: str, entries: list[dict | str]) -> None:
    project = os.path.join(config, "projects", "-tmp-project")
    os.makedirs(project, exist_ok=True)
    with open(os.path.join(project, f"{SESSION}.jsonl"), "w", encoding="utf-8") as f:
        for e in entries:
            f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")


def run(config: str, *args: str) -> str:
    env = {**os.environ, "CLAUDE_CONFIG_DIR": config}
    out = subprocess.run([sys.executable, SCRIPT, *args], env=env, capture_output=True, text=True)
    check("非0で終わらない", out.returncode == 0, out.stderr)
    return out.stdout.strip()


def test_over() -> None:
    print("最後の応答の3つの和がしきい値を超えたら OVER")
    with tempfile.TemporaryDirectory() as d:
        write_transcript(d, [assistant(1, 1, 10), {"type": "user"}, assistant(2, 1_000, 199_000)])
        got = run(d, SESSION)
        check("OVER 200002", got == "OVER\t200002\t200000", repr(got))


def test_under() -> None:
    print("前の応答が大きくても、最後の応答で決める")
    with tempfile.TemporaryDirectory() as d:
        write_transcript(d, [assistant(0, 0, 300_000), assistant(5, 5, 100)])
        got = run(d, SESSION)
        check("UNDER 110", got == "UNDER\t110\t200000", repr(got))


def test_exact_threshold() -> None:
    print("しきい値ちょうどは UNDER")
    with tempfile.TemporaryDirectory() as d:
        write_transcript(d, [assistant(0, 0, 200_000)])
        got = run(d, SESSION)
        check("UNDER 200000", got == "UNDER\t200000\t200000", repr(got))


def test_skip_sidechain() -> None:
    print("isSidechain が true の行と読めない行は飛ばす")
    with tempfile.TemporaryDirectory() as d:
        write_transcript(d, [assistant(0, 0, 150_000), assistant(0, 0, 900_000, sidechain=True), "{broken"])
        got = run(d, SESSION)
        check("UNDER 150000", got == "UNDER\t150000\t200000", repr(got))


def test_no_transcript() -> None:
    print("transcript が無ければ UNKNOWN")
    with tempfile.TemporaryDirectory() as d:
        got = run(d, SESSION)
        check("UNKNOWN no-transcript", got == "UNKNOWN\tno-transcript", repr(got))


def test_no_usage() -> None:
    print("usage のある応答が無ければ UNKNOWN")
    with tempfile.TemporaryDirectory() as d:
        write_transcript(d, [{"type": "user"}, {"type": "assistant", "isSidechain": False, "message": {}}])
        got = run(d, SESSION)
        check("UNKNOWN no-usage", got == "UNKNOWN\tno-usage", repr(got))


def test_no_argument() -> None:
    print("セッションIDが無ければ UNKNOWN")
    with tempfile.TemporaryDirectory() as d:
        got = run(d)
        check("UNKNOWN usage", got == "UNKNOWN\tusage", repr(got))


if __name__ == "__main__":
    test_over()
    test_under()
    test_exact_threshold()
    test_skip_sidechain()
    test_no_transcript()
    test_no_usage()
    test_no_argument()
    if failures:
        print(f"\nFAILED: {len(failures)}")
        sys.exit(1)
    print("\nall ok")
