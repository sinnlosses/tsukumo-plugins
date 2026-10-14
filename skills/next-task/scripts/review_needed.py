#!/usr/bin/env python3
"""受け入れで新しい文脈のレビューを掛けるかを、difficulty と差分のパスから決める。

使い方:
  python3 review_needed.py --difficulty opus             # 作業ツリーの変更（未追跡の新しいファイルも含む）
  python3 review_needed.py --difficulty sonnet main..gh-1 # 範囲を渡すと git diff の引数になる

出力は1行:
  REVIEW<TAB>opus                 difficulty が opus（差分によらない）
  REVIEW<TAB>code<TAB><数>        文書の拡張子でないパスがある（数はそのパスの数）
  SKIP<TAB>docs-only              変わったパスが全部文書
  NOTHING                         範囲つきで差分が空
  NO_RANGE                        範囲なしで作業ツリーの変更が空（作業先が別のリポジトリのタスクなら範囲の渡し忘れ）
"""

from __future__ import annotations

import argparse
import os
import sys

_TASK_WORKFLOW_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "task-model", "scripts")
)
if _TASK_WORKFLOW_SCRIPTS not in sys.path:
    sys.path.insert(0, _TASK_WORKFLOW_SCRIPTS)

import gitcmd  # noqa: E402

DOC_EXTENSIONS = frozenset({".md", ".mdx", ".markdown", ".txt", ".rst", ".adoc"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--difficulty", required=True)
    ap.add_argument("diff_args", nargs="*", help="git diff に渡す範囲などの引数")
    args = ap.parse_args()

    print(decide(args.difficulty, changed_paths(args.diff_args), bool(args.diff_args)))
    return 0


def decide(difficulty: str, paths: list[str], has_range: bool) -> str:
    if not paths:
        return "NOTHING" if has_range else "NO_RANGE"
    if difficulty == "opus":
        return "REVIEW\topus"
    code = [p for p in paths if os.path.splitext(p)[1].lower() not in DOC_EXTENSIONS]
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
