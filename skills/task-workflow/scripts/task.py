#!/usr/bin/env python3
"""1件1ファイル＋台帳の形のタスク運用を操作する入口コマンド。

使い方: tw <status|new|claim|release|done|ship|prune|migrate|migrate-layout|config-doctor|show|edit|plan-check|verify|verify-check|pause|step|commit-guard|handback-guard> ...

正典は `docs/task-workflow-redesign.md`（5章が `task` コマンド、4章が状態と台帳、
3章がタスクファイル、6章が送り出し、5.9・10章が `migrate`）。`install.sh` が PATH 上に張る
`tw` と、plugin の `bin/tw` から呼ぶ。`commit-guard`・`handback-guard` の `--agent-scoped` は plugin の
`hooks/hooks.json` が付け、`agent_type` の末尾が `no-delegate` のときだけ関門を掛ける。スキル側の呼び方の正典は task-workflow の WORKFLOW.md「`tw` コマンドの参照」。

出力は常に stdout（先頭語で種類を判定する TSV）、stderr は使い方の誤りだけ、
終了コードは5.2の表のとおり。データの不備で traceback を出さない
（traceback は「環境の故障」の合図として取っておく）。`migrate` の実体は `legacy.py`（旧形式の
読み取りと実際の書き換え）にあり、ここは結果を印字するだけ（`ship.py`/`cmd_ship` と同じ形）。
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Collection, Mapping
from typing import Callable, NoReturn

import beads
import commit_guard
import cross_review
import fold
import handback_guard
import init
import layout
import ledger
import legacy
import metrics
import relayout
import ship
import taskfile
import tracker


# --- 形式の判定（5.2） -----------------------------------------------------


def detect_format(toplevel: str) -> tuple[str, str | None]:
    tasks_json = os.path.join(toplevel, layout.LEGACY_ROOT, "tasks.json")
    task_dir = os.path.join(toplevel, layout.LEGACY_ROOT, "task")
    if os.path.exists(tasks_json):
        if os.path.isdir(task_dir) and any(n.endswith(".md") for n in os.listdir(task_dir)):
            return "INVALID", "develop/tasks.json と develop/task/ の両方がある（移行が途中）"
        return "LEGACY", None
    if os.path.exists(os.path.join(toplevel, layout.CONFIG_PATH)):
        return "NEW", None
    if os.path.exists(os.path.join(toplevel, layout.LEGACY_DIRECTION_PATH)):
        return "NEW", None
    return "MISSING", None


def _format_refusal(toplevel: str) -> tuple[str, int] | None:
    """形式（`detect_format`）のために、サブコマンド（`migrate`・`config-doctor` を除く）を打たずに出す行と終了コード。打てるなら `None`。"""
    kind, detail = detect_format(toplevel)
    if kind == "INVALID":
        return f"INVALID\t{detail}", 3
    if kind == "LEGACY":
        return "LEGACY\ttw migrate --dry-run", 5
    if kind == "MISSING":
        return "MISSING", 6
    return None


# --- git の薄いラッパ（非0を「失敗」として使う呼び出しは例外を投げない） -------


def _run_git(toplevel: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=toplevel, capture_output=True, text=True)


def _list_base_task_filenames(toplevel: str, base: str) -> list[str]:
    r = _run_git(toplevel, ["ls-tree", "--name-only", "-r", base, "--", layout.task_dir(toplevel)])
    if r.returncode != 0:
        return []
    return [os.path.basename(p) for p in r.stdout.splitlines() if p.endswith(".md")]


def _read_base_task_text(toplevel: str, base: str, filename: str) -> str | None:
    r = _run_git(toplevel, ["show", f"{base}:{layout.task_dir(toplevel)}/{filename}"])
    return r.stdout if r.returncode == 0 else None


def _history_ids_at_base(toplevel: str) -> set[str]:
    base = ledger.base_branch(toplevel)
    r = _run_git(toplevel, ["show", f"{base}:{layout.HISTORY_TASKS_PATH}"])
    if r.returncode != 0:
        return set()
    return {m.group(1) for m in layout.HISTORY_HEADING_PATTERN.finditer(r.stdout)}


# --- タスクの読み取り（主ブランチを正とし、作業ツリーだけの分は local として足す） ---


def load_tasks(
    toplevel: str,
) -> tuple[dict[str, taskfile.Task], dict[str, str], list[str]]:
    """`(id→Task, id→INVALID理由, ローカルにしか無いID)` を返す（5.3: 主ブランチを正とする）。"""
    tasks: dict[str, taskfile.Task] = {}
    invalid: dict[str, str] = {}

    base = ledger.base_branch(toplevel)
    for filename in _list_base_task_filenames(toplevel, base):
        stem = os.path.splitext(filename)[0]
        text = _read_base_task_text(toplevel, base, filename)
        if text is None:
            continue
        parsed, err = taskfile.parse(text)
        if err is not None or parsed is None or parsed.id != stem:
            invalid[stem] = err or f"ファイル名（{stem}）と id（{parsed.id if parsed else '?'}）が不一致"
            continue
        tasks[parsed.id] = parsed

    task_dir = os.path.join(toplevel, layout.task_dir(toplevel))
    local_only: list[str] = []
    for stem in taskfile.local_task_ids(task_dir):
        if stem in tasks or stem in invalid:
            continue  # 主ブランチにもある ID は主ブランチを正とする
        parsed, err = taskfile.read_task_file(taskfile.task_path(task_dir, stem))
        if err is not None or parsed is None:
            invalid[stem] = err or "読めない"
            continue
        tasks[parsed.id] = parsed
        local_only.append(parsed.id)

    return tasks, invalid, local_only


def is_resolved(dep_id: str, tasks: dict[str, taskfile.Task]) -> bool:
    """4.1: done/dropped は解決済み。タスクファイルに無い ID（アーカイブ済み）も解決済み。"""
    t = tasks.get(dep_id)
    return t is None or t.status in ("done", "dropped")


def readiness(task: taskfile.Task, tasks: dict[str, taskfile.Task], claims: set[str]) -> str:
    if task.status == "hold":
        return "HOLD"
    if task.status != "todo":
        return "-"
    if task.id in claims:
        return "CLAIMED"
    blocked = [d for d in task.dependencies if not is_resolved(d, tasks)]
    if blocked:
        return "BLOCKED:" + ",".join(blocked)
    return "READY"


# WORKFLOW.md「summary」の「1行に収める」に反すると見なす幅。80桁はその一行だけで端末が
# 折り返す長さ（旧 status.py の実測: 52件中14件が該当。狭めると大半に火が点いて合図にならない）。
LONG_SUMMARY_WIDTH = 80


def display_width(s: str) -> int:
    """端末に出したときの桁数。日本語（East Asian Wide/Fullwidth）は2桁。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes}m" if minutes else f"{hours}h"


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
    elapsed = format_elapsed(age) if age is not None else "?"
    return "CLAIMED", f"{os.path.basename(worktree)} {elapsed}"


def _section_bullets(text: str, heading_prefix: str) -> int:
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith(heading_prefix)), None)
    if start is None:
        return 0
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return sum(1 for l in lines[start + 1 : end] if l.strip().startswith("- "))


