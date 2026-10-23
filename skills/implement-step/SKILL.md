---
name: implement-step
description: "並びの段「実装」。着手の印が立ったタスク1件の `## やること` の段を、difficulty に応じたモデルの委譲先に1段ずつ渡して済ませ、段ごとの結果を委譲先の `phase_result` で返させる。並びを読むオーケストレーター（task-workflow）がタスクの ID を渡して呼ぶ。印の無いタスクには使わない。"
user-invocable: false
metadata:
  workflow-step: "true"
  workflow-step-order: "1"
  workflow-step-title: "実装"
  workflow-brief-implementer: "../task-workflow/parts/implementer/implementer-request.md"
---

# 実装の段

並びの段の1つ。手順の本文は `task-workflow` の `SKILL.md` の手順5〜5dと、そこから読む副ファイル（下の表。`task-workflow` の `parts/implementer/`）で、このスキルは入口と出口だけを持つ。
守る関門は `tw handback-guard`（`tw step`・`tw verify`・`tw pause`）と `tw commit-guard`。最後の段の返却には `## 構造の変更` を書く。

| 副ファイル | 中身 |
| --- | --- |
| `delegate.md` | 手順5の (b)〜(f) と手順5c（実装の依頼文の場合分け） |
| `implementer-request.md` | 実装の依頼文の型（`tw brief implementer` が節を選んで値を埋める。metadata の `workflow-brief-implementer` が指す） |
| `implementer-brief.md` | 実装役が最初に読んで従う決まり（依頼文が絶対パスを渡す） |
| `rewrite-plan.md` | `## やること` を書き直してから委譲するとき |
| `handback-read.md` | 手順5b（返りを見る） |
| `return-abnormal.md` | 手順5bの返りの異常系 |
| `unresponsive.md` | 手順5d（委譲先の無応答） |

入口と出口の正典は tsukumo の契約 `docs/architecture/workflow-contract.md`「スキルの契約」
（https://github.com/sinnlosses/tsukumo/blob/main/docs/architecture/workflow-contract.md）。

## 受け取るもの

- 呼び出しの引数: タスクの ID 1つ（以下 `T-xxx`）
- Beads から: `tw show T-xxx` の `## やること` の段と `## 完了条件`、`tw plan-check T-xxx` の計画の形

## 手順

1. 段が要るかを決める（下の「飛ばすとき」）。要らなければ `skipped` を返して終わる
2. オーケストレーターの手順5〜5dを、上の表の副ファイルのとおりに進める（委譲先の選び方・依頼文・返りの読み方・無応答の扱いは変えない）
3. 委譲先は段ごとに `phase_result` を呼ぶ（`parts/implementer/implementer-brief.md` の「段の進め方と返却」。依頼文に足す項目は無い）

## 飛ばすとき

- `tw claim` の行末が `direct=Y` で `tw plan-check T-xxx` が `PLAN_REGISTERED`（近道）のときは飛ばさない。
  委譲の代わりに `task-workflow` の `flows/direct.md` の手順1〜3（直す・検証）をメインで行い、下の「メインで走るとき」で返す
- 委譲しない流れ（`loopable: N` をユーザーが直接呼んだとき）も飛ばさない。`flows/main.md` のとおりメインで段を進める
- この段が `skipped` を返すのは、`## やること` に作業が無いと分かり `dropped` にするときだけ（`tw done --dropped` は送り出しの段が打つ）

## 返すもの

- 申し送り（`finished`・`skipped` で返す前に必ず書く）: `tw relay T-xxx 実装 --body-file -`。行の形と時機は task-model の WORKFLOW.md「段の申し送り」の
  「鍵 `実装` の書き方と読み方」
- **委譲先で走る段**（委譲する流れ）: 委譲先が段ごとに `phase_result` を呼ぶ（手順3）。メインは返却のたびに、オーケストレーターの
  「進み具合の帯（`work_plan`）」の表どおりに `work_plan` を呼ぶ（`phase_result` で済んだ段は `phaseSummary` を省けない）
- **メインで走るとき**（近道・委譲しない流れ）: 段を終えるたびに `work_plan` で次の位置へ進め、`phaseSummary` に段のまとめを書く
- `skipped`: `work_plan` の `skippedPhases` にこの段の名前（帯の段が `tw plan-phases` の並びにあるときだけ）を入れて進める
- `stopped`（委譲先が `phase_result` の `stopped` を返した（返却の先頭が `止めた:`）・`CONFLICT`・人に預けて `tw release` した）: `report` の `workPlanClosing` に `stopped` を渡し、後の段を呼ばない
