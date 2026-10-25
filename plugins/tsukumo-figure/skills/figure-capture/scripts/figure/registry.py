"""図の型の登録簿。型を足すときは FigureType を継いで @register を付け、`figure/__init__.py` で import する。"""

from __future__ import annotations

from typing import Dict, List, Type

from .scene import Scene
from .theme import Theme


class FigureError(Exception):
    """spec が不正なとき。メッセージは利用者に見せる文（1行1件）。"""


class FigureType:
    """型が持つのは検査と配置だけ。色は theme の役、HTML への包みは page が持つ。"""

    name = ""

    def check(self, spec: dict) -> List[str]:
        """spec の誤りを1件1行で返す。空なら通る。"""
        raise NotImplementedError

    def build(self, spec: dict, theme: Theme) -> Scene:
        """check を通った spec から Scene を作る。"""
        raise NotImplementedError


TYPES: Dict[str, FigureType] = {}


def register(cls: Type[FigureType]) -> Type[FigureType]:
    if not cls.name:
        raise ValueError("型の name が空")
    TYPES[cls.name] = cls()
    return cls


def get(name: str) -> FigureType:
    if name not in TYPES:
        known = "・".join(sorted(TYPES)) or "なし"
        raise FigureError(f"未対応の型 `{name}`（対応: {known}）")
    return TYPES[name]
