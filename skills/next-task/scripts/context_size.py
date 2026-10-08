#!/usr/bin/env python3
"""main の文脈の大きさを、そのセッションの transcript の最後の応答の usage から読み、しきい値と比べる。

使い方:
  python3 context_size.py <セッションID>

出力は1行（終了コードはいつも0）:
  OVER<TAB><トークン数><TAB><しきい値>     しきい値を超えた
  UNDER<TAB><トークン数><TAB><しきい値>    しきい値以下
  UNKNOWN<TAB><理由>                        transcript が無い・usage のある応答が無い

transcript は `$CLAUDE_CONFIG_DIR`（無ければ `~/.claude`）の `projects/*/<セッションID>.jsonl`。
transcript には会話がそのまま入っているので、usage の数のほかは読まず、出さない。
transcript の形は Claude Code の文書に無く、変わったら UNKNOWN になる。
"""

from __future__ import annotations

import glob
import json
import os
import sys

THRESHOLD = 200_000

# 1回の要求で送ったトークンの内訳。足すと、その応答の時点の文脈の大きさになる。
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1]:
        print("UNKNOWN\tusage")
        return 0
    print(judge(sys.argv[1]))
    return 0


def judge(session_id: str) -> str:
    path = transcript_path(session_id)
    if path is None:
        return "UNKNOWN\tno-transcript"
    tokens = last_main_tokens(path)
    if tokens is None:
        return "UNKNOWN\tno-usage"
    verdict = "OVER" if tokens > THRESHOLD else "UNDER"
    return f"{verdict}\t{tokens}\t{THRESHOLD}"


def transcript_path(session_id: str) -> str | None:
    if os.sep in session_id or session_id.startswith("."):
        return None
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    pattern = os.path.join(glob.escape(config), "projects", "*", glob.escape(session_id) + ".jsonl")
    found = glob.glob(pattern)
    if not found:
        return None
    return max(found, key=os.path.getmtime)


def last_main_tokens(path: str) -> int | None:
    """`isSidechain` が true の行（サブエージェント）を飛ばし、main の最後の応答の usage の和を返す。"""
    last: int | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            tokens = main_tokens(line)
            if tokens is not None:
                last = tokens
    return last


def main_tokens(line: str) -> int | None:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain") is True:
        return None
    message = entry.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return None
    values = [usage.get(k, 0) for k in USAGE_KEYS]
    if not all(isinstance(v, int) for v in values):
        return None
    return sum(values)


if __name__ == "__main__":
    sys.exit(main())
