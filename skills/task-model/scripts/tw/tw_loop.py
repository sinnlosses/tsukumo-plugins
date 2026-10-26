"""`tw loop-check`（`/loop` を続けてよいかを台帳から数えて返す）。"""

from __future__ import annotations

from datetime import datetime, timezone

import gitinfo
import layout
import ledger
import loopcheck
import metrics


def cmd_loop_check(toplevel: str) -> None:
    """`CONTINUE` か `STOP\\t<理由>\\t<数>`、続けて `SHIPPED_TODAY\\t<件数>\\t<ID,… か ->`。終了コードはいつも0。"""
    try:
        events = metrics.read_events(ledger.ledger_root(cwd=toplevel))[0]
    except (OSError, gitinfo.GitCommandError):
        events = []
    limit = layout.read_config(toplevel).loop_max_ships
    decision = loopcheck.decide(events, datetime.now(timezone.utc), limit)
    print("CONTINUE" if decision.stop is None else f"STOP\t{decision.stop}\t{decision.count}")
    print(f"SHIPPED_TODAY\t{len(decision.shipped)}\t{','.join(decision.shipped) or '-'}")
