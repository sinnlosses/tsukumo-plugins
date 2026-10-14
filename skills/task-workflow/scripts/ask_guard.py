"""`tw ask-guard`: Claude Code の PreToolUse hook の入力（stdin の JSON）を読み、`AskUserQuestion` を、
入力の `cwd` の作業ツリーに着手の控え（`ledger.open_claims`）があり、控えのタスクのどれかが `loopable: Y` で、
台帳 `flow/` にそのタスクの最後の `claim` より後の `lap`（`stage` が `retro`）が無いときに拒む。
`agent_type` は見ない（主のセッションにも委譲先にも掛かる）。

拒むときだけ stdout に deny の JSON を1行出す。通すときは何も出さない。終了コードはいつも0
（hook の取り決め。`tw` の「stdout は先頭語の TSV」の例外）。
"""

from __future__ import annotations

import json
import os
from typing import IO

import beads
import gitinfo
import ledger
import metrics

ASK_TOOL = "AskUserQuestion"
RETRO_STAGE = "retro"


def run(stdin: IO[str], stdout: IO[str]) -> None:
    try:
        payload = json.loads(stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    reason = decide(payload)
    if reason is None:
        return
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


def decide(payload: dict) -> str | None:
    """拒む理由。通すなら `None`。"""
    cwd = payload.get("cwd")
    if payload.get("tool_name") != ASK_TOOL or not isinstance(cwd, str) or not os.path.isdir(cwd):
        return None
    try:
        claims = ledger.open_claims(cwd=cwd)
        if not claims:
            return None
        toplevel = gitinfo.git_toplevel(cwd)
        root = ledger.ledger_root(cwd)
    except gitinfo.GitCommandError:
        return None
    loopable = [task_id for task_id in claims if _is_loopable(toplevel, task_id)]
    if not loopable:
        return None
    retro = _in_retro(root, loopable)
    refused = [task_id for task_id in loopable if task_id not in retro]
    if not refused:
        return None
    shown = ",".join(refused)
    return (
        f"着手の印（{shown}）のタスクは loopable: Y（利用者に聞かずに進める約束）なので AskUserQuestion を拒む。"
        f"質問せず、tw edit {shown} --loopable N で直し、tw show {shown} の ## 注意 の最後に質問が要った理由を1行足して"
        f" tw edit {shown} --section 注意 --body-file - で書き、tw release {shown} で着手を戻して止める。"
        "作業ツリーに作業が残っていなければ /loop は次の1件へ進み、残っていれば続けずに人に預けて終える"
    )


def _is_loopable(toplevel: str, task_id: str) -> bool:
    try:
        issue = beads.show(toplevel, beads.to_bd_id(task_id))
    except (beads.BeadsError, OSError):
        return False
    if issue is None:
        return False
    task = beads.to_task(issue)[0]
    return task is not None and task.loopable == "Y"


def _in_retro(root: str, task_ids: list[str]) -> set[str]:
    """最後の `claim` より後に振り返りの `lap` があるタスク。"""
    try:
        events = metrics.read_events(root)[0]
    except OSError:
        return set()
    started: set[str] = set()
    for event in events:
        if event.task not in task_ids:
            continue
        if event.kind == "claim":
            started.discard(event.task)
        elif event.kind == "lap" and event.fields.get("stage") == RETRO_STAGE:
            started.add(event.task)
    return started
