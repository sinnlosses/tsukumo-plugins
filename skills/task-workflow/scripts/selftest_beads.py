#!/usr/bin/env python3
"""`task.py` の Beads との往復（`bd`・トラッカー・設定の読み）の自己テスト。

使い方: python3 selftest_beads.py [テストの関数名 ...]（名前を渡すとそれだけを流す）

一時ディレクトリに git リポジトリと作業ツリー2本を作り、`bd init --stealth` で `.beads` を置いて、
`task.py` を実際に子プロセスで（取り合いは同時に）起こして確かめる。本物の `bd` を使う
（無ければ非0で終わる。家の向け先は `selftest_support.beads_home`）。テストは概念ごとに
`selftest_b_*.py`（core・sync・claims）へ分け、この入口がそれを並列に流す。足場は `selftest_b_support.py`、
偽の GitHub は `selftest_b_fakegithub.py`。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import beads  # noqa: E402
import selftest_b_claims  # noqa: E402
import selftest_b_core  # noqa: E402
import selftest_b_sync  # noqa: E402
from selftest_support import beads_home, finish, local, run_parallel, select  # noqa: E402

MODULES = (
    selftest_b_claims,
    selftest_b_sync,
    selftest_b_core,
)


def main() -> None:
    tests = tuple(t for m in MODULES for t in m.TESTS)
    with beads_home((beads.PREFIX_LOCAL, beads.PREFIX_GITHUB)):
        outputs = run_parallel(select(tests, sys.argv[1:]))
    finish([*outputs, local.lines])


if __name__ == "__main__":
    main()
