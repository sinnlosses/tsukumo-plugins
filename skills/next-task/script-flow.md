# ワークフロースクリプトの流れ（`tw config` の `flow` が `script`）

手順4で着手の印を立てたあと、`task-workflow` を呼ばずに plugin のワークフロースクリプト `task-flow` で実装・受け入れ・振り返りを進め、完了の通知を受けて送り出す。
`Workflow` を呼べるのはメインだけで、委譲先には呼べない。ワークフロースクリプトの `args`・戻り値・`lap` は plugin の README「ワークフロースクリプト `workflows/task-flow.js`」。
`flow` が `skills` のときはこのファイルを読まない（`task-workflow` を呼ぶ）。

1. **再開の位置を読む**: `tw relay T-xxx 再開` が `NONE` でなければ、本文の段の名前（`implement`・`accept`・`retrospect`）をワークフロースクリプトの `from` に渡す。
   本文がワークフロースクリプトの段の名前でなければ（`flow` を切り替える前のスキルの全名）、`from` は渡さず最初から始める。

2. **プログレスバーを渡す**: `tw plan-phases T-xxx` の第3列の JSON をそのまま `work_plan` に渡す（`delegatedRange` つき）。
   再開なら `--through` で `from` の手前の段までを済ませた位置にする（`accept` なら `実装`、`retrospect` なら `受け入れ`）。

3. **ワークフロースクリプトを呼ぶ**: `Workflow` に、plugin の名前を接頭辞にしたワークフロースクリプトの名前（plugin の README「ワークフロースクリプト」の「呼び方」）を渡す。読めなければ `scriptPath` に、この plugin の `workflows/task-flow.js` の絶対パスを渡す。`args`:
   - `taskId`: タスクの ID
   - `phaseCount`: 手順2の JSON の `delegatedRange.count`（無ければ 1。ワークフロースクリプトが計画を書き直したときはワークフロースクリプトが置き換える）
   - `steps`: `tw config` の `steps` の行（`flow` が `script` ならワークフロースクリプトの段の名前で出る）
   - `model`: `tw show T-xxx` の `difficulty`
   - `from`: 手順1で決めたとき

4. **完了の通知を受ける**: 先にプログレスバーの位置を確かめて `work_plan` を渡す（渡すまで作業のツールは拒まれる）。位置は `tw plan-phases T-xxx --through <走った最後の段>`
   （`振り返り`・`受け入れ`・`実装` のうち、ワークフロースクリプトが走らせた最後のもの）。`phaseSummary` は戻り値から書く。
   - 戻り値が `{ stopped: true, phase }`: `tw relay T-xxx 再開 --body-file -` に `phase` を書き、`tw release T-xxx` を打って、`workPlanClosing: stopped` の `report` を渡す
   - 通ったとき: 申し送りをワークフロースクリプトの戻り値から書く（形は `task-model` の `WORKFLOW.md`「段の申し送り」）。
     `tw relay T-xxx 実装` に `conventionLines`、`tw relay T-xxx 受け入れ` に `accept.judgment` の判定と `findings`（`- 指摘:`）・`visual`（`- 目視:`）・`notes`、
     `tw relay T-xxx 振り返り` に `retrospect.line`。ワークフロースクリプトが走らせなかった段の鍵は書かない。続けて Skill ツールで `tsukumo-workflow:ship-step` に ID を渡して呼ぶ

5. **完了報告**は `task-workflow` と同じ決まり（完了報告の節。`/loop` の続行判断も同じ）で書く。
