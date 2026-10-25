#!/usr/bin/env python3
"""図の構造（spec の JSON）から、図の HTML を作る。--png を付けると撮って PNG にもする。

使い方:
  python3 figure_render.py SPEC.json --out FIG.html [--png FIG.png] [--theme dark|light] [--heading]
  python3 figure_render.py --types            # 対応する型の一覧
SPEC.json に `-` を渡すと標準入力から読む。

終了コード: 0 成功 / 2 spec の誤り（理由を1行ずつ標準エラーへ） / 3 撮れない（--png のとき）
標準ライブラリだけで動く（--png だけ playwright が要る）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from figure import TYPES, FigureError, render  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", nargs="?", help="spec の JSON のパス（- で標準入力）")
    ap.add_argument("--out", help="書き出す HTML のパス")
    ap.add_argument("--png", help="撮った PNG のパス（図の部分だけ）")
    ap.add_argument("--theme", default="dark")
    ap.add_argument("--heading", action="store_true", help="図の上に題を置く（レポートに載せる画像には付けない）")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--types", action="store_true")
    args = ap.parse_args(argv)

    if args.types:
        print("\n".join(sorted(TYPES)))
        return 0
    if not args.spec or not args.out:
        ap.error("spec と --out が要る")

    try:
        text = sys.stdin.read() if args.spec == "-" else open(args.spec, encoding="utf-8").read()
        spec = json.loads(text)
    except (OSError, ValueError) as e:
        print(f"spec を読めない: {e}", file=sys.stderr)
        return 2
    try:
        html, scene = render(spec, args.theme, args.heading)
    except FigureError as e:
        print(str(e), file=sys.stderr)
        return 2

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"書いた: {args.out}（{scene.width:.0f}x{scene.height:.0f}）")

    if args.png:
        from capture import CaptureUnavailable, capture

        try:
            capture(args.out, args.png, scene.width, args.scale)
        except CaptureUnavailable as e:
            print(f"撮れない: {e}", file=sys.stderr)
            return 3
        print(f"撮った: {args.png}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
