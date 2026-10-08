"""`tw status`・`config`・`show`（読むだけのサブコマンド）。"""

from __future__ import annotations

import os
import sys
import time
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


def classify_claim(
    root: str,
    task_id: str,
    base_task: taskfile.Task | None,
    worktrees: list[ledger.Worktree],
) -> tuple[str, str]:
    """4.3の判定。`(表示語, 詳細)` を返す。表示語は `CLAIMED` か `STALE:*`。"""
    d = ledger.claim_dir(root, task_id)
    owner = ledger.read_owner(d)
    if owner is None:
        age = time.time() - os.stat(d).st_mtime
        return ("STALE:no-owner", "") if age > 60 else ("CLAIMED", "書き込み中")
    worktree = owner.get("worktree", "?")
    if base_task is not None and base_task.status in ("done", "dropped"):
        return "STALE:shipped", worktree
    if not any(w.path == worktree for w in worktrees):
        return "STALE:gone", worktree
    age = ledger.owner_age_seconds(d)
    elapsed = tw_base.format_elapsed(age) if age is not None else "?"
    return "CLAIMED", f"{os.path.basename(worktree)} {elapsed}"


def _section_bullets(text: str, heading_prefix: str) -> int:
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith(heading_prefix)), None)
    if start is None:
        return 0
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return sum(1 for l in lines[start + 1 : end] if l.strip().startswith("- "))


def cmd_status(toplevel: str, show_all: bool, check: bool) -> None:
    root = ledger.ledger_root(cwd=toplevel)
    tasks, invalid, local_only = tw_base.load_tasks(toplevel)
    claims = set(ledger.list_claims(root))
    worktrees = ledger.list_worktrees(cwd=toplevel)
    history_path = os.path.join(toplevel, layout.HISTORY_TASKS_PATH)
    history = taskfile.history_ids(history_path)

    if check:
        problems = [f"{stem}:{reason}" for stem, reason in invalid.items()]
        problems += [
            f"{tid}:タスクファイルと {layout.HISTORY_TASKS_PATH} の両方にある" for tid in tasks if tid in history
        ]
        for tid, t in tasks.items():
            if t.status in ("todo", "hold"):
                error = taskfile.validate_body(taskfile.strip_result_section(t.body))
                if error is not None:
                    problems.append(f"{tid}:{error}")
        if problems:
            print("INVALID\t" + "; ".join(problems))
            raise SystemExit(3)
        print("OK")
        return

    def marker_of(tid: str) -> str:
        if tid in claims:
            label, detail = classify_claim(root, tid, tasks[tid], worktrees)
            return detail if label == "CLAIMED" else f"{label}({detail})"
        return "local" if tid in local_only else "-"

    stale_entries = []
    for tid in claims:
        label, detail = classify_claim(root, tid, tasks.get(tid), worktrees)
        if label != "CLAIMED":
            stale_entries.append(f"{tid}:{label}({detail})" if detail else f"{tid}:{label}")

    _print_status_table(tasks, invalid, claims, show_all, marker_of, stale_entries)
    tw_base.print_split_claims(ledger.split_claims(cwd=toplevel))
    _print_retrospect_due(toplevel)
    _print_legacy_progress(toplevel)
    _print_old_layout(toplevel)


