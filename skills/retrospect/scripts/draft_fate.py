"""ドラフトの行方（直近 `WINDOW_DAYS` 日に積んだドラフトが、観点ごとに採用・却下・未決のどれになったか）を数える。

出す行（節の中。先頭語で読む）:

- `WINDOW\t<N>d\t<絞りの決まり>`（いつも1行目）
- `FATE\t<観点>\t<積んだ>\t<採用>\t<却下>\t<未決>\t<絞り中|->`（観点の名前順）
- `TOTAL\t<積んだ>\t<採用>\t<却下>\t<未決>\t<絞り中の観点を・でつなぐ|なし>`
- 該当が無ければ `WINDOW` の行のあとに `EMPTY`。Beads が読めなければ `-\t<理由>` の1行だけ

ドラフトに数えるのは metadata `draft`=`Y` か `from_draft` を持つ課題で、`created_at` が期間の中のもの。
採用は閉じた課題（label `cancelled` なし）、却下は label `cancelled` つきで閉じた課題、ほかは未決。
観点は `from_draft`、無ければ本文の `- 観点:` の行、どちらも無ければ `不明`。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

_TASK_WORKFLOW_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "task-workflow", "scripts")
)
if _TASK_WORKFLOW_SCRIPTS not in sys.path:
    sys.path.insert(0, _TASK_WORKFLOW_SCRIPTS)

import beads  # noqa: E402
import taskfile  # noqa: E402

WINDOW_DAYS = 28
THROTTLE_MIN_DROPPED = 3
SECTION_TITLE = f"ドラフトの行方（直近{WINDOW_DAYS}日）"
THROTTLED = "絞り中"


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


def print_section(root: str, now: datetime) -> None:
    print()
    print(f"===== {SECTION_TITLE} =====")
    try:
        if not beads.is_initialized(root):
            print("-\tBeads が無い（この節は飛ばす）")
            return
        issues = beads.list_issues(root)
    except (beads.BeadsError, OSError) as e:
        print(f"-\tBeads が読めない（{e}）")
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
