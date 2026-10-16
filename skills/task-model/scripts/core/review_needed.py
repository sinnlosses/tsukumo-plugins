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

    print(decide(args.difficulty, changed_paths(args.diff_args), bool(args.diff_args), code_globs()))
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
