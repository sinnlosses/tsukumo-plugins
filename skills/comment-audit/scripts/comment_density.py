#!/usr/bin/env python3
"""コメントの量を測り、監査の順番を決める材料を出す（どれを消すかは決めない）。

使い方:
  python3 comment_density.py <パス>...            # git ls-files で拾ったファイルを集計
  python3 comment_density.py src --top 20         # コメントの割合が高いファイルを20件
  python3 comment_density.py src --exclude test   # パスに "test" を含むファイルを除く

数えるのは空行を除いた行。コメントのブロックは「続けて並んだコメント行」のまとまり。
行頭のコメントだけを数え、コードの行末に付いたコメントは数えない（量の見積もりには足りる）。
文字列リテラルの中の `//` や `*` をコメントと見誤ることがあるので、数は目安として読む。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

# 拡張子ごとの行コメントの印。ブロックコメント（/* */）は C 系の言語だけ見る。
LINE_MARKS = {
    ".ts": ("//",), ".tsx": ("//",), ".js": ("//",), ".jsx": ("//",), ".mjs": ("//",),
    ".java": ("//",), ".kt": ("//",), ".scala": ("//",), ".go": ("//",), ".rs": ("//",),
    ".swift": ("//",), ".c": ("//",), ".h": ("//",), ".cpp": ("//",), ".cs": ("//",),
    ".py": ("#",), ".rb": ("#",), ".sh": ("#",),
}
C_LIKE = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".java", ".kt", ".scala", ".go", ".rs",
          ".swift", ".c", ".h", ".cpp", ".cs"}
BUCKETS = ((1, 1), (2, 4), (5, 9), (10, 10**9))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--min-lines", type=int, default=40, help="上位表に載せるファイルの最小行数")
    ap.add_argument("--exclude", action="append", default=[])
    args = ap.parse_args()

    files = [f for f in tracked_files(args.paths)
             if os.path.splitext(f)[1] in LINE_MARKS and not any(x in f for x in args.exclude)]
    if not files:
        print("対象のファイルが無い", file=sys.stderr)
        return 1

    total_code = total_comment = 0
    sizes: list[int] = []
    rows: list[tuple[float, int, int, str]] = []
    for f in files:
        code, comment, blocks = measure(f)
        total_code += code
        total_comment += comment
        sizes.extend(blocks)
        if code + comment >= args.min_lines:
            rows.append((comment / (code + comment), comment, code, f))

    lines = total_code + total_comment
    print(f"ファイル {len(files)} / 空行を除いた行 {lines} / コメント行 {total_comment}"
          f"（{pct(total_comment, lines)}） / ブロック {len(sizes)}")
    print()
    print("| ブロックの長さ | ブロック数 | コメント行に占める割合 |")
    print("| --- | ---: | ---: |")
    for lo, hi in BUCKETS:
        hit = [s for s in sizes if lo <= s <= hi]
        label = f"{lo}行" if lo == hi else (f"{lo}行以上" if hi >= 10**9 else f"{lo}〜{hi}行")
        print(f"| {label} | {len(hit)}（{pct(len(hit), len(sizes))}） | {pct(sum(hit), total_comment)} |")
    print()
    print(f"| コメントの割合 | コメント行 | コード行 | ファイル |")
    print("| ---: | ---: | ---: | --- |")
    for ratio, comment, code, f in sorted(rows, reverse=True)[: args.top]:
        print(f"| {ratio:.0%} | {comment} | {code} | `{f}` |")
    return 0


def tracked_files(paths: list[str]) -> list[str]:
    out = subprocess.run(["git", "ls-files", "--", *paths], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(out.stderr.strip() or "git ls-files が失敗した")
    return [line for line in out.stdout.splitlines() if line]


def measure(path: str) -> tuple[int, int, list[int]]:
    ext = os.path.splitext(path)[1]
    marks = LINE_MARKS[ext]
    c_like = ext in C_LIKE
    code = comment = 0
    blocks: list[int] = []
    run = 0
    in_block = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            s = raw.strip()
            if not s:
                continue
            is_comment = in_block or s.startswith(marks) or (c_like and s.startswith("/*"))
            if c_like and (in_block or s.startswith("/*")):
                in_block = "*/" not in s
            if is_comment:
                comment += 1
                run += 1
                continue
            code += 1
            if run:
                blocks.append(run)
                run = 0
    if run:
        blocks.append(run)
    return code, comment, blocks


def pct(n: int, d: int) -> str:
    return f"{n * 100 // d}%" if d else "0%"


if __name__ == "__main__":
    sys.exit(main())
