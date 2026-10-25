"""Scene を1枚の HTML にする。スクリプトも外への読み込みも持たない（フォントは端末のものだけ）。"""

from __future__ import annotations

from typing import Optional
from xml.sax.saxutils import escape

from .scene import Scene, to_svg
from .theme import Theme


def render_page(
    scene: Scene,
    theme: Theme,
    heading: Optional[str] = None,
    eyebrow: Optional[str] = None,
) -> str:
    """heading と eyebrow があれば図の上に置く。無ければ図だけ（レポートに載せる画像は題を持たない）。"""
    head = ""
    if eyebrow:
        head += f'<p class="eyebrow">{escape(eyebrow)}</p>\n'
    if heading:
        head += f"<h1>{escape(heading)}</h1>\n"
    pad = "2rem" if head else "0"
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{escape(scene.title)}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --paper: {theme.color("paper")}; --ink: {theme.color("ink")};
    --muted: {theme.color("muted")}; --accent: {theme.color("accent")};
  }}
  body {{ font-family: {theme.sans}; background: var(--paper); color: var(--ink); padding: {pad}; }}
  .eyebrow {{ font-family: {theme.mono}; font-size: 0.8rem; letter-spacing: 0.18em; color: var(--muted); margin-bottom: 0.5rem; }}
  h1 {{ font-size: 1.75rem; font-weight: 500; line-height: 1.25; margin-bottom: 1rem; }}
  svg {{ width: 100%; display: block; }}
</style>
</head>
<body>
{head}{to_svg(scene, theme)}
</body>
</html>
"""
