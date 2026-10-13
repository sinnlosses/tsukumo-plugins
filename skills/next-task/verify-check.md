手順6の `tw verify-check` の出力の読み方。`accept.md` の「受け入れのあとは…`tw verify-check` を打つ」から読まれる。

| `tw verify-check` の出力 | すること |
| --- | --- |
| `VERIFIED_SAME\t<木の SHA>` | 検証コマンドを打たずに次へ（委譲先が `tw verify` で通した中身と、整形のあとの中身が同じ） |
| `NOT_VERIFIED\t<理由>` | `tw verify` を Bash の背景（`run_in_background`）で起こし、待たずに手順6aへ（出力は手順6bで読む）。理由ごとの前置きは下の表 |
| `NOTHING` | 検証コマンドが無い。完了条件を目視で確かめる |

`NOT_VERIFIED` の理由の意味は `skills/task-workflow/WORKFLOW.md` の `verify-check` の行。理由ごとの対処:

| 理由 | 対処 |
| --- | --- |
| `none` | `tw verify` を打つ |
| `base` | `tw verify` が主ブランチを取り込んでから打つ。送る前の検証コマンドの行があるときは、検証コマンドの代わりにそのコマンドで打って控えを取る。取り込みが衝突しなければ `base` にならず、鍵が同じなら `VERIFIED_SAME` |
| `head`・`content`・`command` | `tw verify` を打つ |
| `scheme` | 第3列（照らした側のチェックアウトの `tw_verify.py` のパス）のチェックアウトの `bin/tw` と版を揃えてから `tw verify` を打つ |
