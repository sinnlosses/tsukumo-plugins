"""`tw hold`（判断待ちのタスクを着手から外して `hold` にし、`/loop` が続けられるかを出す）。"""

from __future__ import annotations

import sys

import beads
import taskfile
import tracker
import tw_base
import tw_claim


def cmd_hold(toplevel: str, task_id: str, reason_path: str) -> None:
    """自分の着手中なら印を外し、`hold` にして `## 注意` の最後に `reason_path` の論点を足す。

    出力の最後は `HELD\\tT-xxx` と、取り直した一覧での `CONTINUE\\tY|N\\t<READY かつ loopable: Y の件数>`。
    """
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    reason = tw_base.read_body(reason_path).strip()
    error = "--reason-file の中身が空（空白だけ）" if not reason else taskfile.check_section_content(reason)
    if error is not None:
        print(f"usage: {error}。書き換えない", file=sys.stderr)
        raise SystemExit(2)
    issue = beads.show(toplevel, bd_id)
    if issue is None:
        print(f"NOT_READY\t{shown}\t存在しない")
        raise SystemExit(4)
    if beads.is_draft(issue):
        print(f"NOT_READY\t{shown}\tDRAFT")
        raise SystemExit(4)
    if issue.status not in ("open", "in_progress", *beads.HOLD_STATUSES):
        print(f"NOT_READY\t{shown}\t{issue.status}")
        raise SystemExit(4)
    if issue.status == "in_progress":
        tw_claim.cmd_release(toplevel, shown, False)
    _write_hold(toplevel, bd_id, shown, reason)
    print(f"HELD\t{shown}")
    ready = _ready_loopable(toplevel, shown)
    print(f"CONTINUE\t{'Y' if ready else 'N'}\t{ready}")


def _write_hold(toplevel: str, bd_id: str, shown: str, reason: str) -> None:
    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None:
        print(f"NOT_READY\t{shown}\t存在しない")
        raise SystemExit(4)
    current = beads.compose_body(
        str(issue.raw.get("description") or ""),
        str(issue.raw.get("acceptance_criteria") or ""),
        str(issue.raw.get("notes") or ""),
        None,
    )
    caution = taskfile.section_content(current, taskfile.CAUTION_HEADING)
    added = reason if taskfile.is_blank(caution) else f"{caution}\n{reason}"
    body = taskfile.replace_section(current, taskfile.CAUTION_HEADING, added)
    if body is None:
        print(f"usage: いまの本文に {taskfile.CAUTION_HEADING} が無い。書き換えない", file=sys.stderr)
        raise SystemExit(2)
    parts = beads.split_body(body)
    cmd = ["update", bd_id, "--body-file", "-", "--acceptance", parts.acceptance, "--notes", parts.notes]
    if issue.status not in beads.HOLD_STATUSES:
        cmd += ["--status", beads.HOLD_STATUS]
    r = beads.run(toplevel, cmd, tw_base.actor(toplevel), parts.description)
    if r.returncode != 0:
        raise beads.BeadsError(f"bd update が失敗: {(r.stderr or r.stdout).strip()}")
    tw_base.print_lines(pulled + trk.after([bd_id]))


def _ready_loopable(toplevel: str, shown: str) -> int:
    snap = tw_base.beads_snapshot(toplevel)
    return sum(
        1
        for tid, t in snap.tasks.items()
        if tid != shown
        and t.status == "todo"
        and t.loopable == "Y"
        and tw_base.readiness(t, snap.tasks, snap.claims) == "READY"
    )
