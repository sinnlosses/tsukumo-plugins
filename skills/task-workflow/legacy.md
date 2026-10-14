# 旧形式の読み取り（移行前のプロジェクトのために残しているもの）

`tw` は、次の旧形式をいまも読む。tsukumo では移行が済んでいるが、ほかのプロジェクトに残っていないと確かめられないので消していない。消すのは、残っていないと確かめたとき（コードと文書を同じコミットで直す）。

| 旧形式 | 読むコード | 中身 | 消せるとき |
| --- | --- | --- | --- |
| `docs/history/tasks.md`（完了タスクのアーカイブ。見出し `## <ID>`） | `scripts/tw_base.py` の `history_ids_at_base`（`tw_new.py` の採番が、主ブランチのこのファイルの最大の番号を避ける）、`retrospect/scripts/material.py` の `print_task`（Beads に無い課題の本文の出典。`layout.HISTORY_TASKS_PATH` を開く） | 過去の `T-xxx` は git の履歴とともに動かさない | このファイルを持つプロジェクトが無くなったとき |
| `docs/history/progress.md` | `retrospect/scripts/material.py` の `print_progress`・`find_progress_section`（Beads に無い古いタスクの小節を出す）、`retrospect/detail.md` の出典の記述 | 同上 | 同上 |
| `pending`（切り替え前の独自の状態）を `hold` と読む | `scripts/beads.py` の `LEGACY_HOLD_STATUS`・`HOLD_STATUSES`、`scripts/tw_edit.py` | 書くのは常に `deferred` | Beads に `pending` の課題が残っていないとき |
| 設定の `root` キー | `scripts/layout.py` の `IGNORED_KEYS` | 書いてあっても読み捨てる | 設定に `root` を書いたプロジェクトが無いとき |
| 設定の `store` が `beads` 以外 | `scripts/layout.py` の `STORE_VALUES` | `files` を含め `INVALID` | 同上 |
| `bd kv` の `task-workflow.project-items`（旧い1つの塊） | `scripts/tracker.py` の `LEGACY_ITEMS_KEY`・`_migrate_legacy_items` | 最初の読み込みで課題ごとのキーへ移して消す | 旧い塊を持つ Beads が無いとき |