def _print_status_table(
    tasks: dict[str, taskfile.Task],
    invalid: dict[str, str],
    claims: set[str],
    show_all: bool,
    marker_of: Callable[[str], str],
    stale_entries: list[str],
    extra_rows: "list[list[str]] | None" = None,
    sort_key: Callable[[str], object] = taskfile.id_number,
) -> None:
    """`status` の行と `---` 以降の集計（`invalid` の行まで）。ファイル方式と Beads 方式で同じ形。

    `extra_rows` は番号順の行のあとに足す行（Beads 方式の振り分け前の課題）。集計には数えない。
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

    for row in extra_rows or []:
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


def _print_legacy_progress(toplevel: str) -> None:
    progress_path = os.path.join(toplevel, "develop", "progress.md")
    if os.path.exists(progress_path):
        with open(progress_path, encoding="utf-8") as f:
            text = f.read()
        unresolved = _section_bullets(text, "## 未解決")
        notes = _section_bullets(text, "## 注意")
        print(
            f"legacy_progress\tdevelop/progress.md\t未解決 {unresolved} / 注意 {notes}"
            "\t（移行の残り。振り分けたら消す）"
        )


def _print_old_layout(toplevel: str) -> None:
    config = layout.read_config(toplevel)
    if config.legacy:
        print(f"old_layout\t{config.source}\ttw migrate-layout --dry-run")
    elif layout.stranded_legacy_places(toplevel, config):
        print(f"old_layout\t{layout.LEGACY_ROOT}/\ttw migrate-layout --dry-run")


CONFIG_COMMAND_KEYS = ("verify", "verify_before_ship", "format", "hook_tally")


def cmd_config(toplevel: str) -> None:
    """解けた設定を `<キー>\\t<値>\\t<config|default>` で出す（読むだけ）。"""
    config = layout.read_config(toplevel)
    if config.source is None:
        print(f"MISSING\t{layout.CONFIG_PATH}")
        raise SystemExit(6)
    print(f"CONFIG\t{config.source}" + ("（旧節）" if config.legacy else ""))
    for key in layout.CONFIG_KEYS:
        value = getattr(config, key)
        if key == "base_branch" and value is None:
            try:
                value = ledger.base_branch(toplevel)
            except ledger.NoBaseBranch:
                value = None
        elif key == "backup" and value is None and config.store == layout.STORE_BEADS:
            value = beads.backup_dir(toplevel)
        if value is None:
            value = layout.NO_COMMAND if key in CONFIG_COMMAND_KEYS else "-"
        print(f"{key}\t{value}\t{'config' if key in config.written else 'default'}")
    print(f"direction\t{layout.direction_path(toplevel)}")
    print(f"draft\t{layout.draft_dir(toplevel)}")
    if config.store == layout.STORE_FILES:
        print(f"task\t{layout.task_dir(toplevel)}")


def _beads_classify(toplevel: str, issue: beads.Issue, worktree_names: set[str], base: str) -> tuple[str, str]:
    """着手の印の判定（WORKFLOW.md「status と着手の印」の取り残しの表を Beads の欄で）。"""
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


def cmd_beads_status(toplevel: str, show_all: bool, check: bool) -> None:
    snap = tw_base.beads_snapshot(toplevel)
    if check:
        if snap.invalid:
            print("INVALID\t" + "; ".join(f"{k}:{v}" for k, v in snap.invalid.items()))
            raise SystemExit(3)
        print("OK")
        return

    base = ledger.base_branch(toplevel)
    worktree_names = {os.path.basename(w.path) for w in ledger.list_worktrees(cwd=toplevel)}
    labels = {tid: _beads_classify(toplevel, snap.issues[tid], worktree_names, base) for tid in snap.claims}

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
    _print_legacy_progress(toplevel)
    _print_old_layout(toplevel)


def cmd_show(toplevel: str, task_id: str, store: str) -> None:
    """タスク1件をタスクファイルの形で出す（読むだけ）。"""
    if store == layout.STORE_FILES:
        if not taskfile.ID_PATTERN.match(task_id):
            print(f"usage: {task_id!r} が T-999 の形式でない", file=sys.stderr)
            raise SystemExit(2)
        path = taskfile.task_path(os.path.join(toplevel, layout.task_dir(toplevel)), task_id)
        text = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
        else:
            text = tw_base.read_base_task_text(toplevel, ledger.base_branch(toplevel), f"{task_id}.md")
        if text is None:
            print(f"NOT_READY\t{task_id}\t存在しない")
            raise SystemExit(4)
        sys.stdout.write(text)
        return
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
