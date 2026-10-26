"""`tw loop-check`: `/loop` を続けてよいかを、台帳 `flow/` の今日の出来事から決める。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from metrics import Event

HOLD_STREAK_LIMIT = 3
RECENT_SHIPS = 3
VERIFY_FAILED_LIMIT = 2
SETTLED_RESULTS = ("SHIPPED", "NOTHING")


@dataclass(frozen=True)
class Decision:
    stop: str | None  # `max_ships`・`hold_streak`・`verify_failed`。続けてよければ `None`
    count: int
    shipped: list[str]


def decide(events: list[Event], now: datetime, max_ships: int) -> Decision:
    """`now` のローカル日付の 0 時以降の出来事で、停止規則を (a)(b)(c) の順に見る。`events` は時刻順。"""
    midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    today = [e for e in events if e.at >= midnight]
    ships = [e for e in today if e.kind == "ship"]
    shipped = [e.task for e in ships if e.fields.get("result") == "SHIPPED"]
    if len(shipped) >= max_ships:
        return Decision("max_ships", len(shipped), shipped)
    streak = 0
    for e in reversed(today):
        if e.kind == "hold":
            streak += 1
        elif e.kind == "ship" and e.fields.get("result") in SETTLED_RESULTS:
            break
    if streak >= HOLD_STREAK_LIMIT:
        return Decision("hold_streak", streak, shipped)
    failed = sum(1 for e in ships[-RECENT_SHIPS:] if e.fields.get("result") == "VERIFY_FAILED")
    if failed >= VERIFY_FAILED_LIMIT:
        return Decision("verify_failed", failed, shipped)
    return Decision(None, len(shipped), shipped)
