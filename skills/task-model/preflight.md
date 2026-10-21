# 起動前の確認（入口・登録の入口・一覧の入口に共通）

## 依存を確かめる

`command -v tw` が何も返さなければ、`tw` が PATH に無いとして `MISSING` を報告して終了する。
入れ方は `/plugin install tsukumo-workflow@tsukumo-plugins` の 1 つだけを案内する（plugin が有効なあいだ、`bin/tw` が Bash ツールの PATH に入る）。
端末で人が打つ `tw` は plugin では届かないので、README の「端末で `tw` を打つ」の wrapper を案内する。

## 設定が読めないとき

`` !`tw config 2>&1 || true` `` の出力が `MISSING` の行か、`tw config` を打てない旨（git の外など）なら、
用意の入口（`WORKFLOW.md` の用語）を案内して終了する。
