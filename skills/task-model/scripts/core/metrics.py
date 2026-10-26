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
VERIFY_PASSED_RESULTS = ("VERIFIED", "VERIFIED_UNSTAMPED")
HIGHER_IS_WORSE = (
    "lead_median_seconds",
    "lead_max_seconds",
    "verify_per_task",
    "verify_failed",
    "verify_real",
    "verify_flaky",
    "verify_infra",
    "verify_unobserved",
    "verify_blocked",
    "ship_verify_failed",
    "reclaim",
    "fix_within_7d",
    "long_delegation",
    "long_handback",
    "escalate_suggest",
)
OPEN_LIMIT = 5
FIX_WINDOW = timedelta(days=7)
LOWER_IS_WORSE = ("shipped", "reflection_none_ratio")
STAGE_ORDER = ("計画", "直し", "委譲", "検証", "受け入れ", "レビュー", "振り返り", "合流", "送り出し", "反映")
ROUTES = ("direct", "normal", "split", "escalated")
LAP_STAGE_NAMES = {"direct": "直し", "delegate": "委譲", "accept": "受け入れ", "review": "レビュー", "retro": "振り返り", "land": "合流", "sync": "反映"}
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


def leading_number(value: str) -> float | None:
    """`12`・`2.0`・`50% (1/2)` の先頭の数。`-` は `None`。"""
    head = value.split("%")[0].strip()
    try:
        return float(head)
    except ValueError:
        return None


def lead_seconds(events: list[Event], shipped: Event) -> float | None:
    claims = [e for e in events if e.kind == "claim" and e.task == shipped.task and e.at <= shipped.at]
    return (shipped.at - claims[-1].at).total_seconds() if claims else None


def shipped_run(events: list[Event], ship: Event) -> list[Event] | None:
    """`ship` までの、そのタスクの最後の `claim` 以降の出来事（`ship` を含む）。`claim` が無ければ `None`。"""
    own = [e for e in events if e.task == ship.task and e.at <= ship.at]
    claims = [i for i, e in enumerate(own) if e.kind == "claim"]
    if not claims:
        return None
    return [e for e in own[claims[-1] :] if e.kind != "ship" or e is ship]


def fix_tasks(events: list[Event], shipped: list[Event]) -> set[str]:
    """`shipped` のどれかを、送り出しから7日以内に `fixes` で結んだ直しのタスク。1本の直しは複数の元を指しても1本。"""
    return {
        n.task
        for n in events
        if n.kind == "new"
        for s in shipped
        if s.task in str(n.fields.get("fixes", "")).split(",") and s.at <= n.at <= s.at + FIX_WINDOW
    }


def verify_cause(events: list[Event], failed: Event) -> str:
    """落ちた `verify` の内訳（`real`・`flaky`・`infra`）。書かれた `cause` が `infra` ならそれ、
    同じタスクの次の `claim` までに同じ `key` で通った回があれば `flaky`、ほかは `real`。"""
    if failed.fields.get("cause") == "infra":
        return "infra"
    key = failed.fields.get("key")
    if not key:
        return "real"
    for later in events[events.index(failed) + 1 :]:
        if later.task != failed.task:
            continue
        if later.kind == "claim":
            break
        if later.kind == "verify" and later.fields.get("key") == key and later.fields.get("result") in VERIFY_PASSED_RESULTS:
            return "flaky"
    return "real"


