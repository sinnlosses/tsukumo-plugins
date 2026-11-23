---
name: architecture-map
description: リポジトリのアーキテクチャの地図（大・中・小の3段の箱と、箱どうしのエッジ、地図と実装のずれ）を地図のファイル `.architecture-map.json` に書く。dependency-cruiser で依存を抽出できる JS・TS のリポジトリが対象。地図の定義（TOML）が無ければ、ディレクトリ・README・決定の記録から大・中の箱を下書きして利用者に直させ、あれば下書きせず抽出と照合を走らせて地図のファイルを書き直す。ユーザーが「アーキテクチャの地図を作って」「地図を更新して」「構成図の元になる地図を書いて」「地図と実装のずれを見て」と言ったとき、依存の構造を箱とエッジで俯瞰したいときに使う。JS・TS 以外の言語・依存を抽出できないリポジトリには使わない。
---

# アーキテクチャの地図を書く

**大・中の箱は人が決め、小の箱と実際のエッジは依存の抽出が作る。** このスキルは、地図の定義（TOML）と dependency-cruiser の JSON から
地図のファイル（`.architecture-map.json`）を書くスクリプトを持ち、定義が無いリポジトリでは定義の下書きの手順を持つ。
地図のファイルを読むのは地図を描く側で、このスキルはファイルを書くだけで、どの描き手の口も呼ばない。
契約は版 1（欄・箱の ID・パスの書き方・ずれの種類）で、正典は tsukumo の `docs/architecture/architecture-map.md`。
tsukumo が無い環境でも、このスキルは同じに動く。

## 前提

- 対象のリポジトリが JS・TS で、dependency-cruiser（`npx dependency-cruiser`）が動くこと。ほかの言語の抽出器は初版に無い
- Python 3.13 以降（箱に属するかの判定に `glob.translate` を使う）。標準ライブラリだけで動く
- 抽出の設定（`.dependency-cruiser.cjs` など）があれば、そのまま使う。設定の依存の規則（`forbidden`）の名前に共通の接頭辞があれば、意図したエッジの2つ目の出どころになる

## 手順

### 1. 依存を抽出する

```sh
mkdir -p /tmp/architecture-map
npx dependency-cruiser <対象のディレクトリ> --output-type json --config <設定のファイル> > /tmp/architecture-map/dependencies.json
```

出力は対象のリポジトリの外（`/tmp/architecture-map/`）に置く。設定が無いときは `--config` を外す（意図したエッジは定義に書いたものだけになる）。

### 2. 地図の定義があるか見る

リポジトリに地図の定義（TOML。置き場の例は `docs/architecture-map.toml`）があるかを、利用者に聞くか、ファイル名で探して決める。

- **ある**: 下書きしない。手順 4 へ進み、スクリプトを走らせて地図のファイルを書き直すだけにする。定義は人が直すもので、このスキルは書き換えない
- **無い**: 手順 3 で下書きする

### 3. 定義を下書きする（定義が無いときだけ）

ディレクトリの並び・README・決定の記録（ADR など）を読み、TOML の定義を書く。**書いたら利用者に読ませて直してもらい、直った版でだけ手順 4 へ進む**（下書きのまま走らせない）。

書く欄と形は次のとおり（IDs は人が付ける名前で、親の ID を頭に付けなくてよい）。

```toml
overview = "このリポジトリは何で、どんな部分からなり、外と何をやり取りするかを3文まで"
forbidden_prefixes = ["<依存の規則の名前の共通の接頭辞>"]   # 規則から意図したエッジを読むとき。無ければ省く

[[lanes]]                       # 任意。実行の境界の区画。書くなら大の箱は全部 lane を持つ
id = "backend"                  # 区画の id（区切り1つの形。箱の ID と同じ字でもよい）
title = "サーバー側"             # 任意
kind = "runtime"                # runtime・crosscut・external のどれか。色の欄は無い

[[boxes]]                       # 大の箱
id = "server"
lane = "backend"                # lanes の id。大の箱だけ。external = true の箱は kind = "external" の区画へ
title = "サーバー"               # 任意。無ければ id を出す
summary = "動詞で1行。何を受け持つか"
purpose = "なぜほかと分かれているかを1行"   # 任意
overview = "3文までの地の文"                 # 任意
aliases = ["旧い ID"]            # 改名したときだけ
external = true                 # リポジトリの外の相手のときだけ。paths・children は書かない
place = { column = 0, row = 0 } # 任意。親の図の格子の位置
links = { adr = ["docs/adr/0001-x.md"], terms = ["用語"] }   # 任意

  [[boxes.children]]            # 中の箱
  id = "server/report"
  summary = "..."
  paths = ["src/server/report/**", "src/server/report/.*"]

[[edges]]                       # 意図したエッジ（どの段の箱の ID でもよい）
from = "server/report"
to = "shared"
label = "import"
```

