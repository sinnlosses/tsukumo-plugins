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


TWO_TESTS = "def test_one():\n    assert 1\n\n\ndef test_two():\n    assert 2\n"
EXPECTED = "test/e2e/expected/home.html"
JS_TESTS = "it('a', () => {});\ntest('b', () => {});\n"


def init_marks_repo(d: str) -> None:
    init_repo(d)
    write(d, "tests/test_a.py", TWO_TESTS)
    write(d, EXPECTED, "<p>a</p>\n")
    write(d, "test/e2e/expected/other.html", "<p>b</p>\n")
    write(d, "test/a.spec.ts", JS_TESTS)
    git(d, "add", "tests", "test")
    git(d, "commit", "-q", "-m", "tests")


def marks_of(output: str) -> list[str]:
    return [line.split("\t")[1] for line in output.splitlines() if line.startswith("MARK\t")]


def test_marks_table() -> None:
    print("テストの削除・skip・expected だけの差分は MARK の行で印を付け、境界の外では出ない")
    cases: list[tuple[str, list[tuple[str, str | None]], list[str]]] = [
        ("テストのファイルを消す", [("tests/test_a.py", None)], ["test-deleted"]),
        ("テストの定義を1つ消す", [("tests/test_a.py", "def test_one():\n    assert 1\n")], ["test-deleted"]),
        ("定義を消して別の定義を足す（差し引き0）", [("tests/test_a.py", "def test_one():\n    assert 1\n\n\ndef test_three():\n    assert 3\n")], []),
        ("テストの本体だけ書き換える", [("tests/test_a.py", TWO_TESTS.replace("assert 2", "assert 20"))], []),
        ("テストのファイルを足す", [("tests/test_b.py", TWO_TESTS)], []),
        ("テストに skip を足す", [("tests/test_a.py", "import pytest\n\n\n@pytest.mark.skip\n" + TWO_TESTS)], ["test-skipped"]),
        ("テストの定義を消して skip を足す", [("tests/test_a.py", "import pytest\n\n\n@pytest.mark.skip\ndef test_one():\n    assert 1\n")], ["test-deleted", "test-skipped"]),
        *[
            (f"JS の {before} を {after} に書き換える（skip しただけ）",
             [("test/a.spec.ts", JS_TESTS.replace(before, after, 1))], ["test-skipped"])
            for before, after in (("it(", "it.skip("), ("test(", "test.skip("), ("it(", "xit("), ("test(", "test.fixme("))
        ],
        ("テストでないファイルに skip の語を足す", [("a.py", "x = 1  # pytest.mark.skip\n")], []),
        ("テストでないファイルを消す", [("a.py", None)], []),
        ("expected だけ変える", [(EXPECTED, "<p>c</p>\n")], ["expected-only"]),
        ("expected を2つとも変える", [(EXPECTED, "<p>c</p>\n"), ("test/e2e/expected/other.html", "<p>d</p>\n")], ["expected-only"]),
        ("expected とコードを一緒に変える", [(EXPECTED, "<p>c</p>\n"), ("a.py", "x = 2\n")], []),
        ("テスト置き場の expected でない資料を変える", [("tests/data.json", "{}\n")], []),
        ("文書だけ変える", [("README.md", "b\n")], []),
    ]
    for label, edits, want in cases:
        with tempfile.TemporaryDirectory() as d:
            init_marks_repo(d)
            for path, body in edits:
                if body is None:
                    os.remove(os.path.join(d, path))
                else:
                    write(d, path, body)
            got = marks_of(run(d, "--difficulty", "sonnet"))
            check(f"{label}: 印は {want}", got == want, repr(got))


def main() -> int:
    test_marks_table()
    test_docs_only()
    test_untracked_code()
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

