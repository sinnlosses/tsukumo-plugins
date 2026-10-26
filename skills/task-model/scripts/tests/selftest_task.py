#!/usr/bin/env python3
"""`task.py` の自己テスト。

使い方: python3 selftest_task.py [--module selftest_t_<名前> ...] [テストの関数名 ...]
（関数名を渡すとそれだけを流す。`--module` は繰り返せて、指定のモジュールの分だけを流す。無い名前は非0）

一時ディレクトリに git リポジトリと作業ツリー2本と `.beads` を作り（本物の `bd` を一時の HOME で打つ）、`task.py` を実際に
子プロセスで（並行するテストは同時に）起こして検証する。テストは領域ごとに `selftest_t_*.py` へ分け、この入口がそれを並列に流す。
`bd` が PATH に無ければ非0で終わる。落ちたら非0で終わる（`selftest.py` と同じ形）。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tw"))

import selftest_t_accept  # noqa: E402
import selftest_t_brief  # noqa: E402
import selftest_t_finish  # noqa: E402
import selftest_t_flow  # noqa: E402
import selftest_t_guards  # noqa: E402
import selftest_t_phases  # noqa: E402
import selftest_t_plan  # noqa: E402
import selftest_t_ship  # noqa: E402
import selftest_t_tasks  # noqa: E402
import selftest_t_verify  # noqa: E402
import beads  # noqa: E402
from selftest_support import beads_home, finish, local, require_all_listed, run_parallel, select  # noqa: E402

MODULES = (
    selftest_t_verify,
    selftest_t_plan,
    selftest_t_phases,
    selftest_t_guards,
    selftest_t_accept,
    selftest_t_brief,
    selftest_t_finish,
    selftest_t_flow,
    selftest_t_ship,
    selftest_t_tasks,
)


def take_modules(argv: list[str]) -> tuple[tuple, list[str]]:
    """`--module <名前>`（繰り返し可）を取り除き、指定のモジュール（無ければ全部）と残りの引数を返す。"""
    names: list[str] = []
    rest: list[str] = []
    it = iter(argv)
    for arg in it:
        if arg == "--module":
            names.append(next(it, ""))
        else:
            rest.append(arg)
    known = {m.__name__: m for m in MODULES}
    unknown = [n for n in names if n not in known]
    if unknown:
        print("該当するモジュールが無い: " + ", ".join(unknown))
        raise SystemExit(1)
    return (tuple(known[n] for n in dict.fromkeys(names)) if names else MODULES), rest


def main() -> None:
    require_all_listed(MODULES)
    modules, only = take_modules(sys.argv[1:])
    tests = tuple(t for m in modules for t in m.TESTS)
    with beads_home((beads.PREFIX_LOCAL,)):
        outputs = run_parallel(select(tests, only))
    finish([*outputs, local.lines])


if __name__ == "__main__":
    main()
