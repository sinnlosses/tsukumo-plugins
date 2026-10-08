#!/usr/bin/env python3
"""`task.py`（1件1ファイル＋台帳の形）の自己テスト。

使い方: python3 selftest_task.py [テストの関数名 ...]（名前を渡すとそれだけを流す）

一時ディレクトリに git リポジトリと作業ツリー2本を作り、`task.py` を実際に
子プロセスで（並行するテストは同時に）起こして検証する。テストは領域ごとに `selftest_t_*.py` へ分け、この入口がそれを並列に流す。正典は
`docs/task-workflow-redesign.md`。落ちたら非0で終わる（`selftest.py` と同じ形）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import selftest_t_flow  # noqa: E402
import selftest_t_guards  # noqa: E402
import selftest_t_migrate  # noqa: E402
import selftest_t_plan  # noqa: E402
import selftest_t_ship  # noqa: E402
import selftest_t_tasks  # noqa: E402
import selftest_t_verify  # noqa: E402
from selftest_support import finish, run_parallel, select  # noqa: E402

MODULES = (
    selftest_t_verify,
    selftest_t_plan,
    selftest_t_guards,
    selftest_t_flow,
    selftest_t_ship,
    selftest_t_migrate,
    selftest_t_tasks,
)


def main() -> None:
    tests = tuple(t for m in MODULES for t in m.TESTS)
    finish(run_parallel(select(tests, sys.argv[1:])))


if __name__ == "__main__":
    main()
