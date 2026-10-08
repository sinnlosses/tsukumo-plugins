"""`tw metrics`: 台帳の `flow/` の記録（`ledger.record_event`）から、期間ごとの流れの数を出す。

出力は TSV で、行頭が数の名前、2列目が今の期間、3列目が前の同じ長さの期間。
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import ledger

DAYS_DEFAULT = 7
VERIFY_FAILED_RESULTS = ("FORMAT_FAILED", "VERIFY_NOT_PASSED")
HIGHER_IS_WORSE = (
    "lead_median_seconds",
    "lead_max_seconds",
    "verify_per_task",
    "verify_failed",
    "ship_verify_failed",
    "reclaim",
)
LOWER_IS_WORSE = ("shipped", "reflection_none_ratio")
STAGE_ORDER = ("計画", "直し", "委譲", "検証", "受け入れ", "レビュー", "振り返り", "送り出し")
ROUTES = ("direct", "normal")
LAP_STAGE_NAMES = {"direct": "直し", "delegate": "委譲", "accept": "受け入れ", "review": "レビュー", "retro": "振り返り"}
KIND_STAGE_NAMES = {"claim": "計画", "step": "委譲", "verify": "検証", "done": "送り出し"}


@dataclass(frozen=True, eq=False)
class Event:
    at: datetime
    kind: str
    task: str
    fields: dict


def _parse_row(line: str) -> Event | None:
    try:
        row = json.loads(line)
        at = datetime.fromisoformat(row["t"])
        if at.tzinfo is None:
            return None
        return Event(at, str(row["event"]), str(row["task"]), row)
    except (ValueError, KeyError, TypeError):
        return None


def read_events(root: str) -> tuple[list[Event], int]:
    """`(出来事を時刻順に, 読み飛ばした壊れた行の数)`。"""
    d = ledger.flow_dir(root)
    if not os.path.isdir(d):
        return [], 0
    events: list[Event] = []
    skipped = 0
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(d, name), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip() == "":
                    continue
                event = _parse_row(line)
                if event is None:
                    skipped += 1
                else:
                    events.append(event)
    return sorted(events, key=lambda e: e.at), skipped


def _number(value: str) -> float | None:
    """`12`・`2.0`・`50% (1/2)` の先頭の数。`-` は `None`。"""
    head = value.split("%")[0].strip()
    try:
        return float(head)
    except ValueError:
        return None


def _lead_seconds(events: list[Event], shipped: Event) -> float | None:
    claims = [e for e in events if e.kind == "claim" and e.task == shipped.task and e.at <= shipped.at]
    return (shipped.at - claims[-1].at).total_seconds() if claims else None


def _column(events: list[Event], start: datetime, end: datetime) -> dict[str, str]:
    inside = [e for e in events if start <= e.at < end]
    shipped = [e for e in inside if e.kind == "ship" and e.fields.get("result") == "SHIPPED"]
    leads = [s for s in (_lead_seconds(events, e) for e in shipped) if s is not None]
    verifies = [e for e in inside if e.kind == "verify"]
    verify_tasks = {e.task for e in verifies}
    reclaims = [
        e
        for e in inside
        if e.kind == "claim"
        and any(r.kind == "release" and r.task == e.task for r in events[: events.index(e)])
    ]
    dones = [
        e for e in inside if e.kind == "done" and not e.fields.get("dropped") and e.fields.get("reflection") != "skipped"
    ]
    clean = [e for e in dones if e.fields.get("reflection") == "none"]
    return {
        "shipped": str(len(shipped)),
        "lead_median_seconds": str(round(statistics.median(leads))) if leads else "-",
        "lead_max_seconds": str(round(max(leads))) if leads else "-",
        "verify_per_task": f"{len(verifies) / len(verify_tasks):.1f}" if verifies else "-",
        "verify_failed": str(sum(1 for e in verifies if e.fields.get("result") in VERIFY_FAILED_RESULTS)),
        "ship_verify_failed": str(sum(1 for e in inside if e.kind == "ship" and e.fields.get("result") == "VERIFY_FAILED")),
        "reclaim": str(len(reclaims)),
        "reflection_none_ratio": f"{round(100 * len(clean) / len(dones))}% ({len(clean)}/{len(dones)})" if dones else "-",
    }


def _stage_of(event: Event) -> str | None:
    if event.kind == "lap":
        return LAP_STAGE_NAMES.get(str(event.fields.get("stage")))
    return KIND_STAGE_NAMES.get(event.kind)


def _route(run: list[Event]) -> str:
    """`direct`（`lap direct` があり `lap delegate` が無い）か `normal`。"""
    stages = {e.fields.get("stage") for e in run if e.kind == "lap"}
    return "direct" if "direct" in stages and "delegate" not in stages else "normal"


def _verify_spans(run: list[Event]) -> list[tuple[datetime, datetime]]:
    """`verify` の区間を、重なりを繋いだ和集合にして時刻順に。"""
    spans = sorted(
        (e.at - timedelta(seconds=e.fields["seconds"]), e.at)
        for e in run
        if e.kind == "verify" and isinstance(e.fields.get("seconds"), (int, float))
    )
    merged: list[tuple[datetime, datetime]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _overlap_seconds(spans: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> float:
    return sum(max(0.0, (min(end, e) - max(start, s)).total_seconds()) for s, e in spans)


def stage_durations(events: list[Event], days: int) -> dict[tuple[str, str, str], list[float]]:
    """直近 days 日に送り出したタスクごとに、`(段, difficulty, 道)` → 所要秒の並び。

    `verify` は記録された所要秒で、区間は `[記録の時刻 − 所要秒, 記録の時刻]`。ほかの段の所要時間は、
    その出来事から同じタスクの次の `verify` でない出来事までの差から、`verify` の区間と重なった秒を引いたもの。
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    durations: dict[tuple[str, str, str], list[float]] = {}
    shipped = [e for e in events if e.kind == "ship" and e.fields.get("result") == "SHIPPED" and start <= e.at <= now]
    for ship in shipped:
        own = [e for e in events if e.task == ship.task and e.at <= ship.at]
        claims = [i for i, e in enumerate(own) if e.kind == "claim"]
        if not claims:
            continue
        run = [e for e in own[claims[-1] :] if e.kind != "ship" or e is ship]
        route = _route(run)
        verifying = _verify_spans(run)
        for i, e in enumerate(run[:-1]):
            stage = _stage_of(e)
            if stage is None:
                continue
            if e.kind == "verify":
                seconds = e.fields.get("seconds")
                if not isinstance(seconds, (int, float)):
                    continue
            else:
                end = next(n.at for n in run[i + 1 :] if n.kind != "verify")
                seconds = (end - e.at).total_seconds() - _overlap_seconds(verifying, e.at, end)
            durations.setdefault((stage, str(e.fields.get("difficulty", "?")), route), []).append(float(seconds))
    return durations