def read_body(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


# --- status（5.3） ---------------------------------------------------------


def cmd_status(toplevel: str, show_all: bool, check: bool) -> None:
    root = ledger.ledger_root(cwd=toplevel)
    tasks, invalid, local_only = load_tasks(toplevel)
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
    _print_split_claims(ledger.split_claims(cwd=toplevel))
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
        ready = readiness(t, tasks, claims)
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
        1 for tid, t in tasks.items() if t.status == "todo" and readiness(t, tasks, claims) == "READY"
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


# --- config ------------------------------------------------------------------

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


# --- new（5.4） -------------------------------------------------------------


def cmd_new(toplevel: str, args: argparse.Namespace) -> None:
    summary = args.summary.strip()
    if summary == "" or "\n" in args.summary:
        print("usage: --summary は改行を含まない1行にする", file=sys.stderr)
        raise SystemExit(2)

    deps = tuple(d for d in (x.strip() for x in args.deps.split(",")) if d) if args.deps else ()
    for d in deps:
        if not taskfile.ID_PATTERN.match(d):
            print(f"usage: --deps の {d!r} が T-999 の形式でない", file=sys.stderr)
            raise SystemExit(2)

    body = read_body(args.body_file)
    error = taskfile.validate_new_body(body, args.hold)
    if error is not None:
        print(f"usage: {error}", file=sys.stderr)
        raise SystemExit(2)
    if args.direct:
        _refuse_direct(args.difficulty, body)
    plan_base = _registered_plan_base(toplevel, body) if taskfile.has_plan(body) else None

    root = ledger.ledger_root_for_write(cwd=toplevel)
    if not ledger.acquire_lock(root):
        age = ledger.lock_owner_age_seconds(root)
        hint = f"\trmdir {shlex.quote(os.path.join(root, ledger.LOCK_DIR_NAME))}" if age and age > 60 else ""
        print(f"LOCKED\t採番の錠が取れない{hint}")
        raise SystemExit(4)
    try:
        tasks, invalid, _ = load_tasks(toplevel)

        status = "hold" if args.hold else "todo"
        history_path = os.path.join(toplevel, layout.HISTORY_TASKS_PATH)
        candidate_ids = (
            list(tasks) + [i for i in invalid if taskfile.ID_PATTERN.match(i)]
            + list(taskfile.history_ids(history_path))
            + list(_history_ids_at_base(toplevel))
        )
        candidates = [0] + [taskfile.id_number(i) for i in candidate_ids]
        last_id = ledger.read_last_id(root)
        if last_id is not None:
            candidates.append(last_id)
        number = max(candidates) + 1
        task_id = taskfile.format_id(number)

        task_dir = os.path.join(toplevel, layout.task_dir(toplevel))
        os.makedirs(task_dir, exist_ok=True)
        path = taskfile.task_path(task_dir, task_id)
        rendered = taskfile.render(
            taskfile.Task(task_id, summary, status, args.difficulty, args.loopable, deps, body, "Y" if args.direct else "N")
        )
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(rendered)

        ledger.write_last_id(root, number)
        if plan_base is not None:
            ledger.write_plan_base(root, task_id, plan_base)
        print(f"CREATED\t{task_id}\t{layout.task_dir(toplevel)}/{task_id}.md")
    finally:
        ledger.release_lock(root)


def _refuse_direct(difficulty: str, body: str) -> None:
    reason = taskfile.direct_refusal(difficulty, body)
    if reason is not None:
        print(f"usage: {reason}（--direct を外す）", file=sys.stderr)
        raise SystemExit(2)


def _direct_column(direct: str, difficulty: str, body: str, registered: bool) -> str:
    """`CLAIMED` の行末に足す近道の列。印が無ければ空。"""
    if direct != "Y":
        return ""
    reason = taskfile.direct_refusal(difficulty, body) or (None if registered else "登録時の計画が古い")
    return "\tdirect=Y" if reason is None else f"\tdirect=N:{reason}"


def _registered_plan_base(toplevel: str, body: str) -> str:
    """登録時に控える SHA（計画のリポジトリの `HEAD` と主ブランチの分かれ目）。

    名指すファイルがその木に無い、または `### 作業先` が git のリポジトリの根でなければ終了コード2。
    """
    repo = _plan_repo(toplevel, body)
    if repo is None:
        print(
            f"usage: {taskfile.PLAN_WORK_REPO_HEADING} の {taskfile.plan_work_repo(body)[0]} が git のリポジトリの根でない",
            file=sys.stderr,
        )
        raise SystemExit(2)
    base = ledger.base_branch(repo)
    r = _run_git(repo, ["merge-base", "HEAD", base])
    if r.returncode != 0:
        print(f"INVALID\t{base} との分かれ目が引けない（{r.stderr.strip()}）")
        raise SystemExit(3)
    sha = r.stdout.strip()
    paths, _ = taskfile.plan_files(body)
    missing = [p for p in paths if _run_git(repo, ["cat-file", "-e", f"{sha}:{p.rstrip('/')}"]).returncode != 0]
    if missing:
        where = base if repo == toplevel else f"{repo} の {base}"
        print(
            f"usage: {taskfile.PLAN_FILES_HEADING} の {', '.join(missing)} が {where} に無い"
            "（新しいファイルは置くディレクトリを名指す）",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return sha


def _plan_repo(toplevel: str, body: str) -> str | None:
    """計画の名指すファイルを読むリポジトリ。`### 作業先` が無ければ `toplevel`、あってもリポジトリの根でなければ `None`。"""
    path, _ = taskfile.plan_work_repo(body)
    if path is None:
        return toplevel
    if not os.path.isdir(path):
        return None
    r = _run_git(path, ["rev-parse", "--show-toplevel"])
    if r.returncode != 0 or os.path.realpath(r.stdout.strip()) != os.path.realpath(path):
        return None
    return path


def _registered_plan_changes(toplevel: str, base_sha: str | None, tip: str | None, body: str) -> list[str] | None:
    """登録時の計画が名指すファイルのうち、控えた `base_sha` から `tip` までに変わったもの。控えが無ければ `None`。"""
    if base_sha is None:
        return None
    repo = _plan_repo(toplevel, body)
    if repo is None:
        return ["(作業先が読めない)"]
    if tip is None or _run_git(repo, ["cat-file", "-e", f"{base_sha}^{{commit}}"]).returncode != 0:
        return ["(控えた SHA が無い)"]
    paths, _ = taskfile.plan_files(body)
    if not paths:
        return ["(名指すファイルが読めない)"]
    r = _run_git(repo, ["diff", "--name-only", base_sha, tip, "--", *paths])
    if r.returncode != 0:
        return ["(差分が引けない)"]
    return [line for line in r.stdout.splitlines() if line]


def _plan_tip(toplevel: str, body: str) -> str | None:
    """着手時に比べる、計画のリポジトリの主ブランチの先端。"""
    repo = _plan_repo(toplevel, body)
    return _base_tip(repo) if repo is not None else None


def _base_tip(toplevel: str) -> str | None:
    r = _run_git(toplevel, ["rev-parse", ledger.base_branch(toplevel)])
    return r.stdout.strip() if r.returncode == 0 else None


# --- claim（5.5） -----------------------------------------------------------


def _task_difficulty(toplevel: str, task_id: str, issue: beads.Issue | None = None) -> str:
    """記録に入れる `difficulty`。引けなければ `?`（記録のために元のサブコマンドを落とさない）。"""
    try:
        if issue is not None:
            task = beads.to_task(issue)[0]
        elif layout.read_config(toplevel).store == layout.STORE_BEADS:
            issue = beads.show(toplevel, beads.to_bd_id(task_id))
            task = beads.to_task(issue)[0] if issue is not None else None
        else:
            task = taskfile.read_task_file(taskfile.task_path(os.path.join(toplevel, layout.task_dir(toplevel)), task_id))[0]
    except Exception:
        return "?"
    return task.difficulty if task is not None else "?"


def _record(
    toplevel: str, event: str, task_id: str, issue: beads.Issue | None = None, **fields: str | int | bool
) -> None:
    ledger.record_event(toplevel, event, task_id, _task_difficulty(toplevel, task_id, issue), **fields)


def _claimed_here(toplevel: str) -> list[str]:
    """この作業ツリーが着手の印を持つタスク（`done` にしたあと `ship` までのものも含む）。引けなければ空。"""
    try:
        if layout.read_config(toplevel).store == layout.STORE_BEADS:
            actor = _actor(toplevel)
            return [
                beads.to_task_id(i.bd_id)
                for i in beads.list_issues(toplevel)
                if i.status == "in_progress" and i.assignee == actor
            ]
        root = ledger.ledger_root(cwd=toplevel)
        return [
            t
            for t in ledger.list_claims(root)
            if (ledger.read_owner(ledger.claim_dir(root, t)) or {}).get("worktree") == toplevel
        ]
    except Exception:
        return []


def _record_claimed(toplevel: str, event: str, **fields: str | int | bool) -> None:
    for task_id in _claimed_here(toplevel):
        _record(toplevel, event, task_id, **fields)


REFLECTION_SKIPPED_LINE = "- 振り返り: 近道（省いた）"


def _reflection_of(result: str) -> str:
    """`## 結果` の `- 振り返り:` の行が `none`（兆候なし）・`skipped`（近道で省いた）・`some`・`unknown`（行が無い）。"""
    for line in result.splitlines():
        if line.startswith("- 振り返り:"):
            if line.strip() == REFLECTION_SKIPPED_LINE:
                return "skipped"
            return "none" if "兆候なし" in line else "some"
    return "unknown"


def cmd_claim(toplevel: str, task_id: str) -> None:
    if not taskfile.ID_PATTERN.match(task_id):
        print(f"usage: {task_id!r} が T-999 の形式でない", file=sys.stderr)
        raise SystemExit(2)

    root = ledger.ledger_root_for_write(cwd=toplevel)
    branch_setting, base = _claim_preflight(toplevel)

    tasks, invalid, _ = load_tasks(toplevel)
    if task_id in invalid:
        print(f"INVALID\t{invalid[task_id]}")
        raise SystemExit(3)
    task = tasks.get(task_id)
    if task is None:
        print(f"NOT_READY\t{task_id}\t存在しない")
        raise SystemExit(4)
    if task.status != "todo":
        print(f"NOT_READY\t{task_id}\t{task.status}")
        raise SystemExit(4)
    blocked = [d for d in task.dependencies if not is_resolved(d, tasks)]
    if blocked:
        print(f"NOT_READY\t{task_id}\tBLOCKED:{','.join(blocked)}")
        raise SystemExit(4)

    split = ledger.split_claims(cwd=toplevel)
    held = next((s for s in split if s.task_id == task_id), None)
    if held is not None:
        age = _age_seconds(held.claimed_at)
        print(f"TAKEN\t{task_id}\t{held.worktree}\t{format_elapsed(age) if age is not None else '?'}")
        raise SystemExit(4)
    branch_after_sync = ledger.current_branch(cwd=toplevel)
    head = ledger.head_sha_or_none(cwd=toplevel)
    if not ledger.try_claim(root, task_id, toplevel, branch_after_sync, head):
        d = ledger.claim_dir(root, task_id)
        owner = ledger.read_owner(d) or {}
        age = ledger.owner_age_seconds(d)
        elapsed = format_elapsed(age) if age is not None else "?"
        print(f"TAKEN\t{task_id}\t{owner.get('worktree', '?')}\t{elapsed}")
        raise SystemExit(4)
    ledger.mark_open_claim(task_id, cwd=toplevel)
    _record(toplevel, "claim", task_id)

    plan_base = ledger.read_plan_base(root, task_id)
    registered = False
    if plan_base is not None and taskfile.has_plan(task.body):
        tip = _plan_tip(toplevel, task.body)
        if tip is not None:
            ledger.write_plan_tip(root, task_id, tip)
        if _registered_plan_changes(toplevel, plan_base, tip, task.body) == []:
            ledger.write_plan_mark(root, task_id, PLAN_REGISTERED)
            registered = True

    _claim_branch_out(
        toplevel, task_id, branch_setting, base, branch_after_sync, f"{layout.task_dir(toplevel)}/{task_id}.md",
        _direct_column(task.direct, task.difficulty, task.body, registered),
    )
    _print_split_claims(split)


def _print_split_claims(split: list[ledger.SplitClaim]) -> None:
    """古い台帳にだけある着手の印（`TW_STATE_DIR` をそろえていないセッションの印）の1行。無ければ出さない。"""
    if split:
        entries = ",".join(f"{s.task_id}:{os.path.basename(s.worktree)}" for s in split)
        print(f"split_claims\t{len(split)}\t{entries}")


def _claim_preflight(toplevel: str) -> tuple[str, str]:
    """`claim` の git の前提（`- ブランチ:` が読める・clean・未送りなし・主ブランチへ追い付く）。

    `(ブランチの設定の先頭語, 主ブランチ)` を返す。前提を欠けば出力して `SystemExit`。
    追い付くか枝を切るのに `.git` へ書けなければ、印を立てる前に `ledger.GitReadOnly`。
    ファイル方式と Beads 方式の `claim` が同じものを通る。
    """
    branch_setting = layout.read_config(toplevel).branch
    if branch_setting not in layout.BRANCH_VALUES:
        print(f"INVALID\t- ブランチ: の値 {branch_setting!r} を機械が読めない")
        raise SystemExit(3)

    if not ledger.is_clean(cwd=toplevel):
        print("DIRTY")
        raise SystemExit(4)

    base = ledger.base_branch(toplevel)
    branch = ledger.current_branch(cwd=toplevel)
    if branch != base:
        ahead = _run_git(toplevel, ["rev-list", "--count", f"{base}..HEAD"])
        if ahead.returncode == 0 and ahead.stdout.strip() not in ("0", ""):
            print(f"UNSHIPPED\t{ahead.stdout.strip()}")
            raise SystemExit(4)
        # 追い付き済みでも `merge` は ORIG_HEAD を書くので、要るときだけ打つ。
        if _run_git(toplevel, ["merge-base", "--is-ancestor", base, "HEAD"]).returncode != 0:
            ledger.require_git_writable(toplevel)
            r = _run_git(toplevel, ["merge", "--ff-only", base])
            if r.returncode != 0:
                print(f"INVALID\t{base} へ追い付けない（{r.stderr.strip()}）")
                raise SystemExit(3)
    if branch_setting in ("既定", "作業ブランチを切る"):
        ledger.require_git_writable(toplevel)
    return branch_setting, base


def _claim_branch_out(
    toplevel: str, task_id: str, branch_setting: str, base: str, branch_after_sync: str, where: str, direct: str = ""
) -> None:
    """印を立てたあと、設定なら作業ブランチを切って `CLAIMED` を出す（`where` は3列目、`direct` は行末の列）。"""
    if branch_setting in ("既定", "作業ブランチを切る"):
        feature_branch = f"{layout.FEATURE_BRANCH_PREFIX}{task_id}"
        r = _run_git(toplevel, ["checkout", "-b", feature_branch, base])
        if r.returncode != 0:
            print(f"CLAIMED\t{task_id}\t{where}\tbranch=(切れない: {r.stderr.strip()}){direct}")
            return
        print(f"CLAIMED\t{task_id}\t{where}\tbranch={feature_branch}{direct}")
    else:
        print(f"CLAIMED\t{task_id}\t{where}\tbranch={branch_after_sync}{direct}")


# --- release（5.6） ---------------------------------------------------------


def cmd_release(toplevel: str, task_id: str, force: bool) -> None:
    if not taskfile.ID_PATTERN.match(task_id):
        print(f"usage: {task_id!r} が T-999 の形式でない", file=sys.stderr)
        raise SystemExit(2)
    root = ledger.ledger_root_for_write(cwd=toplevel)
    owner = ledger.read_owner(ledger.claim_dir(root, task_id)) or {}
    result = ledger.release_claim(root, task_id, toplevel, force=force)
    if result == "RELEASED":
        _clear_open_claim_in(owner.get("worktree", toplevel), task_id)
        _record(toplevel, "release", task_id)
    if result in ("RELEASED", "NOT_CLAIMED"):
        print(f"{result}\t{task_id}")
        return
    print(f"{result}\t{task_id}")
    raise SystemExit(4)


def _clear_open_claim_in(worktree: str, task_id: str) -> None:
    """`worktree` の作業ツリー固有の控えを消す。作業ツリーが消えていれば何もしない。"""
    if not os.path.isdir(worktree):
        return
    try:
        ledger.clear_open_claim(task_id, cwd=worktree)
    except ledger.GitCommandError:
        return


# --- done（5.7） ------------------------------------------------------------


def cmd_done(toplevel: str, task_id: str, dropped: bool, result_path: str) -> None:
    if not taskfile.ID_PATTERN.match(task_id):
        print(f"usage: {task_id!r} が T-999 の形式でない", file=sys.stderr)
        raise SystemExit(2)

    root = ledger.ledger_root(cwd=toplevel)
    owner = ledger.read_owner(ledger.claim_dir(root, task_id))
    if owner is None or owner.get("worktree") != toplevel:
        print(f"NOT_OWNER\t{task_id}")
        raise SystemExit(4)
    ledger.require_git_writable(toplevel)

    task_dir = os.path.join(toplevel, layout.task_dir(toplevel))
    path = taskfile.task_path(task_dir, task_id)
    task, err = taskfile.read_task_file(path)
    if err is not None or task is None:
        print(f"INVALID\t{err or '読めない'}")
        raise SystemExit(3)

    error = taskfile.validate_body(taskfile.strip_result_section(task.body))
    if error is not None:
        print(f"INVALID\t{error}")
        raise SystemExit(3)

    result = read_body(result_path).strip()
    if result == "":
        print("usage: --result-file の中身が空", file=sys.stderr)
        raise SystemExit(2)

    new_status = "dropped" if dropped else "done"
    new_body = taskfile.set_result_section(task.body, result)
    rendered = taskfile.render(
        dataclasses.replace(task, status=new_status, body=new_body)
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(rendered)

    relpath = os.path.join(layout.task_dir(toplevel), f"{task_id}.md")
    _run_git(toplevel, ["add", relpath])
    print(f"DONE\t{task_id}\t{relpath}\tstaged")
    _record(toplevel, "done", task_id, dropped=dropped, reflection=_reflection_of(result))
    ledger.clear_open_claim(task_id, cwd=toplevel)
    _print_commits_since_claim(toplevel, task_id, owner.get("head"))


def _commits_since_claim(toplevel: str, head: str | None) -> list[str]:
    """`head`（claim 時の HEAD）から今の HEAD までにできた、主ブランチに無いコミット（古い順、短縮ハッシュ）。

    `head` が無い（控えの無い古い印。ファイル方式は owner の `head=`、Beads 方式は metadata の
    `task_claim_head`）か `git log` が引けなければ空のまま返す（`task done` はそれを「委譲先の
    コミットは無い」と同じに扱い、落とさない）。
    """
    if not head:
        return []
    base = ledger.base_branch(toplevel)
    r = _run_git(toplevel, ["log", "--format=%h", "--reverse", "HEAD", f"^{head}", f"^{base}"])
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line]


def _print_commits_since_claim(toplevel: str, shown_id: str, head: str | None) -> None:
    """claim から今までに委譲先が作ったコミットがあれば `COMMITS_SINCE_CLAIM` の行で知らせる。

    `next-task` の手順6（受け入れる）向けの合図で、`DONE` の判定・終了コードは変えない。
    """
    commits = _commits_since_claim(toplevel, head)
    if commits:
        print(f"COMMITS_SINCE_CLAIM\t{shown_id}\t{','.join(commits)}")


# --- edit・plan-check（`## やること` を作業より先に書いたか） ----------------

PLAN_FIRST = "first"
PLAN_AFTER_WORK = "after-work"
PLAN_REGISTERED = "registered"


def _parse_dep_list(raw: str | None, pattern: re.Pattern[str], flag: str, form: str) -> tuple[str, ...]:
    deps = tuple(d for d in (x.strip() for x in raw.split(",")) if d) if raw else ()
    for d in deps:
        if not pattern.match(d):
            print(f"usage: {flag} の {d!r} が {form} の形式でない", file=sys.stderr)
            raise SystemExit(2)
    return deps


def _find_cycle(graph: Mapping[str, tuple[str, ...]], task_id: str, new_dep: str) -> list[str] | None:
    """`task_id` が `new_dep` に依存する辺を足すと閉じる道（`A→B→A`）。閉じなければ None。自分自身は長さ1の循環。"""
    if new_dep == task_id:
        return [task_id, task_id]
    seen = {new_dep}
    stack = [(new_dep, [task_id, new_dep])]
    while stack:
        node, path = stack.pop()
        for nxt in graph.get(node, ()):
            if nxt == task_id:
                return [*path, task_id]
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, [*path, nxt]))
    return None


