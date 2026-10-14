# 起動前の確認（next-task・plan-tasks・list-tasks 共通）

## 依存を確かめる

`command -v tw` が何も返さなければ、`tw` が PATH に張られていないとして `MISSING` を報告して終了する。
入れ方は次のとおり案内する。

- plugin で入れるなら `/plugin install tsukumo-workflow@tsukumo-plugins`
- リンクで入れるなら `task-model` を含めて `./install.sh` を打ち直す

## 設定が読めないとき

`` !`tw config 2>&1 || true` `` の出力が `MISSING` の行か、`tw config` を打てない旨（git の外など）なら、
`/setup-tasks` を案内して終了する。
