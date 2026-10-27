#!/usr/bin/env python3
"""受け入れで新しい文脈のレビューを掛けるかを、difficulty と差分のパスから決める。

使い方:
  python3 review_needed.py --difficulty opus             # 作業ツリーの変更（未追跡の新しいファイルも含む）
  python3 review_needed.py --difficulty sonnet main..gh-1 # 範囲を渡すと git diff の引数になる

出力は1行:
  REVIEW<TAB>opus                 difficulty が opus（差分によらない）
  REVIEW<TAB>code<TAB><数>        文書の拡張子でないパス、または設定 review_code_globs に当たるパスがある（数はそのパスの数）
  SKIP<TAB>docs-only              変わったパスが全部文書
  NOTHING                         範囲つきで差分が空
  NO_RANGE                        範囲なしで作業ツリーの変更が空（作業先が別のリポジトリのタスクなら範囲の渡し忘れ）

判定の行のあとに、テストの扱いが怪しい差分の印を1種類1行で足す（止めない。受け入れ役の依頼文へ渡す）:
  MARK<TAB>test-deleted<TAB><パス,…>   テストのファイルを消した、またはテストの定義が差し引きで減った
  MARK<TAB>test-skipped<TAB><パス,…>   テストのファイルに skip の記述を足した
  MARK<TAB>expected-only<TAB><パス,…>  変わったパスが全部 expected ディレクトリ（テストの置き場の下）の期待値
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import gitcmd
import layout

DOC_EXTENSIONS = frozenset({".md", ".mdx", ".markdown", ".txt", ".rst", ".adoc"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--difficulty", required=True)
    ap.add_argument("diff_args", nargs="*", help="git diff に渡す範囲などの引数")
    args = ap.parse_args()

    paths = changed_paths(args.diff_args)
    print(decide(args.difficulty, paths, bool(args.diff_args), code_globs()))
    if paths:
        patch = git_lines("diff", "-U0", *(args.diff_args or ["HEAD"]))
        for kind, found in marks(paths, patch):
            print(f"MARK\t{kind}\t{','.join(found[:MARK_PATHS])}{',…' if len(found) > MARK_PATHS else ''}")
    return 0


def code_globs() -> tuple[str, ...]:
    """cwd のリポジトリの設定 `review_code_globs`。設定が無ければ空。"""
    try:
        return layout.read_config(gitcmd.out(["rev-parse", "--show-toplevel"])).review_code_globs
    except (gitcmd.GitCommandError, layout.ConfigError) as e:
        sys.exit(str(e))


def glob_regex(glob: str) -> re.Pattern[str]:
    """`**` は `/` を越え、`*`・`?` は越えない。`**/` は0個以上のディレクトリ。"""
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out))


def is_code(path: str, globs: tuple[str, ...]) -> bool:
    if os.path.splitext(path)[1].lower() not in DOC_EXTENSIONS:
        return True
    return any(glob_regex(g).fullmatch(path) for g in globs)


def decide(difficulty: str, paths: list[str], has_range: bool, globs: tuple[str, ...] = ()) -> str:
    if not paths:
        return "NOTHING" if has_range else "NO_RANGE"
    if difficulty == "opus":
        return "REVIEW\topus"
    code = [p for p in paths if is_code(p, globs)]
    if code:
        return f"REVIEW\tcode\t{len(code)}"
    return "SKIP\tdocs-only"


MARK_PATHS = 5
TEST_DIRS = frozenset({"test", "tests", "__tests__", "e2e"})
TEST_NAME = re.compile(r"(?:test_.*\.py|.*_test\.py|selftest_.*\.py|.*\.(?:test|spec)\.[jt]sx?)")
TEST_DEFINITION = re.compile(
    r"\s*(?:(?:async\s+)?def (?:test|selftest)_|(?:it|test)(?:\.(?:skip|fixme|only))?\(|xit\()"
)
SKIP_NOTATION = re.compile(
    r"pytest\.mark\.skip|pytest\.skip\(|unittest\.skip|skipTest\(|\b(?:it|test|describe)\.skip\(|\bx(?:it|describe)\(|test\.fixme\("
)


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    return bool(TEST_DIRS & set(parts[:-1])) or TEST_NAME.fullmatch(parts[-1]) is not None


def is_expected_path(path: str) -> bool:
    return is_test_path(path) and "expected" in path.split("/")[:-1]


def marks(paths: list[str], patch: list[str]) -> list[tuple[str, list[str]]]:
    """差分から `(印の種類, パス)` を、test-deleted・test-skipped・expected-only の順に返す。`patch` は `git diff -U0` の行。"""
    deleted: dict[str, bool] = {}
    removed: dict[str, int] = {}
    added: dict[str, int] = {}
    skipped: dict[str, bool] = {}
    old = current = ""
    for line in patch:
        if line.startswith("--- "):
            old = line[4:].removeprefix("a/")
        elif line.startswith("+++ "):
            current = line[4:].removeprefix("b/")
            if current == "/dev/null":
                current = old
                deleted[current] = True
        elif current and is_test_path(current):
            body = line[1:]
            if line.startswith("-") and TEST_DEFINITION.match(body):
                removed[current] = removed.get(current, 0) + 1
            elif line.startswith("+"):
                if TEST_DEFINITION.match(body):
                    added[current] = added.get(current, 0) + 1
                if SKIP_NOTATION.search(body):
                    skipped[current] = True
    found: list[tuple[str, list[str]]] = []
    lost = sorted(p for p in deleted if is_test_path(p))
    lost += sorted(p for p in removed if p not in deleted and removed[p] > added.get(p, 0))
    if lost:
        found.append(("test-deleted", sorted(lost)))
    if skipped:
        found.append(("test-skipped", sorted(skipped)))
    if paths and all(is_expected_path(p) for p in paths):
        found.append(("expected-only", paths))
    return found


def changed_paths(diff_args: list[str]) -> list[str]:
    if diff_args:
        return sorted(set(git_lines("diff", "--name-only", *diff_args)))
    tracked = git_lines("diff", "--name-only", "HEAD")
    untracked = git_lines("ls-files", "--others", "--exclude-standard")
    return sorted(set(tracked) | set(untracked))


def git_lines(*args: str) -> list[str]:
    out = gitcmd.run(list(args))
    if out.returncode != 0:
        sys.exit(out.stderr.strip() or f"git {args[0]} が失敗した")
    return [line for line in out.stdout.splitlines() if line]


if __name__ == "__main__":
    sys.exit(main())
