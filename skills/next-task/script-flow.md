# ワークフロースクリプトの流れ（`tw config` の `flow` が `script`）

手順4で着手の印を立てたあと、`task-workflow` を呼ばずに plugin のワークフロースクリプト `task-flow` で実装・受け入れ・振り返りを進め、完了の通知を受けて送り出す。
`Workflow` を呼べるのはメインだけで、委譲先には呼べない。ワークフロースクリプトの `args`・戻り値・`lap` は plugin の README「ワークフロースクリプト `workflows/task-flow.js`」。
`flow` が `skills` のときはこのファイルを読まない（`task-workflow` を呼ぶ）。

1. **再開の位置を読む**: `tw relay T-xxx 再開` が `NONE` でなければ、本文の段の名前（`implement`・`accept`・`retrospect`）をワークフロースクリプトの `from` に渡す。
   本文がワークフロースクリプトの段の名前でなければ（`flow` を切り替える前のスキルの全名）、`from` は渡さず最初から始める。

1a. **計画を確かめ、必要なら書き直す**（ワークフロースクリプトを起こす前に、メインの手順で済ませる。ワークフロースクリプトの中で書き直すと、段の数が tsukumo に届かず実装の段が番号を持たない）:
   `tw plan-check T-xxx` を打つ。
   - `PLAN_REGISTERED`・`PLAN_FIRST`: そのまま手順2へ進む
   - `PLAN_STALE`・`PLAN_NOT_FIRST`・`PLAN_UNCOVERED`: まず `tw plan-phases T-xxx` の第3列の JSON（範囲は `pending`）を `work_plan` に渡す。
     続けて `Agent`（`agentType: tsukumo-workflow:no-delegate`）に書き直しを頼む。依頼文は次のとおり（`T-xxx` は ID に置き換える）:
     > タスク T-xxx は着手の印が立っている。作業ツリーは今の cwd。ファイルの書き換えは Edit・Write で行う。最初に `tw brief implementer T-xxx --rewrite` を打ち、出力の2行目以降の決まりを読んで従う。登録時の `## やること` は古いので、いまの主ブランチで調べ直して書き直す（`tw edit` で節の中身だけを渡す。実装の作業はしない）。書き直したあと `tw plan-check T-xxx` を打ち、`PLAN_FIRST`（または `PLAN_REGISTERED`）が出て、`PLAN_STALE`・`PLAN_NOT_FIRST`・`PLAN_UNCOVERED` の行が続かないことを確かめる（足りなければ `tw edit` で書き直して打ち直す）。返すのは、書き直したか・書き直したあとの `## やること` の段の数・打った `tw plan-check` の `PLAN_FIRST`・`PLAN_REGISTERED` の行。書き直せず止めるなら、理由を返す。

     返却を受けたら、`tw plan-phases T-xxx --through 計画` の JSON をそのまま `work_plan` に渡す（`phaseSummary` に書き直しの要点を書く）。ここで範囲の数と段の名前が確定する。書き直せず止まったときは `tw release T-xxx` を打ち、`workPlanClosing: stopped` の `report` を渡して終える。
   - `NOT_OWNER` など着手の印が読めない行: 進めず、その行を報告して止める

2. **プログレスバーを渡す**: 手順1a で計画を書き直したなら、確定した `tw plan-phases T-xxx --through 計画` の JSON を手順3に使う（手順1a で `work_plan` に渡し済みなので、渡し直さない）。書き直していなければ、`tw plan-phases T-xxx` の第3列の JSON をそのまま `work_plan` に渡す（`delegatedRange` つき）。`phases` の要素は段の id と名前の `{id, name}` で、id は `tw plan-phases` だけが生む。同じ JSON の `phases` を手順3で Workflow の `phaseIds` にも渡す（メインが id を作らない・書き換えない）。ここで `phases` に `impl-` で始まる id が無く `impl` だけなら、計画の範囲が未確定なので手順1a へ戻る（ワークフロースクリプトの起動も拒まれる）。
   再開なら `--through` で `from` の手前の段までを済ませた位置にする（`accept` なら `実装`、`retrospect` なら `受け入れ`）。

