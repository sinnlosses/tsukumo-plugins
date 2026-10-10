"""`tw new`・`adopt`（タスクを登録する）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

import beads
import layout
import taskfile
import tracker
import tw_base
import tw_plan


def cmd_new(toplevel: str, args: argparse.Namespace) -> None:
    summary = args.summary.strip()
    if summary == "" or "\n" in args.summary:
        print("usage: --summary は改行を含まない1行にする", file=sys.stderr)
        raise SystemExit(2)
    deps = tuple(d for d in (x.strip() for x in args.deps.split(",")) if d) if args.deps else ()
    for d in deps:
        if not layout.ANY_ID_PATTERN.match(d):
            print(f"usage: --deps の {d!r} が T-999・GH-5・PROJ-123 の形式でない", file=sys.stderr)
            raise SystemExit(2)
    body = tw_base.read_body(args.body_file)
    error = taskfile.validate_new_body(body, args.hold)
    if error is not None:
        print(f"usage: {error}", file=sys.stderr)
        raise SystemExit(2)
    if args.direct:
        tw_plan.refuse_direct(args.difficulty, body)
    plan_base = tw_plan.registered_plan_base(toplevel, body) if taskfile.has_plan(body) else None

    snap = tw_base.beads_snapshot(toplevel)
    missing = [d for d in deps if d not in snap.issues]
    if missing:
        print(f"usage: --deps の {','.join(missing)} が Beads に無い（解決済みなら外す）", file=sys.stderr)
        raise SystemExit(2)

    parts = beads.split_body(body)
    actor = tw_base.actor(toplevel)
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
        tw_base.print_lines(lines)
        return

    number = _next_number(toplevel, snap)
    for _ in range(NEW_ATTEMPTS):
        bd_id = beads.format_bd_id(number)
        r = beads.run(toplevel, create_cmd(bd_id), actor, parts.description)
        if r.returncode == 0:
            beads.write_last_id(toplevel, number)
            print(f"CREATED\t{beads.to_task_id(bd_id)}\tbeads:{bd_id}")
            tw_base.print_lines(trk.after([bd_id]))
            return
        if "already exists" not in (r.stderr + r.stdout):
            raise beads.BeadsError(f"bd create が失敗: {(r.stderr or r.stdout).strip()}")
        number += 1
    print(f"LOCKED\t{NEW_ATTEMPTS}回続けて番号を取られた")
    raise SystemExit(4)


# 番号の取り合いに続けて負けたら諦める回数（`bd create --id` は同じ番号を1つしか作らない）。
NEW_ATTEMPTS = 20


def _next_number(toplevel: str, snap: tw_base.BeadsSnapshot) -> int:
    """Beads の番号・`bd kv` の最後の番号・主ブランチの `docs/history/tasks.md` のうち最大の次。"""
    candidates = [0]
    candidates += [n for n in (beads.id_number(i.bd_id) for i in snap.issues.values()) if n is not None]
    last = beads.read_last_id(toplevel)
    if last is not None:
        candidates.append(last)
    candidates += [taskfile.id_number(i) for i in tw_base.history_ids_at_base(toplevel)]
    return max(candidates) + 1


def cmd_adopt(toplevel: str, args: argparse.Namespace) -> None:
    """振り分け前の課題（トラッカーから取り込んだものなど）に番号・difficulty・loopable を付ける。"""
    old = beads.to_bd_id(args.bd_id)
    body = tw_base.read_body(args.body_file)
    error = taskfile.validate_new_body(body, hold=False)
    if error is not None:
        print(f"usage: {error}", file=sys.stderr)
        raise SystemExit(2)
    if args.direct:
        tw_plan.refuse_direct(args.difficulty, body)
    plan_base = tw_plan.registered_plan_base(toplevel, body)
    trk = tracker.session(toplevel)
    pulled = trk.before([old])
    issue = beads.show(toplevel, old)
    if issue is None:
        print(f"NOT_READY\t{args.bd_id}\t存在しない")
        raise SystemExit(4)
    actor = tw_base.actor(toplevel)
    new_id = old
    key = tracker.jira_key(issue) if trk.tracker.kind == "jira" else None
    if key is not None:
        renamed, lines = tracker.jira_rename(toplevel, [old])
        if key != old and old not in renamed:
            tw_base.print_lines(pulled + lines)
            raise SystemExit(3)
        new_id = key
    # github（`issue_prefix` が `gh`）は取り込みの時点で Issue 番号の ID になっているので番号を振らない。
    elif not beads.is_numbered(old) and not trk.bidirectional:
        number = _next_number(toplevel, tw_base.beads_snapshot(toplevel))
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
    tw_base.print_lines(pulled + trk.after([new_id]))
