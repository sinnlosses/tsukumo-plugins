"""横断の振り返りの記録（`layout.RETROSPECT_RECORD_PATH`）から、最後の日付と次が来ているか（due）を出す。

最後の日付は、主ブランチの版と作業ツリーの版の見出しの日付の遅いほう（書いたその場で due が消え、
主ブランチへ入れれば他の作業ツリーでも消える）。記録が無いあいだは、台帳の `flow/` の最も古い
出来事を起点にする（`flow/` が無ければ due にしない）。git や台帳が読めなくても例外を出さない。
"""

from __future__ import annotations

import os
import subprocess
from datetime import date, datetime, timezone

import layout
import ledger
import metrics

INTERVAL_DAYS = 7


def last_date(toplevel: str) -> date | None:
    texts = [_base_text(toplevel), _worktree_text(toplevel)]
    dates = [
        d
        for text in texts
        if text is not None
        for d in (_parse_date(m.group(1)) for m in layout.RETROSPECT_RECORD_HEADING_PATTERN.finditer(text))
        if d is not None
    ]
    return max(dates) if dates else None


def due(toplevel: str) -> tuple[str, int] | None:
    """due なら `(最後の日付 | "-", 経過日数)`、まだなら `None`。"""
    last = last_date(toplevel)
    if last is not None:
        elapsed = (date.today() - last).days
        return (last.isoformat(), elapsed) if elapsed >= INTERVAL_DAYS else None
    first = _first_flow_event(toplevel)
    if first is None:
        return None
    elapsed = (datetime.now(timezone.utc) - first).days
    return ("-", elapsed) if elapsed >= INTERVAL_DAYS else None


def _base_text(toplevel: str) -> str | None:
    try:
        base = ledger.base_branch(toplevel)
    except (ledger.GitCommandError, ledger.NoBaseBranch):
        return None
    r = subprocess.run(
        ["git", "show", f"{base}:{layout.RETROSPECT_RECORD_PATH}"], cwd=toplevel, capture_output=True, text=True
    )
    return r.stdout if r.returncode == 0 else None


def _worktree_text(toplevel: str) -> str | None:
    try:
        with open(os.path.join(toplevel, layout.RETROSPECT_RECORD_PATH), encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _first_flow_event(toplevel: str) -> datetime | None:
    try:
        events, _ = metrics.read_events(ledger.ledger_root(cwd=toplevel))
    except (ledger.GitCommandError, OSError):
        return None
    return events[0].at if events else None


def _parse_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
