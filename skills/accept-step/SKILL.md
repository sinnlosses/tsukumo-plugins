---
name: accept-step
description: "並びの段「受け入れ」。実装の段が済んだタスク1件を、tw accept と受け入れ役のエージェント（agents/acceptor.md。書き換えない判定役）で受け入れ、差し戻しを回して判定を決める。並びを読むオーケストレーター（task-workflow）がタスクの ID を渡して呼ぶ。印の無いタスクには使わない。"
user-invocable: false
metadata:
  workflow-step: "true"
  workflow-step-order: "2"
---

# 受け入れの段

並びの段の1つ。手順の本文は `task-workflow` の `parts/acceptor/accept.md`（オーケストレーターの手順6）と `parts/acceptor/acceptor-return.md`（受け入れ役の返却の行）で、
このスキルは入口と出口だけを持つ。入口と出口の正典は tsukumo の契約 `docs/architecture/workflow-contract.md`「スキルの契約」
（https://github.com/sinnlosses/tsukumo/blob/main/docs/architecture/workflow-contract.md）。

判定は受け入れ役のエージェントがこの段の中の道具として行う。選び方（一覧の末尾一致・`model` の渡し方）は
`parts/acceptor/accept.md` のとおりで、同梱の `agents/acceptor.md`（Opus 固定・`Agent`・`Edit`・`Write`・`NotebookEdit` を持たない）は並びに出さない。

## 受け取るもの

- 呼び出しの引数: タスクの ID 1つ（以下 `T-xxx`）
- Beads から: `tw show T-xxx` の `## 完了条件`、`tw accept T-xxx` が読む着手の控えと差分の範囲
- 設定: `tw config` の検証コマンド
- 申し送りから（task-model の WORKFLOW.md「段の申し送り」）: `tw relay T-xxx 実装` の本文（実装の段の `- 規約:`・friction log・`- ドラフト:` の行）。
  `parts/acceptor/accept.md` が受け入れ役に渡す「委譲先の段ごとの返却の friction log の行」はここから読む。`NONE` なら friction log を書いていない返却として渡す

## 手順

1. 段が要るかを決める（下の「飛ばすとき」）。要らなければ `skipped` を返して終わる
2. オーケストレーターの手順6を、`parts/acceptor/accept.md` とそこから読む副ファイルのとおりに進める（`tw accept`・受け入れ役の依頼・差し戻し・`tw verify-check` の読み方は変えない）

## 飛ばすとき

- 近道（`tw claim` の行末が `direct=Y` で `tw plan-check T-xxx` が `PLAN_REGISTERED`）で実装の段がメインで直したとき。`tw lap T-xxx accept` は打たない

## 返すもの

この段はメインで走る。

- 申し送り（`finished`・`skipped` で返す前に必ず書く）: `tw relay T-xxx 受け入れ --body-file -` に、判定の語と、受け入れ役の最後の返却の
  `- 材料:`・`- 指摘:`・`- 付随:`・`- 目視:`・`- 預けること:` の行（差し戻しの回をまたいだ `- 指摘:` の `規約:` の行も）を渡す。`skipped` は飛ばしたわけの1行を渡す
- `finished`: 判定が `受け入れ` か `預ける`（`accept.md` の表で手順7へ進むもの）で済んだら、オーケストレーターの「進み具合の帯（`work_plan`）」の表の
  `--through 受け入れ` の行どおりに `work_plan` を呼び、`phaseSummary` に判定のまとめを書く
- `skipped`: `work_plan` の `skippedPhases` にこの段の名前（帯の段が `tw plan-phases` の並びにあるときだけ。近道の並びには無い）を入れて進める
- `stopped`（`accept.md` の表で手順7へ進まずに止まるもの。人に預けて `tw release T-xxx` したときなど）: `report` の `workPlanClosing` に `stopped` を渡し、後の段を呼ばない
