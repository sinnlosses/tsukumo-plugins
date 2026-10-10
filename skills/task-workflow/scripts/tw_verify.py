"""`tw verify`・`verify-check`（検証コマンドが通った中身の控え）。"""

from __future__ import annotations

import subprocess
import time

import beads
import fold
import layout
import ledger
import ship
import taskfile
import tw_base
import tw_plan


VERIFY_TAIL_LINES = 40


def cmd_verify(toplevel: str) -> None:
    verify_command = ship.read_stamp_command(toplevel)
    if verify_command is None:
        print("NOTHING\t(検証コマンドが無い)")
        return
    unplanned = _unplanned_work(toplevel)
    if unplanned:
        ledger.clear_verify_stamp(cwd=toplevel)
        for shown in unplanned:
            print(
                f"PLAN_MISSING\t{shown}\t検証コマンドを打っていない。"
                f"先に ## やること を書いて tw edit {shown} --after-work --body-file - で渡し、打ち直す"
            )
        raise SystemExit(10)
    base = ledger.base_branch(toplevel)
    if fold.can_fold(toplevel, base):
        ledger.clear_verify_stamp(cwd=toplevel)
        ledger.require_git_writable(toplevel)
    folded = fold.fold_base(toplevel, base)
    if folded.kind == "CONFLICT":
        ledger.clear_verify_stamp(cwd=toplevel)
        print("CONFLICT\t" + (",".join(folded.conflict_files) or "?"))
        raise SystemExit(7)
    folded_line = ""
    if folded.kind == "FOLDED":
        folded_line = f"FOLDED\t{folded.old_base}..{folded.new_base}"
        print(folded_line)
    log_path = ledger.verify_log_path(cwd=toplevel)
    format_command = layout.read_config(toplevel).format
    started = time.monotonic()
    open(log_path, "w", encoding="utf-8").close()
    if format_command is not None:
        with open(log_path, "a", encoding="utf-8") as log:
            formatted = subprocess.run(["sh", "-c", format_command], cwd=toplevel, stdout=log, stderr=subprocess.STDOUT)
        if formatted.returncode != 0:
            ledger.clear_verify_stamp(cwd=toplevel)
            with open(log_path, encoding="utf-8", errors="replace") as f:
                format_tail = "\n".join(f.read().splitlines()[-VERIFY_TAIL_LINES:])
            tw_base.record_claimed(toplevel, "verify", result="FORMAT_FAILED", seconds=round(time.monotonic() - started))
            _print_verify_end(folded_line, f"FORMAT_FAILED\t{log_path}", format_tail)
            raise SystemExit(10)
    before = ledger.content_key(verify_command, cwd=toplevel)
    with open(log_path, "a", encoding="utf-8") as log:
        r = subprocess.run(["sh", "-c", verify_command], cwd=toplevel, stdout=log, stderr=subprocess.STDOUT)
    with open(log_path, encoding="utf-8", errors="replace") as f:
        tail = "\n".join(f.read().splitlines()[-VERIFY_TAIL_LINES:])
    if r.returncode != 0:
        ledger.clear_verify_stamp(cwd=toplevel)
        tw_base.record_claimed(toplevel, "verify", result="VERIFY_NOT_PASSED", seconds=round(time.monotonic() - started))
        _print_verify_end(folded_line, f"VERIFY_NOT_PASSED\t{ledger.keep_failed_log(log_path)}", tail)
        raise SystemExit(10)
    if ledger.content_key(verify_command, cwd=toplevel) != before:
        ledger.clear_verify_stamp(cwd=toplevel)
        verdict = f"VERIFIED_UNSTAMPED\t{log_path}"
    else:
        ledger.write_verify_stamp(before, cwd=toplevel)
        verdict = f"VERIFIED\t{before.tree}\t{log_path}"
    tw_base.record_claimed(toplevel, "verify", result=verdict.split("\t")[0], seconds=round(time.monotonic() - started))
    _print_verify_end(folded_line, verdict, tail)


def cmd_verify_check(toplevel: str) -> None:
    verify_command = ship.read_stamp_command(toplevel)
    if verify_command is None:
        print("NOTHING\t(検証コマンドが無い)")
        return
    stamp = ledger.read_verify_stamp(cwd=toplevel)
    if stamp is None:
        print("NOT_VERIFIED\tnone")
        return
    base = ledger.base_branch(toplevel)
    if fold.can_fold(toplevel, base) and not (
        layout.read_config(toplevel).verify_before_ship is not None and fold.folds_cleanly(toplevel, base)
    ):
        print("NOT_VERIFIED\tbase")
        return
    now = ledger.content_key(verify_command, cwd=toplevel)
    if now == stamp:
        print(f"VERIFIED_SAME\t{now.tree}")
        return
    reason = "command" if now.verify_command != stamp.verify_command else "head" if now.head != stamp.head else "content"
    print(f"NOT_VERIFIED\t{reason}")


def _print_verify_end(folded_line: str, verdict: str, tail: str) -> None:
    print(verdict)
    print(tail)
    if folded_line:
        print(folded_line)
    print(verdict)


def _unplanned_work(toplevel: str) -> list[str]:
    """この作業ツリーが印を持つ着手中のタスクのうち、`## やること` が空のまま作業が始まっているもの。"""
    actor = tw_base.actor(toplevel)
    found: list[str] = []
    for issue in beads.list_issues(toplevel):
        if issue.status != "in_progress" or issue.assignee != actor or beads.ship_mark(issue) is not None:
            continue
        if not taskfile.is_blank(str(issue.raw.get("notes") or "")):
            continue
        metadata = issue.raw.get("metadata")
        head = metadata.get(beads.CLAIM_HEAD_KEY) if isinstance(metadata, dict) else None
        if tw_plan.plan_state(toplevel, head, "-") == tw_plan.PLAN_AFTER_WORK:
            found.append(beads.to_task_id(issue.bd_id))
    return found
