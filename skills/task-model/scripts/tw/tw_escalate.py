"""`tw escalate-check`（委譲が長引いたとき difficulty を上げる提案の行を出す）と `tw beat-watch`（委譲先の無音の見張り）。"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import agent_scope
import beads
import escalation
import gitinfo
import ledger
import metrics
import stamps
import tw_base

WATCH_INTERVAL_SECONDS = 5
ESCALATE_EVERY = 6


def cmd_escalate_check(toplevel: str, task_id: str) -> None:
    """引き金が新しく立った分だけ `ESCALATE_SUGGEST` の行を出し、`escalate-suggest` を台帳へ残す。

    止めない（終了コードはいつも0）。着手の印が無い・台帳が読めないときは何も出さない。
    """
    shown = beads.to_task_id(beads.to_bd_id(task_id))
    if shown not in tw_base.claimed_here(toplevel):
        return
    try:
        events = metrics.read_events(ledger.ledger_root(cwd=toplevel))[0]
    except (OSError, gitinfo.GitCommandError):
        return
    for name, detail, next_model in escalation.suggestions(events, shown, datetime.now(timezone.utc)):
        tw_base.record(toplevel, "escalate-suggest", shown, trigger=name)
        print(f"ESCALATE_SUGGEST\t{shown}\t{name}\t{detail}\t{next_model}")


def last_beat(toplevel: str, agent_id: str) -> int | None:
    """hook（`tw-hook heartbeat`）が書いた `agent_id` の最終活動の epoch 秒。無いか読めなければ `None`。"""
    path = os.path.join(stamps.worktree_state_read_dir(toplevel), ledger.AGENT_BEATS_DIR_NAME, agent_id)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def silence_line(now: float, last: float, threshold: int, shown: bool) -> tuple[str | None, bool]:
    """無音が `threshold` 秒以上で、この無音でまだ出していなければ `SILENT <n>s` を返す。

    返す2つ目は「この無音で出した」の次の状態。`last` が進んだら呼び手が `False` に戻す。
    """
    quiet = int(now - last)
    if quiet >= threshold and not shown:
        return f"SILENT {quiet}s", True
    return None, shown


def cmd_beat_watch(toplevel: str, task_id: str, agent_id: str, threshold: str) -> None:
    """委譲先の鼓動を読み、無音が `threshold` 秒以上になったら `SILENT <n>s` を1回出す。30秒ごとに `escalate-check` と同じ提案も出す。

    止めるまで回る（`Monitor` の失効か `TaskStop`）。
    """
    if agent_scope.agent_id({"agent_id": agent_id}) is None:
        print(f"usage: agent_id {agent_id!r} がファイル名に使えない", file=sys.stderr)
        raise SystemExit(2)
    if not threshold.isdigit() or int(threshold) <= 0:
        print(f"usage: 秒数 {threshold!r} が正の整数でない", file=sys.stderr)
        raise SystemExit(2)
    limit = int(threshold)
    last = time.time()
    shown = False
    count = 0
    while True:
        beat = last_beat(toplevel, agent_id)
        if beat is not None and beat > last:
            last = beat
            shown = False
        line, shown = silence_line(time.time(), last, limit, shown)
        if line is not None:
            print(line, flush=True)
        count += 1
        if count % ESCALATE_EVERY == 0:
            try:
                cmd_escalate_check(toplevel, task_id)
            except Exception:  # noqa: BLE001 見張りを提案の読みの不具合で止めない
                pass
            sys.stdout.flush()
        time.sleep(WATCH_INTERVAL_SECONDS)
