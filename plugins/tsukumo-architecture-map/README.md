# tsukumo-architecture-map

リポジトリのアーキテクチャの地図を、地図のファイル `.architecture-map.json`（作業ディレクトリの根。JSON）として書く作り手。
地図のファイルを書くだけで、読む側（tsukumo など）の口を呼ばない。ほかのどのリポジトリでも使える。

## 範囲

- **初版で地図を作れるのは、dependency-cruiser で依存を抽出できる JS・TS のリポジトリだけ**。ほかの言語の抽出器は後から足す
- Python 3.13 以降が要る（モジュールに属するかの判定に `glob.translate` を使う）。標準ライブラリだけで動く

## 契約

地図のファイルの形は**契約の版 1**。欄・モジュールの ID・パスの書き方・ずれの種類・作り手の義務の正典は、tsukumo の
`docs/architecture/architecture-map.md`（判断の経緯は `docs/architecture/adr/0082-architecture-map-contract.md`）。
契約を満たす作り手なら、これ以外のものも同じに当てられる。

## 作り手の役

| 役 | 内容 |
| --- | --- |
| 抽出 | dependency-cruiser の JSON から、小のモジュール（ディレクトリ）と実際のエッジ（ファイルの組の本数つき）を作る |
| 照合 | 人が直す地図の定義の大・中のモジュールと意図したエッジを抽出と比べ、契約の7種類のずれを出す |
| 下書き | 地図の定義が無いリポジトリで、ディレクトリと文書から大・中のモジュールを AI が下書きする（スキル） |

スキルは `tsukumo-architecture-map:architecture-map`。手順と定義の書き方は `skills/architecture-map/SKILL.md`、スクリプトは
`skills/architecture-map/scripts/architecture_map.py`（自己テストは同じ場所の `architecture_map_selftest.py`）。

```
/plugin install tsukumo-architecture-map@tsukumo-plugins
```

## 入力と出力

- 入力: 地図の定義（TOML。人が直す。このスクリプトは書き換えない）と、dependency-cruiser の JSON（`--output-type json`）
- 出力: `.architecture-map.json`。一時ファイルに書いてから名前を替えて置く。同じ入力からは同じバイト列を書く
- 区画（任意）: 定義の頭の欄のあとの `[[lanes]]`（`id`・`title`・`kind = "runtime" | "crosscut" | "external"`）と大のモジュールの `lane` を、地図のファイルの頭の `lanes`（`overview` と `boxes` のあいだ）とモジュールの `lane` へ写す。書かない定義からは今と同じファイルが出る。決まりは tsukumo の `docs/architecture/adr/0085-architecture-map-lanes.md`
- エントリポイント（任意）: モジュールの `entrypoints`（読み始めるファイルのパスの配列）を地図のファイルのモジュールへ写す。定義に書いたものが最優先で、モジュールの `paths` に当たらないものは書き出さずに止める。書かなければ、モジュールの `paths` の共通の頭のディレクトリの直下から `index.*` → ディレクトリと同名 → `main.*` の順で最初の1つを選び、無ければ欄を書かない（外の世界のモジュールには書かない）。決まりは tsukumo の `docs/architecture/adr/0089-architecture-map-entrypoints.md`
- 終了コード: 0（error のずれ無し）・1（error のずれあり。ファイルは書く）・2（定義か入力が壊れている。書かない）
- コミットするかはリポジトリが決める
