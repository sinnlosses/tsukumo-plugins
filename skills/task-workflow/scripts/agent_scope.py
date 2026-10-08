"""`tw commit-guard`・`tw handback-guard` の `--agent-scoped` の判定。

引数なしの呼び出し（`agents/no-delegate.md` の frontmatter の hook）は常に関門を掛ける。
`--agent-scoped`（plugin の `hooks/hooks.json`。主のセッションにも掛かる）では、
入力の `agent_type` の末尾（`:` で割った最後）が `no-delegate` のときだけ関門を掛け、
`agent_type` が無い・ほかの名前のときは通す。
"""

from __future__ import annotations

GUARDED_AGENT = "no-delegate"


def applies(payload: dict, agent_scoped: bool) -> bool:
    if not agent_scoped:
        return True
    agent_type = payload.get("agent_type")
    return isinstance(agent_type, str) and agent_type.rsplit(":", 1)[-1] == GUARDED_AGENT