def stage_lines(events: list[Event], days: int) -> list[str]:
    durations = stage_durations(events, days)
    return [
        f"STAGE\t{stage}\t{difficulty}\t{route}\t{len(values)}\t{round(statistics.median(values))}\t{round(max(values))}"
        for (stage, difficulty, route), values in sorted(
            durations.items(), key=lambda kv: (STAGE_ORDER.index(kv[0][0]), kv[0][1], ROUTES.index(kv[0][2]))
        )
    ]


def columns(events: list[Event], days: int) -> tuple[dict[str, str], dict[str, str]]:
    """`(直近 days 日の数, その前の同じ長さの期間の数)`。"""
    now = datetime.now(timezone.utc)
    span = timedelta(days=days)
    return _column(events, now - span, now + timedelta(seconds=1)), _column(events, now - 2 * span, now - span)


def worse(name: str, current: str, previous: str) -> bool:
    """前の期間より悪くなったか。どちらかが `-` なら比べない。"""
    now, before = _number(current), _number(previous)
    if now is None or before is None:
        return False
    if name in LOWER_IS_WORSE:
        return now < before
    return name in HIGHER_IS_WORSE and now > before


def _read_for_days(toplevel: str, days: int) -> tuple[list[Event], int]:
    if days < 1:
        print("usage: --days は1以上", file=sys.stderr)
        raise SystemExit(2)
    return read_events(ledger.ledger_root(cwd=toplevel))


def cmd_metrics(toplevel: str, days: int) -> None:
    events, skipped = _read_for_days(toplevel, days)
    if not events:
        print("EMPTY")
        if skipped:
            print(f"SKIPPED\t{skipped}")
        return
    current, previous = columns(events, days)
    print(f"PERIOD\t{days}d\tcurrent\tprevious")
    for name, value in current.items():
        print(f"{name}\t{value}\t{previous[name]}")
    if skipped:
        print(f"SKIPPED\t{skipped}")


def cmd_metrics_stages(toplevel: str, days: int) -> None:
    events, skipped = _read_for_days(toplevel, days)
    print("\n".join(stage_lines(events, days)) or "EMPTY")
    if skipped:
        print(f"SKIPPED\t{skipped}")
