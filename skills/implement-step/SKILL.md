---
name: implement-step
description: "並びの段「実装」。着手の印が立ったタスク1件の `## やること` の段を、difficulty のモデルの委譲先に1段ずつ渡して済ませ、段ごとの結果を委譲先の `phase_result` で返させる。並びを読むオーケストレーター（task-workflow）がタスクの ID を渡して呼ぶ。印の無いタスクには使わない。"
user-invocable: false
metadata:
  workflow-step: "true"
  workflow-step-order: "1"
---

# 実装の段

並びの段の1つ。手順の本文は `task-workflow` の `parts/implementer.md`（オーケストレーターの手順5〜5d）で、このスキルは入口と出口だけを持つ。
入口と出口の正典は tsukumo の契約 `docs/architecture/workflow-contract.md`「スキルの契約」
（https://github.com/sinnlosses/tsukumo/blob/main/docs/architecture/workflow-contract.md）。

## 受け取るもの

- 呼び出しの引数: タスクの ID 1つ（以下 `T-xxx`）
- Beads から: `tw show T-xxx` の `## やること` の段と `## 完了条件`、`tw plan-check T-xxx` の計画の形
- 設定: `tw config` の `flow` と `parts.implementer`

## 手順

1. 段が要るかを決める（下の「飛ばすとき」）。要らなければ `skipped` を返して終わる
2. オーケストレーターの手順5〜5dを、`parts/implementer.md` から読む副ファイルのとおりに進める（委譲先の選び方・依頼文・返りの読み方・無応答の扱いは変えない）
3. 手順5cの依頼文に、次の1項を足す: 「段を済ませたら、`SubagentHandback` の直前に MCP ツール `phase_result` を
   `phase`＝渡された段の番号・`count`＝`## やること` の段の数・`outcome`＝`finished`（止めたときは `stopped` と `reason`、
   要らない段と分かったときは `skipped`）で呼ぶ。`phase_result` が一覧に無ければ呼ばない。返却の1行目は今までどおり書く。
   計画だけの回は呼ばない」

## 飛ばすとき

- `tw claim` の行末が `direct=Y` で `tw plan-check T-xxx` が `PLAN_REGISTERED`（近道）のときは飛ばさない。
  委譲の代わりに `task-workflow` の `flows/direct.md` の手順1〜3（直す・検証）をメインで行い、下の「メインで走るとき」で返す
- 委譲しない流れ（`loopable: N` をユーザーが直接呼んだとき、`flow` が `main`）も飛ばさない。`flows/main.md` のとおりメインで段を進める
- この段が `skipped` を返すのは、`## やること` に作業が無いと分かり `dropped` にするときだけ（`tw done --dropped` は送り出しの段が打つ）

## 返すもの

- **委譲先で走る段**（委譲する流れ）: 委譲先が段ごとに `phase_result` を呼ぶ（手順3）。メインは返却のたびに、オーケストレーターの
  「進み具合の帯（`work_plan`）」の表どおりに `work_plan` を呼ぶ（`phase_result` で済んだ段は `phaseSummary` を省けない）
- **メインで走るとき**（近道・委譲しない流れ）: 段を終えるたびに `work_plan` で次の位置へ進め、`phaseSummary` に段のまとめを書く
- `skipped`: `work_plan` の `skippedPhases` にこの段の名前（帯の段が `tw plan-phases` の並びにあるときだけ）を入れて進める
- `stopped`（委譲先が `止めた`・`CONFLICT`・人に預けて `tw release` した）: `report` の `workPlanClosing` に `stopped` を渡し、後の段を呼ばない
