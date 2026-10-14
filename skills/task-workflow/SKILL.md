---
name: task-workflow
description: "着手の印が立ったタスク1件を、委譲→受け入れ→振り返り→送り出しの段で主ブランチへ送るまで進めるオーケストレーター。入口のスキルが tw claim で印を立てたあと、Skill ツールでタスクの ID と tw claim の出力の行を渡して呼ぶ。段を並べ、lap を打ち、パーツ（実装役・受け入れ役・振り返り・送り出し）を名前で呼ぶ。印の無いタスクには使わない。"
user-invocable: false
---

着手の印が立ったタスク1件を、送り出して完了報告を書くまで進める。ルールは `task-model` スキルの
`WORKFLOW.md`（以下「正典」）。`tw` は `task-model` のタスク運用のコマンド（`tw-reference.md`「`tw` コマンドの参照」）。
**`tw` の代わりに `bd` や台帳を手で操作しない。**

呼ぶ側から受け取るもの: タスクID（`T-xxx`）、`tw claim` の `CLAIMED` の行（`branch=` と行末の `direct=` の印）、
呼ばれ方（`/loop` からか、ユーザーが直接呼んだか）と、`/loop` から回っているならその依頼文。
手順の番号は入口の手順0〜4に続けて 4a から振る（副ファイルの「手順6」などはこの番号を指す）。

段の並び（既定の流れ）: 4a 作業ツリーを確かめる → 5 委譲（パーツ「実装役」`parts.implementer`）→ 6 受け入れ（パーツ「受け入れ役」`parts.acceptor`）→
6a 振り返り（パーツ「振り返り」`parts.retrospect`）→ 6b 合流 → 7・8 送り出し（パーツ「送り出し」`parts.ship`）→ 完了報告。
流れの変種は `flows/`（近道 `direct.md`・メインで行う `main.md`・`HOLD` を決める `hold.md`）、
パーツの契約と既定の依頼文は `parts/`（`implementer.md`・`acceptor.md`・`retrospect.md`・`ship.md`）にある。
lap はパーツではなくこのスキルが段の前後で打つ。

## このプロジェクトの設定（`tw config`）

!`tw config 2>&1 || true`

`tracker` の行が `github` なら、タスクID は Issue 番号の `GH-<n>` のことがある（下の `T-xxx` とコミットの件名の
`T-xxx:` をそう読み替える）。
以下の**検証コマンド**は `verify` の行、**整形コマンド**は `format` の行の値に読み替える。`verify` が `なし` なら打たず、`## 完了条件`
だけで受け入れを判定してその旨を報告に書く。`format` が `なし` なら整形は打たない。
`MISSING` の行などで設定が読めないときは `task-model` スキルの `preflight.md` に従う。

**流れとパーツの選び**: `flow` の行で流れを決める（`delegate` は下の手順のとおり、`main` は手順5(a)）。
`parts.<枠>` の行の値で各段のパーツを呼ぶ。`既定` は同梱のパーツを一覧の末尾一致で探す（選び方は各パーツの副ファイル。
末尾一致が複数当たったら選ばずに止め、`tw config set parts.<枠> <全名>` で選ぶよう人に案内して `tw release T-xxx` する）。
それ以外の値は全名で、一覧（Agent ツールのエージェントか、スキル）に出ている名前と完全一致で呼ぶ。一覧に無ければ
既定のパーツや `general-purpose` に代えず、行を添えて人に預け `tw release T-xxx` する。`なし` は `parts.retrospect` だけが取り、手順6aを飛ばす。

## 手順

`HOLD` のタスクを渡されたとき（印はまだ立っていない）は、先に `${CLAUDE_SKILL_DIR}/flows/hold.md` を読んで従う。

4a. **作業ツリーを確かめる**: 検証コマンドの実行に要るもの（依存のインストール済みバイナリなど）と、
   組み立て済みの成果物が揃っているかをメインが確かめる（何を見るかはプロジェクトの CLAUDE.md の
   立ち上げの記述に従う）。無ければ委譲せず、人に立ち上げを頼んで `tw release T-xxx` する。
   作業先が別のリポジトリのときは、続けて `${CLAUDE_SKILL_DIR}/other-repo.md` の「委譲の前に本体の汚れを控える（手順4aのあと）」に従う。
   続けて `tw plan-check T-xxx` を打つ（手順5で使う）。

