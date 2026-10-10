"""`tw` のサブコマンドが共有する部品（設定の有無・git の薄いラッパ・タスクの読み取り・出力の整形・Beads の actor・流れの記録）。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import beads
import layout
import ledger
import taskfile


def format_refusal(toplevel: str) -> tuple[str, int] | None:
    """設定ファイルが無いために、サブコマンド（`config-doctor`・`land` を除く）を打たずに出す行と終了コード。打てるなら `None`。"""
    if os.path.exists(os.path.join(toplevel, layout.CONFIG_PATH)):
        return None
    return "MISSING", 6


def run_git(toplevel: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=toplevel, capture_output=True, text=True)


def history_ids_at_base(toplevel: str) -> set[str]:
    base = ledger.base_branch(toplevel)
    r = run_git(toplevel, ["show", f"{base}:{layout.HISTORY_TASKS_PATH}"])
    if r.returncode != 0:
        return set()
    return {m.group(1) for m in layout.HISTORY_HEADING_PATTERN.finditer(r.stdout)}


def is_resolved(dep_id: str, tasks: dict[str, taskfile.Task]) -> bool:
    """4.1: done/dropped は解決済み。一覧に無い ID（アーカイブ済み）も解決済み。"""
    t = tasks.get(dep_id)
    return t is None or t.status in ("done", "dropped")


def readiness(task: taskfile.Task, tasks: dict[str, taskfile.Task], claims: set[str]) -> str:
    if task.status == "hold":
        return "HOLD"
    if task.status != "todo":
        return "-"
    if task.id in claims:
        return "CLAIMED"
    blocked = [d for d in task.dependencies if not is_resolved(d, tasks)]
    if blocked:
        return "BLOCKED:" + ",".join(blocked)
    return "READY"


def format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes}m" if minutes else f"{hours}h"


def age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - at).total_seconds()


def read_body(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def _task_difficulty(toplevel: str, task_id: str, issue: beads.Issue | None = None) -> str:
    """記録に入れる `difficulty`。引けなければ `?`（記録のために元のサブコマンドを落とさない）。"""
    try:
        if issue is None:
            issue = beads.show(toplevel, beads.to_bd_id(task_id))
        task = beads.to_task(issue)[0] if issue is not None else None
    except Exception:
        return "?"
    return task.difficulty if task is not None else "?"


def record(
    toplevel: str, event: str, task_id: str, issue: beads.Issue | None = None, **fields: str | int | bool
) -> None:
    ledger.record_event(toplevel, event, task_id, _task_difficulty(toplevel, task_id, issue), **fields)


def claimed_here(toplevel: str) -> list[str]:
    """この作業ツリーが着手の印を持つタスク（`done` にしたあと `ship` までのものも含む）。引けなければ空。"""
    try:
        me = actor(toplevel)
        return [
            beads.to_task_id(i.bd_id)
            for i in beads.list_issues(toplevel)
            if i.status == "in_progress" and i.assignee == me
        ]
    except Exception:
        return []


def require_claimed(toplevel: str, task_id: str) -> None:
    """引数で渡されたタスクIDが、この作業ツリーの着手の印と一致するか確かめる。違えば `NOT_OWNER`（終了コード4）。"""
    if not beads.is_initialized(toplevel):
        print("usage: この作業ツリーには着手の印が無い。タスクIDを付けずに打つ", file=sys.stderr)
        raise SystemExit(2)
    shown = beads.to_task_id(bd_task_id(task_id))
    if shown not in claimed_here(toplevel):
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)


def record_claimed(toplevel: str, event: str, **fields: str | int | bool) -> None:
    for task_id in claimed_here(toplevel):
        record(toplevel, event, task_id, **fields)


def print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


def actor(toplevel: str) -> str:
    """Beads の actor（着手の印の持ち主）は作業ツリーの名前。"""
    return os.path.basename(toplevel)


def bd_task_id(task_id: str) -> str:
    """`T-123`・`GH-5`・`PROJ-123`・Beads の ID（仮の `gh-new-…`・取り込んだままの `gh-1790…-1-4dfc`）を受ける。"""
    if not re.fullmatch(r"(?i:t|gh)-[0-9a-z.-]+|(?i:[a-z][a-z0-9_]+)-\d+", task_id):
        print(f"usage: {task_id!r} が T-999・GH-5・PROJ-123 の形式でない", file=sys.stderr)
        raise SystemExit(2)
    return beads.to_bd_id(task_id)


@dataclass(frozen=True)
class BeadsSnapshot:
    tasks: dict  # T-xxx → taskfile.Task
    invalid: dict  # 見せる ID → 理由
    triage: list  # 振り分け前の beads.Issue（番号・difficulty・loopable が無い）
    issues: dict  # 見せる ID → beads.Issue
    claims: set  # in_progress の見せる ID


def beads_snapshot(toplevel: str) -> BeadsSnapshot:
    tasks: dict[str, taskfile.Task] = {}
    invalid: dict[str, str] = {}
    triage: list[beads.Issue] = []
    issues: dict[str, beads.Issue] = {}
    claims: set[str] = set()
    for issue in beads.list_issues(toplevel):
        shown = beads.to_task_id(issue.bd_id)
        issues[shown] = issue
        task, err = beads.to_task(issue)
        if err is not None:
            invalid[shown] = err
            continue
        if task is None:
            triage.append(issue)
            continue
        tasks[shown] = task
        if issue.status == "in_progress":
            claims.add(shown)
    return BeadsSnapshot(tasks, invalid, triage, issues, claims)

