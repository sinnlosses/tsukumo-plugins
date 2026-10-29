---
name: figure-capture
description: 図の構造（節点と流れ）を JSON で書くと、配色と配置を決めて HTML にし、図の部分だけを PNG に撮る。ブロック図・時間・棒グラフのような基本形ではなく、専用の描き方が要る型（いまはサンキー＝量の流れ）を、座標を手で書かずに載せたいときに使う。ユーザーが「サンキー図にして」「量の流れを図にして」「レポートに図を載せたい」「この内訳を図で見せて」と言ったとき、文や表では量の割合・流れが読みにくいときは、「サンキー」という言葉が無くても使う。縦に長い図・箇所が10を超える図・触って読む成果物（タブ・畳み・ズーム）には使わない。
---

# 図の構造から PNG を作る

**構造だけを書き、配色・配置・HTML は決まった規則に任せる。** 書き手が座標や色を書くと、回ごとに崩れ方が違う
（字が縮む・下端が切れる・暗い面から浮く）。このスキルは構造（spec）を検査して SVG の HTML を作り、
ヘッドレスのブラウザで図の部分だけを撮る。HTML はスクリプトも外への読み込みも持たない。

## いつ使うか

| 載せたいもの | 手段 |
| --- | --- |
| 量が割れて流れる内訳（件数・時間・費用の配分） | このスキルのサンキー |
| 構造・流れ・時間・数の推移の基本形 | tsukumo のレポートの塊（`graph`・`mermaid`・`timeline`・`chart`）が先。足りないときだけここへ |
| 縦に長い図・節点が1列に8個を超える図 | 使わず、文と表に分ける（載せたときの高さの上限で縮み、字が読めなくなる） |
| タブ・畳み・ズームなど触って読むもの | 対象外（スクリプトを持たないので動かない） |

## 手順

1. spec を JSON で書く（置き場はリポジトリの外の `/tmp` など。会話由来の数を含むなら外へ複製しない）
2. 作って撮る:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/figure_render.py /tmp/figure/spec.json --out /tmp/figure/fig.html --png /tmp/figure/fig.png
   ```

   終了コード 2 は spec の誤りで、理由が1行ずつ出る。直して打ち直す。3 は撮れない環境（`--png` を外せば HTML だけできる）。
   撮るには Python の `playwright` と chromium が要り、入れるのは人がやる（グローバルな導入にあたる）。
3. **撮った PNG を開いて読む。** 字が読めるか・下端が切れていないか・帯が絡んでいないかを見てから載せる
4. tsukumo の `report` ツールがあれば、`image` の塊で載せる（`caption` に題を書く。画像には題を入れない）。無ければパスを渡す

題を画像に入れたい（単体で見せる）ときだけ `--heading` を付ける。明るい面は `--theme light`。

## spec（サンキー）

```json
{
  "type": "sankey",
  "title": "figure に図を置いた report は 98件で、638件の 15%",
  "unit": "件",
  "columns": ["受付", "figure 欄"],
  "nodes": [
    {"id": "all", "label": "受け付けた report"},
    {"id": "none", "label": "none（図なし）", "sub": "540件 · 85%"},
    {"id": "fig", "label": "図あり", "accent": true}
  ],
  "links": [
    {"from": "all", "to": "none", "value": 540},
    {"from": "all", "to": "fig", "value": 98, "accent": true}
  ]
}
```

- 流れは隣の列へだけつなぐ。列は流れから決まる（`column` で上書きもできる）。3列までを目安にし、4列が上限
- 流入と流出の両方を持つ節点は、2つの合計を一致させる（太さが数を表すので、ずれると嘘になる）
- 4px に満たない流れは断られる。小さいものは「その他」にまとめる
- `accent` は目立たせたい1本の流れと、その行き先の節点にだけ付ける。色は書かない
- 全項目と決まりごとは `scripts/figure/sankey.py` の冒頭

## 型を足す

型は「検査と配置」だけを持ち、色と HTML は共通のものを使う。足すときは:

1. `scripts/figure/<型>.py` に `FigureType` を継いだクラスを書き、`@register` を付ける。`check(spec)` は誤りを1件1行で返し、`build(spec, theme)` は `Scene`（長方形・曲線・文字）を返す。色は `paper`・`ink`・`muted`・`accent` の役の名前で書く
2. `scripts/figure/__init__.py` で import する
3. `figure_render.py` で描いて、不正な spec を断る・viewBox からはみ出さないを手で確かめる

`python3 ${CLAUDE_SKILL_DIR}/scripts/figure_render.py --types` で登録済みの型が出る。

## 由来

配置の規則（帯は両端で水平につなぐ・1つの尺度・暗い面と1色の差し色）は、
[cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design)（MIT）のサンキーの手引きを参考にした。
コードは写していない。字の下限（14px）は、載せたときに縮んでも読めるように、この生成器で決めた。