5. **委譲する**（パーツ「実装役」。契約は `${CLAUDE_SKILL_DIR}/parts/implementer.md`）: 小節は (a) 近道と `loopable: N`、(b) `plan-check` で形を決める、(c) 計画どおりに委譲する・書き直しから委譲する、
   (d) 委譲先の選び方と依頼、(e) 並列の段、(f) 無音の見張りの順に読む。
   委譲は `## やること` の段（`### <n>. <名前>`）を1段ずつ頼む。最初の委譲で段1を渡し、
   委譲先は段を1つ済ませるたびに返す。次の段は手順5bで、**同じ委譲先を `SendMessage` で再開して**渡す
   （新しい委譲先は起こさない。例外は `difficulty` を上げるときと手順5dの引き継ぎと、(e) の並列の段だけ）。
   最初の委譲を投げる直前に `tw lap T-xxx delegate start` を打つ（並列の段でも1回だけ）。
   投げた直後に (f) の見張りを起こす。

   **(a) 近道と `loopable: N`**: `CLAIMED` の行末が `direct=Y` で `tw plan-check T-xxx` が `PLAN_REGISTERED` なら、
   委譲せず `${CLAUDE_SKILL_DIR}/flows/direct.md` を読んで従う（手順5〜6bを飛ばし、`tw verify` → 手順7 → 8）。
   `loopable: N` のタスク（ユーザーが直接呼んだとき）と、`flow` の行が `main` のときのタスク（近道に当たらないものすべて）は委譲せずメインで行う。
   `${CLAUDE_SKILL_DIR}/flows/main.md` を読んで従う。

   **(b)〜(f)**: `${CLAUDE_SKILL_DIR}/parts/implementer/delegate.md` を読んで従う（(b) `plan-check` で形を決める、(c) 計画どおりに委譲する・書き直しから委譲する、
   (d) 委譲先の選び方と依頼、(e) 並列の段、(f) 無音の見張り。手順5c の実装の依頼文もここ）。

5b. **返りを見る**（委譲先の hand-back か、Agent ツールの完了の通知が届くたびに）:
   `${CLAUDE_SKILL_DIR}/parts/implementer/handback-read.md` を読んで従う（返却の1行目で分ける表と、`plan-check` の行を読む表）。

5c. **実装の依頼文**: 手順5(d)の依頼に足す項目は `${CLAUDE_SKILL_DIR}/parts/implementer/delegate.md` の手順5c にある。

5d. **委譲先の無応答に対応する**（手順5で投げたあと、報告が来ないまま長く止まったとき。完了の通知が
   「背景の作業を待っている」で届いたときも。手順5(f)の見張りの `SILENT` の通知もここに含める）: `${CLAUDE_SKILL_DIR}/parts/implementer/unresponsive.md` を読んで従う。

6. **受け入れる**（パーツ「受け入れ役」。契約は `${CLAUDE_SKILL_DIR}/parts/acceptor.md`）: 作業ツリーを直し始める前に、委譲先の最後の段（`段 N/N`）の hand-back が届いていることを
   確かめる（並列の段の担当を起こしたときは、どの担当も最後に渡した段を返していることを確かめる）。
   確かめた直後に手順5(f)の見張りを `TaskStop` で畳み、`tw lap T-xxx delegate end` を打ち、作業ツリーを cwd にした残りのプロセスが無いかを `ps` で見て（あれば止める）、`tw accept T-xxx` を打つ（出力の読み方は下の `accept.md`）。
   届いていなければ `TaskStop` か `SendMessage` で止めてから触る。報告をそのまま信用しない。

   受け入れの続き（目視待ちの報告・完了条件の目視・`tw accept` の出力の表・受け入れ役の起こし方と判定の表・
   差し戻し・別のリポジトリのとき・`COMMITS_SINCE_CLAIM`）は `${CLAUDE_SKILL_DIR}/parts/acceptor/accept.md` を読んで従う。
   受け入れのあとの `tw verify-check` の出力は `${CLAUDE_SKILL_DIR}/parts/acceptor/verify-check.md` で読む。

6a. **振り返る**（パーツ「振り返り」。`/loop` から回っているときも。委譲せずメインで行う）: `${CLAUDE_SKILL_DIR}/parts/retrospect.md` を読んで従う。
   `parts.retrospect` の行が `なし` なら、`tw lap T-xxx retro` を打たずに飛ばし、`## 結果` の行を `- 振り返り: 設定で外した` にする。

6b. **合流する**（手順6で `tw verify` を背景で起こしたときだけ。`VERIFIED_SAME` で起こさなかったときは手順7へ）:
   `${CLAUDE_SKILL_DIR}/parts/acceptor/verify-merge.md` を読んで従う（背景の `tw verify` の完了を待ち、出力の最後の行で分ける表）。

7. **完了にしてコミットして送る**（パーツ「送り出し」）: `tw finish` が `tw done`・コミット・`tw ship` を順に打つ。
   `${CLAUDE_SKILL_DIR}/parts/ship.md` の手順7を読んで従う（`tw finish` の打ち方・`## 結果` に写す規約の2行・`COMMITS_SINCE_CLAIM`・`OPEN_SPAN`。
   `parts.ship` が全名のときの呼び方もここ）。

8. **送ったあとを読む**: 手順7の `tw finish` の出力の最後の行（`tw ship` の判定）で分ける。
   `${CLAUDE_SKILL_DIR}/parts/ship.md` の手順8の表に従う（`SHIPPED`・`NOTHING` は完了、それ以外は `parts/ship/ship-stopped.md`）。push はしない。

## 完了報告のフォーマット

手順8のあと（手順8より前で止まったときも）、`${CLAUDE_SKILL_DIR}/completion-report.md` を読んで報告を書く。

**`/loop` の続行判断**: 続けるかは `${CLAUDE_SKILL_DIR}/completion-report.md` の「`/loop` の続行判断」で決める。
