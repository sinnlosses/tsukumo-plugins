"""期日を過ぎた介入（課題の `## 介入`）ごとに、狙った兆候の基準線といまの値を数で比べて判定する。

介入に数えるのは、`## 介入` を持ち、閉じて label `cancelled` の無い課題。送り出しは `closed_at`、登録は `created_at`。
期日は送り出しに `- 判定の期日:` の `<数>日`（無ければ `DEFAULT_DUE_DAYS`）を足した時刻で、出すのは期日が `(今 − days, 今]` に入ったもの。
窓は基準線 `[登録 − WINDOW_DAYS, 登録)`、いま `[期日 − WINDOW_DAYS, 期日)`。

測る名前は `- 狙う兆候:` の行のバッククォートの語のうち表の名前に当たる最初の語、無ければ行頭の名前。表と取り方:

- `tw metrics` の数: 窓の `metrics.column_between` の値。分母は窓の `shipped`。件数の数（`COUNT_METRICS`）は
  窓の `shipped` で割った割合にし、割合・秒・1件あたりの数と、分母そのものの `shipped` は割らない
- 判定の口の SIGNAL の種類（`material.SIGNAL_KINDS`）: 窓の中の `gate` の出来事を持つタスク（同じタスクは最後の1件）を分母、
  その種類を含むタスクを分子にした割合
- hook の規則のキー（拒否の記録に出た `rule`）: 窓の中のその `rule` の拒否の数を、窓の `shipped` で割った割合

判定の順: 名前が決まらない → `判定保留`（`測れない兆候`）、同じ名前の別の介入が比較の範囲
`[登録 − WINDOW_DAYS, 期日)` の中で送り出された → `confounded`、どちらかの分母が `MIN_DENOMINATOR` 未満 → `判定保留`、
ほかは良い向きへの相対の変化が `EFFECT_MARGIN` 以上なら `helped`、悪い向きに以上なら `hurt`、間は `no observed effect`。

出す行（期日の順）: `INTERVENTION\t<ID>\t<名前|->\t<基準線>\t<いま>\t<判定>\t<理由|->`（値は `<値>(n=<分母>)`、測れなければ `-`）、
続けて `no observed effect` と `hurt` の介入ごとに `REVERT\t<ID>\t<観点|->\t<判定>\t<出し先の種類|->`。該当が無ければ `EMPTY`。
会話の中身は読まない。データの不備で traceback を出さない。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

_TASK_MODEL_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "task-model", "scripts", "core")
)
if _TASK_MODEL_SCRIPTS not in sys.path:
    sys.path.insert(0, _TASK_MODEL_SCRIPTS)

import beads  # noqa: E402
import draft_fate  # noqa: E402
import gitinfo  # noqa: E402
import ledger  # noqa: E402
import material  # noqa: E402
import metrics  # noqa: E402
import taskfile  # noqa: E402

WINDOW_DAYS = 28
MIN_DENOMINATOR = 10
DEFAULT_DUE_DAYS = 14
EFFECT_MARGIN = 0.2
SECTION_TITLE = "期日を過ぎた介入（期間の中に期日が来たもの）"

HELPED = "helped"
HURT = "hurt"
NO_EFFECT = "no observed effect"
CONFOUNDED = "confounded"
PENDING = "判定保留"
REVERT_VERDICTS = (NO_EFFECT, HURT)

UNMEASURABLE = "測れない兆候"
TOO_FEW = f"分母<{MIN_DENOMINATOR}"

METRIC_NAMES = metrics.HIGHER_IS_WORSE + metrics.LOWER_IS_WORSE
COUNT_METRICS = ("verify_failed", "verify_real", "verify_flaky", "verify_infra", "ship_verify_failed", "reclaim", "long_delegation", "long_handback", "escalate_suggest")


@dataclass(frozen=True)
class Denial:
    at: datetime
    rule: str


@dataclass(frozen=True)
class Measure:
    value: float
    denominator: int

    def shown(self) -> str:
        return f"{round(self.value, 3):g}(n={self.denominator})"


@dataclass(frozen=True)
class Intervention:
    task_id: str
    name: str | None
    registered: datetime
    shipped: datetime
    due: datetime
    viewpoint: str
    target: str


@dataclass(frozen=True)
class Verdict:
    task_id: str
    name: str | None
    baseline: Measure | None
    current: Measure | None
    verdict: str
    reason: str
    viewpoint: str
    target: str


def resolve_signal(line: str, rule_keys: set[str]) -> str | None:
    """`- 狙う兆候:` の値から測る名前。表に当たらなければ `None`。"""
    names = set(METRIC_NAMES) | set(material.SIGNAL_KINDS) | rule_keys
    for quoted in re.findall(r"`([^`]+)`", line):
        if quoted.strip() in names:
            return quoted.strip()
    heads = [n for n in names if line.strip().startswith(n)]
    return max(heads, key=len) if heads else None


def _due_days(line: str) -> int:
    m = re.search(r"(\d+)\s*日", line)
    return int(m.group(1)) if m else DEFAULT_DUE_DAYS


def interventions(issues: list[beads.Issue], rule_keys: set[str]) -> list[Intervention]:
    found: list[Intervention] = []
    for issue in issues:
        if issue.status != "closed" or beads.CANCELLED_LABEL in issue.labels:
            continue
        description = str(issue.raw.get("description") or "")
        fields = taskfile.intervention(description)
        registered = beads.parse_time(issue.raw.get("created_at"))
        shipped = beads.parse_time(issue.raw.get("closed_at"))
        if fields is None or registered is None or shipped is None:
            continue
        metadata = issue.raw.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        viewpoint = str(metadata.get(beads.FROM_DRAFT_KEY) or "") or taskfile.draft_viewpoint(description) or "-"
        found.append(
            Intervention(
                beads.to_task_id(issue.bd_id),
                resolve_signal(fields.get(taskfile.INTERVENTION_SIGNAL, ""), rule_keys),
                registered,
                shipped,
                shipped + timedelta(days=_due_days(fields.get(taskfile.INTERVENTION_DUE, ""))),
                viewpoint,
                fields.get(taskfile.INTERVENTION_TARGET, "") or "-",
            )
        )
    return found


def _shipped(events: list[metrics.Event], start: datetime, end: datetime) -> int:
    return sum(1 for e in events if e.kind == "ship" and e.fields.get("result") == "SHIPPED" and start <= e.at < end)


def measure(
    name: str, events: list[metrics.Event], denials: list[Denial], start: datetime, end: datetime
) -> Measure | None:
    """窓 `[start, end)` の値。値が読めなければ `None`。"""
    if name in METRIC_NAMES:
        value = metrics.leading_number(metrics.column_between(events, start, end)[name])
        shipped = _shipped(events, start, end)
        if value is None:
            return None
        if name in COUNT_METRICS:
            value = value / shipped if shipped else 0.0
        return Measure(value, shipped)
    if name in material.SIGNAL_KINDS:
        latest: dict[str, str] = {}
        for e in events:
            if e.kind == material.GATE_EVENT and start <= e.at < end:
                latest[e.task] = str(e.fields.get("signals") or "")
        hits = sum(1 for s in latest.values() if name in s.split("・"))
        return Measure(hits / len(latest) if latest else 0.0, len(latest))
    shipped = _shipped(events, start, end)
    count = sum(1 for d in denials if d.rule == name and start <= d.at < end)
    return Measure(count / shipped if shipped else 0.0, shipped)


def _lower_is_better(name: str) -> bool:
    return name not in metrics.LOWER_IS_WORSE


def compare(name: str, baseline: Measure, current: Measure) -> str:
    better = (baseline.value - current.value) if _lower_is_better(name) else (current.value - baseline.value)
    if baseline.value == 0:
        return NO_EFFECT if better == 0 else (HELPED if better > 0 else HURT)
    change = better / baseline.value
    if change >= EFFECT_MARGIN:
        return HELPED
    if change <= -EFFECT_MARGIN:
        return HURT
    return NO_EFFECT


def judge(
    issues: list[beads.Issue], events: list[metrics.Event], denials: list[Denial], now: datetime, days: int
) -> list[Verdict]:
    window = timedelta(days=WINDOW_DAYS)
    every = interventions(issues, {d.rule for d in denials})
    due = sorted((i for i in every if now - timedelta(days=days) < i.due <= now), key=lambda i: (i.due, i.task_id))
    verdicts: list[Verdict] = []
    for a in due:
        if a.name is None:
            verdicts.append(Verdict(a.task_id, None, None, None, PENDING, UNMEASURABLE, a.viewpoint, a.target))
            continue
        baseline = measure(a.name, events, denials, a.registered - window, a.registered)
        current = measure(a.name, events, denials, a.due - window, a.due)
        others = [
            b.task_id
            for b in every
            if b.task_id != a.task_id and b.name == a.name and a.registered - window <= b.shipped < a.due
        ]
        if others:
            verdict, reason = CONFOUNDED, "重なり:" + ",".join(sorted(others))
        elif baseline is None or current is None or min(baseline.denominator, current.denominator) < MIN_DENOMINATOR:
            verdict, reason = PENDING, TOO_FEW
        else:
            verdict, reason = compare(a.name, baseline, current), "-"
        verdicts.append(Verdict(a.task_id, a.name, baseline, current, verdict, reason, a.viewpoint, a.target))
    return verdicts


def read_denials(root: str) -> list[Denial]:
    """共有の git dir の hook の拒否の記録のうち、時刻と `rule` の読める行。"""
    found: list[Denial] = []
    for row in material.denial_rows(root):
        at = beads.parse_time(row.get("at"))
        if at is not None and at.tzinfo is not None and isinstance(row.get("rule"), str):
            found.append(Denial(at, row["rule"]))
    return found


def print_section(root: str, now: datetime, days: int) -> None:
    print()
    print(f"===== {SECTION_TITLE} =====")
    issues = draft_fate.read_issues(root)
    if issues is None:
        return
    try:
        events, _ = metrics.read_events(ledger.ledger_root(cwd=root))
    except (gitinfo.GitCommandError, OSError):
        events = []
    print("\n".join(section_lines(judge(issues, events, read_denials(root), now, days))))


def section_lines(verdicts: list[Verdict]) -> list[str]:
    if not verdicts:
        return ["EMPTY"]
    lines = [
        f"INTERVENTION\t{v.task_id}\t{v.name or '-'}\t{v.baseline.shown() if v.baseline else '-'}"
        f"\t{v.current.shown() if v.current else '-'}\t{v.verdict}\t{v.reason}"
        for v in verdicts
    ]
    return lines + [
        f"REVERT\t{v.task_id}\t{v.viewpoint}\t{v.verdict}\t{v.target}" for v in verdicts if v.verdict in REVERT_VERDICTS
    ]
