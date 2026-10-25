#!/usr/bin/env python3
"""図の HTML をヘッドレスのブラウザで開き、図（svg#figure）だけを PNG に撮る。

使い方: python3 capture.py FIG.html FIG.png [--width 1000] [--scale 2]

Python の playwright と、その chromium が要る（無ければ案内を出して終了コード3）。
ネットワークは使わない。製品版の Chrome（channel）は起こさない。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


class CaptureUnavailable(Exception):
    pass


def capture(html: str, png: str, width: float = 1000, scale: float = 2.0) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise CaptureUnavailable(
            "Python の playwright が無い。`python3 -m pip install playwright` のあと"
            " `python3 -m playwright install chromium-headless-shell` を人が打つ"
        ) from e
    url = Path(os.path.abspath(html)).as_uri()
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            raise CaptureUnavailable(
                "chromium を起こせない。`python3 -m playwright install chromium-headless-shell` を人が打つ"
            ) from e
        try:
            ctx = browser.new_context(
                viewport={"width": int(width), "height": 900}, device_scale_factor=scale
            )
            page = ctx.new_page()
            page.goto(url)
            page.locator("svg#figure").screenshot(path=png)
        finally:
            browser.close()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("html")
    ap.add_argument("png")
    ap.add_argument("--width", type=float, default=1000)
    ap.add_argument("--scale", type=float, default=2.0)
    args = ap.parse_args(argv)
    try:
        capture(args.html, args.png, args.width, args.scale)
    except CaptureUnavailable as e:
        print(f"撮れない: {e}", file=sys.stderr)
        return 3
    print(f"撮った: {args.png}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
