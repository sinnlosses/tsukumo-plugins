#!/usr/bin/env python3
"""`figure_render.py` と `figure/` の自己テスト。

使い方: python3 selftest_figure.py

標準ライブラリだけで動く。撮る段（playwright と chromium）は、無ければ飛ばす。落ちたら非0で終わる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from figure import TYPES, FigureError, FigureType, register, render  # noqa: E402
from figure.sankey import compute_layout  # noqa: E402
from figure.scene import Path, Rect, Scene, Text, text_bounds, to_svg  # noqa: E402
from figure.theme import DARK  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


TWO = {
    "type": "sankey",
    "title": "受け付けた report の内訳",
    "unit": "件",
    "columns": ["受付", "figure 欄"],
    "nodes": [
        {"id": "all", "label": "受け付けた report"},
        {"id": "none", "label": "none（図なし）", "sub": "540件 · 85%"},
        {"id": "fig", "label": "図あり", "accent": True},
    ],
    "links": [
        {"from": "all", "to": "none", "value": 540},
        {"from": "all", "to": "fig", "value": 98, "accent": True},
    ],
}

THREE = {
    "type": "sankey",
    "title": "CI の予算",
    "unit": "分",
    "nodes": [
        {"id": "ci", "label": "CI"},
        {"id": "unit", "label": "単体テスト"},
        {"id": "e2e", "label": "E2E"},
        {"id": "ok", "label": "通った"},
        {"id": "ng", "label": "落ちた"},
    ],
    "links": [
        {"from": "ci", "to": "unit", "value": 5200},
        {"from": "ci", "to": "e2e", "value": 4000},
        {"from": "unit", "to": "ok", "value": 4600},
        {"from": "unit", "to": "ng", "value": 600},
        {"from": "e2e", "to": "ok", "value": 3000},
        {"from": "e2e", "to": "ng", "value": 1000},
    ],
}


def scene_of(spec: dict) -> Scene:
    return render(spec)[1]


def test_renders_and_is_safe() -> None:
    print("図の HTML は読め、スクリプトも外への参照も持たない")
    for name, spec in (("2列", TWO), ("3列", THREE)):
        html, scene = render(spec)
        svg = to_svg(scene, DARK)
        try:
            ET.fromstring(svg)
            parsed = True
        except ET.ParseError as e:
            parsed = False
            print(f"    {e}")
        check(f"{name}: SVG が XML として読める", parsed)
        low = html.lower()
        check(f"{name}: script が無い", "<script" not in low)
        check(f"{name}: 外への参照が無い", "@import" not in low and "url(" not in low and 'href="http' not in low and "src=" not in low)
        check(f"{name}: 題が alt に入る", spec["title"] in svg)


def test_ribbons_fill_bars() -> None:
    print("帯の太さの合計が、節点の太さと一致する")
    for name, spec in (("2列", TWO), ("3列", THREE)):
        lay = compute_layout(spec)
        for n in lay.nodes:
            outs = sum(l.thick for l in lay.links if l.src == n.id)
            ins = sum(l.thick for l in lay.links if l.dst == n.id)
            ok = all(abs(t - n.h) < 1e-6 for t in (outs, ins) if t)
            check(f"{name}: `{n.id}`", ok, f"h={n.h:.3f} out={outs:.3f} in={ins:.3f}")


def test_inside_viewbox() -> None:
    print("すべての部品が viewBox の中に収まる")
    for name, spec in (("2列", TWO), ("3列", THREE)):
        scene = scene_of(spec)
        out = []
        for it in scene.items:
            if isinstance(it, Rect):
                box = (it.x, it.y, it.x + it.w, it.y + it.h)
            elif isinstance(it, Path):
                box = it.bbox
            else:
                box = text_bounds(it)
            if box[0] < -0.5 or box[1] < -0.5 or box[2] > scene.width + 0.5 or box[3] > scene.height + 0.5:
                out.append((type(it).__name__, [round(v) for v in box]))
        check(f"{name}: はみ出しなし", not out, f"{out[:3]} / {scene.width:.0f}x{scene.height:.0f}")


def test_min_font_size() -> None:
    print("字は 14px 以上（縮んでも読める下限）")
    for name, spec in (("2列", TWO), ("3列", THREE)):
        small = [it.size for it in scene_of(spec).items if isinstance(it, Text) and it.size < 14]
        check(f"{name}: 小さい字なし", not small, str(small))


def test_barycenter_order() -> None:
    print("帯が交差しないように列の中を並べ替える")
    spec = {
        "type": "sankey",
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": "y"}, {"id": "x"}],
        "links": [{"from": "a", "to": "x", "value": 10}, {"from": "b", "to": "y", "value": 10}],
    }
    lay = compute_layout(spec)
    by = {n.id: n for n in lay.nodes}
    check("x が y より上（入力では y が先）", by["x"].y < by["y"].y)


def test_validation() -> None:
    print("不正な spec は理由つきで断る")
    base = json.loads(json.dumps(TWO))

    def errs(mut) -> str:
        spec = json.loads(json.dumps(base))
        mut(spec)
        try:
            render(spec)
        except FigureError as e:
            return str(e)
        return ""

    check("未知の節点", "nodes に無い" in errs(lambda s: s["links"][0].update({"to": "zzz"})))
    check("正でない値", "正の数" in errs(lambda s: s["links"][0].update({"value": 0})))
    check("流入と流出の不一致", "一致しない" in errs(lambda s: s["links"].append({"from": "none", "to": "fig", "value": 1})))
    check("輪", "輪" in errs(lambda s: s["links"].extend([{"from": "none", "to": "all", "value": 540}])))
    check("薄すぎる流れ", "薄すぎる" in errs(lambda s: s["links"][1].update({"value": 1})))
    check("未知の型", "未対応の型" in errs(lambda s: s.update({"type": "nope"})))
    skip = {
        "type": "sankey",
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        "links": [
            {"from": "a", "to": "b", "value": 100},
            {"from": "b", "to": "c", "value": 60},
            {"from": "a", "to": "c", "value": 40},
        ],
    }
    try:
        render(skip)
        found = ""
    except FigureError as e:
        found = str(e)
    check("列をまたぐ流れ", "隣の列" in found, found)


def test_registry_is_open() -> None:
    print("型を足すと、検査・配置・書き出しがそのまま使える")

    @register
    class Dot(FigureType):
        name = "dot-test"

        def check(self, spec: dict) -> list[str]:
            return [] if "label" in spec else ["label が無い"]

        def build(self, spec: dict, theme) -> Scene:
            s = Scene(200, 100, "点", "点が1つ")
            s.items.append(Rect(90, 40, 20, 20, "accent"))
            s.items.append(Text(100, 90, spec["label"], 16, "middle"))
            return s

    try:
        html, _ = render({"type": "dot-test", "label": "A"})
        check("追加した型が描ける", "<svg" in html and ">A<" in html)
        try:
            render({"type": "dot-test"})
            check("追加した型の検査が効く", False)
        except FigureError:
            check("追加した型の検査が効く", True)
    finally:
        TYPES.pop("dot-test", None)


def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "figure_render.py"), *args],
        capture_output=True,
        text=True,
        input=stdin,
    )


def test_cli() -> None:
    print("コマンドの口")
    with tempfile.TemporaryDirectory() as d:
        spec = os.path.join(d, "spec.json")
        out = os.path.join(d, "fig.html")
        with open(spec, "w", encoding="utf-8") as f:
            json.dump(TWO, f, ensure_ascii=False)
        r = run(spec, "--out", out)
        check("成功で 0・HTML ができる", r.returncode == 0 and os.path.getsize(out) > 500, r.stderr)
        r = run("-", "--out", out, "--theme", "light", stdin=json.dumps(THREE, ensure_ascii=False))
        check("標準入力と light が通る", r.returncode == 0, r.stderr)
        r = run("-", "--out", out, stdin="{}")
        check("不正な spec は 2", r.returncode == 2 and "未対応の型" in r.stderr, r.stderr)
        r = run("-", "--out", out, stdin="これは JSON ではない")
        check("読めない spec は 2", r.returncode == 2, r.stderr)
        r = run("--types")
        check("--types が型を出す", r.returncode == 0 and "sankey" in r.stdout.split(), r.stdout)


def test_capture() -> None:
    print("撮る（playwright と chromium があるときだけ）")
    from capture import CaptureUnavailable, capture

    with tempfile.TemporaryDirectory() as d:
        html, scene = render(TWO)
        path, png = os.path.join(d, "f.html"), os.path.join(d, "f.png")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            capture(path, png, scene.width, 1.0)
        except CaptureUnavailable as e:
            print(f"  skip 撮れない環境: {e}")
            return
        with open(png, "rb") as f:
            head = f.read(8)
        check("PNG ができる", head == b"\x89PNG\r\n\x1a\n")


def main() -> int:
    test_renders_and_is_safe()
    test_ribbons_fill_bars()
    test_inside_viewbox()
    test_min_font_size()
    test_barycenter_order()
    test_validation()
    test_registry_is_open()
    test_cli()
    test_capture()
    if failures:
        print(f"\n{len(failures)} 件失敗")
        return 1
    print("\nすべて ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
