"""`tw edit`・`plan-check`（本文と依存を書き換え、`## やること` を作業より先に書いたかを出す）。"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import sys
from collections.abc import Collection, Mapping
from typing import NoReturn

import beads
import layout
import ledger
import taskfile
import tracker
import tw_base
import tw_plan


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
        tasks, _, _ = tw_base.load_tasks(toplevel)
        graph = {i: t.dependencies for i, t in tasks.items()}
        dependencies = _apply_dep_edit(task_id, task.dependencies, add, remove, set(tasks), graph)
    body = task.body
    if args.body_file:
        body = _section_body(args, task.body, tw_base.read_body(args.body_file))
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
        state = tw_plan.plan_state(toplevel, owner.get("head"), args.body_file, own)
        _refuse_plan_after_work(task_id, state, args.after_work)
        root = ledger.ledger_root_for_write(cwd=toplevel)

    rendered = taskfile.render(dataclasses.replace(task, dependencies=dependencies, body=body, direct=direct))
    with open(path, "w", encoding="utf-8") as f:
        f.write(rendered)
    if state is not None:
        ledger.write_plan_mark(root, task_id, state)
    print(f"EDITED\t{task_id}")
    if state == tw_plan.PLAN_AFTER_WORK:
        print(f"PLAN_AFTER_WORK\t{task_id}\t作業の後に書いた")
    _print_direct_off(task_id, direct_off)


def _edited_direct(current: str, wanted: str | None, difficulty: str, body: str) -> tuple[str, str | None]:
    """`edit` のあとの近道の印と、書き換えで基準を外れて外したときの理由。"""
    if wanted == "Y":
        tw_plan.refuse_direct(difficulty, body)
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
    if state == tw_plan.PLAN_AFTER_WORK and not after_work:
        print(
            f"WORK_BEFORE_PLAN\t{shown}\t書き込んでいない。作業が始まっている。"
            f"作業の後と承知で書くなら --after-work を付けて打ち直す"
        )
        raise SystemExit(4)


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
    stale = tw_plan.registered_plan_changes(toplevel, plan_base, ledger.read_plan_tip(root, task_id), task.body)
    _print_plan_check(task_id, task.body, ledger.read_plan_mark(root, task_id), plan_base, stale)


def _print_plan_check(
    shown: str, body: str, mark: str | None, plan_base: str | None, stale: list[str] | None
) -> None:
    """`stale` は登録時の計画が名指すファイルのうち着手時までに変わったもの（`tw_plan.registered_plan_changes`）。

    段（`taskfile.plan_steps`）の読めない計画は、印によらず書き直しの経路（`PLAN_NOT_FIRST`・`steps`）へ回す。
    """
    has_plan = taskfile.has_plan(body)
    specs, steps_error = taskfile.plan_step_specs(body) if has_plan else ((), None)
    if has_plan and steps_error is not None:
        print(f"PLAN_NOT_FIRST\t{shown}\tsteps")
        return
    if has_plan and mark == tw_plan.PLAN_FIRST:
        print(f"PLAN_FIRST\t{shown}")
    elif has_plan and mark == tw_plan.PLAN_REGISTERED:
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


def cmd_beads_edit(toplevel: str, args: argparse.Namespace) -> None:
    """本文・summary・difficulty・loopable・todo↔hold を書き換える（ファイル方式で手で直していたもの）。"""
    bd_id = tw_base.bd_task_id(args.task_id)
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
        snap = tw_base.beads_snapshot(toplevel)
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
        body = _section_body(args, current, tw_base.read_body(args.body_file))
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
            and issue.assignee == tw_base.actor(toplevel)
            and taskfile.has_plan(body)
            and taskfile.plan_changed(current, body)
            and not metadata.get(beads.PLAN_KEY)
        ):
            state = tw_plan.plan_state(toplevel, metadata.get(beads.CLAIM_HEAD_KEY), args.body_file, None)
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
        r = beads.run(toplevel, cmd, tw_base.actor(toplevel), stdin)
        if r.returncode != 0:
            raise beads.BeadsError(f"bd update が失敗: {(r.stderr or r.stdout).strip()}")
    for edge in dep_edges:
        r = beads.run(toplevel, edge, tw_base.actor(toplevel))
        if r.returncode != 0:
            raise beads.BeadsError(f"bd {' '.join(edge[:2])} が失敗: {(r.stderr or r.stdout).strip()}")
    print(f"EDITED\t{shown}")
    if state == tw_plan.PLAN_AFTER_WORK:
        print(f"PLAN_AFTER_WORK\t{shown}\t作業の後に書いた")
    _print_direct_off(shown, direct_off)
    tw_base.print_lines(pulled + trk.after([bd_id]))


def cmd_beads_plan_check(toplevel: str, task_id: str) -> None:
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    issue = beads.show(toplevel, bd_id)
    if issue is None or issue.status != "in_progress" or issue.assignee != tw_base.actor(toplevel):
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)
    metadata = issue.raw.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    plan_body = f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n"
    plan_base = metadata.get(beads.PLAN_BASE_KEY) or None
    stale = tw_plan.registered_plan_changes(toplevel, plan_base, metadata.get(beads.PLAN_TIP_KEY) or None, plan_body)
    _print_plan_check(shown, plan_body, metadata.get(beads.PLAN_KEY) or None, plan_base, stale)
