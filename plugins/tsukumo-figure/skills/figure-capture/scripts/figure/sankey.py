"""サンキー図（量の流れ）の型。

spec:
  type: "sankey"
  title: 図の題（alt テキストと、題つきで書き出すときの見出し）
  unit: 数に付ける単位（例 "件"）。省略できる
  columns: 列の見出し（省略できる。列の数と合わせる）
  note: 図の下に置く1行（省略できる）
  nodes: [{id, label?, sub?, column?, accent?}]   sub は数の代わりに出す文字。column は省くと流れから決める
  links: [{from, to, value, accent?}]            value は正の数。accent は図で1本だけ目立たせる流れ

決まりごと:
  - 流れは隣の列へだけつなぐ（列をまたぐ流れは中間の節点を足す）
  - 流入と流出の両方を持つ節点は、2つの合計が一致していること（太さが数を表すので、ずれたら嘘になる）
  - 帯と節点の太さは1つの尺度で決まり、4px 未満になる流れは図から外す（「その他」にまとめる）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .registry import FigureError, FigureType, register
from .scene import Path, Rect, Scene, Text, estimate_text_width
from .theme import HEAD_SIZE, NAME_SIZE, SUB_SIZE, Theme

BAR_W = 14
PLOT_H = 320
PLOT_W = 560
TOP = 88
BOTTOM = 36
MIN_THICK = 4
MAX_COLUMNS = 4
MAX_PER_COLUMN = 8
LABEL_PAD = 24
LABEL_SPACING = 48


@dataclass
class Node:
    id: str
    label: str
    sub: str
    column: int
    value: float
    accent: bool
    x: float = 0.0
    y: float = 0.0
    h: float = 0.0


@dataclass
class Link:
    src: str
    dst: str
    value: float
    accent: bool
    thick: float = 0.0
    y_src: float = 0.0
    y_dst: float = 0.0


@dataclass
class Layout:
    nodes: List[Node]
    links: List[Link]
    columns: List[List[Node]]
    width: float
    height: float
    k: float


def _fmt(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _parse(spec: dict) -> Tuple[List[Node], List[Link], List[str]]:
    errors: List[str] = []
    unit = spec.get("unit", "")
    raw_nodes = spec.get("nodes")
    raw_links = spec.get("links")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return [], [], ["nodes が空か、配列ではない"]
    if not isinstance(raw_links, list) or not raw_links:
        return [], [], ["links が空か、配列ではない"]

    ids: List[str] = []
    for i, n in enumerate(raw_nodes):
        nid = n.get("id") if isinstance(n, dict) else None
        if not isinstance(nid, str) or not nid:
            errors.append(f"nodes[{i}] に id（空でない文字列）が無い")
        elif nid in ids:
            errors.append(f"nodes の id `{nid}` が重なっている")
        else:
            ids.append(nid)

    links: List[Link] = []
    sum_in: Dict[str, float] = {i: 0.0 for i in ids}
    sum_out: Dict[str, float] = {i: 0.0 for i in ids}
    for i, ln in enumerate(raw_links):
        if not isinstance(ln, dict):
            errors.append(f"links[{i}] がオブジェクトではない")
            continue
        src, dst, val = ln.get("from"), ln.get("to"), ln.get("value")
        if src not in ids or dst not in ids:
            errors.append(f"links[{i}] の from・to が nodes に無い（{src!r} → {dst!r}）")
            continue
        if src == dst:
            errors.append(f"links[{i}] が同じ節点 `{src}` へ戻っている")
            continue
        if not _is_number(val) or val <= 0:
            errors.append(f"links[{i}]（{src} → {dst}）の value は正の数にする")
            continue
        links.append(Link(src, dst, float(val), bool(ln.get("accent", False))))
        sum_out[src] += float(val)
        sum_in[dst] += float(val)
    if errors:
        return [], [], errors

    for i in ids:
        if sum_in[i] and sum_out[i] and abs(sum_in[i] - sum_out[i]) > 1e-9:
            errors.append(
                f"節点 `{i}` の流入 {_fmt(sum_in[i])} と流出 {_fmt(sum_out[i])} が一致しない"
            )

    columns = _columns(ids, links, raw_nodes, errors)
    if errors:
        return [], [], errors

    nodes: List[Node] = []
    for raw in raw_nodes:
        nid = raw["id"]
        value = max(sum_in[nid], sum_out[nid])
        if value == 0:
            errors.append(f"節点 `{nid}` につながる流れが無い")
            continue
        sub = raw.get("sub")
        nodes.append(
            Node(
                nid,
                str(raw.get("label", nid)),
                sub if isinstance(sub, str) else f"{_fmt(value)}{unit}",
                columns[nid],
                value,
                bool(raw.get("accent", False)),
            )
        )
    return nodes, links, errors


def _columns(
    ids: List[str], links: List[Link], raw_nodes: List[dict], errors: List[str]
) -> Dict[str, int]:
    preds: Dict[str, List[str]] = {i: [] for i in ids}
    for ln in links:
        preds[ln.dst].append(ln.src)
    col: Dict[str, int] = {}
    pending = list(ids)
    while pending:
        progressed = False
        for nid in list(pending):
            if all(p in col for p in preds[nid]):
                col[nid] = 1 + max((col[p] for p in preds[nid]), default=-1)
                pending.remove(nid)
                progressed = True
        if not progressed:
            errors.append(f"流れが輪になっている（{'・'.join(pending)}）")
            return {}
    for raw in raw_nodes:
        c = raw.get("column")
        if c is not None:
            if not isinstance(c, int) or isinstance(c, bool) or c < 0:
                errors.append(f"節点 `{raw['id']}` の column は0以上の整数にする")
            else:
                col[raw["id"]] = c
    for ln in links:
        if col[ln.dst] - col[ln.src] != 1:
            errors.append(
                f"流れ `{ln.src}` → `{ln.dst}` が隣の列へつながっていない。中間の節点を足す"
            )
    used = sorted(set(col.values()))
    if used and used != list(range(len(used))):
        errors.append("column に空きの列がある")
    if len(used) > MAX_COLUMNS:
        errors.append(f"列が {len(used)} あり、{MAX_COLUMNS} を超える。図を2つに分ける")
    for c in used:
        n = sum(1 for v in col.values() if v == c)
        if n > MAX_PER_COLUMN:
            errors.append(f"列 {c + 1} の節点が {n} あり、{MAX_PER_COLUMN} を超える。「その他」にまとめる")
    if len(used) < 2:
        errors.append("列が1つしかない")
    return col


def _order(columns: List[List[Node]], links: List[Link]) -> None:
    """帯の交差を減らすために、列の中の並びを隣の列の重心で並べ替える（同じ重心は入力順）。"""

    def pos(col: List[Node]) -> Dict[str, float]:
        return {n.id: (i + 0.5) / len(col) for i, n in enumerate(col)}

    for _ in range(4):
        for c in range(1, len(columns)):
            prev = pos(columns[c - 1])
            columns[c] = _by_barycenter(columns[c], [(l.src, l.dst, l.value) for l in links], prev, True)
        for c in range(len(columns) - 2, -1, -1):
            nxt = pos(columns[c + 1])
            columns[c] = _by_barycenter(columns[c], [(l.src, l.dst, l.value) for l in links], nxt, False)


def _by_barycenter(
    col: List[Node],
    edges: List[Tuple[str, str, float]],
    other: Dict[str, float],
    use_incoming: bool,
) -> List[Node]:
    keyed = []
    for i, n in enumerate(col):
        num = den = 0.0
        for src, dst, v in edges:
            if use_incoming and dst == n.id and src in other:
                num += other[src] * v
                den += v
            if not use_incoming and src == n.id and dst in other:
                num += other[dst] * v
                den += v
        keyed.append((num / den if den else (i + 0.5) / len(col), i, n))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in keyed]


def compute_layout(spec: dict) -> Layout:
    nodes, links, errors = _parse(spec)
    if errors:
        raise FigureError("\n".join(errors))
    count = max(n.column for n in nodes) + 1
    columns: List[List[Node]] = [[n for n in nodes if n.column == c] for c in range(count)]
    _order(columns, links)

    gap = 56 if count >= 3 else 32
    k = min(
        (PLOT_H - gap * (len(col) - 1)) / sum(n.value for n in col) for col in columns
    )
    stacks = [sum(n.value for n in col) * k + gap * (len(col) - 1) for col in columns]
    tallest = max(stacks)
    for col, stack in zip(columns, stacks):
        y = TOP + (tallest - stack) / 2
        for n in col:
            n.y, n.h = y, n.value * k
            y += n.h + gap

    left = max(
        max(estimate_text_width(n.label, NAME_SIZE), estimate_text_width(n.sub, SUB_SIZE))
        for n in columns[0]
    ) + LABEL_PAD
    right = max(
        max(estimate_text_width(n.label, NAME_SIZE), estimate_text_width(n.sub, SUB_SIZE))
        for n in columns[-1]
    ) + LABEL_PAD
    plot_w = PLOT_W
    if count >= 3:
        mid = max(
            max(estimate_text_width(n.label, NAME_SIZE), estimate_text_width(n.sub, SUB_SIZE))
            for col in columns[1:-1]
            for n in col
        )
        plot_w = max(plot_w, (mid + 48) * (count - 1))
    for c, col in enumerate(columns):
        x = left + plot_w * c / (count - 1)
        for n in col:
            n.x = x

    by_id = {n.id: n for n in nodes}
    for l in links:
        l.thick = l.value * k
    for n in nodes:
        y = n.y
        for l in sorted((l for l in links if l.src == n.id), key=lambda l: by_id[l.dst].y):
            l.y_src = y
            y += l.thick
        y = n.y
        for l in sorted((l for l in links if l.dst == n.id), key=lambda l: by_id[l.src].y):
            l.y_dst = y
            y += l.thick

    height = tallest + TOP + BOTTOM + (28 if spec.get("note") else 0)
    return Layout(nodes, links, columns, left + plot_w + BAR_W + right, height, k)


def _spread(centers: List[float]) -> List[float]:
    """外側のラベルの縦の中心を、重ならないように下へ送る。"""
    out: List[float] = []
    for c in centers:
        out.append(c if not out else max(c, out[-1] + LABEL_SPACING))
    return out


@register
class Sankey(FigureType):
    name = "sankey"

    def check(self, spec: dict) -> List[str]:
        _, _, errors = _parse(spec)
        if errors:
            return errors
        lay = compute_layout(spec)
        thin = []
        for n in lay.nodes:
            if n.h < MIN_THICK:
                thin.append(f"節点 `{n.id}` が {n.h:.1f}px で薄すぎる。小さい流れは「その他」にまとめる")
        for l in lay.links:
            if l.thick < MIN_THICK:
                thin.append(f"流れ `{l.src}` → `{l.dst}` が {l.thick:.1f}px で薄すぎる。「その他」にまとめる")
        return thin

    def build(self, spec: dict, theme: Theme) -> Scene:
        lay = compute_layout(spec)
        by_id = {n.id: n for n in lay.nodes}
        title = str(spec.get("title", "サンキー図"))
        unit = spec.get("unit", "")
        flows = "、".join(
            f"{by_id[l.src].label}→{by_id[l.dst].label} {_fmt(l.value)}{unit}" for l in lay.links
        )
        scene = Scene(lay.width, lay.height, title, f"{title}。{flows}"[:400])

        heads = spec.get("columns")
        if isinstance(heads, list):
            for c, text in enumerate(heads[: len(lay.columns)]):
                cx = lay.columns[c][0].x + BAR_W / 2
                scene.items.append(Text(cx, 32, str(text), HEAD_SIZE, "middle", 500, "muted", mono=True))

        ribbons = sorted(lay.links, key=lambda l: l.accent)
        for l in ribbons:
            src, dst = by_id[l.src], by_id[l.dst]
            x0, x1 = src.x + BAR_W, dst.x
            mid = (x0 + x1) / 2
            t = l.thick
            d = (
                f"M{x0:.2f},{l.y_src:.2f} C{mid:.2f},{l.y_src:.2f} {mid:.2f},{l.y_dst:.2f} {x1:.2f},{l.y_dst:.2f}"
                f" L{x1:.2f},{l.y_dst + t:.2f} C{mid:.2f},{l.y_dst + t:.2f} {mid:.2f},{l.y_src + t:.2f} {x0:.2f},{l.y_src + t:.2f} Z"
            )
            bbox = (x0, min(l.y_src, l.y_dst), x1, max(l.y_src, l.y_dst) + t)
            fill, opacity = ("accent", 0.34) if l.accent else ("muted", 0.22)
            scene.items.append(Path(d, fill, opacity, bbox))

        for n in lay.nodes:
            scene.items.append(Rect(n.x, n.y, BAR_W, n.h, "accent" if n.accent else "ink"))

        last = len(lay.columns) - 1
        for c, col in enumerate(lay.columns):
            if c in (0, last):
                centers = _spread([n.y + n.h / 2 for n in col])
                for n, cy in zip(col, centers):
                    fill = "accent" if n.accent else "ink"
                    if c == 0:
                        x, anchor = n.x - 14, "end"
                    else:
                        x, anchor = n.x + BAR_W + 14, "start"
                    scene.items.append(Text(x, cy - 2, n.label, NAME_SIZE, anchor, 600, fill))
                    scene.items.append(Text(x, cy + 20, n.sub, SUB_SIZE, anchor, 400, "muted", mono=True))
                    lay.height = max(lay.height, cy + 20 + BOTTOM)
            else:
                for n in col:
                    cx = n.x + BAR_W / 2
                    fill = "accent" if n.accent else "ink"
                    scene.items.append(Text(cx, n.y - 28, n.label, NAME_SIZE, "middle", 600, fill, halo=True))
                    scene.items.append(Text(cx, n.y - 8, n.sub, SUB_SIZE, "middle", 400, "muted", mono=True, halo=True))

        note = spec.get("note")
        if note:
            scene.items.append(Text(24, lay.height - 14, str(note), SUB_SIZE, "start", 400, "muted"))
        scene.height = lay.height
        return scene
