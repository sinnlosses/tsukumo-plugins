"""`tw accept`: 受け入れの機械的な並びを1回で打つ。

打つ部品の出力の行はそのまま並べ、止まる行が出たらそこで以降を打たない（終了コードは部品のもの）。
`REVIEW` は止まる行ではなく、レビューを挟むための区切りで、木の控えを出して終了コード0で返す。
`review_needed.py`・`review_snapshot.py` は同じ `scripts/` にあり、subprocess で打つ。
作業先が別のリポジトリなら、判定・木の控え・`verify-check` は作業先の作業ツリー（枝はタスクIDの小文字）で打つ。
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

import beads
import gitcmd
import gitinfo
import layout
import stamps
import taskfile
import tw_base
import tw_claim
import tw_edit
import tw_handback
import tw_verify

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core")
STOP_WORDS = ("NOT_CLAIMED", "NOT_OWNER")
FORMAT_TAIL_LINES = 40


@dataclass(frozen=True)
class _OtherRepo:
    """作業先が別のリポジトリのタスクの、本体・枝・作業ツリー・主ブランチ。"""

    main: str
    branch: str
    tree: str
    base: str


def cmd_accept(toplevel: str, task_id: str, after_review: bool) -> None:
    shown = beads.to_task_id(tw_base.bd_task_id(task_id))
    issue = beads.show(toplevel, tw_base.bd_task_id(task_id))
    if not after_review:
        _check_last_step(toplevel, shown, issue)
        _run_part(lambda: tw_handback.cmd_lap(toplevel, task_id, "accept", "start"))
    other = _other_repo(toplevel, shown, issue)
    if other is not None:
        _check_main_dirty(toplevel, shown, other)
    if not after_review:
        _run_part(lambda: tw_edit.cmd_plan_check(toplevel, task_id))
        if _review_gate(toplevel, task_id, issue, other):
            return
    if other is None:
        _format(toplevel)
    _run_part(lambda: tw_verify.cmd_verify_check(toplevel if other is None else other.tree))
    tw_handback.close_open_lap(toplevel, task_id, "accept")


def _check_last_step(toplevel: str, shown: str, issue: beads.Issue | None) -> None:
    """段が2つ以上の計画で、着手側の段の控え（`step`・`pause`）が `1..N-1` のどれかを欠けば `NOT_LAST_STEP` で終了コード4。

    着手していない・`loopable: N`（メインで行う流れ）・段が読めないか1つの計画は見ない。
    """
    if issue is None or shown not in tw_base.claimed_here(toplevel):
        return
    if beads.attribute_values(issue, beads.LOOPABLE_KEY) == ["N"]:
        return
    specs, error = taskfile.plan_step_specs(f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n")
    if error is not None or len(specs) < 2:
        return
    stamped = {s.step for s in stamps.read_step_stamps(cwd=toplevel) if s.task_id == shown}
    missing = [str(n) for n in range(1, len(specs)) if n not in stamped]
    if missing:
        print(f"NOT_LAST_STEP\t{shown}\t{','.join(missing)}/{len(specs)}")
        raise SystemExit(4)


def _other_repo(toplevel: str, shown: str, issue: beads.Issue | None) -> _OtherRepo | None:
    """`### 作業先` が別のリポジトリなら、その作業先。枝の作業ツリーが無ければ `MISSING` で終了コード6。"""
    if issue is None:
        return None
    plan_body = f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n"
    main = tw_claim.other_repo_main(toplevel, plan_body)
    if main is None:
        return None
    branch = shown.lower()
    trees = [w.path for w in gitinfo.list_worktrees(cwd=main) if w.branch == branch]
    if not trees:
        print(f"MISSING\t作業先 {main} の作業ツリー（枝 {branch}）")
        raise SystemExit(6)
    return _OtherRepo(main, branch, trees[0], gitinfo.base_branch(main))


def _check_main_dirty(toplevel: str, shown: str, other: _OtherRepo) -> None:
    """作業先の本体の汚れたパスを、着手の控え・委譲先の枝と比べて行で出す。

    控えにあれば `MAIN_DIRTY_BEFORE`、中身が枝の同じパスと同じなら `MAIN_DIRTY_FROM_DELEGATE`、
    どちらでもなければ `MAIN_DIRTY_UNKNOWN`。後ろ2つが1行でもあれば全部出してから終了コード4で止まる。
    """
    before = stamps.read_main_dirty(shown, cwd=toplevel)
    stop = False
    for path in gitinfo.dirty_paths(cwd=other.main):
        if before is not None and path in before:
            print(f"MAIN_DIRTY_BEFORE\t{path}")
        elif _same_blob_as_branch(other, path):
            print(f"MAIN_DIRTY_FROM_DELEGATE\t{path}")
            stop = True
        else:
            print(f"MAIN_DIRTY_UNKNOWN\t{path}")
            stop = True
    if stop:
        raise SystemExit(4)


def _same_blob_as_branch(other: _OtherRepo, path: str) -> bool:
    if not os.path.isfile(os.path.join(other.main, path)):
        return False
    here = gitcmd.run(["hash-object", "--", path], other.main)
    there = gitcmd.run(["rev-parse", "--verify", "--quiet", f"{other.branch}:{path}"], other.main)
    return here.returncode == 0 and there.returncode == 0 and here.stdout.strip() == there.stdout.strip()


def _run_part(part: Callable[[], None]) -> None:
    """部品の出力をそのまま出す。非0の終了はその終了コードで、終了コード0でも止まる行（`NOT_CLAIMED`）なら4で止まる。"""
    buffer = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buffer):
        try:
            part()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    output = buffer.getvalue()
    sys.stdout.write(output)
    stopped = any(line.split("\t", 1)[0] in STOP_WORDS for line in output.splitlines())
    if code != 0:
        raise SystemExit(code)
    if stopped:
        raise SystemExit(4)


def _review_gate(toplevel: str, task_id: str, issue: beads.Issue | None, other: _OtherRepo | None) -> bool:
    """レビューが要るなら `lap review` と木の控えを出して `True`。要らなければ判定の行だけ出して `False`。"""
    difficulty = _difficulty(issue)
    tree = toplevel if other is None else other.tree
    span = () if other is None else (f"{other.base}..{other.branch}",)
    decided = _review_script(tree, "review_needed.py", "--difficulty", difficulty, *span)
    print(decided)
    if not decided.startswith("REVIEW"):
        return False
    _run_part(lambda: tw_handback.cmd_lap(toplevel, task_id, "review", "start"))
    print(f"SNAPSHOT\t{_review_script(tree, 'review_snapshot.py')}")
    return True


def _difficulty(issue: beads.Issue | None) -> str:
    if issue is None:
        return "sonnet"
    values = beads.attribute_values(issue, beads.DIFFICULTY_KEY)
    return values[0] if len(values) == 1 else "sonnet"


def _review_script(cwd: str, name: str, *args: str) -> str:
    path = os.path.join(SCRIPTS, name)
    if not os.path.isfile(path):
        print(f"MISSING\t{path}")
        raise SystemExit(6)
    r = subprocess.run([sys.executable, path, *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"INVALID\t{name}: {r.stderr.strip() or r.stdout.strip()}")
        raise SystemExit(3)
    return r.stdout.strip()


def _format(toplevel: str) -> None:
    command = layout.read_config(toplevel).format
    if command is None:
        return
    r = subprocess.run(["sh", "-c", command], cwd=toplevel, capture_output=True, text=True)
    if r.returncode != 0:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-FORMAT_TAIL_LINES:])
        print(f"FORMAT_FAILED\t{command}")
        print(tail)
        raise SystemExit(10)
    print("FORMATTED")
