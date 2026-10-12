#!/usr/bin/env python3
"""`diff_added_comment_lines.py` の自己テスト。

使い方: python3 selftest_diff_added_comment_lines.py

標準ライブラリだけで動く。落ちたら非0で終わる。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "diff_added_comment_lines.py")

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def git(repo: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def write(repo: str, path: str, body: str) -> None:
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(body)


def run(repo: str, *args: str) -> str:
    out = subprocess.run(
        [sys.executable, SCRIPT, *args], cwd=repo, capture_output=True, text=True
    )
    check("非0で終わらない", out.returncode == 0, out.stderr)
    return out.stdout


def init_repo(d: str) -> None:
    git(d, "init", "-q")
    git(d, "config", "user.email", "a@example.com")
    git(d, "config", "user.name", "a")


def test_line_comment_added() -> None:
    print("行コメントの追加行を拾う")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "a.ts", "function f() {\n  return 1\n}\n")
        git(d, "add", "a.ts")
        git(d, "commit", "-q", "-m", "init")
        write(
            d,
            "a.ts",
            "function f() {\n  // added comment\n  const x = 2 // trailing\n  return 1\n}\n",
        )
        out = run(d)
        check("追加した行コメントが1行出る", out == "a.ts:2\t// added comment\n", out)


def test_code_line_not_picked() -> None:
    print("コードの追加行は拾わない")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "a.ts", "function f() {\n  return 1\n}\n")
        git(d, "add", "a.ts")
        git(d, "commit", "-q", "-m", "init")
        write(d, "a.ts", "function f() {\n  const x = 2\n  return 1\n}\n")
        out = run(d)
        check("出力が空", out == "", out)


def test_removed_comment_not_picked() -> None:
    print("削除したコメント行は拾わない")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "a.ts", "function f() {\n  // old comment\n  return 1\n}\n")
        git(d, "add", "a.ts")
        git(d, "commit", "-q", "-m", "init")
        write(d, "a.ts", "function f() {\n  return 1\n}\n")
        out = run(d)
        check("出力が空", out == "", out)


def test_unknown_extension_ignored() -> None:
    print("対象外の拡張子は無視する")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "a.txt", "line one\n")
        git(d, "add", "a.txt")
        git(d, "commit", "-q", "-m", "init")
        write(d, "a.txt", "line one\n# looks like a comment but .txt is not tracked\n")
        out = run(d)
        check("出力が空", out == "", out)


def main() -> int:
    test_line_comment_added()
    test_code_line_not_picked()
    test_removed_comment_not_picked()
    test_unknown_extension_ignored()
    if failures:
        print(f"\n{len(failures)} 件失敗")
        return 1
    print("\nすべて ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
