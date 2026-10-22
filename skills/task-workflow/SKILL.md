---
name: task-workflow
description: "着手の印が立ったタスク1件を、設定の並び（既定は委譲→受け入れ→振り返り→送り出し）の段で主ブランチへ送るまで進めるオーケストレーター。入口のスキルが tw claim で印を立てたあと、Skill ツールでタスクの ID と tw claim の出力の行を渡して呼ぶ。段を並べ、lap を打ち、並びのスキル（既定は実装・受け入れ・振り返り・送り出し）を名前で呼ぶ。印の無いタスクには使わない。"
user-invocable: false
---

着手の印が立ったタスク1件を、送り出して完了報告を書くまで進める。ルールは `task-model` スキルの
`WORKFLOW.md`（以下「正典」）。`tw` は `task-model` のタスク運用のコマンド（`tw-reference.md`「`tw` コマンドの参照」）。
**`tw` の代わりに `bd` や台帳を手で操作しない。**

呼ぶ側から受け取るもの: タスクID（`T-xxx`）、`tw claim` の `CLAIMED` の行（`branch=` と行末の `direct=` の印）、
呼ばれ方（`/loop` からか、ユーザーが直接呼んだか）と、`/loop` から回っているならその依頼文。
手順の番号は入口の手順0〜4に続けて 4a から振る（副ファイルの「手順6」などはこの番号を指す）。

段の並び: 4a 作業ツリーを確かめる → 4b `tw config` の `steps` の行の並びのスキルを順に呼ぶ → 完了報告。
既定の並びの各スキルの本文は手順5〜8で、5 委譲（implement-step）→ 6 受け入れ（accept-step）→ 6a 振り返り・6b 合流（retrospect-step）→ 7・8 送り出し（ship-step）。
流れの変種は `flows/`（近道 `direct.md`・メインで行う `main.md`・`HOLD` を決める `hold.md`）、
各段の手順の副ファイルは `parts/implementer/`・`parts/acceptor/`・`parts/retrospect/`・`parts/ship/` にある。
lap は並びのスキルではなく、このスキル（オーケストレーター）と `tw` と hook が段の前後で書く（段ごとに誰が書くかは task-model の `tw-reference.md`「hook の関門」節の lap の段落）。

## このプロジェクトの設定（`tw config`）

!`tw config 2>&1 || true`

`tracker` の行が `github` なら、タスクID は Issue 番号の `GH-<n>` のことがある（下の `T-xxx` とコミットの件名の
`T-xxx:` をそう読み替える）。
以下の**検証コマンド**は `verify` の行、**整形コマンド**は `format` の行の値に読み替える。`verify` が `なし` なら打たず、`## 完了条件`
だけで受け入れを判定してその旨を報告に書く。`format` が `なし` なら整形は打たない。
`MISSING` の行などで設定が読めないときは `task-model` スキルの `preflight.md` に従う。

**並びの読み分け**: `steps` の行の値（1行の JSON の配列）で決める。空でない配列なら、第3列が `config` でも `default` でも手順4bで並びを順に呼ぶ。
`[]`（`tw` が plugin のチェックアウトの外にあり、既定の並びが取れない）なら、手順4bを飛ばして手順5〜8をこの順に進める。
流れは `loopable: N` のタスクが手順5(a)のメインで行う流れに、近道の印のタスクが `flows/direct.md` に分かれ、ほかは委譲する流れ。
振り返りを省くときは、並び（`tw config set steps`）から `retrospect-step` を外す。

## 手順

`HOLD` のタスクを渡されたとき（印はまだ立っていない）は、先に `${CLAUDE_SKILL_DIR}/flows/hold.md` を読んで従う。

4a. **作業ツリーを確かめる**: 検証コマンドの実行に要るもの（依存のインストール済みバイナリなど）と、
   組み立て済みの成果物が揃っているかをメインが確かめる（何を見るかはプロジェクトの CLAUDE.md の
   立ち上げの記述に従う）。無ければ委譲せず、人に立ち上げを頼んで `tw release T-xxx` する。
   続けて `tw plan-check T-xxx` を打つ（手順5で使う）。

