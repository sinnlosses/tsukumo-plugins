"""`tw pause`・`step`・`lap` と `handback-guard` の関門（委譲先の返却）。"""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
from typing import Callable

import beads
import gitinfo
import layout
import ledger
import metrics
import stamps
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

    タスクIDだけなら着手の印と突き合わせて、引数なしと同じに控える。
    段を名指せば、段の控え（`_write_step_stamps`）を種類 `pause` で残す。
    """
    if task_id is not None:
        if step is None:
            tw_base.require_claimed(toplevel, task_id)
        else:
            shown, specs, n = _owned_step(toplevel, task_id, step)
            key = _write_step_stamps(toplevel, shown, specs, n, "pause")
            tw_base.record(toplevel, "pause", shown, step=n, steps=len(specs))
            print(f"PAUSED\t{key.tree}")
            return
    key = _current_key(toplevel)
    stamps.write_pause_stamp(key, cwd=toplevel)
    others = [tree for task_id in tw_base.claimed_here(toplevel) for tree in task_trees(toplevel, task_id)[1:]]
    for tree in dict.fromkeys(others):
        stamps.write_pause_stamp(_current_key(tree), cwd=tree)
    tw_base.record_claimed(toplevel, "pause")
    print(f"PAUSED\t{key.tree}")


def cmd_step(toplevel: str, task_id: str, step: str) -> None:
    """途中の段（`## やること` の最後でない段）を済ませた印を、段の控え（`_write_step_stamps`）で残す。"""
    shown, specs, n = _owned_step(toplevel, task_id, step)
    if n == len(specs):
        print(f"LAST_STEP\t{shown}\t{n}/{len(specs)}\t最後の段は tw verify を通してから返す")
        raise SystemExit(4)
    failed = _failed_step_check(task_trees(toplevel, shown))
    if failed is not None:
        tree, output = failed
        print(f"STEP_CHECK_FAILED\t{shown}\t{n}/{len(specs)}\t{tree}")
        print(output)
        raise SystemExit(4)
    key = _write_step_stamps(toplevel, shown, specs, n, "step")
    tw_base.record(toplevel, "step", shown, step=n, steps=len(specs))
    for touched in tw_plan.main_touched(task_trees(toplevel, shown)[-1], _claimed_plan_body(toplevel, shown)):
        print(f"MAIN_TOUCHED\t{shown}\t{touched}")
    print(f"STEPPED\t{shown}\t{n}/{len(specs)}\t{key.tree}")


STEP_CHECK_TAIL_LINES = 20


def _failed_step_check(trees: list[str]) -> tuple[str, str] | None:
    """各作業ツリーの設定の `step_check` を、その木を cwd にして打ち、最初に落ちた `(木, 出力の末尾)`。
    `step_check` が無い木と、設定が読めない木は飛ばす（読めない設定は `tw verify` が `INVALID` にする）。"""
    for tree in trees:
        try:
            command = layout.read_config(tree).step_check
        except layout.ConfigError:
            continue
        if command is None:
            continue
        r = subprocess.run(["sh", "-c", command], cwd=tree, capture_output=True, text=True)
        if r.returncode != 0:
            lines = (r.stdout + r.stderr).splitlines()
            return tree, "\n".join(lines[-STEP_CHECK_TAIL_LINES:])
    return None


def _owned_step(toplevel: str, task_id: str, step: str) -> tuple[str, tuple[taskfile.PlanStep, ...], int]:
    """`(表示のタスクID, 段, 段の番号)`。自分の着手でなければ `NOT_OWNER`（終了コード4）、段が読めないか番号が段の外なら終了コード2。"""
    shown = beads.to_task_id(beads.to_bd_id(task_id))
    if not tw_base.owns(toplevel, shown):
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
) -> stamps.ContentKey:
    """段 `n` の控えを、タスクの作業ツリー（`task_trees`）それぞれに、段 `n` と並列の組になる段の触るファイルを
    外したいまの鍵で書き、着手中でないタスクの段の控えを消す。着手した作業ツリーの鍵を返す。"""
    excluded = taskfile.parallel_files(specs, n)
    claimed = tw_base.claimed_here(toplevel)
    key = _current_key(toplevel, excluded)
    for tree in task_trees(toplevel, shown):
        tree_key = key if tree == toplevel else _current_key(tree, excluded)
        stamps.write_step_stamp(stamps.StepStamp(tree_key, shown, n, kind), cwd=tree)
        stale = [s.task_id for s in stamps.read_step_stamps(cwd=tree) if s.task_id not in claimed]
        stamps.remove_step_stamps(stale, cwd=tree)
    return key


LAP_STAGES = ("direct", "delegate", "accept", "review", "retro", "land", "sync")
LAP_EDGES = ("start", "end")


def cmd_lap(toplevel: str, task_id: str, stage: str, edge: str | None = None, model: str | None = None) -> None:
    if stage not in LAP_STAGES:
        print(f"usage: 段 {stage!r} が {'・'.join(LAP_STAGES)} のどれでもない", file=sys.stderr)
        raise SystemExit(2)
    if edge is not None and edge not in LAP_EDGES:
        print(f"usage: 端 {edge!r} が {'・'.join(LAP_EDGES)} のどちらでもない", file=sys.stderr)
        raise SystemExit(2)
    if model is not None and (stage, edge) != ("delegate", "start"):
        print("usage: --model は delegate start にだけ付けられる", file=sys.stderr)
        raise SystemExit(2)
    shown = beads.to_task_id(beads.to_bd_id(task_id))
    if shown not in tw_base.claimed_here(toplevel):
        print(f"NOT_CLAIMED\t{shown}")
        return
    if edge is None:
        tw_base.record(toplevel, "lap", shown, stage=stage)
        print(f"LAPPED\t{shown}\t{stage}")
        return
    if edge == "end":
        close_open_lap(toplevel, shown, stage)
        print(f"LAPPED\t{shown}\t{stage}\t{edge}")
        return
    extra = {"model": model} if model is not None else {}
    tw_base.record(toplevel, "lap", shown, stage=stage, edge=edge, **extra)
    if stage == "accept":
        close_open_lap(toplevel, shown, "delegate")
    print(f"LAPPED\t{shown}\t{stage}\t{edge}")


def close_open_lap(toplevel: str, task_id: str, stage: str) -> None:
    """最後の `claim` 以降で段 `stage` が開いていれば、その `end` を `flow/` に書く（何も出さない）。"""
    shown = beads.to_task_id(beads.to_bd_id(task_id))
    try:
        events = metrics.read_events(ledger.ledger_root(cwd=toplevel))[0]
    except (OSError, gitinfo.GitCommandError):
        return
    if stage in metrics.open_stages(events, shown):
        tw_base.record(toplevel, "lap", shown, stage=stage, edge="end")


def task_trees(toplevel: str, task_id: str) -> list[str]:
    """タスクの作業ツリー。先頭は着手した `toplevel`。

    `## やること` の `### 作業先` が別のリポジトリなら、そのリポジトリの作業ツリーのうち、
    枝の名前がタスクIDを小文字にしたものを続ける。
    """
    work_repo, _ = taskfile.plan_work_repo(_claimed_plan_body(toplevel, task_id))
    if work_repo is None or not os.path.isdir(work_repo):
        return [toplevel]
    try:
        if gitinfo.git_common_dir(work_repo) == gitinfo.git_common_dir(toplevel):
            return [toplevel]
        worktrees = gitinfo.list_worktrees(cwd=work_repo)
    except gitinfo.GitCommandError:
        return [toplevel]
    return [toplevel, *(w.path for w in worktrees if w.branch == task_id.lower())]


def _current_key(tree: str, excluded: tuple[str, ...] = ()) -> stamps.ContentKey:
    """`tree` のいまの中身の鍵（検証コマンドは `tree` の設定ファイルのもの）。`excluded` のパスを木から外す。"""
    return stamps.content_key(ship.read_stamp_command(tree) or "", cwd=tree, excluded=excluded)


def _claimed_plan_body(toplevel: str, task_id: str) -> str:
    """着手中のタスクの `## やること` の節だけの本文。読めなければ空。"""
    issue = beads.show(toplevel, beads.to_bd_id(task_id))
    return f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n" if issue is not None else ""


STRUCTURE_HEADING = "## 構造の変更"


STRUCTURE_EDGE_LINE = re.compile(r"[+\-~] .+ -> .+")
STRUCTURE_NONE_LINE = re.compile(r"なし: .+")


def _structure_section_lines(body: str) -> list[str] | None:
    """`## 構造の変更` の節の本文の、空でない行（前後の空白は除く）。節が無ければ `None`。"""
    lines = body.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == STRUCTURE_HEADING), None)
    if start is None:
        return None
    section: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if line.strip():
            section.append(line.strip())
    return section


def _structure_section_problem(body: str) -> str | None:
    """`## 構造の変更` の節が無い・中身の形が違うときの、拒む理由の前半。正しければ `None`。"""
    section = _structure_section_lines(body)
    if section is None:
        return f"最後の段の返却の本文に {STRUCTURE_HEADING} の節が無い"
    if not section:
        return f"{STRUCTURE_HEADING} の節が空"
    if len(section) == 1 and STRUCTURE_NONE_LINE.fullmatch(section[0]):
        return None
    bad = next((n for n, line in enumerate(section, 1) if not STRUCTURE_EDGE_LINE.fullmatch(line)), None)
    if bad is None:
        return None
    return f"{STRUCTURE_HEADING} の節の{bad}行目が形に合わない"


def handback_refusal(where: str, body: str | None = None) -> tuple[str, str] | None:
    """`where` の作業ツリーから委譲先が返すのを拒む `(規則のキー, 理由)`。通すなら `None`。

    規則のキーは `PLAN_OR_VERIFY_MISSING`（計画か検証が欠けた）と `STRUCTURE_SECTION`（構造の節が合わない）。

    `body`（返却の本文。読める経路でだけ渡る）があり、計画・検証が揃った最後の段の返却（`tw step` の印も
    `tw pause` の控えも使わず `verify-check` が `VERIFIED_SAME`）で、本文に `## 構造の変更` の節が無いか、中身が形（各行が `+|-|~ A -> B` か、全体が `なし: <理由>` の1行）に合わなければ拒む。

    控えのタスクごとに、その作業ツリー（`task_trees`）を1つずつ見る。作業ツリーを通すのは、作業が無い
    （着手した作業ツリーでは `claim` 時の `HEAD` より後のコミットも、変更も無い。
    作業先の作業ツリーでは主ブランチとの分かれ目より後のコミットも、変更も無い）か、`plan-check` が通ったうえで
    その作業ツリーの `verify-check` が通っているか `tw step` の印が最後でない段をいまの中身で指しているか、
    その作業ツリーの `tw pause` の控えがいまの中身と同じとき。段の印のいまの中身は、その段と並列の組になる段の
    触るファイルを外して取る。
    検証が通る返却でも、計画の段が2つ以上でその作業ツリーにも着手した作業ツリーにも段の控えが1つも無ければ、段を飛ばしたとして拒む。
    """
    if not os.path.isdir(where):
        return None
    claims = ledger.open_claims(cwd=where)
    if not claims:
        return None
    toplevel = gitinfo.git_toplevel(where)
    last_stage: list[str] = []
    gaps = [line for task_id in claims for line in _handback_gaps(toplevel, task_id, last_stage)]
    if not gaps:
        problem = _structure_section_problem(body) if body is not None and last_stage else None
        if problem is not None:
            return "STRUCTURE_SECTION", (
                f"{problem}（着手の印 {','.join(claims)}）。"
                f"{STRUCTURE_HEADING} に、各行が tsukumo の graph の記法（例: + A -> B: 語）の行か、"
                f"全体が なし: <理由> の1行の節を書いてから返す"
            )
        return None
    shown = ",".join(claims)
    return "PLAN_OR_VERIFY_MISSING", (
        f"着手の印（{shown}）がある作業ツリー（{toplevel}）のタスクに作業があるのに、計画か検証が欠けたまま返そうとした"
        f"（{' / '.join(gaps)}）。## やること が無ければ tw edit <ID> --section 'やること' --body-file - で書き、"
        f"tw verify を通してから返す（作業先が別のリポジトリなら、コミットのあとに作業先の作業ツリーで打つ）。"
        f"最後でない段を済ませて返すときは tw step <ID> <段の番号> を打ってから返す。目視待ちで返すとき・判断が要って止めて返すとき・計画を作業の後に書いたときは、"
        f"tw pause を打ってから返す（並列の段の担当は tw pause <ID> <段の番号>。tw step・tw pause は着手した作業ツリーで打つ。打ったあとに中身を変えたら打ち直す）"
    )


def _handback_gaps(toplevel: str, task_id: str, last_stage: list[str] | None = None) -> list[str]:
    """作業がある作業ツリーの、通っていない `plan-check`・`verify-check` の行。作業が無ければ空。

    `last_stage` を渡すと、`verify-check` が `VERIFIED_SAME` で通った作業ツリーをそこへ足す。

    作業先の作業ツリーの `verify-check` の行には、その作業ツリーのパスを添える。
    """
    # plan-check は取り込みが消した着手の印を控えから戻すので、claim 時の HEAD はそのあとに読む。
    plan_line = _first_output_line(lambda: tw_edit.cmd_plan_check(toplevel, task_id))
    issue = beads.show(toplevel, beads.to_bd_id(task_id))
    metadata = issue.raw.get("metadata") if issue is not None else None
    head = metadata.get(beads.CLAIM_HEAD_KEY) if isinstance(metadata, dict) else None
    plan_ok = plan_line.split("\t")[0] in HANDBACK_PLAN_OK
    gaps: list[str] = []
    for tree in task_trees(toplevel, task_id):
        work_head = head if tree == toplevel else _fork_point(tree)
        if tw_plan.plan_state(tree, work_head, ()) == tw_plan.PLAN_FIRST:
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
            if verify_line.startswith("NOT_VERIFIED\tscheme"):
                verify_line += "（別の版の tw が控えた。クローンと取り込みの版を揃えてから tw verify を打ち直す）"
            gaps.append(verify_line if tree == toplevel else f"{verify_line}（{tree}）")
        elif plan_ok and verify_line.startswith("VERIFIED_SAME") and _skipped_steps(toplevel, tree, task_id):
            gaps.append(
                f"段の控えが無い。渡された段だけを進めて tw step {task_id} <段の番号> を打って返す"
                + ("" if tree == toplevel else f"（{tree}）")
            )
        elif last_stage is not None and verify_line.startswith("VERIFIED_SAME"):
            last_stage.append(tree)
    return gaps


def _skipped_steps(toplevel: str, tree: str, task_id: str) -> bool:
    """計画の段が2つ以上なのに、`tree` にも着手した作業ツリー `toplevel` にもこのタスクの段の控えが1つも無い。"""
    specs, _ = taskfile.plan_step_specs(_claimed_plan_body(toplevel, task_id))
    if len(specs) < 2:
        return False
    return not any(
        s.task_id == task_id for where in dict.fromkeys((tree, toplevel)) for s in stamps.read_step_stamps(cwd=where)
    )


def _tree_verify_line(tree: str) -> str:
    """`tree` の `verify-check` の行。`tw verify` がそこで形式のために打てない（`tw_base.format_refusal`）なら、控えを書けないので `NOTHING`。"""
    if tw_base.format_refusal(tree) is not None:
        return "NOTHING\t(tw verify が打てない形式)"
    return _first_output_line(lambda: tw_verify.cmd_verify_check(tree))


def _fork_point(tree: str) -> str | None:
    """`tree` の `HEAD` と主ブランチの分かれ目。引けなければ `None`。"""
    r = tw_base.run_git(tree, ["merge-base", "HEAD", gitinfo.base_branch(tree)])
    return r.stdout.strip() if r.returncode == 0 else None


def _current_stamp_kinds(toplevel: str, tree: str, task_id: str) -> set[str]:
    """`tree` の控えのうち、いまの中身と同じものの種類（`pause`・`step`）。

    `tw pause` の控えはいまの鍵と、段の控えはその段と並列の組になる段の触るファイルを外したいまの鍵と照らす。
    段の `step` の控えは段がいまの計画の最後より前のときだけ効く。
    """
    keys: dict[tuple[str, ...], stamps.ContentKey] = {}

    def key(excluded: tuple[str, ...]) -> stamps.ContentKey:
        if excluded not in keys:
            keys[excluded] = _current_key(tree, excluded)
        return keys[excluded]

    kinds: set[str] = set()
    pause = stamps.read_pause_stamp(cwd=tree)
    if pause is not None and pause == key(()):
        kinds.add("pause")
    task_stamps = [s for s in stamps.read_step_stamps(cwd=tree) if s.task_id == task_id]
    if not task_stamps:
        return kinds
    specs, _ = taskfile.plan_step_specs(_claimed_plan_body(toplevel, task_id))
    for stamp in task_stamps:
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
