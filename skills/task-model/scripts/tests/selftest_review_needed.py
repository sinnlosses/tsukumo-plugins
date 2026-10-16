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
SCRIPT = os.path.join(HERE, "..", "core", "review_needed.py")

from selftest_review_support import git, write  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


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
    print("範囲なしで差分が空なら NO_RANGE、範囲つきで空なら NOTHING")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        got = run(d, "--difficulty", "opus")
        check("範囲なしは NO_RANGE", got == "NO_RANGE", repr(got))
        git(d, "checkout", "-q", "-b", "work")
        got = run(d, "--difficulty", "opus", "main..work")
        check("範囲つきは NOTHING", got == "NOTHING", repr(got))


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


def write_globs(d: str, line: str | None) -> None:
    body = 'verify = "なし"\n' + (line + "\n" if line is not None else "")
    write(d, ".tw/config.toml", body)


def test_code_globs() -> None:
    print("review_code_globs に当たる文書は REVIEW code")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write_globs(d, 'review_code_globs = ["skills/**", "agents/**"]')
        write(d, "skills/x/SKILL.md", "s\n")
        got = run(d, "--difficulty", "sonnet")
        check("skills/ 配下の .md は REVIEW code（設定のファイルも文書でないので数える）", got == "REVIEW\tcode\t2", repr(got))
        git(d, "add", ".tw/config.toml")
        git(d, "commit", "-q", "-m", "config")
        got = run(d, "--difficulty", "sonnet")
        check("skills/ 配下の .md だけなら REVIEW code 1", got == "REVIEW\tcode\t1", repr(got))
        git(d, "add", "skills")
        git(d, "commit", "-q", "-m", "skill")
        write(d, "agents/a.md", "a\n")
        write(d, "agents/deep/b.md", "b\n")
        got = run(d, "--difficulty", "sonnet")
        check("** は深いディレクトリも越える", got == "REVIEW\tcode\t2", repr(got))
        os.remove(os.path.join(d, "agents/a.md"))
        os.remove(os.path.join(d, "agents/deep/b.md"))
        write(d, "docs/guide.md", "g\n")
        write(d, "README.md", "r\n")
        got = run(d, "--difficulty", "sonnet")
        check("通常の docs の .md は SKIP docs-only", got == "SKIP\tdocs-only", repr(got))


def test_code_globs_absent_or_empty() -> None:
    print("設定が無い・キーが無い・空のときは従来どおり")
    for label, line in (("設定ファイルなし", "-"), ("キーなし", None), ("空の配列", "review_code_globs = []")):
        with tempfile.TemporaryDirectory() as d:
            init_repo(d)
            if line != "-":
                write_globs(d, line)
                git(d, "add", ".tw/config.toml")
                git(d, "commit", "-q", "-m", "config")
            write(d, "skills/x/SKILL.md", "s\n")
            got = run(d, "--difficulty", "sonnet")
            check(f"{label}: skills/ 配下の .md は SKIP docs-only", got == "SKIP\tdocs-only", repr(got))


def test_glob_boundary() -> None:
    print("* は / を越えず、** は越える")
    with tempfile.TemporaryDirectory() as d:
        init_repo(d)
        write_globs(d, 'review_code_globs = ["agents/*.md", "docs/**/api.md"]')
        git(d, "add", ".tw/config.toml")
        git(d, "commit", "-q", "-m", "config")
        write(d, "agents/a.md", "a\n")
        got = run(d, "--difficulty", "sonnet")
        check("agents/*.md は直下の .md に当たる", got == "REVIEW\tcode\t1", repr(got))
        os.remove(os.path.join(d, "agents/a.md"))
        write(d, "agents/sub/b.md", "b\n")
        got = run(d, "--difficulty", "sonnet")
        check("agents/*.md は下のディレクトリの .md に当たらない", got == "SKIP\tdocs-only", repr(got))
        os.remove(os.path.join(d, "agents/sub/b.md"))
        write(d, "docs/api.md", "x\n")
        got = run(d, "--difficulty", "sonnet")
        check("docs/**/api.md は間のディレクトリが0個でも当たる", got == "REVIEW\tcode\t1", repr(got))
        os.remove(os.path.join(d, "docs/api.md"))
        write(d, "docs/a/b/api.md", "x\n")
        got = run(d, "--difficulty", "sonnet")
        check("docs/**/api.md は深いディレクトリでも当たる", got == "REVIEW\tcode\t1", repr(got))


def test_broken_config() -> None:
    print("壊れた設定は理由を出して非0で終わる")
    for label, line in (
        ("配列でない", 'review_code_globs = "skills/**"'),
        ("空の要素", 'review_code_globs = [""]'),
        ("文字列でない要素", "review_code_globs = [1]"),
    ):
        with tempfile.TemporaryDirectory() as d:
            init_repo(d)
            write_globs(d, line)
            write(d, "README.md", "b\n")
            out = subprocess.run(
                [sys.executable, SCRIPT, "--difficulty", "sonnet"], cwd=d, capture_output=True, text=True
            )
            check(
                f"{label}: 終了コード非0で行番号つきの理由",
                out.returncode != 0 and ".tw/config.toml:2:" in out.stderr and out.stdout == "",
                out.stdout + out.stderr,
            )


def main() -> int:
    test_code_globs()
    test_code_globs_absent_or_empty()
    test_glob_boundary()
    test_broken_config()
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
