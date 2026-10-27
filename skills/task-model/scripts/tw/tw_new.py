"""`tw new`・`adopt`（タスクを登録する）。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import beads
import layout
import taskfile
import tracker
import tw_base
import tw_plan


@dataclass(frozen=True)
class _Checked:
    """検査を通した1件分の入力。`deps` は `T-9`・`GH-5` の形のID。"""

    summary: str
    deps: tuple[str, ...]
    fixes: tuple[str, ...]
    body: str
    plan_base: str | None


def _check_one(toplevel: str, args: argparse.Namespace) -> _Checked:
    """1件分の引数と本文を検査する（何も書かない）。誤りは `usage:` の行と終了コード2。"""
    summary = args.summary.strip()
    if summary == "" or "\n" in args.summary:
        print("usage: --summary は改行を含まない1行にする", file=sys.stderr)
        raise SystemExit(2)
    deps = tuple(d for d in (x.strip() for x in args.deps.split(",")) if d) if args.deps else ()
    for d in deps:
        if not layout.ANY_ID_PATTERN.match(d):
            print(f"usage: --deps の {d!r} が T-999・GH-5・PROJ-123 の形式でない", file=sys.stderr)
            raise SystemExit(2)
    fixes = tuple(dict.fromkeys(x.strip().upper() for x in args.fixes.split(",") if x.strip()))
    for f in fixes:
        if not layout.ANY_ID_PATTERN.match(f):
            print(f"usage: --fixes の {f!r} が T-999・GH-5・PROJ-123 の形式でない", file=sys.stderr)
            raise SystemExit(2)
    missing_fixes = [f for f in fixes if beads.show(toplevel, beads.to_bd_id(f)) is None]
    if missing_fixes:
        print(f"usage: --fixes の {','.join(missing_fixes)} が Beads に無い", file=sys.stderr)
        raise SystemExit(2)
    if args.draft and (args.hold or args.direct):
        print("usage: --draft は --hold・--direct と一緒に使えない", file=sys.stderr)
        raise SystemExit(2)
    body = tw_base.read_body(args.body_file)
    error = taskfile.validate_draft_body(body) if args.draft else taskfile.validate_new_body(body, args.hold, args.difficulty)
    if error is None and args.candidate is not None:
        error = _candidate_refusal(args.draft, body, args.candidate)
    if error is not None:
        print(f"usage: {error}", file=sys.stderr)
        raise SystemExit(2)
    if args.direct:
        tw_plan.refuse_direct(args.difficulty, body)
    plan_base = tw_plan.registered_plan_base(toplevel, body) if taskfile.has_plan(body) and not args.draft else None
    return _Checked(summary, deps, fixes, body, plan_base)


def _metadata(args: argparse.Namespace, checked: _Checked, candidate: tuple[str, str, str] | None) -> dict[str, str]:
    metadata = {beads.DIFFICULTY_KEY: args.difficulty, beads.LOOPABLE_KEY: args.loopable}
    if checked.plan_base is not None:
        metadata[beads.PLAN_BASE_KEY] = checked.plan_base
    if args.draft:
        metadata[beads.DRAFT_KEY] = beads.DRAFT_ON
    if candidate is not None:
        metadata[beads.CANDIDATE_SIGNAL_KEY] = candidate[1]
        metadata[beads.CANDIDATE_PLACE_KEY] = candidate[2]
    if checked.fixes:
        metadata[beads.FIXES_KEY] = ",".join(checked.fixes)
    return metadata


def _create_cmd(
    args: argparse.Namespace,
    checked: _Checked,
    parts: beads.BodyParts,
    metadata: dict[str, str],
    candidate: tuple[str, str, str] | None,
    bd_id: str,
    dep_bd_ids: list[str],
) -> list[str]:
    cmd = ["create", "--id", bd_id, "--title", checked.summary, "--body-file", "-", "--metadata", json.dumps(metadata), "--silent"]
    if args.direct:
        cmd += ["-l", beads.DIRECT_ON]
    if args.kind:
        cmd += ["-l", f"{beads.KIND_LABEL}{args.kind}"]
    if candidate is not None:
        cmd += ["-l", beads.CANDIDATE_LABEL]
    if parts.acceptance:
        cmd += ["--acceptance", parts.acceptance]
    if parts.notes:
        cmd += ["--notes", parts.notes]
    if args.hold or args.draft:
        cmd += ["-s", beads.HOLD_STATUS]
    if dep_bd_ids:
        cmd += ["--deps", ",".join(dep_bd_ids)]
    return cmd


def _record_fixes(toplevel: str, checked: _Checked, bd_id: str) -> None:
    if checked.fixes:
        tw_base.record(toplevel, "new", beads.to_task_id(bd_id), fixes=",".join(checked.fixes))


def _provisional_id(actor: str) -> str:
    # 番号は Issue を立てるまで決まらないので、仮の ID で作ってから `gh-<Issue 番号>` へ付け替える。
    return f"{beads.PROVISIONAL_PREFIX}{re.sub(r'[^0-9a-z]+', '-', actor.lower()).strip('-')}-{time.time_ns()}"


def cmd_new(toplevel: str, args: argparse.Namespace) -> None:
    checked = _check_one(toplevel, args)
    deps, body = checked.deps, checked.body

    trk = tracker.session(toplevel)
    snap = None
    if not trk.bidirectional or len(deps) > SHOW_DEPS_LIMIT:
        snap = tw_base.beads_snapshot(toplevel)
        missing = [d for d in deps if d not in snap.issues]
    else:
        missing = [d for d in deps if beads.show(toplevel, beads.to_bd_id(d)) is None]
    if missing:
        print(f"usage: --deps の {','.join(missing)} が Beads に無い（解決済みなら外す）", file=sys.stderr)
        raise SystemExit(2)

    parts = beads.split_body(body)
    actor = tw_base.actor(toplevel)
    candidate = None
    if args.candidate is not None:
        signal, place = args.candidate[0].strip(), beads.candidate_place(args.candidate[1])
        candidate = (taskfile.draft_viewpoint(body) or "", signal, place)
        recurred = beads.recurred_candidate(beads.list_issues(toplevel), candidate, datetime.now(timezone.utc))
        if recurred is not None:
            _promote(toplevel, trk, recurred, actor, parts.description, candidate)
            return
    metadata = _metadata(args, checked, candidate)
    dep_bd_ids = [beads.to_bd_id(d) for d in deps]

    def create_cmd(bd_id: str) -> list[str]:
        return _create_cmd(args, checked, parts, metadata, candidate, bd_id, dep_bd_ids)

    if trk.bidirectional:
        provisional = _provisional_id(actor)
        beads.run_ok(toplevel, create_cmd(provisional), actor, parts.description)
        bd_id, lines = trk.register(provisional)
        _record_fixes(toplevel, checked, bd_id)
        print(f"CREATED\t{beads.to_task_id(bd_id)}\tbeads:{bd_id}")
        tw_plan.warn_report_names(body)
        tw_plan.warn_large_plan(beads.to_task_id(bd_id), body)
        tw_base.print_lines(lines)
        return

    assert snap is not None
    try:
        bd_id, _ = _create_numbered(toplevel, actor, _next_number(toplevel, snap), create_cmd, parts.description)
    except _Locked as e:
        print(f"LOCKED\t{e}")
        raise SystemExit(4)
    _record_fixes(toplevel, checked, bd_id)
    print(f"CREATED\t{beads.to_task_id(bd_id)}\tbeads:{bd_id}")
    tw_plan.warn_report_names(body)
    tw_plan.warn_large_plan(beads.to_task_id(bd_id), body)
    tw_base.print_lines(trk.push_created([bd_id]))


class _Locked(Exception):
    pass


def _create_numbered(
    toplevel: str, actor: str, number: int, make_cmd: Callable[[str], list[str]], description: str
) -> tuple[str, int]:
    """番号を取り合う作り方（`bd create --id` は同じ番号を1つしか作らない）。作ったIDと次の番号を返す。"""
    for _ in range(NEW_ATTEMPTS):
        bd_id = beads.format_bd_id(number)
        r = beads.run(toplevel, make_cmd(bd_id), actor, description)
        if r.returncode == 0:
            beads.write_last_id(toplevel, number)
            return bd_id, number + 1
        if "already exists" not in (r.stderr + r.stdout):
            raise beads.BeadsError(f"bd create が失敗: {(r.stderr or r.stdout).strip()}")
        number += 1
    raise _Locked(f"{NEW_ATTEMPTS}回続けて番号を取られた")


BATCH_FIELDS = {"summary", "difficulty", "loopable", "deps", "body_file", "hold", "direct", "kind", "fixes"}
BATCH_REF = re.compile(r"^@([1-9][0-9]*)$")


def _batch_entries(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError) as e:
        print(f"usage: --batch の {path} が読めない: {e}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(entries, list) or not entries or not all(isinstance(e, dict) for e in entries):
        print("usage: --batch は要素が1つ以上の、オブジェクトの配列にする", file=sys.stderr)
        raise SystemExit(2)
    return entries


def _batch_refuse(n: int, reason: str) -> None:
    print(f"usage: --batch の{n}件目の{reason}。何も作っていない", file=sys.stderr)
    raise SystemExit(2)


def _batch_args(n: int, entry: dict, base_dir: str) -> tuple[argparse.Namespace, list[int]]:
    """要素 `n`（1始まり）を単件の引数に直す。`deps` の `@k`（前の要素）は外して番号で返す。"""
    unknown = sorted(set(entry) - BATCH_FIELDS)
    if unknown:
        _batch_refuse(n, f"未知の欄 {','.join(unknown)}")
    for key in ("summary", "difficulty", "loopable", "body_file"):
        if not isinstance(entry.get(key), str):
            _batch_refuse(n, f"{key} が文字列でない")
    if entry["difficulty"] not in taskfile.DIFFICULTY_VALUES or entry["loopable"] not in taskfile.LOOPABLE_VALUES:
        _batch_refuse(n, "difficulty か loopable が語彙の外")
    if entry["body_file"] == "-":
        _batch_refuse(n, "body_file に - は使えない（ファイルのパスにする）")
    kind = entry.get("kind")
    if kind is not None and kind not in taskfile.KIND_VALUES:
        _batch_refuse(n, f"kind {kind!r} が語彙の外")
    raw_deps, fixes = entry.get("deps", []), entry.get("fixes", [])
    for key, value in (("deps", raw_deps), ("fixes", fixes)):
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            _batch_refuse(n, f"{key} が文字列の配列でない")
    refs: list[int] = []
    external: list[str] = []
    for d in raw_deps:
        m = BATCH_REF.match(d.strip())
        if m is None:
            external.append(d)
        elif int(m.group(1)) >= n:
            _batch_refuse(n, f"deps の {d} が前の要素を指していない")
        else:
            refs.append(int(m.group(1)))
    args = argparse.Namespace(
        summary=entry["summary"], difficulty=entry["difficulty"], loopable=entry["loopable"],
        deps=",".join(external), fixes=",".join(fixes), hold=bool(entry.get("hold")), draft=False,
        candidate=None, direct=bool(entry.get("direct")), kind=kind,
        body_file=os.path.join(base_dir, entry["body_file"]),
    )
    return args, refs


def cmd_new_batch(toplevel: str, path: str) -> None:
    """`new --batch <JSON>`: 全件を検査してから順に作り、トラッカーへの反映は最後に1回だけ行う。"""
    entries = _batch_entries(path)
    base_dir = os.path.dirname(os.path.abspath(path))
    prepared: list[tuple[argparse.Namespace, _Checked, list[int]]] = []
    for n, entry in enumerate(entries, 1):
        args, refs = _batch_args(n, entry, base_dir)
        try:
            checked = _check_one(toplevel, args)
        except OSError as e:
            _batch_refuse(n, f"body_file が読めない: {e}")
        except SystemExit:
            print(f"usage: （--batch の{n}件目で止めた。何も作っていない）", file=sys.stderr)
            raise
        prepared.append((args, checked, refs))

    trk = tracker.session(toplevel)
    snap = tw_base.beads_snapshot(toplevel)
    missing = sorted({d for _, c, _ in prepared for d in c.deps if d not in snap.issues})
    if missing:
        print(f"usage: --batch の deps の {','.join(missing)} が Beads に無い（解決済みなら外す）。何も作っていない", file=sys.stderr)
        raise SystemExit(2)

    actor = tw_base.actor(toplevel)
    number = None if trk.bidirectional else _next_number(toplevel, snap)
    created: list[str] = []
    failure: str | None = None
    for args, checked, refs in prepared:
        parts = beads.split_body(checked.body)
        metadata = _metadata(args, checked, None)
        dep_bd_ids = [beads.to_bd_id(d) for d in checked.deps] + [created[r - 1] for r in refs]

        def make_cmd(bd_id: str) -> list[str]:
            return _create_cmd(args, checked, parts, metadata, None, bd_id, dep_bd_ids)

        try:
            if number is None:
                bd_id = _provisional_id(actor)
                beads.run_ok(toplevel, make_cmd(bd_id), actor, parts.description)
            else:
                bd_id, number = _create_numbered(toplevel, actor, number, make_cmd, parts.description)
        except (beads.BeadsError, _Locked) as e:
            failure = str(e)
            break
        created.append(bd_id)

    lines: list[str] = []
    finals = {b: b for b in created}
    if trk.bidirectional:
        finals, lines = trk.register_many(created)
    elif created:
        lines = trk.push_created(created)
    for (_, checked, _refs), bd_id in zip(prepared, created):
        final = finals[bd_id]
        _record_fixes(toplevel, checked, final)
        print(f"CREATED\t{beads.to_task_id(final)}\tbeads:{final}")
        tw_plan.warn_report_names(checked.body)
        tw_plan.warn_large_plan(beads.to_task_id(final), checked.body)
    tw_base.print_lines(lines)
    if failure is not None:
        for n, (_, checked, _refs) in enumerate(prepared[len(created):], len(created) + 1):
            print(f"NOT_CREATED\t@{n}\t{checked.summary}")
        print(f"usage: --batch の{len(created) + 1}件目を作るのに失敗して止めた: {failure}", file=sys.stderr)
        raise SystemExit(4)


SHOW_DEPS_LIMIT = 3


def _candidate_refusal(draft: bool, body: str, candidate: list[str]) -> str | None:
    """`--candidate <SIGNAL の種類> <場所>` の誤り。黄のドラフトだけが候補になる。"""
    if not draft:
        return "--candidate は --draft と一緒に使う"
    color = taskfile.draft_color(body)
    if color != "黄" or not taskfile.draft_viewpoint(body):
        found = "赤（赤は1件でも承認の列へ積む。--candidate を外す）" if color == "赤" else "色か観点の無い行"
        return f"--candidate は `- 観点: 黄 <観点>` の行を持つドラフトだけ（{found}）"
    signal, place = candidate[0].strip(), beads.candidate_place(candidate[1])
    if not signal or not place or any(c in s for s in (signal, place) for c in "\t\n"):
        return "--candidate の SIGNAL の種類と場所は、タブ・改行を含まない空でない値"
    return None


def _promote(
    toplevel: str, trk: tracker.Session, issue: beads.Issue, actor: str, description: str, key: tuple[str, str, str]
) -> None:
    """候補の印を外して承認の列（`DRAFT`）へ上げ、2件目の本文を comment に残す。2件目の課題は作らない。"""
    beads.run_ok(toplevel, ["update", issue.bd_id, "--remove-label", beads.CANDIDATE_LABEL], actor)
    beads.run_ok(toplevel, ["comment", issue.bd_id, "--stdin"], actor, f"{RECURRED_HEADING}\n\n{description.strip()}\n")
    print("PROMOTED\t" + "\t".join((beads.to_task_id(issue.bd_id), *key)))
    tw_base.print_lines(trk.after([issue.bd_id]))


RECURRED_HEADING = "## 再発"

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
    error = taskfile.validate_new_body(body, hold=False, difficulty=args.difficulty)
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
    cmd += [x for l in issue.labels if l.startswith(beads.DIRECT_LABEL) for x in ("--remove-label", l)]
    cmd += ["--set-metadata", f"{beads.DIFFICULTY_KEY}={args.difficulty}", "--set-metadata", f"{beads.LOOPABLE_KEY}={args.loopable}"]
    if args.direct:
        cmd += ["--add-label", beads.DIRECT_ON]
    if args.kind:
        cmd += beads.kind_label_args(issue, args.kind)
    if args.summary:
        cmd += ["--title", args.summary.strip()]
    beads.run_ok(toplevel, cmd, actor, parts.description)
    print(f"ADOPTED\t{args.bd_id}\t{beads.to_task_id(new_id)}")
    tw_plan.warn_report_names(body)
    tw_plan.warn_large_plan(beads.to_task_id(new_id), body)
    tw_base.print_lines(pulled + trk.after([new_id]))