4b. **並びを進める**（`steps` の値が空でない配列のとき。`[]` なら手順5へ）:
   1. 始める段を、段の申し送り `tw relay T-xxx 再開` で決める（鍵は正典「段の申し送り」の表）。`NONE` なら並びの最初の段から始める。
      本文の全名が並びにあればその段から始め、それより前の段は呼ばない。並びに無ければ選ばずに止め、行を添えて人に預け
      `tw release T-xxx` する（人は `tw claim T-xxx` のあと `tw relay T-xxx 再開 --body-file -` に空の本文を渡して消すか、`tw config set steps` で並びを直す）
   2. 段ごとに、呼ぶ直前に `tw relay T-xxx 再開 --body-file -` へその段の全名を書く（段は `stopped` の前に `tw release` を打つことがあり、
      止まったあとでは印の持ち主でなくなって書けないため）。続けて Skill ツールでその全名を呼び、引数にタスクの ID だけを渡す。
      一覧に無い名前は、ほかのスキルに代えず、行を添えて人に預け `tw release T-xxx` する
   3. 段の結果（`finished`・`skipped`・`stopped`）は、呼んだスキルの「返すもの」のとおりに読む。`finished`・`skipped` なら次の段へ進む
      （次の段の直前の書き込みが `再開` を上書きする）。`stopped` なら後の段を呼ばず、`再開` をそのまま残して完了報告へ進む
   4. 並びの最後の段が `finished`・`skipped` で済んだら、`tw relay T-xxx 再開 --body-file -` に空の本文を渡して消し、完了報告へ進む。
      `tw finish` のあとで印が無く `NOT_OWNER` が返ったら、タスクは閉じているので放っておく

   再開したときは、最初の `work_plan` を再開する段より前の段を済ませた位置（`--through <手前の段の帯の最後の段>`。手前に帯の段が無ければ省く）で呼ぶ。
   前の会話で飛ばした段の印は持ち越さない（`--skipped` は空から始める）。

5. **委譲する**（並びの段 implement-step の本文）: 小節は (a) 近道と `loopable: N`、(b) `plan-check` で形を決める、(c) 計画どおりに委譲する・書き直しから委譲する、
   (d) 委譲先の選び方と依頼、(e) 並列の段、(f) 無音の見張りの順に読む。
   委譲は `## やること` の段（`### <n>. <名前>`）を1段ずつ頼む。最初の委譲で段1を渡し、
   委譲先は段を1つ済ませるたびに返す。次の段は手順5bで、**同じ委譲先を `SendMessage` で再開して**渡す
   （新しい委譲先は起こさない。例外は `difficulty` を上げるときと手順5dの引き継ぎと、(e) の並列の段だけ）。
   委譲の lap（`delegate` の start）は、plugin で入れたとき（スキルの一覧の名前が `tsukumo-workflow:` で始まる）は
   `hooks/hooks.json` の `tw lap-hook` が委譲先の起動（`SubagentStart`）で書くので打たない。
   リンクだけで入れたとき（`hooks/hooks.json` が読まれない）だけ、最初の委譲を投げる直前に `tw lap T-xxx delegate start` を打つ（並列の段でも1回だけ）。
   hook と重なって打っても、所要の集計は二重に数えない。end は手順6の受け入れの頭の `accept` の start で `tw` が書く。
   投げた直後に (f) の見張りを起こす。

   **(a) 近道と `loopable: N`**: `CLAIMED` の行末が `direct=Y` で `tw plan-check T-xxx` が `PLAN_REGISTERED` なら、
   委譲せず `${CLAUDE_SKILL_DIR}/flows/direct.md` を読んで従う（委譲・受け入れ・振り返りを通らず、直す → `tw verify` → 手順7 → 8）。
   `loopable: N` のタスク（ユーザーが直接呼んだとき）は委譲せずメインで行う。
   `${CLAUDE_SKILL_DIR}/flows/main.md` を読んで従う。

   **(b)〜(f)**: `${CLAUDE_SKILL_DIR}/parts/implementer/delegate.md` を読んで従う（(b) `plan-check` で形を決める、(c) 計画どおりに委譲する・書き直しから委譲する、
   (d) 委譲先の選び方と依頼、(e) 並列の段、(f) 無音の見張り。手順5c の実装の依頼文もここ）。

5b. **返りを見る**（委譲先の hand-back か、Agent ツールの完了の通知が届くたびに）:
   `${CLAUDE_SKILL_DIR}/parts/implementer/handback-read.md` を読んで従う（返却の種類で分ける表と、`plan-check` の行を読む表）。
   `steps` が `[]` の経路（implement-step を呼ばない）では、`tw accept` の前に申し送り `実装` を書く（形は task-model の WORKFLOW.md「段の申し送り」の「鍵 `実装` の書き方と読み方」）。

5c. **実装の依頼文**: 依頼文は `tw brief implementer T-xxx` が出し、場合分けは `${CLAUDE_SKILL_DIR}/parts/implementer/delegate.md` の手順5c にある。

