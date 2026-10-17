"""`tw commit-guard`・`tw handback-guard` の `--agent-scoped` の判定。

引数なしの呼び出し（`agents/no-delegate.md` の frontmatter の hook）は常に関門を掛ける。
`--agent-scoped`（plugin の `hooks/hooks.json`。主のセッションにも掛かる）では、入力に `agent_id` があるとき
（サブエージェントの中で発火したとき）だけ関門を掛け、エージェントの名前は見ない。`agent_type` は `--agent` で
起こした主のセッションにも入るので見ない。
"""

from __future__ import annotations

import re

_AGENT_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


def agent_id(payload: dict) -> str | None:
    """入力の `agent_id`。無いか、ファイル名に使えない文字を含めば `None`。"""
    value = payload.get("agent_id")
    return value if isinstance(value, str) and _AGENT_ID.fullmatch(value) else None


def session_id(payload: dict) -> str | None:
    """入力の `session_id`。無いか、ファイル名に使えない文字を含めば `None`。"""
    value = payload.get("session_id")
    return value if isinstance(value, str) and _AGENT_ID.fullmatch(value) else None


def applies(payload: dict, agent_scoped: bool) -> bool:
    return not agent_scoped or agent_id(payload) is not None
