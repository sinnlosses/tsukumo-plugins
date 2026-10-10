"""`tw pause`・`step`・`lap` と `handback-guard` の関門（委譲先の返却）。"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from typing import Callable

import beads
import ledger
import ship
import taskfile
import tw_base
import tw_edit
import tw_plan
import tw_verify


HANDBACK_PLAN_OK = ("PLAN_FIRST", "PLAN_REGISTERED")


HANDBACK_VERIFY_OK = ("VERIFIED_SAME", "NOTHING")


def cmd_pause(toplevel: str, task_id: str | None = None, step: str | None = None) -> None:
    """いまの中身の鍵を、着手した作業ツリーと着手中のタスクの作業先の作業ツリーそれぞれに控える。

    タスクIDだけなら着手の印と突き合わせて、引数なしと同じに控える。段を名指せば、段の控え（`_write_step_stamps`）を種類 `pause` で残す。
    """
    if task_id is not None and step is None:
        tw_base.require_claimed(toplevel, task_id)
    if task_id is not None and step is not None:
        shown, specs, n = _owned_step(toplevel, task_id, step)
        key = _write_step_stamps(toplevel, shown, specs, n, "pause")
        tw_base.record(toplevel, "pause", shown, step=n, steps=len(specs))
        print(f"PAUSED\t{key.tree}")
        return
    key = _current_key(toplevel)
    ledger.write_pause_stamp(key, cwd=toplevel)
    others = [tree for task_id in tw_base.claimed_here(toplevel) for tree in _task_trees(toplevel, task_id)[1:]]
    for tree in dict.fromkeys(others):
        ledger.write_pause_stamp(_current_key(tree), cwd=tree)
    tw_base.record_claimed(toplevel, "pause")
    print(f"PAUSED\t{key.tree}")


def cmd_step(toplevel: str, task_id: str, step: str) -> None:
    """途中の段（`## やること` の最後でない段）を済ませた印を、段の控え（`_write_step_stamps`）で残す。"""
    shown, specs, n = _owned_step(toplevel, task_id, step)
    if n == len(specs):
        print(f"LAST_STEP\t{shown}\t{n}/{len(specs)}\t最後の段は tw verify を通してから返す")
        raise SystemExit(4)
    key = _write_step_stamps(toplevel, shown, specs, n, "step")
    tw_base.record(toplevel, "step", shown, step=n, steps=len(specs))
    print(f"STEPPED\t{shown}\t{n}/{len(specs)}\t{key.tree}")


def _owned_step(toplevel: str, task_id: str, step: str) -> tuple[str, tuple[taskfile.PlanStep, ...], int]:
    """`(表示のタスクID, 段, 段の番号)`。自分の着手でなければ `NOT_OWNER`（終了コード4）、段が読めないか番号が段の外なら終了コード2。"""
    shown = beads.to_task_id(beads.to_bd_id(task_id))
    if shown not in tw_base.claimed_here(toplevel):
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
    claimed = tw_base.claimed_here(toplevel)
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
    shown = beads.to_task_id(beads.to_bd_id(task_id))
    if shown not in tw_base.claimed_here(toplevel):
        print(f"NOT_CLAIMED\t{shown}")
        return
    tw_base.record(toplevel, "lap", shown, stage=stage)
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
    """着手中のタスクの `## やること` の節だけの本文。読めなければ空。"""
    issue = beads.show(toplevel, beads.to_bd_id(task_id))
    return f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n" if issue is not None else ""


def handback_refusal(where: str) -> str | None:
    """`where` の作業ツリーから委譲先が返すのを拒む理由。通すなら `None`。

    控えのタスクごとに、その作業ツリー（`_task_trees`）を1つずつ見る。作業ツリーを通すのは、作業が無い
    （着手した作業ツリーでは `claim` 時の `HEAD` より後のコミットも、変更も無い。
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
    gaps = [line for task_id in claims for line in _handback_gaps(toplevel, task_id)]
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


def _handback_gaps(toplevel: str, task_id: str) -> list[str]:
    """作業がある作業ツリーの、通っていない `plan-check`・`verify-check` の行。作業が無ければ空。

    作業先の作業ツリーの `verify-check` の行には、その作業ツリーのパスを添える。
    """
    issue = beads.show(toplevel, beads.to_bd_id(task_id))
    metadata = issue.raw.get("metadata") if issue is not None else None
    head = metadata.get(beads.CLAIM_HEAD_KEY) if isinstance(metadata, dict) else None
    plan_line = _first_output_line(lambda: tw_edit.cmd_plan_check(toplevel, task_id))
    plan_ok = plan_line.split("\t")[0] in HANDBACK_PLAN_OK
    gaps: list[str] = []
    for tree in _task_trees(toplevel, task_id):
        work_head = head if tree == toplevel else _fork_point(tree)
        if tw_plan.plan_state(tree, work_head, "-") == tw_plan.PLAN_FIRST:
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
    """`tree` の `verify-check` の行。`tw verify` がそこで形式のために打てない（`tw_base.format_refusal`）なら、控えを書けないので `NOTHING`。"""
    if tw_base.format_refusal(tree) is not None:
        return "NOTHING\t(tw verify が打てない形式)"
    return _first_output_line(lambda: tw_verify.cmd_verify_check(tree))


def _fork_point(tree: str) -> str | None:
    """`tree` の `HEAD` と主ブランチの分かれ目。引けなければ `None`。"""
    r = tw_base.run_git(tree, ["merge-base", "HEAD", ledger.base_branch(tree)])
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
