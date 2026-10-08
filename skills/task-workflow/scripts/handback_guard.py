"""`tw handback-guard`: Claude Code の hook の入力（stdin の JSON）を読み、委譲先の返却
（`SubagentStop`・`SubagentHandback` の `PreToolUse`）を、入力の `cwd` について渡された判定が
理由を返したときに拒む。

拒むときだけ stdout に JSON を1行出す（`SubagentStop` は `decision: block`、`PreToolUse` は deny）。
通すときは何も出さない。終了コードはいつも0（hook の取り決め。`tw` の「stdout は先頭語の TSV」の例外）。
"""

from __future__ import annotations

import json
from typing import IO, Callable

import agent_scope

HANDBACK_TOOL = "SubagentHandback"
STOP_EVENTS = ("SubagentStop", "Stop")


def run(
    stdin: IO[str], stdout: IO[str], refusal: Callable[[str], str | None], agent_scoped: bool = False
) -> None:
    try:
        payload = json.loads(stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict) or not agent_scope.applies(payload, agent_scoped):
        return
    out = decide(payload, refusal)
    if out is not None:
        stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


def decide(payload: dict, refusal: Callable[[str], str | None]) -> dict | None:
    """出す JSON。通すなら `None`。"""
    event = payload.get("hook_event_name")
    is_stop = event in STOP_EVENTS
    is_handback = event == "PreToolUse" and payload.get("tool_name") == HANDBACK_TOOL
    cwd = payload.get("cwd")
    if not (is_stop or is_handback) or not isinstance(cwd, str):
        return None
    reason = refusal(cwd)
    if reason is None:
        return None
    if is_stop:
        return {"decision": "block", "reason": reason}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
