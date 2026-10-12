#!/usr/bin/env python3
"""差分で足された行のうち、コメント行だけを拾い出す（当てるかどうかの判定はしない）。

使い方:
  python3 diff_added_comment_lines.py                  # 作業ツリーの差分（git diff そのまま）
  python3 diff_added_comment_lines.py main..HEAD        # 範囲を渡すと git diff の引数になる
  python3 diff_added_comment_lines.py --exclude .test.  # パスに ".test." を含むファイルを除く

出力は `path:新しい行番号<TAB>テキスト` を1行ずつ。拡張子は `comment_density.py` の
`LINE_MARKS` を使う。ブロックコメント（/* */）は開始行だけを拾い、複数行にまたがる本文の
続きの行は見ない（差分の追加行だけを見るため、ブロックの開始がどこにあるか分からない）。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from comment_density import C_LIKE, LINE_MARKS  # noqa: E402

HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("diff_args", nargs="*", help="git diff に渡す範囲などの引数")
    ap.add_argument("--exclude", action="append", default=[])
    args = ap.parse_args()

    out = subprocess.run(["git", "diff", *args.diff_args], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(out.stderr.strip() or "git diff が失敗した")

    for path, line_no, text in added_comment_lines(out.stdout):
        if any(x in path for x in args.exclude):
            continue
        print(f"{path}:{line_no}\t{text}")
    return 0


def added_comment_lines(diff_text: str) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    path = ""
    marks: tuple[str, ...] = ()
    c_like = False
    new_line = 0
    in_hunk = False

    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            in_hunk = False
            target = raw[4:]
            path = target[2:] if target.startswith("b/") else target
            ext = os.path.splitext(path)[1]
            marks = LINE_MARKS.get(ext, ())
            c_like = ext in C_LIKE
            continue
        m = HUNK_HEADER.match(raw)
        if m:
            new_line = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk or not marks:
            continue
        if raw.startswith("+"):
            text = raw[1:]
            if is_comment_line(text, marks, c_like):
                found.append((path, new_line, text.strip()))
            new_line += 1
        elif raw.startswith("-"):
            continue
        else:
            new_line += 1
    return found


def is_comment_line(text: str, marks: tuple[str, ...], c_like: bool) -> bool:
    s = text.strip()
    return bool(s) and (s.startswith(marks) or (c_like and s.startswith("/*")))


if __name__ == "__main__":
    sys.exit(main())