def _apply_dep_edit(
    task_id: str,
    current: tuple[str, ...],
    add: tuple[str, ...],
    remove: tuple[str, ...],
    known: Collection[str],
    graph: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """削除してから追加した依存の並び。誤りは何も書かずに終了コード2。"""

    def refuse(message: str) -> NoReturn:
        print(f"usage: {message}", file=sys.stderr)
        raise SystemExit(2)

    both = [d for d in add if d in remove]
    if both:
        refuse(f"--add-deps と --remove-deps に同じ {','.join(both)} がある")
    absent = [d for d in remove if d not in current]
    if absent:
        refuse(f"--remove-deps の {','.join(absent)} は {task_id} の依存にない")
    unknown = [d for d in add if d not in known]
    if unknown:
        refuse(f"--add-deps の {','.join(unknown)} が台帳に無い（解決済みなら足さない）")
    # 辺を足す順に調べる。先に足した辺も道に入るので、追加どうしで閉じる循環も拾う。
    edges = {**graph, task_id: tuple(d for d in current if d not in remove)}
    for d in add:
        if d in edges[task_id]:
            continue
        cycle = _find_cycle(edges, task_id, d)
        if cycle is not None:
            refuse(f"--add-deps の {d} は循環になる（{'→'.join(cycle)}）")
        edges[task_id] = (*edges[task_id], d)
    return edges[task_id]


def cmd_edit(toplevel: str, args: argparse.Namespace) -> None:
    """ファイル方式の `edit`。本文と依存を書き換え、`## やること` を初めて書いた時点の判定を印に残す。"""
    edits_deps = bool(args.add_deps or args.remove_deps)
    if any([args.summary, args.difficulty, args.loopable, args.status]) or not (args.body_file or edits_deps or args.direct):
        print("usage: ファイル方式の edit は --body-file（と --section・--after-work・--change-frame）か --add-deps・--remove-deps・--direct だけ（ほかはタスクファイルを直に直す）", file=sys.stderr)
        raise SystemExit(2)
    if not args.body_file and any([args.section, args.after_work, args.change_frame]):
        print("usage: --section・--after-work・--change-frame は --body-file と一緒に使う", file=sys.stderr)
        raise SystemExit(2)
    task_id = args.task_id
    if not taskfile.ID_PATTERN.match(task_id):
        print(f"usage: {task_id!r} が T-999 の形式でない", file=sys.stderr)
        raise SystemExit(2)
    path = taskfile.task_path(os.path.join(toplevel, layout.task_dir(toplevel)), task_id)
    if not os.path.exists(path):
        print(f"NOT_READY\t{task_id}\t存在しない")
        raise SystemExit(4)
    task, err = taskfile.read_task_file(path)
    if err is not None or task is None:
        print(f"INVALID\t{err or '読めない'}")
        raise SystemExit(3)
    if task.status not in ("todo", "hold"):
        print(f"NOT_READY\t{task_id}\t{task.status}")
        raise SystemExit(4)
    dependencies = task.dependencies
    if edits_deps:
        add = _parse_dep_list(args.add_deps, taskfile.ID_PATTERN, "--add-deps", "T-999")
        remove = _parse_dep_list(args.remove_deps, taskfile.ID_PATTERN, "--remove-deps", "T-999")
        tasks, _, _ = load_tasks(toplevel)
        graph = {i: t.dependencies for i, t in tasks.items()}
        dependencies = _apply_dep_edit(task_id, task.dependencies, add, remove, set(tasks), graph)
    body = task.body
    if args.body_file:
        body = _section_body(args, task.body, read_body(args.body_file))
        error = taskfile.validate_edited_body(task.body, body)
        if error is not None:
            print(f"usage: {error}", file=sys.stderr)
            raise SystemExit(2)
        _refuse_frame_change(task_id, taskfile.changed_frame_sections(task.body, body), args.change_frame)
    direct, direct_off = _edited_direct(task.direct, args.direct, task.difficulty, body)

    root = ledger.ledger_root(cwd=toplevel)
    owner = ledger.read_owner(ledger.claim_dir(root, task_id))
    state = None
    if (
        args.body_file
        and owner is not None
        and owner.get("worktree") == toplevel
        and taskfile.has_plan(body)
        and taskfile.plan_changed(task.body, body)
        and ledger.read_plan_mark(root, task_id) is None
    ):
        own = f"{layout.task_dir(toplevel)}/{task_id}.md"
        state = _plan_state(toplevel, owner.get("head"), args.body_file, own)
        _refuse_plan_after_work(task_id, state, args.after_work)
        root = ledger.ledger_root_for_write(cwd=toplevel)

    rendered = taskfile.render(dataclasses.replace(task, dependencies=dependencies, body=body, direct=direct))
    with open(path, "w", encoding="utf-8") as f:
        f.write(rendered)
    if state is not None:
        ledger.write_plan_mark(root, task_id, state)
    print(f"EDITED\t{task_id}")
    if state == PLAN_AFTER_WORK:
        print(f"PLAN_AFTER_WORK\t{task_id}\t作業の後に書いた")
    _print_direct_off(task_id, direct_off)


def _edited_direct(current: str, wanted: str | None, difficulty: str, body: str) -> tuple[str, str | None]:
    """`edit` のあとの近道の印と、書き換えで基準を外れて外したときの理由。"""
    if wanted == "Y":
        _refuse_direct(difficulty, body)
        return "Y", None
    if wanted == "N" or current != "Y":
        return "N", None
    reason = taskfile.direct_refusal(difficulty, body)
    return ("N", reason) if reason is not None else ("Y", None)


def _print_direct_off(shown: str, reason: str | None) -> None:
    if reason is not None:
        print(f"DIRECT_OFF\t{shown}\t{reason}")


def _section_body(args: argparse.Namespace, current: str, given: str) -> str:
    """`--section` が無ければ渡された本文、あれば `current` のその節だけを `given` にした本文。"""
    if args.section is None:
        return given
    heading = taskfile.section_heading(args.section)
    if heading is None:
        print(f"usage: --section {args.section!r} は枠の見出しでない: " + "、".join(taskfile.SECTION_HEADINGS), file=sys.stderr)
        raise SystemExit(2)
    error = taskfile.check_section_content(given)
    replaced = taskfile.replace_section(current, heading, given) if error is None else None
    if error is not None or replaced is None:
        print(f"usage: {error or f'いまの本文に {heading} が無い'}", file=sys.stderr)
        raise SystemExit(2)
    return replaced


def _refuse_frame_change(shown: str, changed: list[str], allowed: bool) -> None:
    if changed and not allowed:
        print(
            f"FRAME_CHANGED\t{shown}\t書き込んでいない。{'・'.join(changed)}がいまの本文と違う。"
            f"変えてよいなら --change-frame を付けて打ち直す"
        )
        raise SystemExit(4)


def _refuse_plan_after_work(shown: str, state: str, after_work: bool) -> None:
    if state == PLAN_AFTER_WORK and not after_work:
        print(
            f"WORK_BEFORE_PLAN\t{shown}\t書き込んでいない。作業が始まっている。"
            f"作業の後と承知で書くなら --after-work を付けて打ち直す"
        )
        raise SystemExit(4)


def _file_unplanned_work(toplevel: str) -> list[str]:
    """この作業ツリーが印を持つ着手中のタスクのうち、`## やること` が空のまま作業が始まっているもの。"""
    root = ledger.ledger_root(cwd=toplevel)
    task_dir = os.path.join(toplevel, layout.task_dir(toplevel))
    found: list[str] = []
    for task_id in ledger.list_claims(root):
        owner = ledger.read_owner(ledger.claim_dir(root, task_id))
        if owner is None or owner.get("worktree") != toplevel:
            continue
        task, err = taskfile.read_task_file(taskfile.task_path(task_dir, task_id))
        if err is not None or task is None or task.status not in ("todo", "hold") or taskfile.has_plan(task.body):
            continue
        own = f"{layout.task_dir(toplevel)}/{task_id}.md"
        if _plan_state(toplevel, owner.get("head"), "-", own) == PLAN_AFTER_WORK:
            found.append(task_id)
    return found


def cmd_plan_check(toplevel: str, task_id: str) -> None:
    if not taskfile.ID_PATTERN.match(task_id):
        print(f"usage: {task_id!r} が T-999 の形式でない", file=sys.stderr)
        raise SystemExit(2)
    root = ledger.ledger_root(cwd=toplevel)
    owner = ledger.read_owner(ledger.claim_dir(root, task_id))
    if owner is None or owner.get("worktree") != toplevel:
        print(f"NOT_OWNER\t{task_id}")
        raise SystemExit(4)
    task, err = taskfile.read_task_file(taskfile.task_path(os.path.join(toplevel, layout.task_dir(toplevel)), task_id))
    if err is not None or task is None:
        print(f"INVALID\t{err or '読めない'}")
        raise SystemExit(3)
    plan_base = ledger.read_plan_base(root, task_id)
    stale = _registered_plan_changes(toplevel, plan_base, ledger.read_plan_tip(root, task_id), task.body)
    _print_plan_check(task_id, task.body, ledger.read_plan_mark(root, task_id), plan_base, stale)


def _plan_state(toplevel: str, head: str | None, body_file: str, own_path: str | None) -> str:
    """いま `## やること` を書くと、作業より先（`first`）か作業が始まってから（`after-work`）か。

    作業が始まっているとは、claim した時点の `head` より後のコミットがあるか、タスク自身のファイル
    （`own_path`）と `body_file` 以外に `git status` の変更があること。
    """
    if _commits_since_claim(toplevel, head):
        return PLAN_AFTER_WORK
    ignored = {own_path} if own_path else set()
    if body_file != "-":
        ignored.add(os.path.relpath(os.path.realpath(body_file), os.path.realpath(toplevel)))
    r = _run_git(toplevel, ["status", "--porcelain", "-z", "--untracked-files=all"])
    changed = _porcelain_paths(r.stdout) if r.returncode == 0 else []
    return PLAN_AFTER_WORK if any(p not in ignored for p in changed) else PLAN_FIRST


def _porcelain_paths(out: str) -> list[str]:
    """`git status --porcelain -z` の出力から変更のあるパスを取り出す（名前の変更は元の名前を読み飛ばす）。"""
    paths: list[str] = []
    entries = iter(out.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            next(entries, None)
    return paths


def _print_plan_check(
    shown: str, body: str, mark: str | None, plan_base: str | None, stale: list[str] | None
) -> None:
    """`stale` は登録時の計画が名指すファイルのうち着手時までに変わったもの（`_registered_plan_changes`）。

    段（`taskfile.plan_steps`）の読めない計画は、印によらず書き直しの経路（`PLAN_NOT_FIRST`・`steps`）へ回す。
    """
    has_plan = taskfile.has_plan(body)
    specs, steps_error = taskfile.plan_step_specs(body) if has_plan else ((), None)
    if has_plan and steps_error is not None:
        print(f"PLAN_NOT_FIRST\t{shown}\tsteps")
        return
    if has_plan and mark == PLAN_FIRST:
        print(f"PLAN_FIRST\t{shown}")
    elif has_plan and mark == PLAN_REGISTERED:
        print(f"PLAN_REGISTERED\t{shown}\t{plan_base or '?'}")
    elif has_plan and mark is None and stale is not None:
        print(f"PLAN_STALE\t{shown}\t{','.join(stale) or '?'}")
    else:
        print(f"PLAN_NOT_FIRST\t{shown}\t{'missing' if not has_plan else (mark or 'unrecorded')}")
    if not specs:
        return
    pairs, serial = taskfile.parallel_steps(specs)
    for a, b in pairs:
        print(f"PARALLEL\t{shown}\t{a},{b}")
    for a, b, shared in serial:
        print(f"SERIAL\t{shown}\t{a},{b}\t{','.join(shared)}")
    if not pairs:
        return
    for n, waits in enumerate(taskfile.step_waits(specs), start=1):
        print(f"STEP\t{shown}\t{n}\t{','.join(map(str, waits)) or 'なし'}")


# --- verify・verify-check（検証コマンドが通った中身の控え） ------------------

VERIFY_TAIL_LINES = 40


def _print_verify_end(folded_line: str, verdict: str, tail: str) -> None:
    print(verdict)
    print(tail)
    if folded_line:
        print(folded_line)
    print(verdict)


def cmd_verify(toplevel: str, unplanned_work: Callable[[], list[str]]) -> None:
    verify_command = ship.read_stamp_command(toplevel)
    if verify_command is None:
        print("NOTHING\t(検証コマンドが無い)")
        return
    unplanned = unplanned_work()
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
            _record_claimed(toplevel, "verify", result="FORMAT_FAILED", seconds=round(time.monotonic() - started))
            _print_verify_end(folded_line, f"FORMAT_FAILED\t{log_path}", format_tail)
            raise SystemExit(10)
    before = ledger.content_key(verify_command, cwd=toplevel)
    with open(log_path, "a", encoding="utf-8") as log:
        r = subprocess.run(["sh", "-c", verify_command], cwd=toplevel, stdout=log, stderr=subprocess.STDOUT)
    with open(log_path, encoding="utf-8", errors="replace") as f:
        tail = "\n".join(f.read().splitlines()[-VERIFY_TAIL_LINES:])
    if r.returncode != 0:
        ledger.clear_verify_stamp(cwd=toplevel)
        _record_claimed(toplevel, "verify", result="VERIFY_NOT_PASSED", seconds=round(time.monotonic() - started))
        _print_verify_end(folded_line, f"VERIFY_NOT_PASSED\t{ledger.keep_failed_log(log_path)}", tail)
        raise SystemExit(10)
    if ledger.content_key(verify_command, cwd=toplevel) != before:
        ledger.clear_verify_stamp(cwd=toplevel)
        verdict = f"VERIFIED_UNSTAMPED\t{log_path}"
    else:
        ledger.write_verify_stamp(before, cwd=toplevel)
        verdict = f"VERIFIED\t{before.tree}\t{log_path}"
    _record_claimed(toplevel, "verify", result=verdict.split("\t")[0], seconds=round(time.monotonic() - started))
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


# --- pause・handback-guard（委譲先の返却の関門） ------------------------------

HANDBACK_PLAN_OK = ("PLAN_FIRST", "PLAN_REGISTERED")
HANDBACK_VERIFY_OK = ("VERIFIED_SAME", "NOTHING")


def cmd_pause(toplevel: str, task_id: str | None = None, step: str | None = None) -> None:
    """いまの中身の鍵を、着手した作業ツリーと着手中のタスクの作業先の作業ツリーそれぞれに控える。

    段を名指せば、段の控え（`_write_step_stamps`）を種類 `pause` で残す。
    """
    if (task_id is None) != (step is None):
        print("usage: tw pause [<タスクID> <段の番号>]", file=sys.stderr)
        raise SystemExit(2)
    if task_id is not None and step is not None:
        shown, specs, n = _owned_step(toplevel, task_id, step)
        key = _write_step_stamps(toplevel, shown, specs, n, "pause")
        _record(toplevel, "pause", shown, step=n, steps=len(specs))
        print(f"PAUSED\t{key.tree}")
        return
    key = _current_key(toplevel)
    ledger.write_pause_stamp(key, cwd=toplevel)
    others = [tree for task_id in _claimed_here(toplevel) for tree in _task_trees(toplevel, task_id)[1:]]
    for tree in dict.fromkeys(others):
        ledger.write_pause_stamp(_current_key(tree), cwd=tree)
    _record_claimed(toplevel, "pause")
    print(f"PAUSED\t{key.tree}")


def cmd_step(toplevel: str, task_id: str, step: str) -> None:
    """途中の段（`## やること` の最後でない段）を済ませた印を、段の控え（`_write_step_stamps`）で残す。"""
    shown, specs, n = _owned_step(toplevel, task_id, step)
    if n == len(specs):
        print(f"LAST_STEP\t{shown}\t{n}/{len(specs)}\t最後の段は tw verify を通してから返す")
        raise SystemExit(4)
    key = _write_step_stamps(toplevel, shown, specs, n, "step")
    _record(toplevel, "step", shown, step=n, steps=len(specs))
    print(f"STEPPED\t{shown}\t{n}/{len(specs)}\t{key.tree}")


def _owned_step(toplevel: str, task_id: str, step: str) -> tuple[str, tuple[taskfile.PlanStep, ...], int]:
    """`(表示のタスクID, 段, 段の番号)`。自分の着手でなければ `NOT_OWNER`（終了コード4）、段が読めないか番号が段の外なら終了コード2。"""
    shown = beads.to_task_id(beads.to_bd_id(task_id)) if layout.read_config(toplevel).store == layout.STORE_BEADS else task_id
    if shown not in _claimed_here(toplevel):
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)
    specs, error = taskfile.plan_step_specs(_claimed_plan_body(toplevel, shown))
    if error is not None:
        print(f"usage: {error}", file=sys.stderr)
        raise SystemExit(2)
    if not step.isdigit() or not 1 <= int(step) <= len(specs):
        print(f"usage: 段の番号 {step!r} が 1〜{len(specs)} でない", file=sys.stderr)
        raise SystemExit(2)
    return shown, specs, int(step)


def _write_step_stamps(
    toplevel: str, shown: str, specs: tuple[taskfile.PlanStep, ...], n: int, kind: str
) -> ledger.ContentKey:
    """段 `n` の控えを、タスクの作業ツリー（`_task_trees`）それぞれに、段 `n` と並列の組になる段の触るファイルを
    外したいまの鍵で書き、着手中でないタスクの段の控えを消す。着手した作業ツリーの鍵を返す。"""
    excluded = taskfile.parallel_files(specs, n)
    claimed = _claimed_here(toplevel)
    key = _current_key(toplevel, excluded)
    for tree in _task_trees(toplevel, shown):
        tree_key = key if tree == toplevel else _current_key(tree, excluded)
        ledger.write_step_stamp(ledger.StepStamp(tree_key, shown, n, kind), cwd=tree)
        stale = [s.task_id for s in ledger.read_step_stamps(cwd=tree) if s.task_id not in claimed]
        ledger.remove_step_stamps(stale, cwd=tree)
    return key


LAP_STAGES = ("direct", "delegate", "accept", "review", "retro")


def cmd_lap(toplevel: str, task_id: str, stage: str) -> None:
    if stage not in LAP_STAGES:
        print(f"usage: 段 {stage!r} が {'・'.join(LAP_STAGES)} のどれでもない", file=sys.stderr)
        raise SystemExit(2)
    shown = beads.to_task_id(beads.to_bd_id(task_id)) if layout.read_config(toplevel).store == layout.STORE_BEADS else task_id
    if shown not in _claimed_here(toplevel):
        print(f"NOT_CLAIMED\t{shown}")
        return
    _record(toplevel, "lap", shown, stage=stage)
    print(f"LAPPED\t{shown}\t{stage}")


def _task_trees(toplevel: str, task_id: str) -> list[str]:
    """タスクの作業ツリー。先頭は着手した `toplevel`。

    `## やること` の `### 作業先` が別のリポジトリなら、そのリポジトリの作業ツリーのうち、
    枝の名前がタスクIDを小文字にしたものを続ける。
    """
    work_repo, _ = taskfile.plan_work_repo(_claimed_plan_body(toplevel, task_id))
    if work_repo is None or not os.path.isdir(work_repo):
        return [toplevel]
    try:
        if ledger.git_common_dir(work_repo) == ledger.git_common_dir(toplevel):
            return [toplevel]
        worktrees = ledger.list_worktrees(cwd=work_repo)
    except ledger.GitCommandError:
        return [toplevel]
    return [toplevel, *(w.path for w in worktrees if w.branch == task_id.lower())]


def _current_key(tree: str, excluded: tuple[str, ...] = ()) -> ledger.ContentKey:
    """`tree` のいまの中身の鍵（検証コマンドは `tree` の設定ファイルのもの）。`excluded` のパスを木から外す。"""
    return ledger.content_key(ship.read_stamp_command(tree) or "", cwd=tree, excluded=excluded)


def _claimed_plan_body(toplevel: str, task_id: str) -> str:
    """着手中のタスクの `## やること` を含む本文（Beads 方式は `## やること` の節だけ）。読めなければ空。"""
    if layout.read_config(toplevel).store == layout.STORE_BEADS:
        issue = beads.show(toplevel, beads.to_bd_id(task_id))
        return f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n" if issue is not None else ""
    task, _ = taskfile.read_task_file(taskfile.task_path(os.path.join(toplevel, layout.task_dir(toplevel)), task_id))
    return task.body if task is not None else ""


def _handback_refusal(where: str) -> str | None:
    """`where` の作業ツリーから委譲先が返すのを拒む理由。通すなら `None`。

    控えのタスクごとに、その作業ツリー（`_task_trees`）を1つずつ見る。作業ツリーを通すのは、作業が無い
    （着手した作業ツリーでは `claim` 時の `HEAD` より後のコミットも、タスク自身のファイル以外の変更も無い。
    作業先の作業ツリーでは主ブランチとの分かれ目より後のコミットも、変更も無い）か、`plan-check` が通ったうえで
    その作業ツリーの `verify-check` が通っているか `tw step` の印が最後でない段をいまの中身で指しているか、
    その作業ツリーの `tw pause` の控えがいまの中身と同じとき。段の印のいまの中身は、その段と並列の組になる段の
    触るファイルを外して取る。
    """
    if not os.path.isdir(where):
        return None
    claims = ledger.open_claims(cwd=where)
    if not claims:
        return None
    toplevel = ledger.git_toplevel(where)
    store = layout.read_config(toplevel).store
    gaps = [line for task_id in claims for line in _handback_gaps(toplevel, task_id, store)]
    if not gaps:
        return None
    shown = ",".join(claims)
    return (
        f"着手の印（{shown}）がある作業ツリー（{toplevel}）のタスクに作業があるのに、計画か検証が欠けたまま返そうとした"
        f"（{' / '.join(gaps)}）。## やること が無ければ tw edit <ID> --section 'やること' --body-file - で書き、"
        f"tw verify を通してから返す（作業先が別のリポジトリなら、コミットのあとに作業先の作業ツリーで打つ）。"
        f"最後でない段を済ませて返すときは tw step <ID> <段の番号> を打ってから返す。目視待ちで返すとき・判断が要って止めて返すとき・計画を作業の後に書いたときは、"
        f"tw pause を打ってから返す（並列の段の担当は tw pause <ID> <段の番号>。tw step・tw pause は着手した作業ツリーで打つ。打ったあとに中身を変えたら打ち直す）"
    )


def _handback_gaps(toplevel: str, task_id: str, store: str) -> list[str]:
    """作業がある作業ツリーの、通っていない `plan-check`・`verify-check` の行。作業が無ければ空。

    作業先の作業ツリーの `verify-check` の行には、その作業ツリーのパスを添える。
    """
    if store == layout.STORE_BEADS:
        issue = beads.show(toplevel, beads.to_bd_id(task_id))
        metadata = issue.raw.get("metadata") if issue is not None else None
        head = metadata.get(beads.CLAIM_HEAD_KEY) if isinstance(metadata, dict) else None
        own_path = None
        plan_line = _first_output_line(lambda: cmd_beads_plan_check(toplevel, task_id))
    else:
        owner = ledger.read_owner(ledger.claim_dir(ledger.ledger_root(cwd=toplevel), task_id)) or {}
        head = owner.get("head")
        own_path = f"{layout.task_dir(toplevel)}/{task_id}.md"
        plan_line = _first_output_line(lambda: cmd_plan_check(toplevel, task_id))
    plan_ok = plan_line.split("\t")[0] in HANDBACK_PLAN_OK
    gaps: list[str] = []
    for tree in _task_trees(toplevel, task_id):
        work_head, work_own_path = (head, own_path) if tree == toplevel else (_fork_point(tree), None)
        if _plan_state(tree, work_head, "-", work_own_path) == PLAN_FIRST:
            continue
        stamped = _current_stamp_kinds(toplevel, tree, task_id)
        if "pause" in stamped:
            continue
        if not plan_ok and plan_line not in gaps:
            gaps.append(plan_line)
        if plan_ok and "step" in stamped:
            continue
        verify_line = _tree_verify_line(tree)
        if verify_line.split("\t")[0] not in HANDBACK_VERIFY_OK:
            gaps.append(verify_line if tree == toplevel else f"{verify_line}（{tree}）")
    return gaps


def _tree_verify_line(tree: str) -> str:
    """`tree` の `verify-check` の行。`tw verify` がそこで形式のために打てない（`_format_refusal`）なら、控えを書けないので `NOTHING`。"""
    if _format_refusal(tree) is not None:
        return "NOTHING\t(tw verify が打てない形式)"
    return _first_output_line(lambda: cmd_verify_check(tree))


def _fork_point(tree: str) -> str | None:
    """`tree` の `HEAD` と主ブランチの分かれ目。引けなければ `None`。"""
    r = _run_git(tree, ["merge-base", "HEAD", ledger.base_branch(tree)])
    return r.stdout.strip() if r.returncode == 0 else None


def _current_stamp_kinds(toplevel: str, tree: str, task_id: str) -> set[str]:
    """`tree` の控えのうち、いまの中身と同じものの種類（`pause`・`step`）。

    `tw pause` の控えはいまの鍵と、段の控えはその段と並列の組になる段の触るファイルを外したいまの鍵と照らす。
    段の `step` の控えは段がいまの計画の最後より前のときだけ効く。
    """
    keys: dict[tuple[str, ...], ledger.ContentKey] = {}

    def key(excluded: tuple[str, ...]) -> ledger.ContentKey:
        if excluded not in keys:
            keys[excluded] = _current_key(tree, excluded)
        return keys[excluded]

    kinds: set[str] = set()
    pause = ledger.read_pause_stamp(cwd=tree)
    if pause is not None and pause == key(()):
        kinds.add("pause")
    stamps = [s for s in ledger.read_step_stamps(cwd=tree) if s.task_id == task_id]
    if not stamps:
        return kinds
    specs, _ = taskfile.plan_step_specs(_claimed_plan_body(toplevel, task_id))
    for stamp in stamps:
        if stamp.kind in kinds or stamp.step > len(specs) or (stamp.kind == "step" and stamp.step == len(specs)):
            continue
        if stamp.key == key(taskfile.parallel_files(specs, stamp.step)):
            kinds.add(stamp.kind)
    return kinds


def _first_output_line(command: Callable[[], None]) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            command()
        except SystemExit:
            pass
    lines = buffer.getvalue().splitlines()
    return lines[0] if lines else "?"


# --- ship（5.8・6章） --------------------------------------------------------


def _release_own_claims_when_shipped(root: str, toplevel: str) -> list[str]:
    """4.4・5.8手順7: 自分の作業ツリーの印のうち、主ブランチ（HEAD）で done/dropped に
    なったものを消す。主ブランチを正として読む（`load_tasks` は git show 経由なので、
    直前に送った変更もここで反映済みのものとして見える）。"""
    tasks, _invalid, _local_only = load_tasks(toplevel)
    released: list[str] = []
    for tid in ledger.list_claims(root):
        owner = ledger.read_owner(ledger.claim_dir(root, tid))
        if owner is None or owner.get("worktree") != toplevel:
            continue
        t = tasks.get(tid)
        if t is not None and t.status in ("done", "dropped"):
            ledger.release_claim(root, tid, toplevel)
            ledger.clear_plan_base(root, tid)
            ledger.clear_open_claim(tid, cwd=toplevel)
            released.append(tid)
    return released


@dataclass(frozen=True)
class ShipHooks:
    """`ship` のうち、タスクの置き場（ファイル方式・Beads 方式）で変わる3点。

    `release_shipped`: 送り終えたあと自分の印を消し、消した ID を返す。
    `claimed_branch`: `claim` した時点の枝（作業ブランチから降りる先）。
    `after_send`: `SHIPPED`・`NOTHING` の行のあとに足す行（トラッカー・バックアップ）。
    """

    release_shipped: Callable[[], list[str]]
    claimed_branch: Callable[[str], "str | None"]
    after_send: Callable[[], list[str]]


def _file_ship_hooks(toplevel: str) -> ShipHooks:
    root = ledger.ledger_root_for_write(cwd=toplevel)
    return ShipHooks(
        release_shipped=lambda: _release_own_claims_when_shipped(root, toplevel),
        claimed_branch=lambda tid: (ledger.read_owner(ledger.claim_dir(root, tid)) or {}).get("branch"),
        after_send=lambda: [],
    )


def cmd_ship(toplevel: str, hooks: "ShipHooks | None" = None) -> None:
    if not ledger.is_clean(cwd=toplevel):
        print("DIRTY")
        raise SystemExit(4)

    def resolved_hooks() -> ShipHooks:
        """ファイル方式の hooks は台帳の置き場を作るので、送る道では `require_git_writable` のあとに作る。"""
        return hooks if hooks is not None else _file_ship_hooks(toplevel)

    branch_setting = layout.read_config(toplevel).branch
    base = ledger.base_branch(toplevel)
    branch = ledger.current_branch(cwd=toplevel)

    if branch == base:
        # 4.4: 主ブランチを出している作業ツリーで起こしたときは送る段が無い。
        local_hooks = resolved_hooks()
        ledger.clear_verify_owed(cwd=toplevel)
        released = local_hooks.release_shipped()
        _record_shipped(toplevel, released)
        print(f"SHIPPED\t{base}\t(送る段なし)\treleased={','.join(released) or '-'}")
        _print_lines(local_hooks.after_send())
        return

    ahead = _run_git(toplevel, ["rev-list", "--count", f"{base}..HEAD"])
    ahead_count = int(ahead.stdout.strip()) if ahead.returncode == 0 and ahead.stdout.strip().isdigit() else 0
    if ahead_count == 0:
        local_hooks = resolved_hooks()
        ledger.clear_verify_owed(cwd=toplevel)
        local_hooks.release_shipped()
        print(f"NOTHING\t({base} に無いコミットが無い)")
        _print_lines(local_hooks.after_send())
        return

    worktrees = ledger.list_worktrees(cwd=toplevel)
    base_worktree = ship.find_base_worktree(worktrees, toplevel, base)
    if base_worktree is not None and not ledger.is_clean(cwd=base_worktree.path):
        print(f"MAIN_DIRTY\t{base_worktree.path}")
        raise SystemExit(4)
    ledger.require_git_writable(toplevel)
    hooks = resolved_hooks()

    old_base = _run_git(toplevel, ["rev-parse", base]).stdout.strip()
    config = layout.read_config(toplevel)
    verify_command = config.verify
    verify_owed = ledger.is_verify_owed(cwd=toplevel)
    preship_command = config.verify_before_ship
    stamp = ledger.read_verify_stamp(cwd=toplevel)
    preship_stamped = (
        preship_command is not None
        and stamp is not None
        and stamp.verify_command == preship_command
        and stamp.tree == ledger.content_tree(toplevel)
    )
    outcome = ship.attempt(
        toplevel,
        base_worktree,
        verify_command,
        base,
        verify_owed=verify_owed,
        preship_command=preship_command,
        preship_stamped=preship_stamped,
    )

    if outcome.kind in ("CONFLICT", "VERIFY_FAILED", "RACE"):
        _record_claimed(toplevel, "ship", result=outcome.kind)
    if outcome.kind == "CONFLICT":
        print("CONFLICT\t" + (",".join(outcome.conflict_files) or "?"))
        raise SystemExit(7)
    if outcome.kind == "VERIFY_FAILED":
        # 付け替え済みのまま送っていない。次の `ship` は打ち直しでも検証を飛ばさない（T-777）。
        ledger.mark_verify_owed(outcome.verify_command or "", cwd=toplevel)
        print(f"VERIFY_FAILED\t{outcome.verify_command}\t{outcome.verify_log}")
        if outcome.verify_tail:
            print(outcome.verify_tail)
        raise SystemExit(8)
    if outcome.kind == "RACE":
        print("RACE\t3")
        raise SystemExit(9)

    # ここに来るのは `SENT` だけ（`attempt` は借りがあれば検証を通してからでないと
    # `SENT` を返さない）。送れたので借りは無い。
    ledger.clear_verify_owed(cwd=toplevel)

    branch_note = f"branch={branch}"
    if branch_setting in ("既定", "作業ブランチを切る") and FEATURE_BRANCH.fullmatch(branch):
        # 印を消す前に読む（戻り先は印にある）。
        branch_note = _leave_feature_branch(toplevel, branch, hooks.claimed_branch)

    released = hooks.release_shipped()
    _record_shipped(toplevel, released)
    new_base = _run_git(toplevel, ["rev-parse", base]).stdout.strip()
    preship_note = ""
    if preship_command is not None:
        preship_note = "\tpreship=ran" if outcome.preship_ran else "\tpreship=skipped"
    print(
        f"SHIPPED\t{old_base}..{new_base}\trebased={'yes' if outcome.rebased else 'no'}"
        f"\tverify={outcome.verify_state}{preship_note}\ttries={outcome.tries}\treleased={','.join(released) or '-'}"
        f"\t{branch_note}"
    )
    _print_lines(hooks.after_send())


def _record_shipped(toplevel: str, released: list[str]) -> None:
    for task_id in released:
        _record(toplevel, "ship", task_id, result="SHIPPED")


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


FEATURE_BRANCH = layout.FEATURE_BRANCH_PATTERN


def _leave_feature_branch(toplevel: str, branch: str, claimed_branch: Callable[[str], "str | None"]) -> str:
    """送り終えた `feature/T-xxx` から降りて枝を消す（6.2手順7）。出力の `branch=…` 欄を返す。

    戻り先は `claim` した時点の枝（`claimed_branch`。ファイル方式は印の owner の `branch=`）→ 主ブランチの順に試す。主ブランチを
    別の作業ツリー（本体）が出していると `checkout` は通らないので、作業ツリー固有の枝が
    あればそこへ戻して主ブランチまで追い付かせる。どちらにも移れなければ主ブランチの位置で
    detached HEAD にする（枝を黙って残さない。detached のままでも次の `claim` は主ブランチから切る）。
    消せなかったときは `kept=<枝>` を添えて知らせる。
    """
    base = ledger.base_branch(toplevel)
    m = FEATURE_BRANCH.fullmatch(branch)
    back = claimed_branch(m.group(1)) if m else None
    targets = [b for b in dict.fromkeys([back, base]) if b and b not in (branch, "HEAD")]

    landed = None
    for target in targets:
        if _run_git(toplevel, ["checkout", "-q", target]).returncode == 0:
            if target != base:
                _run_git(toplevel, ["merge", "--ff-only", "-q", base])
            landed = target
            break
    if landed is None:
        if _run_git(toplevel, ["checkout", "-q", "--detach", base]).returncode != 0:
            return f"branch={branch}\tkept={branch}"
        landed = "detached"

    if _run_git(toplevel, ["branch", "-d", branch]).returncode != 0:
        return f"branch={landed}\tkept={branch}"
    return f"branch={landed}"


# --- prune（5.10） ----------------------------------------------------------

# 1件ごとの振り返りが済んだ印。`## 結果` の中のこの形の行（WORKFLOW.md「結果の書き方と知見の置き場」）。
REVIEWED_LINE = re.compile(r"^- 振り返り:")
# 消せるものがこの件数に届くまでは消さない。毎サイクル1件ずつ消すと削除だけのコミットが
# タスクと同じ数だけ積もるため、まとめて1コミットにする。
PRUNE_MIN_DEFAULT = 10


def cmd_prune(toplevel: str, dry_run: bool, minimum: int) -> None:
    """振り返りが済んだ done/dropped のタスクファイルを `git rm` して stage する（コミットしない）。

    判定は `HEAD` の版で done/dropped・台帳に印が無い・`## 結果` に `- 振り返り:` の行がある
    （`reviewed` ＝1件ごとの振り返り済み）。印のあるタスクを除くのは、`ship` が主ブランチの版で
    done を見て印を消すため。対象が `minimum` 件に届かなければ何もしない（`NOTHING`）。
    """
    if not dry_run and not ledger.is_clean(cwd=toplevel):
        print("DIRTY")
        raise SystemExit(4)

    claims = set(ledger.list_claims(ledger.ledger_root(cwd=toplevel)))
    targets: list[tuple[str, str]] = []
    for tid, task in sorted(_tasks_at(toplevel, "HEAD").items(), key=lambda kv: taskfile.id_number(kv[0])):
        if task.status not in ("done", "dropped") or tid in claims:
            continue
        if _has_review_line(task.body):
            targets.append((tid, "reviewed"))

    if not targets:
        print("NOTHING\t(消せるタスクファイルが無い)")
        return
    if len(targets) < minimum:
        print(f"NOTHING\t(消せるのは{len(targets)}件で、{minimum}件に届くまで溜める)")
        return
    if not dry_run:
        ledger.require_git_writable(toplevel)
    for tid, reason in targets:
        print(f"PRUNE\t{tid}\t{reason}")
    if dry_run:
        print(f"PLAN\t{len(targets)}")
        return
    paths = [f"{layout.task_dir(toplevel)}/{tid}.md" for tid, _ in targets]
    r = _run_git(toplevel, ["rm", "-q", "--", *paths])
    if r.returncode != 0:
        raise ledger.GitCommandError(f"git rm が失敗した: {r.stderr.strip()}")
    print(f"PRUNED\t{len(targets)}")


def _tasks_at(toplevel: str, rev: str) -> dict[str, taskfile.Task]:
    r = _run_git(toplevel, ["ls-tree", "--name-only", rev, f"{layout.task_dir(toplevel)}/"])
    tasks: dict[str, taskfile.Task] = {}
    for path in r.stdout.splitlines() if r.returncode == 0 else []:
        stem = os.path.splitext(os.path.basename(path))[0]
        if not path.endswith(".md") or not taskfile.ID_PATTERN.match(stem):
            continue
        shown = _run_git(toplevel, ["show", f"{rev}:{path}"])
        parsed, err = taskfile.parse(shown.stdout) if shown.returncode == 0 else (None, "読めない")
        if err is None and parsed is not None and parsed.id == stem:
            tasks[stem] = parsed
    return tasks


def _has_review_line(body: str) -> bool:
    in_result = False
    for line in body.splitlines():
        if line.startswith("## "):
            in_result = line.strip() == taskfile.RESULT_HEADING
        elif in_result and REVIEWED_LINE.match(line):
            return True
    return False


# --- migrate（5.9・10章） ----------------------------------------------------


def cmd_migrate(toplevel: str, dry_run: bool) -> None:
    if not dry_run:
        ledger.require_git_writable(toplevel)
    result = legacy.migrate(toplevel, dry_run)
    if result.kind == "DIRTY":
        print("DIRTY")
        raise SystemExit(4)
    if result.kind == "NOTHING":
        print(f"NOTHING\t{result.detail}")
        return
    if result.kind == "INVALID":
        print(f"INVALID\t{result.detail}")
        raise SystemExit(3)
    if result.kind == "NOT_READY":
        print(f"NOT_READY\t{result.detail}")
        raise SystemExit(4)

    for path in result.written:
        print(f"WRITE\t{path}")
    if result.moved_sections > 0:
        print(f"MOVE\tprogress 完了したこと {result.moved_sections}小節 → docs/history/progress.md")
    if result.leftover_counts is not None:
        unresolved, note = result.leftover_counts
        print(f"LEFTOVER\tdevelop/progress.md\t未解決 {unresolved} / 注意 {note}")
        if result.preamble_kept:
            print("LEFTOVER\tdevelop/progress.md\t前置き文を残した")
    elif result.progress_removed:
        print("REMOVE\tdevelop/progress.md")
    print("REMOVE\tdevelop/tasks.json")
    print(f"{'PLAN' if dry_run else 'MIGRATED'}\t{result.task_count}")


# --- migrate-layout ----------------------------------------------------------


def cmd_migrate_layout(toplevel: str, dry_run: bool) -> None:
    if not dry_run:
        ledger.require_git_writable(toplevel)
    outcome = relayout.migrate_layout(toplevel, dry_run)
    if outcome.kind == "DIRTY":
        print("DIRTY")
        raise SystemExit(4)
    for line in outcome.lines:
        print(line)
    if outcome.kind == "BUSY":
        raise SystemExit(4)
    if outcome.kind == "INVALID":
        raise SystemExit(3)
    if outcome.kind in ("PLAN", "MIGRATED"):
        print(outcome.kind)


# --- config-doctor（T-021） --------------------------------------------------


def cmd_config_doctor(toplevel: str) -> None:
    """設定と形式のズレの点検（読むだけ・`--fix` は無い）。検査1つに1行、タブ区切りで出す
    （`task status` と同じ形。人向けの段落は出さない）。判定は書き起こさず、既存の部品
    （`ledger.base_branch`・`init.check_config`・旧形式の残りの
    直接の検出）を呼ぶだけ（正典 WORKFLOW.md「ファイル配置と設定ファイル」）。

    `base_branch`・`config`・`legacy` の3行を必ず出す（途中の検査が INVALID でも残りの検査は続ける。呼び出し側が全体像を
    1回の実行で見られるようにする）。終了コードは 0（全部OK）／1（直すものがある）／
    3（INVALID）——最悪のものを返す。`main` の `ledger.NoBaseBranch`・`layout.ConfigError`
    の受け皿（呼び出し側で即 `INVALID` にする仕組み）は使わない。ここで捕まえて次の検査に進む。
    """
    exit_code = 0

    # 検査1: 主ブランチが何で決まったか（順1〜3。決まらなければ INVALID）。
    try:
        base = ledger.base_branch(toplevel)
        order = ledger.base_branch_order(toplevel)
        print(f"base_branch\tOK\t{base}\t順{order}")
    except (ledger.NoBaseBranch, layout.ConfigError) as e:
        print(f"base_branch\tINVALID\t{e}")
        exit_code = 3

    # 検査2: 設定が読めるか（`init.check_config`）。旧い節で読んでいればその行も出す。
    line, config = init.check_config(toplevel)
    kind = line.partition("\t")[0]
    print(f"config\t{line}")
    if kind == "INVALID":
        exit_code = 3
    elif kind != "OK":
        exit_code = max(exit_code, 1)
    if config is not None and config.legacy:
        print(f"old_section\tFOUND\t{config.source}")

    # 検査3: 旧形式の残り（develop/tasks.json・develop/progress.md）があるか。
    tasks_json = os.path.exists(os.path.join(toplevel, "develop", "tasks.json"))
    progress_md = os.path.exists(os.path.join(toplevel, "develop", "progress.md"))
    if tasks_json or progress_md:
        leftover = ", ".join(
            p
            for p, present in (("develop/tasks.json", tasks_json), ("develop/progress.md", progress_md))
            if present
        )
        print(f"legacy\tFOUND\t{leftover}\ttw migrate --dry-run")
        exit_code = max(exit_code, 1)
    else:
        print("legacy\tOK")

    # 検査4: Beads 方式の用意とトラッカー。
    if config is not None and "store" in config.written:
        store_code = _doctor_store(toplevel, config)
        exit_code = 3 if 3 in (exit_code, store_code) else max(exit_code, store_code)

    if exit_code:
        raise SystemExit(exit_code)


# --- Beads 方式（正典 WORKFLOW.md「Beads 方式」） ---------------------------------
#
# 錠・本文・履歴は Beads（`bd`）が持ち、ここは Beads とトラッカーと git をつなぐ。出力の先頭語・
# 列・終了コードはファイル方式と同じにし、スキルが方式で分かれずに済むようにする。違うのは、
# 3列目の置き場が `beads:<Beads の ID>` になること、`done` が stage しないこと、行のあとに
# `TRACKER`・`BACKUP` の行が付くことだけ。


def _actor(toplevel: str) -> str:
    """Beads の actor（着手の印の持ち主）は作業ツリーの名前。"""
    return os.path.basename(toplevel)


def _bd_task_id(task_id: str) -> str:
    """`T-123`・`GH-5`・`PROJ-123`・Beads の ID（仮の `gh-new-…`・取り込んだままの `gh-1790…-1-4dfc`）を受ける。"""
    if not re.fullmatch(r"(?i:t|gh)-[0-9a-z.-]+|(?i:[a-z][a-z0-9_]+)-\d+", task_id):
        print(f"usage: {task_id!r} が T-999・GH-5・PROJ-123 の形式でない", file=sys.stderr)
        raise SystemExit(2)
    return beads.to_bd_id(task_id)


@dataclass(frozen=True)
class BeadsSnapshot:
    tasks: dict  # T-xxx → taskfile.Task
    invalid: dict  # 見せる ID → 理由
    triage: list  # 振り分け前の beads.Issue（番号・difficulty・loopable が無い）
    issues: dict  # 見せる ID → beads.Issue
    claims: set  # in_progress の見せる ID


def _beads_snapshot(toplevel: str) -> BeadsSnapshot:
    tasks: dict[str, taskfile.Task] = {}
    invalid: dict[str, str] = {}
    triage: list[beads.Issue] = []
    issues: dict[str, beads.Issue] = {}
    claims: set[str] = set()
    for issue in beads.list_issues(toplevel):
        shown = beads.to_task_id(issue.bd_id)
        issues[shown] = issue
        task, err = beads.to_task(issue)
        if err is not None:
            invalid[shown] = err
            continue
        if task is None:
            triage.append(issue)
            continue
        tasks[shown] = task
        if issue.status == "in_progress":
            claims.add(shown)
    return BeadsSnapshot(tasks, invalid, triage, issues, claims)


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
    age = _age_seconds(issue.started_at)
    return "CLAIMED", f"{owner} {format_elapsed(age) if age is not None else '?'}"


def _task_commit_on_base(toplevel: str, task_id: str, base: str, since: str | None) -> bool:
    """主ブランチに、着手より後の `T-xxx:`（`T-xxx, …` を含む）の件名のコミットがあるか。"""
    args = ["log", base, "-E", f"--grep=^{task_id}[:,]", "--format=%H", "-1"]
    age = _age_seconds(since)
    if age is not None:
        # コミットの時刻は秒で丸まるので、着手と同じ秒のコミットを落とさないよう1秒さかのぼる。
        args.insert(2, f"--since={int(age) + 1} seconds ago")
    r = _run_git(toplevel, args)
    return r.returncode == 0 and r.stdout.strip() != ""


def _age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - at).total_seconds()


