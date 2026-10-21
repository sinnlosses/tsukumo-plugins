"""`tw status`・`config`・`show`・`find`（読むだけのサブコマンド）。"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from typing import Callable

import beads
import cross_review
import layout
import ledger
import gitinfo
import metrics
import taskfile
import tracker
import tw_base
import tw_config


# WORKFLOW.md「summary」の「1行に収める」に反すると見なす幅。80桁はその一行だけで端末が
# 折り返す長さ（旧 status.py の実測: 52件中14件が該当。狭めると大半に火が点いて合図にならない）。
LONG_SUMMARY_WIDTH = 80


def display_width(s: str) -> int:
    """端末に出したときの桁数。日本語（East Asian Wide/Fullwidth）は2桁。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def cmd_status_claims(toplevel: str) -> None:
    """この作業ツリーが着手の印を持つタスクを 1行の JSON で出す（読むだけ）。

    `claim` は Beads の `started_at`（`claim` のたびに変わる）、`stage` は最後の `claim` 以降の最後の `lap` の段（無ければ `null`）。
    """
    events, _skipped = metrics.read_events(ledger.ledger_root(toplevel))
    claims = []
    for issue in tw_base.claimed_issues(toplevel):
        shown = beads.to_task_id(issue.bd_id)
        claims.append({"task": shown, "stage": metrics.last_lap_stage(events, shown), "claim": issue.started_at})
    print(json.dumps({"claims": claims}, ensure_ascii=False))


def cmd_status(toplevel: str, show_all: bool, check: bool) -> None:
    snap = tw_base.beads_snapshot(toplevel)
    if check:
        if snap.invalid:
            print("INVALID\t" + "; ".join(f"{k}:{v}" for k, v in snap.invalid.items()))
            raise SystemExit(3)
        print("OK")
        return

    base = gitinfo.base_branch(toplevel)
    worktree_names = {os.path.basename(w.path) for w in gitinfo.list_worktrees(cwd=toplevel)}
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
    candidates = {tid for tid in snap.tasks if beads.is_candidate(snap.issues[tid])}
    _print_status_table(
        snap.tasks, snap.invalid, snap.claims, show_all, marker_of, stale_entries, triage_rows, beads.sort_key, candidates
    )
    triage_ids = [row[0] for row in triage_rows]
    print(f"triage\t{len(triage_ids)}\t" + (",".join(triage_ids) or "-"))
    candidate_ids = sorted(candidates, key=beads.sort_key)
    print(f"CANDIDATE\t{len(candidate_ids)}\t" + (",".join(candidate_ids) or "-"))
    if tracker.read_tracker(toplevel).kind == "jira":
        waiting = [tid for tid, i in sorted(snap.issues.items()) if beads.JIRA_CLOSE_LABEL in i.labels]
        print(f"jira_close\t{len(waiting)}\t" + (",".join(waiting) or "-"))
    _print_retrospect_due(toplevel)


CONFIG_COMMAND_KEYS = ("verify", "verify_before_ship", "format", "step_check", "hook_tally")


def config_rows(toplevel: str, config: layout.Config) -> list[tuple[str, str | list[str], str]]:
    """解けた設定の `(キー, 値, config|default)`。値は文字列で、`steps`・`review_code_globs` だけ文字列の配列。"""
    rows: list[tuple[str, str | list[str], str]] = []
    for key in layout.CONFIG_KEYS:
        value = getattr(config, key)
        if key == layout.STEPS_KEY:
            value = list(value) if value is not None else tw_config.steps_default(toplevel)
        elif key == layout.REVIEW_CODE_GLOBS_KEY:
            value = list(value)
        elif key == "base_branch" and value is None:
            try:
                value = gitinfo.base_branch(toplevel)
            except gitinfo.NoBaseBranch:
                value = None
        elif key == "backup" and value is None:
            value = beads.backup_dir(toplevel)
        if value is None:
            value = layout.NO_COMMAND if key in CONFIG_COMMAND_KEYS else "-"
        rows.append((key, value, "config" if key in config.written else "default"))
    return rows


def cmd_config(toplevel: str, as_json: bool = False) -> None:
    """解けた設定を `<キー>\\t<値>\\t<config|default>`（`as_json` なら 1行の JSON）で出す（読むだけ）。"""
    config = layout.read_config(toplevel)
    if config.source is None:
        print(f"MISSING\t{layout.CONFIG_PATH}")
        raise SystemExit(6)
    rows = config_rows(toplevel, config)
    if as_json:
        keys = [{"key": key, "value": value, "source": source} for key, value, source in rows]
        print(json.dumps({"config": config.source, "keys": keys}, ensure_ascii=False))
        return
    print(f"CONFIG\t{config.source}")
    for key, value, source in rows:
        text = layout.steps_text(value) if isinstance(value, list) else value
        print(f"{key}\t{text}\t{source}")


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
    no_comments = issue.raw.get("comment_count") == 0
    result = None if no_comments else beads.last_result(beads.comments(toplevel, bd_id))
    sys.stdout.write(beads.render_task(task, issue, result))


