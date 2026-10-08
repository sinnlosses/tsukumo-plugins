"""`selftest_task.py` と `selftest_beads.py` が共有する自己テストの支え。"""

from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

failures: list[str] = []
local = threading.local()


def say(line: str = "") -> None:
    local.lines.append(line)


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        say(f"  ok   {label}")
    else:
        say(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def write(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失敗: {r.stderr}")
    return r


def weight(n: int):
    """重いテストに付ける。大きいほど先に並列へ乗せる（後ろに残ると全体がその分延びる）。"""

    def mark(test):
        test.weight = n
        return test

    return mark


def run_one(test) -> list[str]:
    local.lines = []
    try:
        test()
    except Exception as e:  # noqa: BLE001  1件の故障で残りのテストを止めない
        check(f"{test.__name__} が落ちずに終わる", False, repr(e))
    return local.lines


def select(tests: tuple, only: list[str]) -> tuple:
    chosen = tuple(t for t in tests if not only or t.__name__ in only)
    chosen = tuple(sorted(chosen, key=lambda t: -getattr(t, "weight", 0)))
    if not chosen:
        print("該当するテストが無い: " + ", ".join(only))
        raise SystemExit(1)
    return chosen


def run_parallel(tests: tuple) -> list[list[str]]:
    with ThreadPoolExecutor(max_workers=min(len(tests), max(1, (os.cpu_count() or 2) // 2))) as pool:
        return list(pool.map(run_one, tests))


def finish(outputs: list[list[str]]) -> None:
    for lines in outputs:
        print("\n".join(lines))
    print()
    if failures:
        print(f"FAILED {len(failures)}件: " + ", ".join(failures))
        raise SystemExit(1)
    print("すべて通った")