def cmd_beads_status(toplevel: str, show_all: bool, check: bool) -> None:
    snap = _beads_snapshot(toplevel)
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


def cmd_beads_new(toplevel: str, args: argparse.Namespace) -> None:
    summary = args.summary.strip()
    if summary == "" or "\n" in args.summary:
        print("usage: --summary は改行を含まない1行にする", file=sys.stderr)
        raise SystemExit(2)
    deps = tuple(d for d in (x.strip() for x in args.deps.split(",")) if d) if args.deps else ()
    for d in deps:
        if not layout.ANY_ID_PATTERN.match(d):
            print(f"usage: --deps の {d!r} が T-999・GH-5・PROJ-123 の形式でない", file=sys.stderr)
            raise SystemExit(2)
    body = read_body(args.body_file)
    error = taskfile.validate_new_body(body, args.hold)
    if error is not None:
        print(f"usage: {error}", file=sys.stderr)
        raise SystemExit(2)
    if args.direct:
        _refuse_direct(args.difficulty, body)
    plan_base = _registered_plan_base(toplevel, body) if taskfile.has_plan(body) else None

    snap = _beads_snapshot(toplevel)
    missing = [d for d in deps if d not in snap.issues]
    if missing:
        print(f"usage: --deps の {','.join(missing)} が Beads に無い（解決済みなら外す）", file=sys.stderr)
        raise SystemExit(2)

    parts = beads.split_body(body)
    actor = _actor(toplevel)
    labels = f"{beads.DIFFICULTY_LABEL}{args.difficulty},{beads.LOOPABLE_LABEL}{args.loopable}"
    if args.direct:
        labels += f",{beads.DIRECT_ON}"

    def create_cmd(bd_id: str) -> list[str]:
        cmd = ["create", "--id", bd_id, "--title", summary, "--body-file", "-", "-l", labels, "--silent"]
        if parts.acceptance:
            cmd += ["--acceptance", parts.acceptance]
        if parts.notes:
            cmd += ["--notes", parts.notes]
        if plan_base is not None:
            cmd += ["--metadata", json.dumps({beads.PLAN_BASE_KEY: plan_base})]
        if args.hold:
            cmd += ["-s", beads.HOLD_STATUS]
        if deps:
            cmd += ["--deps", ",".join(beads.to_bd_id(d) for d in deps)]
        return cmd

    trk = tracker.session(toplevel)
    if trk.bidirectional:
        # 番号は Issue を立てるまで決まらないので、仮の ID で作ってから `gh-<Issue 番号>` へ付け替える。
        provisional = f"{beads.PROVISIONAL_PREFIX}{re.sub(r'[^0-9a-z]+', '-', actor.lower()).strip('-')}-{time.time_ns()}"
        beads.run_ok(toplevel, create_cmd(provisional), actor, parts.description)
        bd_id, lines = trk.register(provisional)
        print(f"CREATED\t{beads.to_task_id(bd_id)}\tbeads:{bd_id}")
        _print_lines(lines)
        return

    number = _next_number(toplevel, snap)
    for _ in range(NEW_ATTEMPTS):
        bd_id = beads.format_bd_id(number)
        r = beads.run(toplevel, create_cmd(bd_id), actor, parts.description)
        if r.returncode == 0:
            beads.write_last_id(toplevel, number)
            print(f"CREATED\t{beads.to_task_id(bd_id)}\tbeads:{bd_id}")
            _print_lines(trk.after([bd_id]))
            return
        if "already exists" not in (r.stderr + r.stdout):
            raise beads.BeadsError(f"bd create が失敗: {(r.stderr or r.stdout).strip()}")
        number += 1
    print(f"LOCKED\t{NEW_ATTEMPTS}回続けて番号を取られた")
    raise SystemExit(4)


