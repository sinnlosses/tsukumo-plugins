---
name: task-workflow
description: "develop/task/ の1件1ファイルと git の外の台帳（または設定で選ぶ Beads 方式）でタスクを管理する運用の正典（タスクファイルの文法、status と着手の印、difficulty の基準、結果の書き方、送り出し、tw コマンドの参照、旧形式からの移行）。/next-task・/plan-tasks・/list-tasks・/retrospect・/setup-tasks が参照する。ユーザーが「タスク運用のルールを教えて」「difficulty の基準は？」「tw コマンドの終了コードは？」「旧形式から移すには？」と聞いたときに読む。手順は持たず、何も書き換えない。"
user-invocable: false
---

このスキルは**ルールの置き場**で、手順を持たない。実際の作業は次が行う。

| やりたいこと | スキル |
| --- | --- |
| 未着手タスクを1件進める | `/next-task` |
| 指示メモをタスクに分解する | `/plan-tasks` |
| 一覧を見る（読み取り専用） | `/list-tasks` |
| プロジェクトにタスク運用を用意する | `/setup-tasks` |

置き場は既定のファイル方式と、設定の行 `- タスクの置き場: beads` で選ぶ Beads 方式（`bd`）の2つで、
`tw` がどちらでも同じサブコマンドで動く（WORKFLOW.md「Beads 方式」）。
ルール本文は [WORKFLOW.md](WORKFLOW.md)。冒頭の目次で節を1つ選んで読む（通読しない）。
プロジェクト固有の値（検証コマンド・整形コマンド・ブランチ）は、そのプロジェクトの CLAUDE.md の
「## タスク運用」節（WORKFLOW.md「ファイル配置と設定ファイル」）。

手順は `scripts/task.py`（PATH 上の `tw`）が持つ。**モデルが手で代用しない**（台帳・採番・送り出しを手で行うと、
取り合いの錠と自己テストで守った手順を素通りする）:

```bash
tw status   # 一覧（読み取り専用）
```
