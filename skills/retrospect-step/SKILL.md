---
name: retrospect-step
description: "並びの段「振り返り」。受け入れが済んだタスク1件について、受け入れ役の材料を振り返りのスキル（retrospect）に渡して兆候を判定し、物差しを通った改善候補をドラフトの課題として積む。並びを読むオーケストレーター（task-workflow）がタスクの ID を渡して呼ぶ。観点や物差しそのものは retrospect が持つ。"
user-invocable: false
metadata:
  workflow-step: "true"
  workflow-step-order: "3"
  workflow-step-title: "振り返り"
---

# 振り返りの段

並びの段の1つ。手順の本文は `task-workflow` の `parts/retrospect/retrospect.md`（オーケストレーターの手順6a）で、このスキルは入口と出口だけを持つ。
振り返りの観点・物差し・判定の口は `retrospect` スキルが持ち、この段はそれを Skill ツールで名前を指して呼ぶ（呼ぶ名前の決め方は `parts/retrospect/retrospect.md`）。
入口と出口の正典は tsukumo の契約 `docs/architecture/workflow-contract.md`「スキルの契約」
（https://github.com/sinnlosses/tsukumo/blob/main/docs/architecture/workflow-contract.md）。

## 受け取るもの

- 呼び出しの引数: タスクの ID 1つ（以下 `T-xxx`）
- Beads から: タスクの本文と履歴（`retrospect` の判定の口がタスクの ID で読む）
- 申し送りから（task-model の WORKFLOW.md「段の申し送り」）: `tw relay T-xxx 受け入れ` の本文（`- 材料:`・`- 指摘:`・`- 付随:` の行）と、
  `tw relay T-xxx 実装` の本文の `- ドラフト:` の行。`受け入れ` が `NONE` なら材料が無いので、`parts/retrospect/retrospect.md` の判定に材料なしとして渡す。`実装` が `NONE` ならドラフトの候補は無い

## 手順

1. 段が要るかを決める（下の「飛ばすとき」）。要らなければ `skipped` を返して終わる
2. オーケストレーターの手順6aを `parts/retrospect/retrospect.md` のとおりに進める（lap・材料の渡し方・ドラフトの積み方・`## 結果` の `- 振り返り:` の1行の決め方は変えない）

## 飛ばすとき

- 近道（`tw claim` の行末が `direct=Y` で `tw plan-check T-xxx` が `PLAN_REGISTERED`）で受け入れの段を飛ばしたとき（`## 結果` の行は `- 振り返り: 近道（省いた）`）

## 返すもの

この段はメインで走る。

- 申し送り（`finished`・`skipped` で返す前に必ず書く）: `tw relay T-xxx 振り返り --body-file -` に、`## 結果` に入れる `- 振り返り:` の1行と、積んだドラフトの ID を渡す。
  `skipped` は上の「飛ばすとき」の `- 振り返り:` の行（`近道（省いた）`）を渡す（`tw finish` の `--reflection` は `skipped`）。並びから外した振り返りは、この段が呼ばれないので返す側にならない（外したときの `## 結果` の行はオーケストレーターが書く）
- `finished`: 振り返りが済んだら、オーケストレーターの「進み具合の帯（`work_plan`）」の表の `--through 振り返り` の行どおりに
  `work_plan` を呼び、`phaseSummary` に `- 振り返り:` の1行の中身を書く
- `skipped`: `work_plan` の `skippedPhases` にこの段の名前（帯の段が `tw plan-phases` の並びにあるときだけ。近道の並びには無い）を入れて進める
- `stopped`（`retrospect` を一覧に見つけられず人に預けたときなど）: `report` の `workPlanClosing` に `stopped` を渡し、後の段を呼ばない