# 番号の取り合いに続けて負けたら諦める回数（`bd create --id` は同じ番号を1つしか作らない）。
NEW_ATTEMPTS = 20


def _next_number(toplevel: str, snap: BeadsSnapshot) -> int:
    """Beads の番号・`bd kv` の最後の番号・主ブランチの `<根>/task/` と `docs/history/tasks.md`・
    ファイル方式の台帳の `last-id`（残っていれば）のうち最大の次。"""
    candidates = [0]
    candidates += [n for n in (beads.id_number(i.bd_id) for i in snap.issues.values()) if n is not None]
    last = beads.read_last_id(toplevel)
    if last is not None:
        candidates.append(last)
    base = ledger.base_branch(toplevel)
    candidates += [
        taskfile.id_number(os.path.splitext(f)[0])
        for f in _list_base_task_filenames(toplevel, base)
        if taskfile.ID_PATTERN.match(os.path.splitext(f)[0])
    ]
    candidates += [taskfile.id_number(i) for i in _history_ids_at_base(toplevel)]
    ledger_last = ledger.read_last_id(ledger.ledger_root(cwd=toplevel))
    if ledger_last is not None:
        candidates.append(ledger_last)
    return max(candidates) + 1


def cmd_beads_claim(toplevel: str, task_id: str) -> None:
    bd_id = _bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    branch_setting, base = _claim_preflight(toplevel)

    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None:
        print(f"NOT_READY\t{shown}\t存在しない")
        raise SystemExit(4)
    task, err = beads.to_task(issue)
    if err is not None:
        print(f"INVALID\t{err}")
        raise SystemExit(3)
    if task is None:
        print(f"NOT_READY\t{shown}\tTRIAGE")
        raise SystemExit(4)
    if issue.status == "in_progress":
        _print_taken(shown, issue)
    if task.status != "todo":
        print(f"NOT_READY\t{shown}\t{task.status}")
        raise SystemExit(4)
    blocked = [beads.to_task_id(d) for d in issue.dependencies if not _beads_resolved(toplevel, d)]
    if blocked:
        print(f"NOT_READY\t{shown}\tBLOCKED:{','.join(blocked)}")
        raise SystemExit(4)

    # `bd update --claim` は依存を見ないので、上で依存を確かめてから取る。取り合いの勝ち負けは Beads が決める。
    actor = _actor(toplevel)
    branch_after_sync = ledger.current_branch(cwd=toplevel)
    claim_args = ["update", bd_id, "--claim", "--set-metadata", f"{beads.CLAIM_BRANCH_KEY}={branch_after_sync}"]
    head = ledger.head_sha_or_none(cwd=toplevel)
    if head is not None:
        claim_args += ["--set-metadata", f"{beads.CLAIM_HEAD_KEY}={head}"]
    metadata = issue.raw.get("metadata")
    plan_base = metadata.get(beads.PLAN_BASE_KEY) if isinstance(metadata, dict) else None
    plan = str(issue.raw.get("notes") or "")
    plan_body = f"{taskfile.PLAN_HEADING}\n{plan}\n"
    registered = False
    if plan_base and not taskfile.is_blank(plan):
        tip = _plan_tip(toplevel, plan_body)
        if tip is not None:
            claim_args += ["--set-metadata", f"{beads.PLAN_TIP_KEY}={tip}"]
        if _registered_plan_changes(toplevel, plan_base, tip, plan_body) == []:
            claim_args += ["--set-metadata", f"{beads.PLAN_KEY}={PLAN_REGISTERED}"]
            registered = True
        else:
            claim_args += ["--unset-metadata", beads.PLAN_KEY]
    r = beads.run(toplevel, claim_args, actor)
    if r.returncode != 0:
        again = beads.show(toplevel, bd_id)
        if again is not None and again.status == "in_progress":
            _print_taken(shown, again)
        raise beads.BeadsError(f"bd update --claim が失敗: {(r.stderr or r.stdout).strip()}")
    ledger.mark_open_claim(shown, cwd=toplevel)
    _record(toplevel, "claim", shown, issue)
    _claim_branch_out(
        toplevel, shown, branch_setting, base, branch_after_sync, f"beads:{bd_id}",
        _direct_column(task.direct, task.difficulty, plan_body, registered),
    )
    _print_lines(pulled + trk.after([bd_id]))


