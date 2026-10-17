"""`tw accept`: 受け入れの機械的な並びを1回で打つ。

打つ部品の出力の行はそのまま並べ、止まる行が出たらそこで以降を打たない（終了コードは部品のもの）。
`REVIEW` は止まる行ではなく、レビューを挟むための区切りで、木の控えを出して終了コード0で返す。
`review_needed.py`・`review_snapshot.py` は同じ `scripts/` にあり、subprocess で打つ。
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
from collections.abc import Callable

import beads
import layout
import tw_base
import tw_edit
import tw_handback
import tw_verify

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core")
STOP_WORDS = ("NOT_CLAIMED", "NOT_OWNER")
FORMAT_TAIL_LINES = 40


def cmd_accept(toplevel: str, task_id: str, after_review: bool) -> None:
    if not after_review:
        _run_part(lambda: tw_handback.cmd_lap(toplevel, task_id, "accept", "start"))
        _run_part(lambda: tw_edit.cmd_plan_check(toplevel, task_id))
        if _review_gate(toplevel, task_id):
            return
    _format(toplevel)
    _run_part(lambda: tw_verify.cmd_verify_check(toplevel))
    tw_base.record(toplevel, "lap", task_id, stage="accept", edge="end")


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


def _review_gate(toplevel: str, task_id: str) -> bool:
    """レビューが要るなら `lap review` と木の控えを出して `True`。要らなければ判定の行だけ出して `False`。"""
    issue = beads.show(toplevel, tw_base.bd_task_id(task_id))
    difficulty = _difficulty(issue)
    decided = _review_script(toplevel, "review_needed.py", "--difficulty", difficulty)
    print(decided)
    if not decided.startswith("REVIEW"):
        return False
    _run_part(lambda: tw_handback.cmd_lap(toplevel, task_id, "review", "start"))
    print(f"SNAPSHOT\t{_review_script(toplevel, 'review_snapshot.py')}")
    return True


def _difficulty(issue: beads.Issue | None) -> str:
    if issue is None:
        return "sonnet"
    values = beads.attribute_values(issue, beads.DIFFICULTY_KEY)
    return values[0] if len(values) == 1 else "sonnet"


def _review_script(toplevel: str, name: str, *args: str) -> str:
    path = os.path.join(SCRIPTS, name)
    if not os.path.isfile(path):
        print(f"MISSING\t{path}")
        raise SystemExit(6)
    r = subprocess.run([sys.executable, path, *args], cwd=toplevel, capture_output=True, text=True)
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
