"""委譲が長引いたとき difficulty を上げる提案の引き金を、台帳の出来事から読む（`tw escalate-check`）。

引き金は 3 つで、読むだけで何も止めない。
- `edit-churn`: 同じファイルへの書き換えが `handback-guard` の数えた回数に達した（`edit-churn` の出来事）
- `verify-failed-streak`: `verify` の落ちが `VERIFY_STREAK` 回以上続いている（`infra` の落ちは数えない）
- `slow-delegation`: 委譲の経過秒が、同じ difficulty の委譲の中央値の `SLOW_FACTOR` 倍を超えた
"""

from __future__ import annotations

import statistics
from datetime import datetime

import metrics
from metrics import Event

VERIFY_STREAK = 2
SLOW_FACTOR = 3
MIN_SAMPLES = 5
MEDIAN_DAYS = 28
NEXT_MODEL = {"haiku": "sonnet", "sonnet": "opus"}


def delegate_start(run: list[Event]) -> Event | None:
    """最後の `delegate` の `start` の `lap`。そのあとに `delegate` の `end` が付いていれば `None`。"""
    start = None
    for e in run:
        if e.kind != "lap" or e.fields.get("stage") != "delegate":
            continue
        if e.fields.get("edge") == "start":
            start = e
        elif e.fields.get("edge") == "end":
            start = None
    return start


def median_seconds(events: list[Event], difficulty: str, days: int = MEDIAN_DAYS) -> float | None:
    """直近 `days` 日の 委譲・`difficulty`・normal の所要秒の中央値。標本が `MIN_SAMPLES` 件に満たなければ `None`。"""
    values = metrics.stage_durations(events, days)[0].get(("委譲", difficulty, "normal"), [])
    return statistics.median(values) if len(values) >= MIN_SAMPLES else None


def _failed_streak(verifies: list[Event]) -> int:
    counted = [e for e in verifies if not (e.fields.get("result") in metrics.VERIFY_FAILED_RESULTS and e.fields.get("cause") == "infra")]
    streak = 0
    for e in reversed(counted):
        if e.fields.get("result") not in metrics.VERIFY_FAILED_RESULTS:
            break
        streak += 1
    return streak


def triggers(
    events: list[Event], task_id: str, now: datetime, median: float | None
) -> list[tuple[str, str, str]]:
    """`(引き金の名前, 詳細, 次のモデル)` の並び。次のモデルが無い（opus）ときは `-`。

    起点は最後の `claim` 以降で開いている `delegate`。同じ起点ですでに `escalate-suggest` を残した引き金は出さない。
    `median` は `median_seconds` の値で、委譲が `normal` の道でないときは呼び手が `None` を渡す。
    """
    run = [e for e in events if e.task == task_id]
    claims = [i for i, e in enumerate(run) if e.kind == "claim"]
    run = run[claims[-1] :] if claims else []
    start = delegate_start(run)
    if start is None:
        return []
    after = run[run.index(start) + 1 :]
    done = {str(e.fields.get("trigger")) for e in after if e.kind == "escalate-suggest"}
    next_model = NEXT_MODEL.get(str(start.fields.get("model") or start.fields.get("difficulty")), "-")
    found: list[tuple[str, str]] = []
    churn = [e for e in after if e.kind == "edit-churn"]
    if churn:
        found.append(("edit-churn", f"同じファイルを{churn[-1].fields.get('edits', '?')}回書き換えた"))
    streak = _failed_streak([e for e in after if e.kind == "verify"])
    if streak >= VERIFY_STREAK:
        found.append(("verify-failed-streak", f"verify が{streak}回続けて落ちた"))
    elapsed = (now - start.at).total_seconds()
    if median is not None and elapsed > median * SLOW_FACTOR:
        found.append(("slow-delegation", f"経過{round(elapsed)}秒が中央値{round(median)}秒の{SLOW_FACTOR}倍を超えた"))
    return [(name, detail, next_model) for name, detail in found if name not in done]


def route_is_normal(events: list[Event], task_id: str) -> bool:
    run = [e for e in events if e.task == task_id]
    claims = [i for i, e in enumerate(run) if e.kind == "claim"]
    return bool(claims) and metrics.route_of(run[claims[-1] :]) == "normal"


def suggestions(events: list[Event], task_id: str, now: datetime) -> list[tuple[str, str, str]]:
    """`triggers` に、委譲の道が normal のときだけ中央値を引いて渡す。"""
    run = [e for e in events if e.task == task_id]
    claims = [i for i, e in enumerate(run) if e.kind == "claim"]
    start = delegate_start(run[claims[-1] :]) if claims else None
    median = None
    if start is not None and route_is_normal(events, task_id):
        median = median_seconds(events, str(start.fields.get("difficulty")))
    return triggers(events, task_id, now, median)