5d. **委譲先の無応答に対応する**（手順5で投げたあと、報告が来ないまま長く止まったとき。完了の通知が
   「背景の作業を待っている」で届いたときも。手順5(f)の見張りの `SILENT` の通知もここに含める）: `${CLAUDE_SKILL_DIR}/parts/implementer/unresponsive.md` を読んで従う。

6. **受け入れる**（並びの段 accept-step の本文。受け入れ役の返却の行は `${CLAUDE_SKILL_DIR}/parts/acceptor/acceptor-return.md`）: 作業ツリーを直し始める前に、委譲先の最後の段（段 `N`）の hand-back が届いていることを
   確かめる（並列の段の担当を起こしたときは、どの担当も最後に渡した段を返していることを確かめる）。
   確かめた直後に手順5(f)の見張りを `TaskStop` で畳み、作業ツリーを cwd にした残りのプロセスが無いかを `ps` で見て（あれば止める）、`tw accept T-xxx` を打つ（出力の読み方は下の `accept.md`。`tw accept` が打つ `accept` の start が、開いた `delegate` を閉じるので `delegate end` は打たない。段が2つ以上の計画で途中の段の控えが欠ければ、`tw accept` は lap を書く前に `NOT_LAST_STEP` で止まる）。
   届いていなければ `TaskStop` か `SendMessage` で止めてから触る。報告をそのまま信用しない。

   受け入れの続き（目視待ちの報告・完了条件の目視・`tw accept` の出力の表・受け入れ役の起こし方と判定の表・
   差し戻し・別のリポジトリのとき・`COMMITS_SINCE_CLAIM`）は `${CLAUDE_SKILL_DIR}/parts/acceptor/accept.md` を読んで従う。
   受け入れのあとの `tw verify-check` の出力は `${CLAUDE_SKILL_DIR}/parts/acceptor/verify-check.md` で読む。

6a. **振り返る**（並びの段 retrospect-step の本文。`/loop` から回っているときも。委譲せずメインで行う）: `${CLAUDE_SKILL_DIR}/parts/retrospect/retrospect.md` を読んで従う。
   並びに `retrospect-step` が無ければ、この手順を通らず（`tw lap T-xxx retro` も打たず）、`tw finish` に `--reflection skipped` を打ち、`## 結果` の行は `- 振り返り: 設定で外した` にする。

6b. **合流する**（手順6で `tw verify` を背景で起こしたときだけ。`VERIFIED_SAME` で起こさなかったときは手順7へ）:
   `${CLAUDE_SKILL_DIR}/parts/acceptor/verify-merge.md` を読んで従う（背景の `tw verify` の完了を待ち、出力の最後の行で分ける表）。

7. **完了にしてコミットして送る**（並びの段 ship-step の本文）: `tw finish` が `tw done`・コミット・`tw ship` を順に打つ。
   `${CLAUDE_SKILL_DIR}/parts/ship/ship.md` の手順7を読んで従う（`tw finish` の打ち方・`## 結果` の必須行・`COMMITS_SINCE_CLAIM`・`OPEN_SPAN`）。

8. **送ったあとを読む**: 手順7の `tw finish` の出力の最後の行（`tw ship` の判定）で分ける。
   `${CLAUDE_SKILL_DIR}/parts/ship/ship.md` の手順8の表に従う（`SHIPPED`・`NOTHING` は完了、それ以外は `parts/ship/ship-stopped.md`）。push はしない。

## 進み具合の帯（`work_plan`）

tsukumo で動かしているとき（`work_plan` の口があるとき）、帯に出す段の並びと位置は **`tw plan-phases T-xxx` が出し、メインは写すだけ**
（段の名前も位置の数え方もメインが作らない）。出力は `PHASES\tT-xxx\t<JSON 1行>` で、JSON のキー（`phases`・`current`・`finishedInGroup`・
あれば `delegatedRange`・`skippedPhases`）は `work_plan` の引数の名前そのまま。第3列の JSON をそのまま渡し、`phaseSummary` だけメインが足す。
`current` は並列の段の配列を1要素に数える（`tw` の出力に従い、自分で数え直さない）。

実装のあとの段は、`tw plan-phases` が `steps` の並びのスキルから組む。既定の並びなら「受け入れ」（手順6）・「振り返り」（手順6a・6b）・「送り出し」（手順7・8）の3段で、
並びから `retrospect-step` を外したときは「振り返り」が消えて2段になる。並びから外したスキルの段は帯に出ず、既定の4つ以外のスキルは、その名前（全名の `:` より後ろ）の段として並びの位置に入る。
委譲する流れと `main` の流れは、計画の段（必要なときだけ）・`## やること` の段・そのあとの段の並びで、`main` の流れには `delegatedRange` が付かない。
近道（`flows/direct.md`）は実装の段が「直す」「検証」になり、「受け入れ」「振り返り」が消える（既定の並びなら「直す」「検証」「送り出し」の3段）。

