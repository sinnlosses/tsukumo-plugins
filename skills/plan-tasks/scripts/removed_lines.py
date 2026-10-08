#!/usr/bin/env python3
"""差分で消された行を、残る先を埋める表の下書きにして出す。

使い方:
  python3 removed_lines.py                  # 作業ツリー（git add 済み・未追跡のファイルを含む）の HEAD からの差分
  python3 removed_lines.py main..HEAD        # 範囲を渡すと git diff の引数になる（未追跡のファイルは見ない）

空行は出さない。前後の空白を除いて同じ中身の行が差分のどこかで足されていれば、その数だけ
消した行と相殺して出さない（同じファイルの別の場所へも、別のファイルへも動かした扱い）。
文字（英数字・かな・漢字）を含まない記号だけの行（`}`・`---`・`` ``` `` など）は無関係な場所の
同じ行と区別が付かないので、相殺に使わず必ず出す。
出力はファイルごとの見出しと、`消した行 | 残る先` の表。残る先は空欄で出す。
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter

FILE_HEADER = "diff --git "


def main() -> int:
    ranged = bool(sys.argv[1:])
    diff_args = sys.argv[1:] or ["HEAD"]
    out = git("diff", "--no-color", "-U0", *diff_args)
    added: Counter[str] = Counter()
    if not ranged:
        for path in git("ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if path:
                added.update(untracked_lines(path))
    print(render(unmatched_removals(out, added)), end="")
    return 0


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args], capture_output=True, text=True, errors="replace"
    )
    if out.returncode != 0:
        sys.exit(out.stderr.strip() or f"git {args[0]} が失敗した")
    return out.stdout


def untracked_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [line.strip() for line in f]
    except OSError:
        return []


def unmatched_removals(
    diff_text: str, extra_added: Counter[str]
) -> list[tuple[str, list[str]]]:
    removed: list[tuple[str, str]] = []
    added: Counter[str] = Counter(extra_added)
    path = ""
    in_hunk = False

    for raw in diff_text.splitlines():
        if raw.startswith(FILE_HEADER):
            in_hunk = False
            path = raw.rsplit(" b/", 1)[-1]
        elif raw.startswith("@@"):
            in_hunk = True
        elif not in_hunk:
            continue
        elif raw.startswith("-"):
            removed.append((path, raw[1:]))
        elif raw.startswith("+"):
            added[raw[1:].strip()] += 1

    by_path: dict[str, list[str]] = {}
    for file_path, text in removed:
        key = text.strip()
        if not key:
            continue
        if has_letter(key) and added[key] > 0:
            added[key] -= 1
            continue
        by_path.setdefault(file_path, []).append(text)
    return list(by_path.items())


def has_letter(text: str) -> bool:
    return any(c.isalnum() for c in text)


def render(files: list[tuple[str, list[str]]]) -> str:
    lines: list[str] = []
    for path, texts in files:
        lines += [f"### {path}", "", "| 消した行 | 残る先 |", "| --- | --- |"]
        lines += [f"| {escape_cell(t.strip())} |  |" for t in texts]
        lines.append("")
    return "\n".join(lines)


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|")


if __name__ == "__main__":
    sys.exit(main())