def flaky_tests(events: list[Event], start: datetime, end: datetime) -> dict[str, int]:
    """`[start, end)` に `flaky` と数えた落ちた `verify` の `tests` に載るテスト名ごとの回数（多い順）。"""
    counts: dict[str, int] = {}
    for e in events:
        if e.kind != "verify" or not start <= e.at < end or e.fields.get("result") not in VERIFY_FAILED_RESULTS:
            continue
        tests = e.fields.get("tests")
        if isinstance(tests, dict) and verify_cause(events, e) == "flaky":
            for name in tests:
                counts[str(name)] = counts.get(str(name), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _has_stage_value(verify: Event, value: str) -> bool:
    """`verify` の記録の `stages`（段名→4値）に `value` の段があるか。欄の無い旧い行・形の違う欄は偽。"""
    stages = verify.fields.get("stages")
    return isinstance(stages, dict) and value in stages.values()


def column_between(events: list[Event], start: datetime, end: datetime) -> dict[str, str]:
    """`[start, end)` の流れの数（`tw metrics` の1列）。"""
    inside = [e for e in events if start <= e.at < end]
    shipped = [e for e in inside if e.kind == "ship" and e.fields.get("result") == "SHIPPED"]
    leads = [s for s in (lead_seconds(events, e) for e in shipped) if s is not None]
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
    fixes = fix_tasks(events, shipped)
    causes = [verify_cause(events, e) for e in verifies if e.fields.get("result") in VERIFY_FAILED_RESULTS]
    return {
        "shipped": str(len(shipped)),
        "lead_median_seconds": str(round(statistics.median(leads))) if leads else "-",
        "lead_max_seconds": str(round(max(leads))) if leads else "-",
        "verify_per_task": f"{len(verifies) / len(verify_tasks):.1f}" if verifies else "-",
        "verify_failed": str(sum(1 for e in verifies if e.fields.get("result") in VERIFY_FAILED_RESULTS)),
        "verify_real": str(causes.count("real")),
        "verify_flaky": str(causes.count("flaky")),
        "verify_infra": str(causes.count("infra")),
        "verify_unobserved": str(sum(1 for e in verifies if _has_stage_value(e, "unobserved"))),
        "verify_blocked": str(sum(1 for e in verifies if _has_stage_value(e, "blocked"))),
        "ship_verify_failed": str(sum(1 for e in inside if e.kind == "ship" and e.fields.get("result") == "VERIFY_FAILED")),
        "reclaim": str(len(reclaims)),
        "reflection_none_ratio": f"{round(100 * len(clean) / len(dones))}% ({len(clean)}/{len(dones)})" if dones else "-",
        "fix_within_7d": f"{round(100 * len(fixes) / len(shipped))}% ({len(fixes)}/{len(shipped)})" if shipped else "-",
        "long_delegation": str(sum(1 for e in inside if e.kind == "long-delegation")),
        "long_handback": str(sum(1 for e in inside if e.kind == "long-handback")),
        "escalate_suggest": str(sum(1 for e in inside if e.kind == "escalate-suggest")),
    }


def _stage_of(event: Event) -> str | None:
    if event.kind == "lap":
        return LAP_STAGE_NAMES.get(str(event.fields.get("stage")))
    return KIND_STAGE_NAMES.get(event.kind)


def route_of(run: list[Event]) -> str:
    """`direct`（`lap direct` があり `lap delegate` が無い）・`split`（`model` 欄を持つ最初の `lap delegate` のモデルが
    `difficulty` と違う）・`escalated`（`split` のあとに `model` が `difficulty` と同じ `lap delegate` がある）・`normal`。"""
    modeled = [e for e in run if e.kind == "lap" and e.fields.get("stage") == "delegate" and "model" in e.fields]
    if modeled and modeled[0].fields["model"] != modeled[0].fields.get("difficulty"):
        return "escalated" if any(e.fields["model"] == e.fields.get("difficulty") for e in modeled[1:]) else "split"
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


def _closing_index(run: list[Event], start: Event, after: int) -> int | None:
    """`start` と同じ段の、`run[after:]` にある最初の `edge: end` の `lap` の位置。"""
    return next(
        (
            j
            for j in range(after, len(run))
            if run[j].kind == "lap" and run[j].fields.get("edge") == "end" and run[j].fields.get("stage") == start.fields.get("stage")
        ),
        None,
    )


def _since_last_claim(events: list[Event], task_id: str) -> list[Event]:
    """そのタスクの最後の `claim` 以降の出来事。`claim` が無ければ空。"""
    own = [e for e in events if e.task == task_id]
    claims = [i for i, e in enumerate(own) if e.kind == "claim"]
    return own[claims[-1] :] if claims else []


def last_lap_stage(events: list[Event], task_id: str) -> str | None:
    """そのタスクの最後の `claim` 以降の最後の `lap` の段。`lap` が無ければ `None`。"""
    stages = [e.fields.get("stage") for e in _since_last_claim(events, task_id) if e.kind == "lap"]
    return str(stages[-1]) if stages and stages[-1] else None


def last_edge(events: list[Event], task_id: str, stage: str) -> str | None:
    """そのタスクの最後の `claim` 以降で、段 `stage` の `edge` 付きの最後の `lap` の `edge`。無ければ `None`。"""
    edges = [
        e.fields.get("edge")
        for e in _since_last_claim(events, task_id)
        if e.kind == "lap" and e.fields.get("stage") == stage and e.fields.get("edge") in ("start", "end")
    ]
    return str(edges[-1]) if edges else None


def open_stages(events: list[Event], task_id: str) -> list[str]:
    """そのタスクの最後の `claim` 以降で、`edge: start` の `lap` に `edge: end` が続かない段（出現順、重複なし）。"""
    run = _since_last_claim(events, task_id)
    stages: list[str] = []
    for i, e in enumerate(run):
        if e.kind != "lap" or e.fields.get("edge") != "start":
            continue
        stage = str(e.fields.get("stage"))
        if _closing_index(run, e, i + 1) is None and stage not in stages:
            stages.append(stage)
    return stages


def _run_spans(run: list[Event]) -> tuple[list[tuple[str, str, datetime, datetime, float]], list[str]]:
    """`run`（1回の着手以降の出来事）から `(段, difficulty, 始め, 終わり, 所要秒)` の並びと、終わりの無い `start` の段名の並びを拾う。

    最後の出来事は次の出来事が無いので拾わない。同じ段の前の `start` がまだ閉じていない `start`（手打ちと hook が重ねた lap）は数えない。
    """
    spans: list[tuple[str, str, datetime, datetime, float]] = []
    opens: list[str] = []
    verifying = _verify_spans(run)
    covered: dict[str, int | None] = {}
    for i, e in enumerate(run[:-1]):
        stage = _stage_of(e)
        if stage is None:
            continue
        edge = e.fields.get("edge") if e.kind == "lap" else None
        if edge == "end":
            continue
        if edge == "start":
            lap_stage = str(e.fields.get("stage"))
            if lap_stage in covered:
                until = covered[lap_stage]
                if until is None or until > i:
                    continue
            closing_at = _closing_index(run, e, i + 1)
            covered[lap_stage] = closing_at
            if closing_at is None:
                opens.append(stage)
                continue
            begin, end = e.at, run[closing_at].at
            seconds = (end - begin).total_seconds()
        elif e.kind == "verify":
            recorded = e.fields.get("seconds")
            if not isinstance(recorded, (int, float)):
                continue
            begin, end = e.at - timedelta(seconds=recorded), e.at
            seconds = recorded
        else:
            begin = e.at
            following = next((n.at for n in run[i + 1 :] if n.kind != "verify"), None)
            if following is None:
                continue
            end = following
            seconds = (end - begin).total_seconds() - _overlap_seconds(verifying, begin, end)
        spans.append((stage, str(e.fields.get("difficulty", "?")), begin, end, float(seconds)))
    return spans, opens


def task_stage_spans(
    events: list[Event], task_id: str
) -> tuple[list[tuple[str, datetime, datetime]], list[str], datetime, datetime] | None:
    """そのタスクの最後の `claim` 以降の `(段, 始め, 終わり)` の並び・終わりの無い段名の並び・着手の時刻・最後の出来事の時刻。`claim` が無ければ `None`。

    `edge` の対でない区間は `stage_durations` と同じく `verify` の区間を除いた断片に割る。同じ段の区間は重なりうる。
    `ship` 前でも使える。最後の出来事の所要は拾わない。
    """
    run = _since_last_claim(events, task_id)
    if not run:
        return None
    spans, opens = _run_spans(run)
    verifying = _verify_spans(run)
    pieces: list[tuple[str, datetime, datetime]] = []
    for stage, _difficulty, begin, end, seconds in spans:
        if seconds == (end - begin).total_seconds():
            pieces.append((stage, begin, end))
            continue
        cursor = begin
        for v_start, v_end in verifying:
            if v_end <= cursor or v_start >= end:
                continue
            if v_start > cursor:
                pieces.append((stage, cursor, v_start))
            cursor = max(cursor, v_end)
        if cursor < end:
            pieces.append((stage, cursor, end))
    return pieces, opens, run[0].at, run[-1].at


def stage_durations(
    events: list[Event], days: int
) -> tuple[dict[tuple[str, str, str], list[float]], dict[str, int]]:
    """`((段, difficulty, 道) → 所要秒の並び, 段 → 終わりの無い start の数)`。対象は直近 days 日に `ship` が `SHIPPED` か `NOTHING` のタスク。

    `verify` は記録された所要秒で、区間は `[記録の時刻 − 所要秒, 記録の時刻]`。`edge: start` の `lap` は同じ段の `edge: end` までの差。
    ほかの段の所要時間は、その出来事から同じタスクの次の `verify` でない出来事までの差から、`verify` の区間と重なった秒を引いたもの。
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    durations: dict[tuple[str, str, str], list[float]] = {}
    opens: dict[str, int] = {}
    shipped = [
        e
        for e in events
        if e.kind == "ship" and e.fields.get("result") in ("SHIPPED", "NOTHING") and start <= e.at <= now
    ]
    for ship in shipped:
        run = shipped_run(events, ship)
        if run is None:
            continue
        route = route_of(run)
        spans, run_opens = _run_spans(run)
        for stage, difficulty, _begin, _end, seconds in spans:
            durations.setdefault((stage, difficulty, route), []).append(seconds)
        for stage in run_opens:
            opens[stage] = opens.get(stage, 0) + 1
    return durations, opens


def stage_lines(events: list[Event], days: int) -> list[str]:
    durations, opens = stage_durations(events, days)
    stage_rows = [
        f"STAGE\t{stage}\t{difficulty}\t{route}\t{len(values)}\t{round(statistics.median(values))}\t{round(max(values))}"
        for (stage, difficulty, route), values in sorted(
            durations.items(), key=lambda kv: (STAGE_ORDER.index(kv[0][0]), kv[0][1], ROUTES.index(kv[0][2]))
        )
    ]
    open_rows = [f"OPEN\t{stage}\t{opens[stage]}" for stage in STAGE_ORDER if stage in opens]
    if not stage_rows and not open_rows:
        return []
    return open_total_lines(opens) + stage_rows + open_rows


def open_total_lines(opens: dict[str, int]) -> list[str]:
    """`OPEN_TOTAL` の行と、合計が `OPEN_LIMIT` を超えたときの `INTEGRITY\tWARN\t<段>=<件数>,…` の行。"""
    total = sum(opens.values())
    lines = [f"OPEN_TOTAL\t{total}"]
    if total > OPEN_LIMIT:
        lines.append("INTEGRITY\tWARN\t" + ",".join(f"{s}={opens[s]}" for s in STAGE_ORDER if s in opens))
    return lines


def columns(events: list[Event], days: int) -> tuple[dict[str, str], dict[str, str]]:
    """`(直近 days 日の数, その前の同じ長さの期間の数)`。"""
    now = datetime.now(timezone.utc)
    span = timedelta(days=days)
    return column_between(events, now - span, now + timedelta(seconds=1)), column_between(events, now - 2 * span, now - span)


def worse(name: str, current: str, previous: str) -> bool:
    """前の期間より悪くなったか。どちらかが `-` なら比べない。"""
    now, before = leading_number(current), leading_number(previous)
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