位置は `--through <段>`（その段までと、それ以前を済ませた位置。段は `計画`・`実装`（全段）・`受け入れ`・`振り返り`・`直す`・`検証`・段の番号・既定の4つ以外のスキルの段の名前）で渡す。
並列の段が順番どおりに返らなかったときだけ `--also <n,…>`（済んだ段の番号）を足す。省略は何も済んでいない位置。
メインで走る段が `skipped` を返し、その段が帯にあるときは、以後の呼び出しで毎回その段の名前を `--skipped <段,…>` に並べて渡す（飛ばした段をすべて。出力の `skippedPhases` がそのまま `work_plan` に渡る）。
帯に無い段（近道の受け入れ・振り返り、並びから外した振り返り）の `skipped` は渡さない。

`work_plan` は**委譲先の返却が届くたびに呼ぶ**（tsukumo の関門が、メインの `work_plan` が受け付けられるまで作業のツールを拒むため）。
返却（`phase_result`、無い環境では渡した段）で帯の位置が進むので、位置は返却で進んだ位置と同じになる。`phase_result` は要約を持たないので、
返却で段が済んだあとの `work_plan` には毎回、返却の本文から段のまとめを `phaseSummary` に書く（省けない）:

| 切れ目 | 呼び方 | `phaseSummary` |
| --- | --- | --- |
| 手順4a の `plan-check` のあと、最初の委譲の前 | `tw plan-phases T-xxx` | 不要（済んだ段が無い） |
| 手順5b: 計画だけの回の返却を受けた直後（`plan-check` の前） | 返却の2行目の `PHASES` の行（委譲先が計画を書いたあとで打った `tw plan-phases T-xxx --through 計画` の出力の写し）の JSON をそのまま渡す。メインが段の名前を作らず、`tw` も打たない | 要 |
| 手順5b: 段 `n` の返却（`n < N`）。差し戻して同じ段をやり直させるときも | `--through n`（順不同の並列なら `--also`）。やり直しは同じ位置 | 要 |
| 手順5b: 最後の段 `N` の返却 | `--through 実装` | 要 |
| 手順6: 受け入れの判定が済み、手順6a へ進む前（手順6a が無ければ手順7へ進む前） | `--through 受け入れ` | 要 |
| 手順6a・6b が済み、手順7の `tw finish` を打つ前 | `--through 振り返り`（「振り返り」が無い設定では呼ばない。手順6の `--through 受け入れ` で送り出しだけが残っている） | 要 |
| 手順4b: 既定の4つ以外のスキルの段が `finished`・`skipped` で済んだあと、次の段を呼ぶ前 | `--through <その段の名前>`（`skipped` なら `--skipped` にも足す） | 要（`skipped` なら飛ばしたわけ） |
| 近道（`flows/direct.md`）: 手順3の `tw verify` の前 | `--through 直す` | 要 |
| 近道: `tw verify` が通ったあと、手順7の前 | `--through 検証` | 要 |

tsukumo の委譲ゲートは、返却が届いた直後の `work_plan` が受け付けられるまで Bash を拒む。計画だけの回の返却の直後は、
段の名前がまだ一度も `work_plan` に渡っていないので、上の表の行のとおり返却の2行目の `PHASES` の行を写して渡す（メインは名前を作らない）。

`tw accept` を打つ前は呼ばない（最後の段の返却で「受け入れ」が今の段になっている）。`main` の流れは返却が無いので、`## やること` の段を終えるたびに
`--through n` を呼び（最後の段は `--through 実装`）、そのあとは委譲する流れの手順6以降の行に従う。
どの流れでも、締めの `report`（`workPlanClosing: finished`）の前に、帯の残りが「送り出し」の1段だけになっている位置にしておく
（残りが2段以上のままの `finished` は tsukumo が差し戻す）。`tw plan-phases` が `NO_PHASE` を返したら指定を直す
（全部済みにする指定は渡せない。最後の段を閉じるのは `report` の `workPlanClosing`）。

## 完了報告のフォーマット

手順8のあと（手順8より前で止まったときも）、`${CLAUDE_SKILL_DIR}/completion-report.md` を読んで報告を書く。

**`/loop` の続行判断**: 続けるかは `${CLAUDE_SKILL_DIR}/completion-report.md` の「`/loop` の続行判断」で決める。