def _print_taken(shown: str, issue: beads.Issue) -> None:
    age = _age_seconds(issue.started_at)
    print(f"TAKEN\t{shown}\t{issue.assignee or '?'}\t{format_elapsed(age) if age is not None else '?'}")
    raise SystemExit(4)


def _beads_resolved(toplevel: str, bd_id: str) -> bool:
    dep = beads.show(toplevel, bd_id)
    return dep is None or dep.status == "closed"


def cmd_beads_release(toplevel: str, task_id: str, force: bool) -> None:
    bd_id = _bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None or issue.status != "in_progress":
        print(f"NOT_CLAIMED\t{shown}")
        _print_lines(pulled)
        return
    actor = _actor(toplevel)
    if not force and issue.assignee != actor:
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)
    beads.run_ok(toplevel, ["unclaim", bd_id] + (["--force"] if force else []), actor)
    marks = [v for v in beads.SHIP_LABELS.values() if v in issue.labels]
    if marks:
        beads.run_ok(toplevel, ["update", bd_id] + [x for m in marks for x in ("--remove-label", m)], actor)
    owner_paths = [w.path for w in ledger.list_worktrees(cwd=toplevel) if os.path.basename(w.path) == issue.assignee]
    for path in owner_paths:
        _clear_open_claim_in(path, shown)
    print(f"RELEASED\t{shown}")
    _record(toplevel, "release", shown, issue)
    _print_lines(pulled + trk.after([bd_id]))


