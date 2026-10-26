"""`tw lap-hook`: Claude Code の hook の入力（stdin の JSON）から、オーケストレーターの段の頭の `lap` を台帳に書く。

- `SubagentStart` で、`agent_type` の最後の `:` より後ろが `IMPLEMENTER_TYPES` のもの → 段 `delegate` の `start`
- 主のセッション（入力に `agent_id` が無い）の `Skill` の `PreToolUse` で、`tool_input.skill` の最後の `:` より後ろが
  `retrospect` のもの → 段 `retro` の `start`
- `SubagentStop` で、`agent_type` の最後の `:` より後ろが `acceptor` のもの → 段 `review` の `end`（開いているときだけ）

書くのは入力の `cwd` の作業ツリーの着手の控えがちょうど1件で、その段が開いていないときだけ。`delegate` は `accept` が
開いているあいだは書かず、`retro` は `accept` の最後の端が `end` のときだけ書く。

stdout には何も出さない（`SubagentStart` の stdout は委譲先の文脈に入りうる）。終了コードはいつも0。
"""

from __future__ import annotations

import json
import os
from typing import IO

import agent_scope
import gitinfo
import ledger
import metrics
import tw_base

IMPLEMENTER_TYPES = ("no-delegate", "general-purpose")
ACCEPTOR_TYPE = "acceptor"
RETRO_SKILL = "retrospect"


def run(stdin: IO[str]) -> None:
    try:
        payload = json.loads(stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    found = _stage(payload)
    cwd = payload.get("cwd")
    if found is None or not isinstance(cwd, str) or not os.path.isdir(cwd):
        return
    try:
        _record(cwd, *found)
    except (gitinfo.GitCommandError, OSError):
        return


def _short_name(value: object) -> str | None:
    return value.rsplit(":", 1)[-1] if isinstance(value, str) else None


def _stage(payload: dict) -> tuple[str, str] | None:
    """`(段, 端)`。書くものが無ければ `None`。"""
    event = payload.get("hook_event_name")
    if event == "SubagentStart" and _short_name(payload.get("agent_type")) in IMPLEMENTER_TYPES:
        return "delegate", "start"
    if event == "SubagentStop" and _short_name(payload.get("agent_type")) == ACCEPTOR_TYPE:
        return "review", "end"
    if event == "PreToolUse" and payload.get("tool_name") == "Skill" and agent_scope.agent_id(payload) is None:
        tool_input = payload.get("tool_input")
        skill = tool_input.get("skill") if isinstance(tool_input, dict) else None
        if _short_name(skill) == RETRO_SKILL:
            return "retro", "start"
    return None


def _record(cwd: str, stage: str, edge: str) -> None:
    claims = ledger.open_claims(cwd=cwd)
    if len(claims) != 1:
        return
    task_id = claims[0]
    events = metrics.read_events(ledger.ledger_root(cwd))[0]
    opened = metrics.open_stages(events, task_id)
    if edge == "end":
        if stage not in opened:
            return
    elif stage in opened or (stage == "delegate" and "accept" in opened):
        return
    if stage == "retro" and metrics.last_edge(events, task_id, "accept") != "end":
        return
    tw_base.record(gitinfo.git_toplevel(cwd), "lap", task_id, stage=stage, edge=edge)
