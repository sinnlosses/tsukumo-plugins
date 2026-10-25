"""図の配色と書体。型は色の役（paper・ink・muted・accent）だけを知り、色そのものはここが決める。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

NAME_SIZE = 20
SUB_SIZE = 16
HEAD_SIZE = 14

SANS = "system-ui, 'Hiragino Sans', 'Noto Sans JP', 'Yu Gothic', sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, 'Hiragino Sans', monospace"


@dataclass(frozen=True)
class Theme:
    name: str
    colors: Dict[str, str]
    sans: str = SANS
    mono: str = MONO

    def color(self, role: str) -> str:
        return self.colors[role]


DARK = Theme(
    "dark",
    {"paper": "#2d3142", "ink": "#f5f5f5", "muted": "#bfc0c0", "accent": "#f08a59"},
)
LIGHT = Theme(
    "light",
    {"paper": "#f6f4ee", "ink": "#1c1c1c", "muted": "#5c5c5c", "accent": "#c8501f"},
)

THEMES: Dict[str, Theme] = {t.name: t for t in (DARK, LIGHT)}
