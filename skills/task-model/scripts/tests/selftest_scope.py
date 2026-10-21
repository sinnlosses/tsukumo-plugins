#!/usr/bin/env python3
"""変えたファイルの一覧（標準入力。1行1パス）から、流す `selftest_t_*` のモジュール名を出す。

使い方: git diff --name-only ... | python3 selftest_scope.py

出力は `ALL`（全件を流す）の1行か、モジュール名の1行ずつ。対応に載っていないファイルが1つでもあれば `ALL`。
`check.sh` がタスクコマンドの自己テストの段を絞るのに使う。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TW_DIR = os.path.join(os.path.dirname(HERE), "tw")
TW_PREFIX = "skills/task-model/scripts/tw/"
TESTS_PREFIX = "skills/task-model/scripts/tests/"

ALL = "ALL"

SCOPED_TW = {
    "tw_accept.py": "selftest_t_accept",
    "tw_brief.py": "selftest_t_brief",
    "tw_finish.py": "selftest_t_finish",
    "tw_ship.py": "selftest_t_ship",
    "tw_verify.py": "selftest_t_verify",
    "tw_plan.py": "selftest_t_plan",
}

# 対応が複数のモジュールにまたがる・広く使われるため、全件に倒す
FULL_TW = (
    "tw_base.py",
    "tw_claim.py",
    "tw_config.py",
    "tw_edit.py",
    "tw_handback.py",
    "tw_maint.py",
    "tw_new.py",
    "tw_relay.py",
    "tw_status.py",
)


def module_for(path: str) -> str | None:
    if path.startswith(TW_PREFIX):
        return SCOPED_TW.get(path[len(TW_PREFIX):])
    if path.startswith(TESTS_PREFIX):
        name = path[len(TESTS_PREFIX):]
        stem, ext = os.path.splitext(name)
        if ext == ".py" and stem.startswith("selftest_t_") and os.path.exists(os.path.join(HERE, name)):
            return stem
    return None


def scope(paths: list[str]) -> list[str]:
    paths = [p for p in paths if p]
    modules: list[str] = []
    for path in paths:
        module = module_for(path)
        if module is None:
            return [ALL]
        if module not in modules:
            modules.append(module)
    return modules or [ALL]


def main() -> None:
    print("\n".join(scope([line.strip() for line in sys.stdin])))


if __name__ == "__main__":
    main()
