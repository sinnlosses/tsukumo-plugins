---
name: task-model
description: "タスクという概念の形（本文の枠、status と着手の印、difficulty の基準、loopable、ドラフトの課題、結果の書き方、送り出し、Beads〔bd〕との対応）と、それを扱う tw コマンドの実装・参照（サブコマンド、終了コード、hook の関門、トラッカー〔GitHub・Jira〕との同期）の正典。タスク運用のスキルが参照する。ユーザーが「タスク運用のルールを教えて」「difficulty の基準は？」「tw コマンドの終了コードは？」「GitHub の Issue とどう同期する？」と聞いたときに読む。手順は持たず、何も書き換えない。"
user-invocable: false
---

このスキルは**規則の置き場**で、手順を持たない（参照専用）。タスクを選ぶ・進める・登録する・一覧する・
用意する手順は、それぞれの入口のスキルが持つ。

タスクの本文・着手の印・履歴は Beads（`bd`）に置き、`tw` が Beads とトラッカーと git をつなぐ
（WORKFLOW.md「Beads とトラッカー」。同期の実装の詳細は [TRACKER.md](TRACKER.md)）。
ルール本文は [WORKFLOW.md](WORKFLOW.md)。サブコマンドの表と終了コード表は [tw-reference.md](tw-reference.md)。冒頭の目次で節を1つ選んで読む（通読しない）。
プロジェクト固有の値（検証コマンド・整形コマンド・ブランチ・トラッカー）は、そのプロジェクトの `.tw/config.toml`
（`tw config` で見る。WORKFLOW.md「ファイル配置と設定ファイル」）。

手順は `scripts/task.py`（PATH 上の `tw`）が持つ。**モデルが手で代用しない**（`bd` を直に打って着手・採番・送り出しを行うと、
取り合いの判定と自己テストで守った手順を素通りする）:

```bash
tw status   # 一覧（読み取り専用）
```