3. **ワークフロースクリプトを呼ぶ**: `Workflow` に、plugin の名前を接頭辞にしたワークフロースクリプトの名前（plugin の README「ワークフロースクリプト」の「呼び方」）を渡す。読めなければ `scriptPath` に、この plugin の `workflows/task-flow.js` の絶対パスを渡す。`args`:
   - `taskId`: タスクの ID
   - `phaseCount`: 手順2の JSON の `delegatedRange.count`（確定した計画の段の数。手順1a で書き直したときは書き直したあとの数）
   - `phaseIds`: 手順2の JSON の `phases`（そのまま。ワークフロースクリプトは `phase()` の題と `agent()` の `phase` をこの id からだけ作る。欠けていると起動が拒まれる）
   - `steps`: `tw config` の `steps` の行（`flow` が `script` ならワークフロースクリプトの段の名前で出る）
   - `model`: `tw show T-xxx` の `difficulty`
   - `from`: 手順1で決めたとき

3a. **進み具合を5分おきに確かめる**: `Workflow` を起こした同じ応答で、背景の Bash（`run_in_background: true`・`timeout: 7200000`）に次を渡す。
   `<dir>` は `Workflow` の戻りの `Transcript dir`、`<前の行>` は前に知らせた行（初回は空）。5分おきに `journal.jsonl` の済んだ agent の数といま走っている役（label）を読み、前の行と変わったときだけ1行出して終わる:

   ```bash
   J='<dir>/journal.jsonl'; prev='<前の行>'
   while :; do sleep 300
     cur="済み $(grep -c '"type":"result"' "$J"; true)・いまの役 $(grep '"type":"started"' "$J" | tail -1 | sed -n 's/.*"label":"\([^"]*\)".*/\1/p')"
     [ "$cur" != "$prev" ] && { echo "$cur"; break; }
   done
   ```

   終わりの知らせで起きたら、出力の1行を自分の言葉で一言にして知らせ、同じ応答で `<前の行>` をその行にして打ち直す。agent の会話の中身（`agent-*.jsonl`）は読まない。
   `Monitor` は使わない（知らせでメインは起きるが、tsukumo はその知らせを新しい事実と数えず、知らせの一言を差し戻す）。`ScheduleWakeup` は `/loop` の中でしか使えない。

4. **完了の通知を受ける**: 先にプログレスバーの位置を確かめて `work_plan` を渡し（渡すまで作業のツールは拒まれる）、続けて手順3aの走っている確かめを `TaskStop` で止める。位置は `tw plan-phases T-xxx --through <走った最後の段>`
   （`振り返り`・`受け入れ`・`実装` のうち、ワークフロースクリプトが走らせた最後のもの）。`phaseSummary` は戻り値から書く。
   - 戻り値が `{ stopped: true, phase }`: 次の順で進める。委譲先の未コミットの変更が作業ツリーに残ったまま release すると、次の周の `tw claim` が `DIRTY` で止まるため、先に退避する（作業ツリーを触るのは自分の作業ツリーだけ）
     1. `git status --short` で未コミットの変更を見る
     2. あれば、新規ファイルを `git add -N <パス>` で入れ、`git diff` を作業ツリーの外の絶対パス（`${TMPDIR}` かスクラッチの `<ID>-stopped.patch`）へ保存する
     3. 保存を確かめてから、`git restore` で追跡済みの変更を戻し、新規ファイルを `git rm -q -f` で消す（`git status --short` が空になる）
     4. `tw relay T-xxx 再開 --body-file -` に `phase` と patch のパス（`git apply <パス>` で読み戻せる）を書く
     5. `tw release T-xxx` を打つ。`LEFTOVER` が出たら退避の漏れなので 2 へ戻る
     6. `workPlanClosing: stopped` の `report` を渡す
   - 通ったとき: 申し送りをワークフロースクリプトの戻り値から書く（形は `task-model` の `WORKFLOW.md`「段の申し送り」）。
     `tw relay T-xxx 実装` に `conventionLines`、`tw relay T-xxx 受け入れ` に `accept.judgment` の判定と `findings`（`- 指摘:`）・`visual`（`- 目視:`）・`notes`、
     `tw relay T-xxx 振り返り` に `retrospect.line`。ワークフロースクリプトが走らせなかった段の鍵は書かない。
     `### 作業先` が別のリポジトリなら、`task-workflow` の置き場の other-repo の「合流」（`tw lap` の囲みと `tw land <枝> --fold`、先頭語の表）を打ち、`LANDED` を確かめてから送り出しへ進む。続けて Skill ツールで `tsukumo-workflow:ship-step` に ID を渡して呼ぶ

5. **完了報告**は `task-workflow` と同じ決まり（完了報告の節。`/loop` の続行判断も同じ）で書く。
