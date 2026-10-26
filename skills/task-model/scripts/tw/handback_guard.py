"""`tw handback-guard`: Claude Code の hook の入力（stdin の JSON）を読み、委譲先の返却
（`SubagentStop`・`SubagentHandback` の `PreToolUse`）を、入力の `cwd` について渡された判定が
理由を返したときに拒む。

拒むときだけ stdout に JSON を1行出す（`SubagentStop` は `decision: block`、`PreToolUse` は deny）。
通すときは何も出さない。終了コードはいつも0（hook の取り決め。`tw` の「stdout は先頭語の TSV」の例外）。

`--agent-scoped` では、サブエージェントが `Edit`・`Write`・`NotebookEdit` を使う `PreToolUse` で、`cwd` の作業ツリーに
着手の控えがあれば `agent_id` ごとの書いた印を残し、返却はその印のあるサブエージェントのものだけを判定する。
書き先が作業ツリーとタスクの作業先の外（`/tmp` など）なら印は残さない。
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import IO, Callable

import agent_scope
import gitinfo
import ledger
import tw_handback

HANDBACK_TOOL = "SubagentHandback"
STOP_EVENTS = ("SubagentStop", "Stop")
WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")


def run(
    stdin: IO[str], stdout: IO[str], refusal: Callable[[str, str | None], tuple[str, str] | None], agent_scoped: bool = False
) -> None:
    try:
        payload = json.loads(stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict) or not agent_scope.applies(payload, agent_scoped):
        return
    if agent_scoped and not _is_writer(payload):
        return
    out = decide(payload, refusal)
    if out is not None:
        stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


def _is_writer(payload: dict) -> bool:
    """書き換える道具の `PreToolUse` なら印を残して `False`（判定しない）。ほかは印があるか。"""
    agent_id = agent_scope.agent_id(payload)
    cwd = payload.get("cwd")
    if agent_id is None or not isinstance(cwd, str) or not os.path.isdir(cwd):
        return False
    try:
        if payload.get("hook_event_name") == "PreToolUse" and payload.get("tool_name") in WRITE_TOOLS:
            if ledger.open_claims(cwd=cwd) and not _writes_outside_work(payload, cwd):
                ledger.mark_agent_writer(agent_id, cwd=cwd)
            return False
        return ledger.is_agent_writer(agent_id, cwd=cwd)
    except (gitinfo.GitCommandError, OSError):
        return False


def _writes_outside_work(payload: dict, cwd: str) -> bool:
    """書き先が、作業ツリーと着手中のタスクの作業先のどれの中でもなければ `True`。

    書き先が読めない・作業先が引けないときは `False`（印を残す側）。
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    path = tool_input.get("notebook_path" if payload.get("tool_name") == "NotebookEdit" else "file_path")
    if not isinstance(path, str) or not path:
        return False
    try:
        target = (pathlib.Path(cwd) / path).resolve()
        toplevel = gitinfo.git_toplevel(cwd)
        roots = {pathlib.Path(toplevel).resolve()}
        if target.is_relative_to(next(iter(roots))):
            return False
        for task_id in ledger.open_claims(cwd=cwd):
            roots.update(pathlib.Path(root).resolve() for root in tw_handback.work_roots(toplevel, task_id))
    except (gitinfo.GitCommandError, OSError, RuntimeError, ValueError):
        return False
    return not any(target.is_relative_to(root) for root in roots)


def _handback_body(payload: dict) -> str | None:
    """`SubagentHandback` の `tool_input.message`。読めなければ `None`。"""
    tool_input = payload.get("tool_input")
    message = tool_input.get("message") if isinstance(tool_input, dict) else None
    return message if isinstance(message, str) else None


def decide(payload: dict, refusal: Callable[[str, str | None], tuple[str, str] | None]) -> dict | None:
    """出す JSON。通すなら `None`。"""
    event = payload.get("hook_event_name")
    is_stop = event in STOP_EVENTS
    is_handback = event == "PreToolUse" and payload.get("tool_name") == HANDBACK_TOOL
    cwd = payload.get("cwd")
    if not (is_stop or is_handback) or not isinstance(cwd, str):
        return None
    verdict = refusal(cwd, _handback_body(payload) if is_handback else None)
    if verdict is None:
        return None
    rule, reason = verdict
    agent_scope.record_denial(payload, "tw-handback-guard", rule)
    if is_stop:
        return {"decision": "block", "reason": reason}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
