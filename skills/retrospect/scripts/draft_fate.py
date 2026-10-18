"""ドラフトの行方（直近 `WINDOW_DAYS` 日に積んだドラフトが、観点ごとに採用・却下・未決のどれになったか）を数える。
あわせて、黄1件で留めた候補（label `candidate` のドラフト）の同じ兆候と失効を引く。

出す行（節の中。先頭語で読む）:

- `WINDOW\t<N>d\t<絞りの決まり>`（いつも1行目）
- `FATE\t<観点>\t<積んだ>\t<採用>\t<却下>\t<未決>\t<絞り中|->`（観点の名前順）
- `TOTAL\t<積んだ>\t<採用>\t<却下>\t<未決>\t<絞り中の観点を・でつなぐ|なし>`
- 該当が無ければ `WINDOW` の行のあとに `EMPTY`。Beads が読めなければ `-\t<理由>` の1行だけ

ドラフトに数えるのは metadata `draft`=`Y` か `from_draft` を持つ課題で、`created_at` が期間の中のもの。
label `candidate` の課題（候補のままのものと、再発せずに閉じたもの）は数えない。上がった候補は印が外れているので数える。
採用は閉じた課題（label `cancelled` なし）、却下は label `cancelled` つきで閉じた課題、ほかは未決。
観点は `from_draft`、無ければ本文の `- 観点:` の行、どちらも無ければ `不明`。

候補の節（`print_candidates`）の行は `<先頭語>\t<ID>\t<観点>\t<SIGNAL の種類>\t<場所>\t<積んでからの日数>d`
（積んだ順。該当が無ければ `EMPTY`）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Tuple

_TASK_MODEL_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "task-model", "scripts", "core")
)
if _TASK_MODEL_SCRIPTS not in sys.path:
    sys.path.insert(0, _TASK_MODEL_SCRIPTS)

import beads  # noqa: E402
import taskfile  # noqa: E402

WINDOW_DAYS = 28
THROTTLE_MIN_DROPPED = 3
SECTION_TITLE = f"ドラフトの行方（直近{WINDOW_DAYS}日）"
THROTTLED = "絞り中"
RECUR_SECTION_TITLE = f"同じ兆候の候補（直近{beads.CANDIDATE_WINDOW_DAYS}日）"
EXPIRED_SECTION_TITLE = f"再発しなかった候補（{beads.CANDIDATE_WINDOW_DAYS}日より前）"

# `(ID, (観点, SIGNAL の種類, 場所), 積んでからの日数)`
CandidateRow = Tuple[str, Tuple[str, str, str], int]


@dataclass(frozen=True)
class Row:
    viewpoint: str
    stacked: int
    adopted: int
    dropped: int
    pending: int

    @property
    def throttled(self) -> bool:
        return self.dropped >= THROTTLE_MIN_DROPPED and self.dropped > self.adopted


def tally(issues: list[beads.Issue], now: datetime) -> list[Row]:
    since = now - timedelta(days=WINDOW_DAYS)
    counts: dict[str, list[int]] = {}
    for issue in issues:
        metadata = issue.raw.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        origin = metadata.get(beads.FROM_DRAFT_KEY)
        if not origin and str(metadata.get(beads.DRAFT_KEY) or "") != beads.DRAFT_ON:
            continue
        if beads.CANDIDATE_LABEL in issue.labels:
            continue
        created = beads.parse_time(issue.raw.get("created_at"))
        if created is None or created < since:
            continue
        viewpoint = str(origin or "") or taskfile.draft_viewpoint(str(issue.raw.get("description") or "")) or beads.FROM_DRAFT_UNKNOWN
        row = counts.setdefault(viewpoint, [0, 0, 0])
        if issue.status != "closed":
            row[2] += 1
        elif beads.CANCELLED_LABEL in issue.labels:
            row[1] += 1
        else:
            row[0] += 1
    return [
        Row(v, adopted + dropped + pending, adopted, dropped, pending)
        for v, (adopted, dropped, pending) in sorted(counts.items())
    ]


def _rows(found: list[beads.Candidate], now: datetime) -> list[CandidateRow]:
    return [(beads.to_task_id(c.issue.bd_id), c.key, (now - c.created).days) for c in found]


def recurring_candidates(issues: list[beads.Issue], signals: set[str], now: datetime) -> list[CandidateRow]:
    """期間の中の候補のうち、SIGNAL の種類が `signals` にあるもの（`tw new --candidate` で上がりうる）。"""
    return _rows([c for c in beads.candidates(issues, now) if c.recent and c.key[1] in signals], now)


def expired_candidates(issues: list[beads.Issue], now: datetime) -> list[CandidateRow]:
    """期間より前に積んで、再発せずに候補のまま残ったもの（週ごとの振り返りが閉じる）。"""
    return _rows([c for c in beads.candidates(issues, now) if not c.recent], now)


def _read_issues(root: str) -> list[beads.Issue] | None:
    """Beads の課題。読めなければ理由の行を出して `None`。"""
    try:
        if not beads.is_initialized(root):
            print("-\tBeads が無い（この節は飛ばす）")
            return None
        return beads.list_issues(root)
    except (beads.BeadsError, OSError) as e:
        print(f"-\tBeads が読めない（{e}）")
        return None


def print_candidates(
    root: str, title: str, head: str, select: Callable[[list[beads.Issue]], list[CandidateRow]]
) -> None:
    print()
    print(f"===== {title} =====")
    issues = _read_issues(root)
    if issues is None:
        return
    rows = select(issues)
    if not rows:
        print("EMPTY")
        return
    for tid, (viewpoint, signal, place), days in rows:
        print(f"{head}\t{tid}\t{viewpoint}\t{signal}\t{place}\t{days}d")


def print_section(root: str, now: datetime) -> None:
    print()
    print(f"===== {SECTION_TITLE} =====")
    issues = _read_issues(root)
    if issues is None:
        return
    print(f"WINDOW\t{WINDOW_DAYS}d\t却下>={THROTTLE_MIN_DROPPED}かつ却下>採用で{THROTTLED}")
    rows = tally(issues, now)
    if not rows:
        print("EMPTY")
        return
    for r in rows:
        print(f"FATE\t{r.viewpoint}\t{r.stacked}\t{r.adopted}\t{r.dropped}\t{r.pending}\t{THROTTLED if r.throttled else '-'}")
    throttled = "・".join(r.viewpoint for r in rows if r.throttled) or "なし"
    print(
        "TOTAL\t"
        + "\t".join(str(sum(getattr(r, k) for r in rows)) for k in ("stacked", "adopted", "dropped", "pending"))
        + f"\t{throttled}"
    )
