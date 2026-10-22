"""`tw edit`・`plan-check`（本文と依存を書き換え、`## やること` を作業より先に書いたかを出す）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Collection, Mapping
from typing import NoReturn

import beads
import layout
import taskfile
import tracker
import tw_base
import tw_config
import tw_plan


def cmd_edit(toplevel: str, args: argparse.Namespace) -> None:
    """本文・summary・difficulty・loopable・todo↔hold・依存・近道の印・種類を書き換える。"""
    bd_id = tw_base.bd_task_id(args.task_id)
    shown = beads.to_task_id(bd_id)
    sections: list[str] = args.section or []
    body_files: list[str] = args.body_file or []
    _check_body_args(sections, body_files)
    if not any([body_files,args.summary, args.difficulty, args.loopable, args.status, args.add_deps, args.remove_deps, args.direct, args.kind]):
        print("usage: 直すもの（--body-file・--summary・--difficulty・--loopable・--status・--add-deps・--remove-deps・--direct・--kind）が無い", file=sys.stderr)
        raise SystemExit(2)
    add = _parse_dep_list(args.add_deps, layout.ANY_ID_PATTERN, "--add-deps", "T-999・GH-5・PROJ-123")
    remove = _parse_dep_list(args.remove_deps, layout.ANY_ID_PATTERN, "--remove-deps", "T-999・GH-5・PROJ-123")
    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None:
        print(f"NOT_READY\t{shown}\t存在しない")
        raise SystemExit(4)
    if (
        args.loopable == "N"
        and beads.attribute_values(issue, beads.LOOPABLE_KEY) == ["Y"]
        and issue.status == "in_progress"
        and issue.assignee == tw_base.actor(toplevel)
    ):
        print(
            f"usage: 着手中の loopable: Y を N に直さない。書き換えない。"
            f"判断待ちで外すなら tw hold {shown} --reason-file - に論点を渡す",
            file=sys.stderr,
        )
        raise SystemExit(2)
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
    current = beads.issue_body(issue)
    body = current
    difficulties = beads.attribute_values(issue, beads.DIFFICULTY_KEY)
    old_difficulty = difficulties[0] if difficulties else "?"
    difficulty = args.difficulty or old_difficulty
    if args.difficulty and not body_files:
        error = taskfile.validate_edited_body(
            current, current, draft=beads.is_draft(issue), old_difficulty=old_difficulty, difficulty=difficulty
        )
        if error is not None:
            print(f"usage: {error}", file=sys.stderr)
            raise SystemExit(2)
    if body_files:
        for name, path in zip(sections or [None], body_files):
            given = tw_base.read_body(path)
            if not given.strip():
                print("usage: --body-file の中身が空（空白だけ）。書き換えない", file=sys.stderr)
                raise SystemExit(2)
            body = given if name is None else _section_body(name, body, given)
        error = taskfile.validate_edited_body(
            current, body, draft=beads.is_draft(issue), old_difficulty=old_difficulty, difficulty=difficulty
        )
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
            state = tw_plan.plan_state(toplevel, metadata.get(beads.CLAIM_HEAD_KEY), body_files)
            _refuse_plan_after_work(shown, state, args.after_work)
            cmd += ["--set-metadata", f"{beads.PLAN_KEY}={state}"]
    if args.summary:
        if "\n" in args.summary or not args.summary.strip():
            print("usage: --summary は改行を含まない1行にする", file=sys.stderr)
            raise SystemExit(2)
        cmd += ["--title", args.summary.strip()]
    for value, key in ((args.difficulty, beads.DIFFICULTY_KEY), (args.loopable, beads.LOOPABLE_KEY)):
        if value:
            cmd += ["--set-metadata", f"{key}={value}"]
    had_direct = "Y" if beads.DIRECT_ON in issue.labels else "N"
    direct, direct_off = _edited_direct(had_direct, args.direct, difficulty, body)
    if direct != had_direct:
        cmd += [x for l in issue.labels if l.startswith(beads.DIRECT_LABEL) for x in ("--remove-label", l)]
        cmd += ["--add-label", beads.DIRECT_ON] if direct == "Y" else []
    if args.kind:
        cmd += beads.kind_label_args(issue, args.kind)
    if args.status:
        if issue.status not in ("open", *beads.HOLD_STATUSES):
            print(f"NOT_READY\t{shown}\t{issue.status}（todo↔hold は着手前だけ）")
            raise SystemExit(4)
        cmd += ["--status", "open" if args.status == "todo" else beads.HOLD_STATUS]
        if beads.is_draft(issue):
            cmd += _draft_promotion_args(toplevel, shown, body, args.status, difficulty)
    if len(cmd) > 2:
        r = beads.run(toplevel, cmd, tw_base.actor(toplevel), stdin)
        if r.returncode != 0:
            raise beads.BeadsError(f"bd update が失敗: {(r.stderr or r.stdout).strip()}")
        if issue.status == "in_progress" and issue.assignee == tw_base.actor(toplevel) and "--set-metadata" in cmd:
            edited = beads.show(toplevel, bd_id)
            if edited is not None:
                beads.write_claim_snapshot(toplevel, edited)
    for edge in dep_edges:
        r = beads.run(toplevel, edge, tw_base.actor(toplevel))
        if r.returncode != 0:
            raise beads.BeadsError(f"bd {' '.join(edge[:2])} が失敗: {(r.stderr or r.stdout).strip()}")
    print(f"EDITED\t{shown}")
    if body_files and any(
        taskfile.section_changed(current, body, heading) for heading in (taskfile.PLAN_HEADING, taskfile.ACCEPTANCE_HEADING)
    ):
        tw_plan.warn_report_names(body)
    if state == tw_plan.PLAN_AFTER_WORK:
        print(f"PLAN_AFTER_WORK\t{shown}\t作業の後に書いた")
    _print_direct_off(shown, direct_off)
    tw_base.print_lines(pulled + trk.after([bd_id]))


def own_plan(toplevel: str, task_id: str) -> tuple[str, beads.Issue, str, str | None, str | None, tuple[list[str], str] | None]:
    """自分の着手の印のタスクの `(表示ID, 課題, 計画の本文, 印, 控えた SHA, 変わったファイルと古い理由)`。持ち主でなければ `NOT_OWNER`（終了コード4）。"""
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    issue = beads.listed(toplevel, bd_id) if tw_base.owns(toplevel, shown) else None
    if issue is None or issue.status != "in_progress" or issue.assignee != tw_base.actor(toplevel):
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)
    metadata = issue.raw.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    plan_body = (
        f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n\n"
        f"{taskfile.ACCEPTANCE_HEADING}\n{issue.raw.get('acceptance_criteria') or ''}\n"
    )
    plan_base = metadata.get(beads.PLAN_BASE_KEY) or None
    drift = tw_plan.registered_plan_drift(toplevel, plan_base, metadata.get(beads.PLAN_TIP_KEY) or None, plan_body)
    return shown, issue, plan_body, metadata.get(beads.PLAN_KEY) or None, plan_base, drift


def cmd_plan_check(toplevel: str, task_id: str) -> None:
    shown, _issue, plan_body, mark, plan_base, drift = own_plan(toplevel, task_id)
    _print_plan_check(shown, plan_body, mark, plan_base, drift)


def cmd_plan_phases(
    toplevel: str, task_id: str, through: str | None, also: str | None, skipped: str | None = None
) -> None:
    """`work_plan` の引数を `PHASES\\tT-xxx\\t<JSON 1行>` で出す。指定が並びに合わなければ `NO_PHASE`（終了コード2）。"""
    shown, issue, plan_body, mark, _base, _stale = own_plan(toplevel, task_id)
    specs, steps_error = taskfile.plan_step_specs(plan_body) if taskfile.has_plan(plan_body) else ((), None)
    if specs and steps_error is None and mark == tw_plan.PLAN_REGISTERED:
        plan_state = taskfile.PLAN_REGISTERED_STATE
    elif specs and steps_error is None and mark in (tw_plan.PLAN_FIRST, tw_plan.PLAN_AFTER_WORK):
        plan_state = taskfile.PLAN_WRITTEN_STATE
    else:
        plan_state = taskfile.PLAN_MISSING_STATE
    config = layout.read_config(toplevel)
    direct = beads.label_values(issue.labels, beads.DIRECT_LABEL) == ["Y"]
    if direct and plan_state == taskfile.PLAN_REGISTERED_STATE:
        flow = taskfile.FLOW_DIRECT
    elif beads.attribute_values(issue, beads.LOOPABLE_KEY) == ["N"]:
        flow = taskfile.FLOW_MAIN
    else:
        flow = taskfile.FLOW_DELEGATE
    also_numbers: tuple[int, ...] = ()
    if also:
        if not all(x.strip().isdigit() for x in also.split(",")):
            print(f"NO_PHASE\t{shown}\t--also は段の番号のコンマ区切り: {also}")
            raise SystemExit(2)
        also_numbers = tuple(int(x) for x in also.split(","))
    steps = config.steps if config.steps is not None else tuple(tw_config.steps_default(toplevel))
    skipped_names = tuple(x.strip() for x in skipped.split(",") if x.strip()) if skipped else ()
    result, error = taskfile.work_plan_phases(
        specs,
        flow,
        plan_state,
        through,
        also_numbers,
        steps,
        skipped_names,
    )
    if result is None:
        print(f"NO_PHASE\t{shown}\t{error}")
        raise SystemExit(2)
    print(f"PHASES\t{shown}\t{json.dumps(result, ensure_ascii=False, separators=(',', ':'))}")


def _draft_promotion_args(toplevel: str, shown: str, body: str, status: str, difficulty: str) -> list[str]:
    """ドラフトを `todo`・`hold` へ進める `bd update` の引数。本文が `tw new`（`hold` なら `--hold`）の検査を通らなければ
    何も書かずに `NOT_READY\\t<ID>\\tDRAFT\\t<理由>`（終了コード4）。"""
    error = taskfile.validate_new_body(body, hold=status == "hold", difficulty=difficulty)
    if error is not None:
        print(f"NOT_READY\t{shown}\tDRAFT\t{error}")
        raise SystemExit(4)
    viewpoint = taskfile.draft_viewpoint(body) or beads.FROM_DRAFT_UNKNOWN
    args = ["--unset-metadata", beads.DRAFT_KEY, "--set-metadata", f"{beads.FROM_DRAFT_KEY}={viewpoint}"]
    if taskfile.has_plan(body):
        args += ["--set-metadata", f"{beads.PLAN_BASE_KEY}={tw_plan.registered_plan_base(toplevel, body)}"]
    return args


def _parse_dep_list(raw: str | None, pattern: re.Pattern[str], flag: str, form: str) -> tuple[str, ...]:
    deps = tuple(d for d in (x.strip() for x in raw.split(",")) if d) if raw else ()
    for d in deps:
        if not pattern.match(d):
            print(f"usage: {flag} の {d!r} が {form} の形式でない", file=sys.stderr)
            raise SystemExit(2)
    return deps


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


def _check_body_args(sections: list[str], body_files: list[str]) -> None:
    """`--section`・`--body-file` の並びの誤りは何も書かずに終了コード2。"""
    message = None
    if sections and not body_files:
        message = "--section は --body-file と一緒に使う"
    elif sections and len(sections) != len(body_files):
        message = "--section と --body-file は同じ数だけ、同じ順に並べる"
    elif not sections and len(body_files) > 1:
        message = "--body-file を並べるときは、それぞれに --section を付ける"
    elif body_files.count("-") > 1:
        message = "標準入力（--body-file -）は1回しか読めない"
    elif len({taskfile.section_heading(s) or s for s in sections}) != len(sections):
        message = "同じ節を2回指している"
    if message is not None:
        print(f"usage: {message}", file=sys.stderr)
        raise SystemExit(2)


def _section_body(section: str, current: str, given: str) -> str:
    """`current` の `section` の節だけを `given` にした本文。"""
    heading = taskfile.section_heading(section)
    if heading is None:
        headings = (*taskfile.SECTION_HEADINGS, taskfile.INTERVENTION_HEADING)
        print(f"usage: --section {section!r} は枠の見出しでない: " + "、".join(headings), file=sys.stderr)
        raise SystemExit(2)
    error = taskfile.check_section_content(given)
    replaced = taskfile.put_section(current, heading, given) if error is None else None
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


def _print_plan_check(
    shown: str, body: str, mark: str | None, plan_base: str | None, drift: tuple[list[str], str] | None
) -> None:
    """`drift` は登録時の計画が名指すファイルの変わりと、古いとみなす理由（`tw_plan.registered_plan_drift`）。

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
    elif has_plan and mark is None and drift is not None:
        print(f"PLAN_STALE\t{shown}\t{','.join(drift[0]) or '?'}\t{drift[1] or '?'}")
    else:
        print(f"PLAN_NOT_FIRST\t{shown}\t{'missing' if not has_plan else (mark or 'unrecorded')}")
    if not specs:
        return
    uncovered, unknown = taskfile.plan_coverage(body)
    if uncovered or unknown:
        listed = [str(n) for n in uncovered] + [f"{n}?" for n in unknown]
        print(f"PLAN_UNCOVERED\t{shown}\t{','.join(listed)}")
    pairs, serial = taskfile.parallel_steps(specs)
    for a, b in pairs:
        print(f"PARALLEL\t{shown}\t{a},{b}")
    for a, b, shared in serial:
        print(f"SERIAL\t{shown}\t{a},{b}\t{','.join(shared)}")
    if not pairs:
        return
    for n, waits in enumerate(taskfile.step_waits(specs), start=1):
        print(f"STEP\t{shown}\t{n}\t{','.join(map(str, waits)) or 'なし'}")
