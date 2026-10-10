"""`tw status`・`config`・`show`（読むだけのサブコマンド）。"""

from __future__ import annotations

import os
import sys
import unicodedata
from typing import Callable

import beads
import cross_review
import layout
import ledger
import taskfile
import tracker
import tw_base


# WORKFLOW.md「summary」の「1行に収める」に反すると見なす幅。80桁はその一行だけで端末が
# 折り返す長さ（旧 status.py の実測: 52件中14件が該当。狭めると大半に火が点いて合図にならない）。
LONG_SUMMARY_WIDTH = 80


def display_width(s: str) -> int:
    """端末に出したときの桁数。日本語（East Asian Wide/Fullwidth）は2桁。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def cmd_status(toplevel: str, show_all: bool, check: bool) -> None:
    snap = tw_base.beads_snapshot(toplevel)
    if check:
        if snap.invalid:
            print("INVALID\t" + "; ".join(f"{k}:{v}" for k, v in snap.invalid.items()))
            raise SystemExit(3)
        print("OK")
        return

    base = ledger.base_branch(toplevel)
    worktree_names = {os.path.basename(w.path) for w in ledger.list_worktrees(cwd=toplevel)}
    labels = {tid: _classify_claim(toplevel, snap.issues[tid], worktree_names, base) for tid in snap.claims}

    def marker_of(tid: str) -> str:
        if tid not in labels:
            return "-"
        label, detail = labels[tid]
        return detail if label == "CLAIMED" else f"{label}({detail})"

    stale_entries = [
        f"{tid}:{label}({detail})" if detail else f"{tid}:{label}"
        for tid, (label, detail) in sorted(labels.items())
        if label != "CLAIMED"
    ]
    open_triage = [i for i in snap.triage if i.status != "closed"]
    triage_rows = [
        [beads.to_task_id(i.bd_id), "todo", "-", "-", ",".join(beads.to_task_id(d) for d in i.dependencies) or "-",
         "TRIAGE", i.assignee or "-", " ".join(i.title.split()) or "-"]
        for i in open_triage
    ]
    _print_status_table(
        snap.tasks, snap.invalid, snap.claims, show_all, marker_of, stale_entries, triage_rows, beads.sort_key
    )
    triage_ids = [row[0] for row in triage_rows]
    print(f"triage\t{len(triage_ids)}\t" + (",".join(triage_ids) or "-"))
    if tracker.read_tracker(toplevel).kind == "jira":
        waiting = [tid for tid, i in sorted(snap.issues.items()) if beads.JIRA_CLOSE_LABEL in i.labels]
        print(f"jira_close\t{len(waiting)}\t" + (",".join(waiting) or "-"))
    _print_retrospect_due(toplevel)


CONFIG_COMMAND_KEYS = ("verify", "verify_before_ship", "format", "hook_tally")


def cmd_config(toplevel: str) -> None:
    """解けた設定を `<キー>\\t<値>\\t<config|default>` で出す（読むだけ）。"""
    config = layout.read_config(toplevel)
    if config.source is None:
        print(f"MISSING\t{layout.CONFIG_PATH}")
        raise SystemExit(6)
    print(f"CONFIG\t{config.source}")
    for key in layout.CONFIG_KEYS:
        value = getattr(config, key)
        if key == "base_branch" and value is None:
            try:
                value = ledger.base_branch(toplevel)
            except ledger.NoBaseBranch:
                value = None
        elif key == "backup" and value is None:
            value = beads.backup_dir(toplevel)
        if value is None:
            value = layout.NO_COMMAND if key in CONFIG_COMMAND_KEYS else "-"
        print(f"{key}\t{value}\t{'config' if key in config.written else 'default'}")
    print(f"direction\t{layout.direction_path(toplevel)}")
    print(f"draft\t{layout.draft_dir(toplevel)}")


def cmd_show(toplevel: str, task_id: str) -> None:
    """タスク1件を front matter と本文の形で出す（読むだけ）。"""
    bd_id = tw_base.bd_task_id(task_id)
    issue = beads.show(toplevel, bd_id)
    if issue is None:
        print(f"NOT_READY\t{beads.to_task_id(bd_id)}\t存在しない")
        raise SystemExit(4)
    task, err = beads.to_task(issue)
    if err is not None:
        print(f"INVALID\t{err}")
        raise SystemExit(3)
    if task is None:
        task = taskfile.Task(beads.to_task_id(bd_id), issue.title or "-", "todo", "-", "-", (), "")
    result = beads.last_result(beads.comments(toplevel, bd_id))
    sys.stdout.write(beads.render_task(task, issue, result))


def _print_status_table(
    tasks: dict[str, taskfile.Task],
    invalid: dict[str, str],
    claims: set[str],
    show_all: bool,
    marker_of: Callable[[str], str],
    stale_entries: list[str],
    extra_rows: list[list[str]],
    sort_key: Callable[[str], object],
) -> None:
    """`status` の行と `---` 以降の集計（`invalid` の行まで）。

    `extra_rows` は番号順の行のあとに足す行（振り分け前の課題）。集計には数えない。
    """
    for tid in sorted(tasks, key=sort_key):
        t = tasks[tid]
        if not show_all and t.status in ("done", "dropped"):
            continue
        ready = tw_base.readiness(t, tasks, claims)
        print(
            "\t".join(
                [
                    tid,
                    t.status,
                    t.difficulty,
                    t.loopable,
                    ",".join(t.dependencies) or "-",
                    ready,
                    marker_of(tid),
                    t.summary,
                ]
            )
        )

    for row in extra_rows:
        print("\t".join(row))

    print("---")
    counts = {s: sum(1 for t in tasks.values() if t.status == s) for s in taskfile.STATUS_VALUES}
    counts["claimed"] = len(claims)
    print("counts\t" + "\t".join(f"{k}={v}" for k, v in counts.items()))

    ready_count = sum(
        1 for tid, t in tasks.items() if t.status == "todo" and tw_base.readiness(t, tasks, claims) == "READY"
    )
    print(f"ready\t{ready_count}")

    todo_loopable_n = sum(1 for t in tasks.values() if t.status == "todo" and t.loopable == "N")
    print(f"todo_loopable\tN={todo_loopable_n}")

    long_ids = [
        tid
        for tid in sorted(tasks, key=sort_key)
        if tasks[tid].status in ("todo", "hold") and display_width(tasks[tid].summary) > LONG_SUMMARY_WIDTH
    ]
    print(f"long_summary\t{len(long_ids)}\t" + (",".join(long_ids) or "-"))

    print(f"stale\t{len(stale_entries)}\t" + (",".join(stale_entries) if stale_entries else "-"))

    print(f"invalid\t{len(invalid)}\t" + (",".join(sorted(invalid)) if invalid else "-"))


def _print_retrospect_due(toplevel: str) -> None:
    found = cross_review.due(toplevel)
    if found is not None:
        last, elapsed = found
        print(f"retrospect_due\t{last}\t{elapsed}d")


def _classify_claim(toplevel: str, issue: beads.Issue, worktree_names: set[str], base: str) -> tuple[str, str]:
    """着手の印の判定（WORKFLOW.md「status と着手の印」の取り残しの表）。"""
    owner = issue.assignee
    if owner is None:
        return "STALE:no-owner", ""
    shown = beads.to_task_id(issue.bd_id)
    if beads.ship_mark(issue) is not None and _task_commit_on_base(toplevel, shown, base, issue.started_at):
        return "STALE:shipped", owner
    if owner not in worktree_names:
        return "STALE:gone", owner
    age = tw_base.age_seconds(issue.started_at)
    return "CLAIMED", f"{owner} {tw_base.format_elapsed(age) if age is not None else '?'}"


def _task_commit_on_base(toplevel: str, task_id: str, base: str, since: str | None) -> bool:
    """主ブランチに、着手より後の `T-xxx:`（`T-xxx, …` を含む）の件名のコミットがあるか。"""
    args = ["log", base, "-E", f"--grep=^{task_id}[:,]", "--format=%H", "-1"]
    age = tw_base.age_seconds(since)
    if age is not None:
        # コミットの時刻は秒で丸まるので、着手と同じ秒のコミットを落とさないよう1秒さかのぼる。
        args.insert(2, f"--since={int(age) + 1} seconds ago")
    r = tw_base.run_git(toplevel, args)
    return r.returncode == 0 and r.stdout.strip() != ""