def cmd_beads_done(toplevel: str, task_id: str, dropped: bool, result_path: str) -> None:
    bd_id = _bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    actor = _actor(toplevel)
    result = read_body(result_path).strip()
    if result == "":
        print("usage: --result-file の中身が空", file=sys.stderr)
        raise SystemExit(2)
    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None or issue.status != "in_progress" or issue.assignee != actor:
        print(f"NOT_OWNER\t{shown}")
        _print_lines(pulled)
        raise SystemExit(4)
    kind = "dropped" if dropped else "done"
    beads.run_ok(toplevel, ["comment", bd_id, "--stdin"], actor, f"{beads.RESULT_HEADING}\n\n{result}\n")
    other = beads.SHIP_LABELS["done" if dropped else "dropped"]
    beads.run_ok(
        toplevel, ["update", bd_id, "--add-label", beads.SHIP_LABELS[kind], "--remove-label", other], actor
    )
    print(f"DONE\t{shown}\tbeads:{bd_id}\tship で閉じる")
    _record(toplevel, "done", shown, issue, dropped=dropped, reflection=_reflection_of(result))
    ledger.clear_open_claim(shown, cwd=toplevel)
    metadata = issue.raw.get("metadata")
    head = metadata.get(beads.CLAIM_HEAD_KEY) if isinstance(metadata, dict) else None
    _print_commits_since_claim(toplevel, shown, head)
    _print_lines(pulled + trk.after([bd_id]))


def _beads_ship_hooks(toplevel: str) -> ShipHooks:
    actor = _actor(toplevel)
    not_closed: list[str] = []

    def release_shipped() -> list[str]:
        closed: list[str] = []
        for issue in beads.list_issues(toplevel):
            mark = beads.ship_mark(issue)
            if issue.status != "in_progress" or issue.assignee != actor or mark is None:
                continue
            reason = "cancelled" if mark == "dropped" else "done"
            r = beads.run(toplevel, ["close", issue.bd_id, "--reason", reason], actor)
            if r.returncode != 0:
                not_closed.append(f"NOT_CLOSED\t{beads.to_task_id(issue.bd_id)}\t{(r.stderr or r.stdout).strip()}")
                continue
            update = ["update", issue.bd_id, "--remove-label", beads.SHIP_LABELS[mark]]
            if mark == "dropped":
                update += ["--add-label", beads.CANCELLED_LABEL]
            if issue.external_ref and tracker.read_tracker(toplevel).kind == "jira":
                update += ["--add-label", beads.JIRA_CLOSE_LABEL]
            beads.run_ok(toplevel, update, actor)
            ledger.clear_open_claim(beads.to_task_id(issue.bd_id), cwd=toplevel)
            closed.append(beads.to_task_id(issue.bd_id))
        return closed

    def claimed_branch(task_id: str) -> str | None:
        issue = beads.show(toplevel, beads.to_bd_id(task_id))
        metadata = issue.raw.get("metadata") if issue is not None else None
        return metadata.get(beads.CLAIM_BRANCH_KEY) if isinstance(metadata, dict) else None

    def after_send() -> list[str]:
        return not_closed + tracker.session(toplevel).sync_all(pull=False) + beads.backup(toplevel)

    return ShipHooks(release_shipped, claimed_branch, after_send)


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
            text = _read_base_task_text(toplevel, ledger.base_branch(toplevel), f"{task_id}.md")
        if text is None:
            print(f"NOT_READY\t{task_id}\t存在しない")
            raise SystemExit(4)
        sys.stdout.write(text)
        return
    bd_id = _bd_task_id(task_id)
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


def cmd_beads_edit(toplevel: str, args: argparse.Namespace) -> None:
    """本文・summary・difficulty・loopable・todo↔hold を書き換える（ファイル方式で手で直していたもの）。"""
    bd_id = _bd_task_id(args.task_id)
    shown = beads.to_task_id(bd_id)
    if args.section is not None and not args.body_file:
        print("usage: --section は --body-file と一緒に使う", file=sys.stderr)
        raise SystemExit(2)
    if not any([args.body_file, args.summary, args.difficulty, args.loopable, args.status, args.add_deps, args.remove_deps, args.direct]):
        print("usage: 直すもの（--body-file・--summary・--difficulty・--loopable・--status・--add-deps・--remove-deps・--direct）が無い", file=sys.stderr)
        raise SystemExit(2)
    add = _parse_dep_list(args.add_deps, layout.ANY_ID_PATTERN, "--add-deps", "T-999・GH-5・PROJ-123")
    remove = _parse_dep_list(args.remove_deps, layout.ANY_ID_PATTERN, "--remove-deps", "T-999・GH-5・PROJ-123")
    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None:
        print(f"NOT_READY\t{shown}\t存在しない")
        raise SystemExit(4)
    dep_edges: list[list[str]] = []
    if add or remove:
        snap = _beads_snapshot(toplevel)
        graph = {k: tuple(beads.to_task_id(d) for d in i.dependencies) for k, i in snap.issues.items()}
        current = tuple(beads.to_task_id(d) for d in issue.dependencies)
        wanted = _apply_dep_edit(shown, current, add, remove, set(snap.issues), graph)
        dep_edges = [["dep", "remove", bd_id, beads.to_bd_id(d)] for d in current if d not in wanted]
        dep_edges += [["dep", "add", bd_id, beads.to_bd_id(d)] for d in wanted if d not in current]
    cmd = ["update", bd_id]
    stdin = None
    state = None
    current = beads.compose_body(
        str(issue.raw.get("description") or ""),
        str(issue.raw.get("acceptance_criteria") or ""),
        str(issue.raw.get("notes") or ""),
        None,
    )
    body = current
    if args.body_file:
        body = _section_body(args, current, read_body(args.body_file))
        error = taskfile.validate_edited_body(current, body)
        if error is not None:
            print(f"usage: {error}", file=sys.stderr)
            raise SystemExit(2)
        _refuse_frame_change(shown, taskfile.changed_frame_sections(current, body), args.change_frame)
        parts = beads.split_body(body)
        cmd += ["--body-file", "-", "--acceptance", parts.acceptance, "--notes", parts.notes]
        stdin = parts.description
        metadata = issue.raw.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if (
            issue.status == "in_progress"
            and issue.assignee == _actor(toplevel)
            and taskfile.has_plan(body)
            and taskfile.plan_changed(current, body)
            and not metadata.get(beads.PLAN_KEY)
        ):
            state = _plan_state(toplevel, metadata.get(beads.CLAIM_HEAD_KEY), args.body_file, None)
            _refuse_plan_after_work(shown, state, args.after_work)
            cmd += ["--set-metadata", f"{beads.PLAN_KEY}={state}"]
    if args.summary:
        if "\n" in args.summary or not args.summary.strip():
            print("usage: --summary は改行を含まない1行にする", file=sys.stderr)
            raise SystemExit(2)
        cmd += ["--title", args.summary.strip()]
    for value, prefix in ((args.difficulty, beads.DIFFICULTY_LABEL), (args.loopable, beads.LOOPABLE_LABEL)):
        if value:
            cmd += [x for l in issue.labels if l.startswith(prefix) for x in ("--remove-label", l)]
            cmd += ["--add-label", f"{prefix}{value}"]
    difficulties = beads.label_values(issue.labels, beads.DIFFICULTY_LABEL)
    difficulty = args.difficulty or (difficulties[0] if difficulties else "?")
    had_direct = "Y" if beads.DIRECT_ON in issue.labels else "N"
    direct, direct_off = _edited_direct(had_direct, args.direct, difficulty, body)
    if direct != had_direct:
        cmd += [x for l in issue.labels if l.startswith(beads.DIRECT_LABEL) for x in ("--remove-label", l)]
        cmd += ["--add-label", beads.DIRECT_ON] if direct == "Y" else []
    if args.status:
        if issue.status not in ("open", *beads.HOLD_STATUSES):
            print(f"NOT_READY\t{shown}\t{issue.status}（todo↔hold は着手前だけ）")
            raise SystemExit(4)
        cmd += ["--status", "open" if args.status == "todo" else beads.HOLD_STATUS]
    if len(cmd) > 2:
        r = beads.run(toplevel, cmd, _actor(toplevel), stdin)
        if r.returncode != 0:
            raise beads.BeadsError(f"bd update が失敗: {(r.stderr or r.stdout).strip()}")
    for edge in dep_edges:
        r = beads.run(toplevel, edge, _actor(toplevel))
        if r.returncode != 0:
            raise beads.BeadsError(f"bd {' '.join(edge[:2])} が失敗: {(r.stderr or r.stdout).strip()}")
    print(f"EDITED\t{shown}")
    if state == PLAN_AFTER_WORK:
        print(f"PLAN_AFTER_WORK\t{shown}\t作業の後に書いた")
    _print_direct_off(shown, direct_off)
    _print_lines(pulled + trk.after([bd_id]))


def _beads_unplanned_work(toplevel: str) -> list[str]:
    actor = _actor(toplevel)
    found: list[str] = []
    for issue in beads.list_issues(toplevel):
        if issue.status != "in_progress" or issue.assignee != actor or beads.ship_mark(issue) is not None:
            continue
        if not taskfile.is_blank(str(issue.raw.get("notes") or "")):
            continue
        metadata = issue.raw.get("metadata")
        head = metadata.get(beads.CLAIM_HEAD_KEY) if isinstance(metadata, dict) else None
        if _plan_state(toplevel, head, "-", None) == PLAN_AFTER_WORK:
            found.append(beads.to_task_id(issue.bd_id))
    return found


def cmd_beads_plan_check(toplevel: str, task_id: str) -> None:
    bd_id = _bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    issue = beads.show(toplevel, bd_id)
    if issue is None or issue.status != "in_progress" or issue.assignee != _actor(toplevel):
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)
    metadata = issue.raw.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    plan_body = f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n"
    plan_base = metadata.get(beads.PLAN_BASE_KEY) or None
    stale = _registered_plan_changes(toplevel, plan_base, metadata.get(beads.PLAN_TIP_KEY) or None, plan_body)
    _print_plan_check(shown, plan_body, metadata.get(beads.PLAN_KEY) or None, plan_base, stale)


