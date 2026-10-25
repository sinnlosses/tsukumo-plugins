"""spec（図の構造）から図の HTML を作る。型は registry に登録し、配色と HTML の包みは共通。"""

from __future__ import annotations

from typing import Optional, Tuple

from . import sankey  # noqa: F401  型の登録のために import する（型を足したらここへ足す）
from .page import render_page
from .registry import TYPES, FigureError, FigureType, get, register
from .scene import Scene
from .theme import THEMES

__all__ = ["FigureError", "FigureType", "TYPES", "register", "render"]


def render(
    spec: dict,
    theme: str = "dark",
    heading: bool = False,
) -> Tuple[str, Scene]:
    """spec を検査して HTML と Scene を返す。不正なら FigureError（メッセージは1行1件）。"""
    if not isinstance(spec, dict):
        raise FigureError("spec がオブジェクトではない")
    if theme not in THEMES:
        raise FigureError(f"未対応の theme `{theme}`（対応: {'・'.join(sorted(THEMES))}）")
    fig = get(str(spec.get("type", "")))
    errors = fig.check(spec)
    if errors:
        raise FigureError("\n".join(errors))
    th = THEMES[theme]
    scene = fig.build(spec, th)
    eyebrow: Optional[str] = spec.get("eyebrow") if heading else None
    return render_page(scene, th, scene.title if heading else None, eyebrow), scene
