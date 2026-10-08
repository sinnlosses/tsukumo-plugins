#!/usr/bin/env python3
"""`review_snapshot.py` の自己テスト。

使い方: python3 selftest_review_snapshot.py

標準ライブラリだけで動く。落ちたら非0で終わる。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "review_snapshot.py")

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def git(repo: str, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return out.stdout


def write(repo: str, path: str, body: str) -> None:
    with open(os.path.join(repo, path), "w", encoding="utf-8") as f:
        f.write(body)


def snapshot(repo: str) -> str:
    out = subprocess.run([sys.executable, SCRIPT], cwd=repo, capture_output=True, text=True)
    check("非0で終わらない", out.returncode == 0, out.stderr)
    return out.stdout.strip()


def init_repo(d: str) -> None:
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "a@example.com")
    git(d, "config", "user.name", "a")
    write(d, ".gitignore", "ignored.txt\n")
    write(d, "a.py", "x = 1\n")
    git(d, "add", ".gitignore", "a.py")
    git(d, "commit", "-q", "-m", "init")


def test_untracked_rewrite() -> None:
    print("前の回からある未追跡のファイルの書き換えが、2つの木の差分に出る")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "new.py", "y = 1\n")
        write(d, "ignored.txt", "secret\n")
        before = snapshot(d)
        write(d, "new.py", "y = 2\n")
        after = snapshot(d)
        diff = git(d, "diff", before, after)
        files = git(d, "ls-tree", "-r", "--name-only", after)
        check("書き換えが出る", "+y = 2" in diff and "-y = 1" in diff, diff)
        check("未追跡のファイルは入る", "new.py" in files)
        check("無視されたファイルは入らない", "ignored.txt" not in files)


def test_no_side_effect() -> None:
    print("実の索引・作業ツリー・stash の山を変えない")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "a.py", "x = 2\n")
        write(d, "new.py", "y = 1\n")
        status = git(d, "status", "--porcelain")
        index = git(d, "ls-files", "--stage")
        snapshot(d)
        check("status が同じ", git(d, "status", "--porcelain") == status)
        check("索引が同じ", git(d, "ls-files", "--stage") == index)
        check("stash が空", git(d, "stash", "list") == "")


def main() -> int:
    test_untracked_rewrite()
    test_no_side_effect()
    if failures:
        print(f"\n{len(failures)} 件落ちた")
        return 1
    print("\nすべて通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