# `MANY` のあとに出す候補の数（next-task が `AskUserQuestion` で見せる上限）。
FIND_CANDIDATES = 4


def cmd_find(toplevel: str, words: list[str], kind: str | None) -> None:
    """`READY` を語と種類で絞り、`FOUND`・`MANY`・`NONE` で返す（読むだけ。WORKFLOW.md「kind（種類）」）。"""
    folded = [w for w in (_fold(w) for w in words) if w]
    if not folded and kind is None:
        print("usage: 語か --kind のどちらかを渡す", file=sys.stderr)
        raise SystemExit(2)
    snap = tw_base.beads_snapshot(toplevel)
    ready = [
        tid
        for tid in sorted(snap.tasks, key=beads.sort_key)
        if snap.tasks[tid].status == "todo" and tw_base.readiness(snap.tasks[tid], snap.tasks, snap.claims) == "READY"
    ]
    hits = [tid for tid in ready if _find_hit(snap.tasks[tid], snap.issues[tid], folded, kind)]
    if not hits:
        print(f"NONE\t{len(ready)}")
        return
    rows = [f"{tid}\t{snap.tasks[tid].loopable}\t{snap.tasks[tid].summary}" for tid in hits]
    if len(hits) == 1:
        print(f"FOUND\t{rows[0]}")
        return
    print(f"MANY\t{len(hits)}")
    for row in rows[:FIND_CANDIDATES]:
        print(f"CANDIDATE\t{row}")
    if len(hits) > FIND_CANDIDATES:
        print(f"MORE\t{len(hits) - FIND_CANDIDATES}")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold().strip()


def _find_hit(task: taskfile.Task, issue: beads.Issue, words: list[str], kind: str | None) -> bool:
    """語は summary と `### 名指すファイル` のどれかに含まれれば当たり（語どうしは OR）。

    種類を渡したら、種類の違うタスクは外す。種類の無いタスクは語だけで決め、語が無ければ当たらない。
    """
    own = beads.kind_of(issue)
    if kind is not None and own is not None and own != kind:
        return False
    if not words:
        return own == kind
    body = beads.compose_body(
        str(issue.raw.get("description") or ""),
        str(issue.raw.get("acceptance_criteria") or ""),
        str(issue.raw.get("notes") or ""),
        None,
    )
    paths, _ = taskfile.plan_files(body)
    targets = [_fold(task.summary), *(_fold(p) for p in paths)]
    return any(w in t for w in words for t in targets)


def _print_status_table(
    tasks: dict[str, taskfile.Task],
    invalid: dict[str, str],
    claims: set[str],
    show_all: bool,
    marker_of: Callable[[str], str],
    stale_entries: list[str],
    extra_rows: list[list[str]],
    sort_key: Callable[[str], object],
    hidden: set[str],
) -> None:
    """`status` の行と `---` 以降の集計（`invalid` の行まで）。

    `extra_rows` は番号順の行のあとに足す行（振り分け前の課題）。集計には数えない。
    `hidden`（候補）は行にも集計にも出さないが、依存の解決には `tasks` のまま数える。
    """
    shown = {tid: t for tid, t in tasks.items() if tid not in hidden}
    for tid in sorted(shown, key=sort_key):
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
    counts = {s: sum(1 for t in shown.values() if t.status == s) for s in taskfile.STATUS_VALUES}
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
        for tid in sorted(shown, key=sort_key)
        if shown[tid].status in ("todo", "hold", "draft") and display_width(shown[tid].summary) > LONG_SUMMARY_WIDTH
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
    if beads.ship_mark(issue) is not None and (
        _shipped_by_record(toplevel, issue, base) or _task_commit_on_base(toplevel, shown, base, issue.started_at)
    ):
        return "STALE:shipped", owner
    if owner not in worktree_names:
        return "STALE:gone", owner
    age = tw_base.age_seconds(issue.started_at)
    return "CLAIMED", f"{owner} {tw_base.format_elapsed(age) if age is not None else '?'}"


def _shipped_by_record(toplevel: str, issue: beads.Issue, base: str) -> bool:
    """`tw ship` が metadata に書いた範囲の先端が、主ブランチに入っているか。使えない記録は偽（件名照合へ戻る）。"""
    return beads.shipped_range_on_base(issue, base, toplevel) is not None


def _task_commit_on_base(toplevel: str, task_id: str, base: str, since: str | None) -> bool:
    """主ブランチに、着手より後の `T-xxx:`（`T-xxx, …` を含む）の件名のコミットがあるか。"""
    args = ["log", base, "-E", f"--grep=^{task_id}[:,]", "--format=%H", "-1"]
    age = tw_base.age_seconds(since)
    if age is not None:
        # コミットの時刻は秒で丸まるので、着手と同じ秒のコミットを落とさないよう1秒さかのぼる。
        args.insert(2, f"--since={int(age) + 1} seconds ago")
    r = tw_base.run_git(toplevel, args)
    return r.returncode == 0 and r.stdout.strip() != ""