def cmd_beads_adopt(toplevel: str, args: argparse.Namespace) -> None:
    """振り分け前の課題（トラッカーから取り込んだものなど）に番号・difficulty・loopable を付ける。"""
    old = beads.to_bd_id(args.bd_id)
    body = read_body(args.body_file)
    error = taskfile.validate_new_body(body, hold=False)
    if error is not None:
        print(f"usage: {error}", file=sys.stderr)
        raise SystemExit(2)
    if args.direct:
        _refuse_direct(args.difficulty, body)
    plan_base = _registered_plan_base(toplevel, body)
    trk = tracker.session(toplevel)
    pulled = trk.before([old])
    issue = beads.show(toplevel, old)
    if issue is None:
        print(f"NOT_READY\t{args.bd_id}\t存在しない")
        raise SystemExit(4)
    actor = _actor(toplevel)
    new_id = old
    key = tracker.jira_key(issue) if trk.tracker.kind == "jira" else None
    if key is not None:
        renamed, lines = tracker.jira_rename(toplevel, [old])
        if key != old and old not in renamed:
            _print_lines(pulled + lines)
            raise SystemExit(3)
        new_id = key
    # github（`issue_prefix` が `gh`）は取り込みの時点で Issue 番号の ID になっているので番号を振らない。
    elif not beads.is_numbered(old) and not trk.bidirectional:
        number = _next_number(toplevel, _beads_snapshot(toplevel))
        for _ in range(NEW_ATTEMPTS):
            new_id = beads.format_bd_id(number)
            r = beads.run(toplevel, ["rename", old, new_id], actor)
            if r.returncode == 0:
                beads.write_last_id(toplevel, number)
                break
            number += 1
        else:
            print(f"LOCKED\t{NEW_ATTEMPTS}回続けて番号を取られた")
            raise SystemExit(4)
    parts = beads.split_body(body)
    cmd = ["update", new_id, "--body-file", "-", "--acceptance", parts.acceptance, "--notes", parts.notes]
    cmd += ["--set-metadata", f"{beads.PLAN_BASE_KEY}={plan_base}"]
    cmd += [
        x
        for l in issue.labels
        if l.startswith((beads.DIFFICULTY_LABEL, beads.LOOPABLE_LABEL, beads.DIRECT_LABEL))
        for x in ("--remove-label", l)
    ]
    cmd += ["--add-label", f"{beads.DIFFICULTY_LABEL}{args.difficulty}", "--add-label", f"{beads.LOOPABLE_LABEL}{args.loopable}"]
    if args.direct:
        cmd += ["--add-label", beads.DIRECT_ON]
    if args.summary:
        cmd += ["--title", args.summary.strip()]
    beads.run_ok(toplevel, cmd, actor, parts.description)
    print(f"ADOPTED\t{args.bd_id}\t{beads.to_task_id(new_id)}")
    _print_lines(pulled + trk.after([new_id]))


def cmd_beads_sync(toplevel: str) -> None:
    lines = tracker.session(toplevel).sync_all(pull=True)
    if not lines:
        print("NOTHING\t(トラッカーなし)")
        return
    _print_lines(lines)
    if any(l.startswith("TRACKER\tFAILED") for l in lines):
        raise SystemExit(10)


def cmd_beads_backup(toplevel: str) -> None:
    lines = beads.backup(toplevel)
    _print_lines(lines)
    if any("\tFAILED\t" in l for l in lines):
        raise SystemExit(10)


def cmd_beads_jira_closed(toplevel: str, task_ids: list[str]) -> None:
    """Jira で閉じたのを人が確かめたものから `jira:close` を外す。"""
    actor = _actor(toplevel)
    for task_id in task_ids:
        bd_id = _bd_task_id(task_id)
        beads.run_ok(toplevel, ["update", bd_id, "--remove-label", beads.JIRA_CLOSE_LABEL], actor)
        print(f"CLEARED\t{beads.to_task_id(bd_id)}")


def _doctor_store(toplevel: str, config: layout.Config) -> int:
    """`config-doctor` の検査4。`store`・（Beads 方式なら）`beads`・`tracker` の行を出し、終了コードを返す。"""
    print(f"store\tOK\t{config.store}")
    if config.store != layout.STORE_BEADS:
        return 0
    code = 0
    if beads.is_initialized(toplevel):
        print(f"beads\tOK\t{beads.beads_dir(toplevel)}")
    else:
        print(f"beads\tMISSING\t{beads.beads_dir(toplevel)}\tinit.py（bd init --stealth）")
        code = 1
    t = tracker.read_tracker(toplevel)
    detail = f"\t{t.project_owner}/{t.project_number}" if t.kind == "github" else ""
    print(f"tracker\tOK\t{t.kind}{detail}")
    return code


# --- 入口 -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tw")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("--all", dest="show_all", action="store_true")
    p_status.add_argument("--check", action="store_true")

    p_new = sub.add_parser("new")
    p_new.add_argument("--summary", required=True)
    p_new.add_argument("--difficulty", required=True, choices=taskfile.DIFFICULTY_VALUES)
    p_new.add_argument("--loopable", required=True, choices=taskfile.LOOPABLE_VALUES)
    p_new.add_argument("--deps", default="")
    p_new.add_argument("--hold", action="store_true")
    p_new.add_argument("--direct", action="store_true")
    p_new.add_argument("--body-file", required=True)

    p_claim = sub.add_parser("claim")
    p_claim.add_argument("task_id")

    p_release = sub.add_parser("release")
    p_release.add_argument("task_id")
    p_release.add_argument("--force", action="store_true")

    p_done = sub.add_parser("done")
    p_done.add_argument("task_id")
    p_done.add_argument("--dropped", action="store_true")
    p_done.add_argument("--result-file", required=True)

    sub.add_parser("ship")

    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_prune.add_argument("--min", dest="minimum", type=int, default=PRUNE_MIN_DEFAULT)

    p_migrate = sub.add_parser("migrate")
    p_migrate.add_argument("--dry-run", dest="dry_run", action="store_true")

    p_migrate_layout = sub.add_parser("migrate-layout")
    p_migrate_layout.add_argument("--dry-run", dest="dry_run", action="store_true")

    sub.add_parser("config-doctor")
    sub.add_parser("config")

    p_show = sub.add_parser("show")
    p_show.add_argument("task_id")

    p_plan_check = sub.add_parser("plan-check")
    p_plan_check.add_argument("task_id")

    sub.add_parser("verify")
    sub.add_parser("verify-check")
    p_lap = sub.add_parser("lap")
    p_lap.add_argument("task_id")
    p_lap.add_argument("stage")
    p_metrics = sub.add_parser("metrics")
    p_metrics.add_argument("--days", type=int, default=metrics.DAYS_DEFAULT)
    p_metrics.add_argument("--stages", action="store_true")
    sub.add_parser("commit-guard").add_argument("--agent-scoped", dest="agent_scoped", action="store_true")
    p_pause = sub.add_parser("pause")
    p_pause.add_argument("task_id", nargs="?")
    p_pause.add_argument("step", nargs="?")
    p_step = sub.add_parser("step")
    p_step.add_argument("task_id")
    p_step.add_argument("step")
    sub.add_parser("handback-guard").add_argument("--agent-scoped", dest="agent_scoped", action="store_true")

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("task_id")
    p_edit.add_argument("--body-file", default=None)
    p_edit.add_argument("--section", default=None)
    p_edit.add_argument("--summary", default=None)
    p_edit.add_argument("--difficulty", default=None, choices=taskfile.DIFFICULTY_VALUES)
    p_edit.add_argument("--loopable", default=None, choices=taskfile.LOOPABLE_VALUES)
    p_edit.add_argument("--status", default=None, choices=("todo", "hold"))
    p_edit.add_argument("--add-deps", dest="add_deps", default=None)
    p_edit.add_argument("--remove-deps", dest="remove_deps", default=None)
    p_edit.add_argument("--after-work", dest="after_work", action="store_true")
    p_edit.add_argument("--change-frame", dest="change_frame", action="store_true")
    p_edit.add_argument("--direct", default=None, choices=taskfile.LOOPABLE_VALUES)

    # 以下は Beads 方式だけ。

    p_adopt = sub.add_parser("adopt")
    p_adopt.add_argument("bd_id")
    p_adopt.add_argument("--difficulty", required=True, choices=taskfile.DIFFICULTY_VALUES)
    p_adopt.add_argument("--loopable", required=True, choices=taskfile.LOOPABLE_VALUES)
    p_adopt.add_argument("--summary", default=None)
    p_adopt.add_argument("--direct", action="store_true")
    p_adopt.add_argument("--body-file", required=True)

    sub.add_parser("sync")
    sub.add_parser("backup")

    p_jira_closed = sub.add_parser("jira-closed")
    p_jira_closed.add_argument("task_ids", nargs="+")

    return parser


BEADS_ONLY_COMMANDS = ("adopt", "sync", "backup", "jira-closed")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "commit-guard":
        commit_guard.run(sys.stdin, sys.stdout, args.agent_scoped)
        return
    if args.command == "handback-guard":
        handback_guard.run(sys.stdin, sys.stdout, _handback_refusal, args.agent_scoped)
        return

    try:
        toplevel = ledger.git_toplevel()
    except ledger.GitCommandError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)

    if args.command not in ("migrate", "config-doctor"):
        refusal = _format_refusal(toplevel)
        if refusal is not None:
            print(refusal[0])
            raise SystemExit(refusal[1])

    # 主ブランチは要る道でだけ問い合わせる（`ledger.base_branch`。決まらなければ INVALID）。
    try:
        store = layout.read_config(toplevel).store if args.command not in ("migrate", "config-doctor") else None
        if args.command == "config":
            cmd_config(toplevel)
            return
        if args.command == "migrate-layout":
            cmd_migrate_layout(toplevel, args.dry_run)
            return
        if store == layout.STORE_BEADS:
            _main_beads(toplevel, args)
            return
        if args.command in BEADS_ONLY_COMMANDS:
            print(f"usage: {args.command} は Beads 方式だけ（ファイル方式では <根>/task/ を直に直す）", file=sys.stderr)
            raise SystemExit(2)
        if args.command == "status":
            cmd_status(toplevel, args.show_all, args.check)
        elif args.command == "new":
            cmd_new(toplevel, args)
        elif args.command == "claim":
            cmd_claim(toplevel, args.task_id)
        elif args.command == "release":
            cmd_release(toplevel, args.task_id, args.force)
        elif args.command == "done":
            cmd_done(toplevel, args.task_id, args.dropped, args.result_file)
        elif args.command == "ship":
            cmd_ship(toplevel)
        elif args.command == "prune":
            cmd_prune(toplevel, args.dry_run, max(args.minimum, 1))
        elif args.command == "migrate":
            cmd_migrate(toplevel, args.dry_run)
        elif args.command == "config-doctor":
            cmd_config_doctor(toplevel)
        elif args.command == "show":
            cmd_show(toplevel, args.task_id, layout.STORE_FILES)
        elif args.command == "edit":
            cmd_edit(toplevel, args)
        elif args.command == "plan-check":
            cmd_plan_check(toplevel, args.task_id)
        elif args.command == "verify":
            cmd_verify(toplevel, lambda: _file_unplanned_work(toplevel))
        elif args.command == "verify-check":
            cmd_verify_check(toplevel)
        elif args.command == "pause":
            cmd_pause(toplevel, args.task_id, args.step)
        elif args.command == "step":
            cmd_step(toplevel, args.task_id, args.step)
        elif args.command == "lap":
            cmd_lap(toplevel, args.task_id, args.stage)
        elif args.command == "metrics":
            (metrics.cmd_metrics_stages if args.stages else metrics.cmd_metrics)(toplevel, args.days)
    except (ledger.NoBaseBranch, layout.ConfigError) as e:
        print(f"INVALID\t{e}")
        raise SystemExit(3)
    except ledger.StateReadOnly as e:
        print(
            f"STATE_READ_ONLY\t{e.path}\t{ledger.STATE_DIR_ENV} を書ける場所に向けるか、{e.path} を書ける場所に足す"
            "（Codex は writable roots、Claude Code は sandbox.filesystem.allowWrite）"
        )
        raise SystemExit(12)
    except ledger.GitReadOnly as e:
        print(
            f"GIT_READ_ONLY\t{e.path}\tsandbox の外で同じコマンドを打ち直す"
            "（Claude Code は sandbox.excludedCommands に tw を足す）"
        )
        raise SystemExit(11)


def _main_beads(toplevel: str, args: argparse.Namespace) -> None:
    if not beads.is_initialized(toplevel):
        print(f"MISSING\t{beads.beads_dir(toplevel)}")
        raise SystemExit(6)
    tracker.read_tracker(toplevel)  # 読めない設定はどのサブコマンドでも先に INVALID にする
    try:
        if args.command == "status":
            cmd_beads_status(toplevel, args.show_all, args.check)
        elif args.command == "new":
            cmd_beads_new(toplevel, args)
        elif args.command == "claim":
            cmd_beads_claim(toplevel, args.task_id)
        elif args.command == "release":
            cmd_beads_release(toplevel, args.task_id, args.force)
        elif args.command == "done":
            cmd_beads_done(toplevel, args.task_id, args.dropped, args.result_file)
        elif args.command == "ship":
            cmd_ship(toplevel, _beads_ship_hooks(toplevel))
        elif args.command == "prune":
            print("NOTHING\t(Beads 方式には消すタスクファイルが無い)")
        elif args.command == "show":
            cmd_show(toplevel, args.task_id, layout.STORE_BEADS)
        elif args.command == "edit":
            cmd_beads_edit(toplevel, args)
        elif args.command == "plan-check":
            cmd_beads_plan_check(toplevel, args.task_id)
        elif args.command == "verify":
            cmd_verify(toplevel, lambda: _beads_unplanned_work(toplevel))
        elif args.command == "verify-check":
            cmd_verify_check(toplevel)
        elif args.command == "pause":
            cmd_pause(toplevel, args.task_id, args.step)
        elif args.command == "step":
            cmd_step(toplevel, args.task_id, args.step)
        elif args.command == "lap":
            cmd_lap(toplevel, args.task_id, args.stage)
        elif args.command == "metrics":
            (metrics.cmd_metrics_stages if args.stages else metrics.cmd_metrics)(toplevel, args.days)
        elif args.command == "adopt":
            cmd_beads_adopt(toplevel, args)
        elif args.command == "sync":
            cmd_beads_sync(toplevel)
        elif args.command == "backup":
            cmd_beads_backup(toplevel)
        elif args.command == "jira-closed":
            cmd_beads_jira_closed(toplevel, args.task_ids)
    except beads.BeadsError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
