"""`tw escalate-check`（委譲が長引いたとき difficulty を上げる提案の行を出す）。"""

from __future__ import annotations

from datetime import datetime, timezone

import beads
import escalation
import gitinfo
import ledger
import metrics
import tw_base


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
