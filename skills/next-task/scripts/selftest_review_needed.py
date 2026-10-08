#!/usr/bin/env python3
"""`review_needed.py` の自己テスト。

使い方: python3 selftest_review_needed.py

標準ライブラリだけで動く。落ちたら非0で終わる。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "review_needed.py")

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
    return out.stdout.strip()


def init_repo(d: str) -> None:
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "a@example.com")
    git(d, "config", "user.name", "a")
    write(d, "README.md", "a\n")
    write(d, "a.py", "x = 1\n")
    git(d, "add", "README.md", "a.py")
    git(d, "commit", "-q", "-m", "init")


def test_docs_only() -> None:
    print("文書だけの変更は SKIP")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "README.md", "b\n")
        write(d, "docs/new.md", "c\n")
        got = run(d, "--difficulty", "sonnet")
        check("SKIP docs-only", got == "SKIP\tdocs-only", repr(got))


def test_untracked_code() -> None:
    print("未追跡の新しいコードのファイルも拾う")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "README.md", "b\n")
        write(d, "src/b.ts", "export {}\n")
        got = run(d, "--difficulty", "sonnet")
        check("REVIEW code 1", got == "REVIEW\tcode\t1", repr(got))


def test_opus_docs_only() -> None:
    print("opus は文書だけでも REVIEW")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write(d, "README.md", "b\n")
        got = run(d, "--difficulty", "opus")
        check("REVIEW opus", got == "REVIEW\topus", repr(got))


def test_nothing() -> None:
    print("差分が空なら NOTHING")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        got = run(d, "--difficulty", "opus")
        check("NOTHING", got == "NOTHING", repr(got))


def test_range() -> None:
    print("範囲を渡すとその範囲のパスを見る")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        git(d, "checkout", "-q", "-b", "work")
        write(d, "a.py", "x = 2\n")
        write(d, "NOTES.MD", "n\n")
        git(d, "add", "a.py", "NOTES.MD")
        git(d, "commit", "-q", "-m", "work")
        got = run(d, "--difficulty", "haiku", "main..work")
        check("REVIEW code 1（拡張子の大小は問わない）", got == "REVIEW\tcode\t1", repr(got))
        write(d, "README.md", "dirty\n")
        got = run(d, "--difficulty", "haiku", "main..work")
        check("範囲の外の変更は見ない", got == "REVIEW\tcode\t1", repr(got))


def test_deleted_code() -> None:
    print("コードのファイルを消しただけでも REVIEW")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        os.remove(os.path.join(d, "a.py"))
        got = run(d, "--difficulty", "sonnet")
        check("REVIEW code 1", got == "REVIEW\tcode\t1", repr(got))


def main() -> int:
    test_docs_only()
    test_untracked_code()
    test_opus_docs_only()
    test_nothing()
    test_range()
    test_deleted_code()
    if failures:
        print(f"\n{len(failures)} 件落ちた")
        return 1
    print("\nすべて通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