小の箱は書かない（ディレクトリから抽出が作る）。段ごとに答える問いを決めて欄を書く。識別子や名前を並べず、地の文で書く。

| 段 | 答える問い | 書く欄 |
| --- | --- | --- |
| 全体 | このリポジトリは何で、どんな部分からなり、外と何をやり取りするか | 頭の `overview`、大の `summary` |
| 大 | 何を受け持ち、なぜほかと分かれているか | `summary`（動詞で1行）、`purpose`（分かれている理由を1行） |
| 中 | 何を受け持ち、どこから受けてどこへ渡すか | `summary`、必要なら `overview`（3文まで） |
| 小 | 何が置かれ、親の機能の中でどの役か | 抽出が作る。`summary` は付けなくてよい |

`summary` はファイルの中身を写さず、決定の記録・要件・用語への導線にする。`overview` は3文まで。

切り方の決まり:

- 中の箱は、小の段（ディレクトリ）が20前後に収まるように切る。1つの中の箱に小が多すぎるときは分ける
- `paths` は glob の配列で、区切りは `**`（0個以上の区切り）か、字と `*` の並び。`?`・`[...]`・`{a,b}`・`!` は使わず、頭に `./`・`/` を付けない
- **ワイルドカード（`*`・`**`）は `.` で始まる区切りに当たらない。** `.` で始まるファイルやディレクトリを中の箱に入れるときは、その区切りを字のまま `paths` に書く（`src/x/.*`・`plugin/.claude-plugin/*`）。書き漏らすと、その `.` ファイルは `unmapped-file` になる
- 同じディレクトリ直下のファイルは1つの中の箱に入れる（分かれると `split-directory`）。1つのファイルを2つの中の箱に入れない（`overlapping-file`）
- 大の箱の `paths` は書かない（子の和をスクリプトが書く）。外の世界の箱は `paths` も `children` も無い

### 4. 地図のファイルを書く

```sh
python3 ${CLAUDE_SKILL_DIR}/scripts/architecture_map.py \
  --definition <定義の TOML> \
  --dependencies /tmp/architecture-map/dependencies.json \
  --output .architecture-map.json
```

リポジトリの根を cwd にして打つ。`--tree <sha>` は、作業ツリーの木の SHA を `source.tree` に書きたいときだけ渡す。`--root` で根を、`--commit` で `source.commit`（既定は `git rev-parse HEAD`）を替えられる。

終了コードは次のとおり。

| コード | 意味 |
| --- | --- |
| 0 | error のずれが無い。ファイルを書いた |
| 1 | error のずれがある。ファイルは書いた。出力のずれの行を読み、定義を直すか実装を直す |
| 2 | 定義か入力が壊れている。ファイルは書かない。出力の理由を直す |

ずれは `undeclared-edge`・`unmapped-file`・`overlapping-file`・`split-directory`・`empty-box`・`missing-link`（error）と `unused-intended-edge`（warn）。
意味と覆い方は契約の正典に従う。同じ入力からは同じバイト列を書く。

### 5. 結果を利用者へ渡す

出力の1行目（実際のエッジの件数と本数、閉じた本数、ずれの件数）と、ずれの種類ごとの件数を伝える。
ずれが error なら、定義を直す案（`paths` の足し引き、意図したエッジの追加）を添える。定義を直すのは利用者で、このスキルは直さない。

`.architecture-map.json` を git にコミットするかは、リポジトリが決める。検証のたびに書き直すなら git の外に置き（`.gitignore`）、
差分を読むなら置く。スクリプトは同じ入力から同じバイト列を書くので、置いても差分は変わった所だけになる。

## 作り手の義務（要点）

- 書くのは地図のファイル（と自分の入力・一時ファイル）だけ。一時ファイルに書いてから名前を替えて置く
- ファイルの中身・差分・会話の中身を書かない。書くのはパス・ID・表示名・役割の1行・用語・数だけ
- 定義を書き換えない
