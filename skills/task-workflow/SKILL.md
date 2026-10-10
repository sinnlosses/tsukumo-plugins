---
name: task-workflow
description: "Beads（bd）にタスクを置き、tw コマンドで管理する運用の正典（タスクの本文の枠、status と着手の印、difficulty の基準、結果の書き方、送り出し、tw コマンドの参照、トラッカー〔GitHub・Jira〕との同期）。/next-task・/plan-tasks・/list-tasks・/retrospect・/setup-tasks が参照する。ユーザーが「タスク運用のルールを教えて」「difficulty の基準は？」「tw コマンドの終了コードは？」「GitHub の Issue とどう同期する？」と聞いたときに読む。手順は持たず、何も書き換えない。"
user-invocable: false
---

このスキルは**ルールの置き場**で、手順を持たない。実際の作業は次が行う。

| やりたいこと | スキル |
| --- | --- |
| 未着手タスクを1件進める | `/next-task` |
| 指示メモをタスクに分解する | `/plan-tasks` |
| 一覧を見る（読み取り専用） | `/list-tasks` |
| プロジェクトにタスク運用を用意する | `/setup-tasks` |

タスクの本文・着手の印・履歴は Beads（`bd`）に置き、`tw` が Beads とトラッカーと git をつなぐ
（WORKFLOW.md「Beads とトラッカー」）。
ルール本文は [WORKFLOW.md](WORKFLOW.md)。冒頭の目次で節を1つ選んで読む（通読しない）。
プロジェクト固有の値（検証コマンド・整形コマンド・ブランチ・トラッカー）は、そのプロジェクトの `.tw/config.toml`
（`tw config` で見る。WORKFLOW.md「ファイル配置と設定ファイル」）。

手順は `scripts/task.py`（PATH 上の `tw`）が持つ。**モデルが手で代用しない**（`bd` を直に打って着手・採番・送り出しを行うと、
取り合いの判定と自己テストで守った手順を素通りする）:

```bash
tw status   # 一覧（読み取り専用）
```
