"""図の部品（長方形・曲線・文字）の並びと、その SVG への書き出し。

型が作るのはこの並び（Scene）だけで、色は役の名前（paper・ink・muted・accent）で持つ。
役を色に引くのは theme、SVG の文字列にするのはこのモジュール。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union
from xml.sax.saxutils import escape, quoteattr

from .theme import Theme


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    fill: str
    opacity: float = 1.0


@dataclass
class Path:
    d: str
    fill: str
    opacity: float
    bbox: Tuple[float, float, float, float]


@dataclass
class Text:
    x: float
    y: float
    text: str
    size: float
    anchor: str = "start"
    weight: int = 400
    fill: str = "ink"
    mono: bool = False
    halo: bool = False


Item = Union[Rect, Path, Text]


@dataclass
class Scene:
    width: float
    height: float
    title: str
    desc: str
    items: List[Item] = field(default_factory=list)


def estimate_text_width(text: str, size: float) -> float:
    """字幅の見積もり。全角は 1em、半角は 0.6em（実測ではなく、はみ出しを避けるための安全側の値）。"""
    total = 0.0
    for ch in text:
        total += size if ord(ch) >= 0x2E80 else size * 0.6
    return total


def text_bounds(t: Text) -> Tuple[float, float, float, float]:
    w = estimate_text_width(t.text, t.size)
    if t.anchor == "end":
        x0 = t.x - w
    elif t.anchor == "middle":
        x0 = t.x - w / 2
    else:
        x0 = t.x
    return (x0, t.y - t.size, x0 + w, t.y + t.size * 0.25)


def _n(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s or "0"


def _item_svg(item: Item, theme: Theme) -> str:
    if isinstance(item, Rect):
        return (
            f'<rect x="{_n(item.x)}" y="{_n(item.y)}" width="{_n(item.w)}" height="{_n(item.h)}"'
            f' fill="{theme.color(item.fill)}"{_opacity(item.opacity)}/>'
        )
    if isinstance(item, Path):
        return f'<path d="{item.d}" fill="{theme.color(item.fill)}"{_opacity(item.opacity)}/>'
    family = theme.mono if item.mono else theme.sans
    halo = (
        f' stroke="{theme.color("paper")}" stroke-width="6" stroke-linejoin="round" paint-order="stroke"'
        if item.halo
        else ""
    )
    return (
        f'<text x="{_n(item.x)}" y="{_n(item.y)}" fill="{theme.color(item.fill)}"'
        f' font-size="{_n(item.size)}" font-weight="{item.weight}"'
        f" font-family={quoteattr(family)} text-anchor=\"{item.anchor}\"{halo}>{escape(item.text)}</text>"
    )


def _opacity(v: float) -> str:
    return "" if v >= 1.0 else f' fill-opacity="{_n(v)}"'


def to_svg(scene: Scene, theme: Theme, svg_id: Optional[str] = "figure") -> str:
    body = "\n  ".join(_item_svg(i, theme) for i in scene.items)
    return (
        f'<svg id="{svg_id}" viewBox="0 0 {_n(scene.width)} {_n(scene.height)}"'
        ' xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="figure-title figure-desc">\n'
        f'  <title id="figure-title">{escape(scene.title)}</title>\n'
        f'  <desc id="figure-desc">{escape(scene.desc)}</desc>\n'
        f'  <rect width="100%" height="100%" fill="{theme.color("paper")}"/>\n'
        f"  {body}\n</svg>"
    )
