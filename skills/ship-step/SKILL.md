---
name: ship-step
description: "並びの段「送り出し」。受け入れの済んだタスク1件を tw finish（tw done・コミット・tw ship）で完了にして主ブランチへ送り、送ったあとの判定を読む。並びを読むオーケストレーター（task-workflow）がタスクの ID を渡して呼ぶ。push はしない。"
user-invocable: false
metadata:
  workflow-step: "true"
  workflow-step-order: "4"
  workflow-step-title: "送り出し"
---

# 送り出しの段

並びの段の1つ。手順の本文は `task-workflow` の `parts/ship/ship.md`（オーケストレーターの手順7・8）で、このスキルは入口と出口だけを持つ。
入口と出口の正典は tsukumo の契約 `docs/architecture/workflow-contract.md`「スキルの契約」
（https://github.com/sinnlosses/tsukumo/blob/main/docs/architecture/workflow-contract.md）。

## 受け取るもの

- 呼び出しの引数: タスクの ID 1つ（以下 `T-xxx`）
- Beads から: `tw show T-xxx` の本文と着手の印（`tw finish` が読む）
- 申し送りから（task-model の WORKFLOW.md「段の申し送り」）: `## 結果` に写す行を、`tw relay T-xxx 実装`（`- 規約:`）・`tw relay T-xxx 受け入れ`
  （`- 指摘:`・`- 目視:`・`- 預けること:`）・`tw relay T-xxx 振り返り`（`- 振り返り:`）の本文から読む。`NONE` のときは、`実装` なら `- 規約: 委譲なし`、
  `受け入れ` なら `- 規約の指摘: なし` で目視・預けることの行を書かず、`振り返り` なら `parts/ship/ship.md` の決まりで `- 振り返り:` の行を決める

## 手順

1. オーケストレーターの手順7・8を `parts/ship/ship.md` のとおりに進める（`## 結果` の書き方・`tw finish` の打ち方・止まったときの表は変えない）
2. 近道（`tw claim` の行末が `direct=Y` で `tw plan-check T-xxx` が `PLAN_REGISTERED`）のときは、`task-workflow` の `flows/direct.md` の手順4・5として進める

この段は飛ばさない（`skipped` を返さない）。`dropped` にするときも `tw finish --dropped` をこの段が打つ。

## 返すもの

この段はメインで走る。

- `finished`（手順8の表で `SHIPPED`・`NOTHING`）: 並びの最後の段なら、オーケストレーターが完了報告の `report` に
  `workPlanClosing: finished` を渡して帯を閉じる（`completion-report.md`）。後ろに段が残っていれば `work_plan` で次の位置へ進め、`phaseSummary` に送った結果を書く
- `stopped`（`parts/ship/ship-stopped.md` の表に従っても送れずに止まったとき、`COMMITS_SINCE_CLAIM` で人に預けたとき）:
  `report` の `workPlanClosing` に `stopped` を渡し、後の段を呼ばない
