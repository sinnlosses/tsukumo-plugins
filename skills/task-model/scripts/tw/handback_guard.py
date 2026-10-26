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
import beads
import gitinfo
import ledger
import tw_handback

HANDBACK_TOOL = "SubagentHandback"
STOP_EVENTS = ("SubagentStop", "Stop")
WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
LONG_DELEGATION_STEPS = 100
HANDBACK_TOKEN_LIMIT = 2000


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
            task_roots = tw_handback.work_roots(toplevel, task_id)
            if task_roots is None:
                return False
            roots.update(pathlib.Path(root).resolve() for root in task_roots)
    except (beads.BeadsError, gitinfo.GitCommandError, OSError, RuntimeError, ValueError):
        return False
    return not any(target.is_relative_to(root) for root in roots)


def _handback_body(payload: dict) -> str | None:
    """`SubagentHandback` の `tool_input.message`。読めなければ `None`。"""
    tool_input = payload.get("tool_input")
    message = tool_input.get("message") if isinstance(tool_input, dict) else None
    return message if isinstance(message, str) else None


def _step_count(transcript_path: str) -> int:
    """委譲先の transcript の歩数（`usage` を持つ assistant メッセージの id の種類数）。中身は読み取らず数だけ返す。読めなければ 0。"""
    ids: set[str] = set()
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue
                message = entry.get("message")
                if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                    continue
                step_id = message.get("id") or entry.get("uuid")
                if isinstance(step_id, str):
                    ids.add(step_id)
    except OSError:
        return 0
    return len(ids)


def _record_long_delegation(payload: dict, cwd: str) -> None:
    """返却を通した委譲の歩数が `LONG_DELEGATION_STEPS` を超えていたら、`long-delegation` を台帳へ件数として残す（記録だけ。落ちても返却を変えない）。"""
    path = payload.get("agent_transcript_path")
    if not isinstance(path, str) or not os.path.isdir(cwd):
        return
    steps = _step_count(path)
    if steps <= LONG_DELEGATION_STEPS:
        return
    try:
        claims = ledger.open_claims(cwd=cwd)
        if len(claims) == 1:
            agent = agent_scope.agent_id(payload)
            ledger.record_event(cwd, "long-delegation", claims[0], "", steps=steps, **({"agent": agent} if agent else {}))
    except (gitinfo.GitCommandError, OSError):
        return


def estimated_tokens(text: str) -> int:
    """ASCII 4文字を1トークン、それ以外の1文字を1トークンとみなした近似（トークナイザは使わない）。"""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return (len(text) - ascii_chars) + ascii_chars // 4


def _length_warning(body: str | None) -> dict | None:
    """返却の本文が `HANDBACK_TOKEN_LIMIT` 相当を超えたときの警告（拒まない。`additionalContext` を足すだけ）。"""
    if body is None or estimated_tokens(body) <= HANDBACK_TOKEN_LIMIT:
        return None
    note = (
        f"返却の本文が約 {estimated_tokens(body)} トークン相当で、目安の {HANDBACK_TOKEN_LIMIT} を超えている"
        "（警告だけで拒まない）。長い材料は `tw relay` に書き、本文は結果と参照に絞る。"
    )
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": note}}


def decide(payload: dict, refusal: Callable[[str, str | None], tuple[str, str] | None]) -> dict | None:
    """出す JSON。通すなら `None`。"""
    event = payload.get("hook_event_name")
    is_stop = event in STOP_EVENTS
    is_handback = event == "PreToolUse" and payload.get("tool_name") == HANDBACK_TOOL
    cwd = payload.get("cwd")
    if not (is_stop or is_handback) or not isinstance(cwd, str):
        return None
    body = _handback_body(payload) if is_handback else None
    verdict = refusal(cwd, body)
    if verdict is None:
        if is_stop:
            _record_long_delegation(payload, cwd)
        return _length_warning(body)
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
