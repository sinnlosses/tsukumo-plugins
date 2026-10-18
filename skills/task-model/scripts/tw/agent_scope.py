"""`tw commit-guard`・`tw handback-guard` の `--agent-scoped` の判定。

引数なしの呼び出し（`agents/no-delegate.md` の frontmatter の hook）は常に関門を掛ける。
`--agent-scoped`（plugin の `hooks/hooks.json`。主のセッションにも掛かる）では、入力に `agent_id` があるとき
（サブエージェントの中で発火したとき）だけ関門を掛け、エージェントの名前は見ない。`agent_type` は `--agent` で
起こした主のセッションにも入るので見ない。
"""

from __future__ import annotations

import re

import ledger

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


def record_denial(payload: dict, hook: str, rule: str) -> None:
    """入力の `cwd` の共有の git dir へ拒否を残す。呼び手は `agent_id` があれば `subagent`（その `agent_id` も残す）、無ければ `main`。"""
    cwd = payload.get("cwd")
    agent = agent_id(payload)
    ledger.record_denial(cwd if isinstance(cwd, str) else None, hook, rule, "subagent" if agent is not None else "main", agent)
