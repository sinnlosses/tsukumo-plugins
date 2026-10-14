# 実装役

| 入力 | 返すもの | 守る関門 |
| --- | --- | --- |
| タスクの ID・段の番号 | 返却の1行目（tsukumo の `docs/architecture/workflow-contract.md`「委譲の返却」）と、最後の段の `## 構造の変更` | `tw handback-guard`（`tw step`・`tw verify`・`tw pause`）、`tw commit-guard` |

手順5〜5dで読む副ファイルは `parts/implementer/` にある。

| 副ファイル | 中身 |
| --- | --- |
| `delegate.md` | 手順5の (b)〜(f) と手順5c（実装の依頼文） |
| `implementer-brief.md` | 実装役が最初に読んで従う決まり（依頼で絶対パスを渡す） |
| `rewrite-plan.md` | `## やること` を書き直してから委譲するとき |
| `handback-read.md` | 手順5b（返りを見る） |
| `return-abnormal.md` | 手順5bの返りの異常系 |
| `unresponsive.md` | 手順5d（委譲先の無応答） |
