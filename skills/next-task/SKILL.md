---
name: next-task
description: "Beads に置いた未着手タスクを1件選び、tw コマンドで着手の印を立て、difficulty のモデルのサブエージェントに委譲し、受け入れてから作業を1コミットにして main へ送る。ユーザーが「次のタスクを進めて」「タスクをやって」と言ったとき、または /loop と組み合わせて全タスク完了までの自動進行に使う。ID を名指して「タスク GH-123 を進めて（bd show GH-123 で読める）」「T-001 をやって」のように頼まれたときも使う（選ばずにその ID で着手する）。タスクの一覧を見たいだけのとき・ID の無い雑談には使わない（一覧は list-tasks）。"
---

タスクを1サイクルぶん前に進めて終わる（全件進めたいときは `/loop /next-task`。続行/停止は `/loop` が
決める）。ルールは `task-workflow` スキルの `WORKFLOW.md`（以下「正典」）。`tw` は
`task-workflow` のタスク運用のコマンド（`tw-reference.md`「`tw` コマンドの参照」）。
**`tw` の代わりに `bd` や台帳を手で操作しない。**

## このプロジェクトの設定（`tw config`）

!`tw config 2>&1 || true`

タスクは Beads に置かれている（正典「Beads とトラッカー」）。`tracker` の行が
`github` なら、タスクID は Issue 番号の `GH-<n>` のことがある（`tw status` の1列目の形のまま使い、下の
`T-xxx` とコミットの件名の `T-xxx:` をそう読み替える）。
以下の**検証コマンド**は `verify` の行、**整形コマンド**は `format` の行の値に読み替える。`verify` が `なし` なら打たず、`## 完了条件`
だけで受け入れを判定してその旨を報告に書く。`format` が `なし` なら整形は打たない。
`MISSING` の行などで設定が読めないときは `task-workflow` スキルの `preflight.md` に従う。

## 手順

0. **依存を確かめる**: `task-workflow` スキルの `preflight.md` に従う。

1. **見渡す**: `tw status`（手順8のあとに取り直した `tw status` が同じ会話に残っていて、その後に状態を変える `tw` を打っていなければ打ち直さない。トラッカーが `jira`・`github` なら、先に `tw sync` で取り込む。
   `TRACKER\tFAILED` は報告に添えて先へ進む）。終了コードが 6 `MISSING`・1・3 なら、
   `${CLAUDE_SKILL_DIR}/start-stopped.md` の「手順1の表」に従って終了する。

1a. **週ごとの振り返り**: `tw status` の `---` の後ろに `retrospect_due` の行があれば、
   `${CLAUDE_SKILL_DIR}/weekly-retrospect.md` を読んで従う。行が無ければ何もしない

2. **取り残しを見る**: 行の `着手可否` が `CLAIMED` で、印の列の作業ツリー名が自分
   （`basename "$(git rev-parse --show-toplevel)"`）なら、`${CLAUDE_SKILL_DIR}/leftover-claim.md` を読んで従う。

3. **選ぶ**: `着手可否` が `READY` の行から1件。呼ばれ方と候補で次のとおり選ぶ:

   | 候補 | `/loop` から | ユーザーが直接呼んだとき |
   | --- | --- | --- |
   | `READY` で `loopable: Y` | 選ぶ | 選ぶ |
   | `READY` で `loopable: N` | 選ばない | 選んでよい（着手前に判断の要る点を確かめる） |
   | `HOLD` | 選ばない | ユーザーが ID か「hold を」と指したとき、または `READY` が無いときに、依存がすべて解決済み（正典「status と着手の印」）のものを選んでよい（選んだら手順3aへ） |
   | 頼み文に ID がある（「タスク GH-123 を進めて」の形も含む） | （頼み文は無い） | `READY` から選ばずにその ID を対象にする |
   | 候補が無い | `${CLAUDE_SKILL_DIR}/no-candidate.md` を読んで従う | 同左 |

3a. **`HOLD` を決める**（手順3で `HOLD` を選んだときだけ。**委譲せずメインで行う**）:
   `${CLAUDE_SKILL_DIR}/hold.md` を読んで従い、終わったら手順4へ進む。

4. **着手の印を立てる**: `tw claim T-xxx`。

   | 出力 | すること |
   | --- | --- |
   | `CLAIMED` | 次へ（手順5の前に `tw plan-check T-xxx` を打つ）。`branch=` が作業する枝。行末の `direct=Y`／`direct=N:<理由>` は委譲しない近道の印の判定（手順5の頭で使う。印が無ければ列が無い） |
   | `TAKEN`・`NOT_READY` | 先を越された・状態が変わった。手順1から別の1件を選び直す（3回続いたら止まる） |
   | `DIRTY`・`UNSHIPPED`・`INVALID`・`GIT_READ_ONLY` | `${CLAUDE_SKILL_DIR}/start-stopped.md` の「手順4の表の残り」に従って終了する |

4a. **作業ツリーを確かめる**: 検証コマンドの実行に要るもの（依存のインストール済みバイナリなど）と、
   組み立て済みの成果物が揃っているかをメインが確かめる（何を見るかはプロジェクトの CLAUDE.md の
   立ち上げの記述に従う）。無ければ委譲せず、人に立ち上げを頼んで `tw release T-xxx` する。
   作業先が別のリポジトリのときは、続けて `${CLAUDE_SKILL_DIR}/other-repo.md` の「委譲の前に本体の汚れを控える（手順4aのあと）」に従う。

5. **委譲する**: 小節は (a) 近道と `loopable: N`、(b) `plan-check` で形を決める、(c) 計画どおりに委譲する・書き直しから委譲する、
   (d) 委譲先の選び方と依頼、(e) 並列の段、(f) 無音の見張りの順に読む。
   委譲は `## やること` の段（`### <n>. <名前>`）を1段ずつ頼む。最初の委譲で段1を渡し、
   委譲先は段を1つ済ませるたびに返す。次の段は手順5bで、**同じ委譲先を `SendMessage` で再開して**渡す
   （新しい委譲先は起こさない。例外は `difficulty` を上げるときと手順5dの引き継ぎと、(e) の並列の段だけ）。
   最初の委譲を投げる直前に `tw lap T-xxx delegate start` を打つ（並列の段でも1回だけ）。
   投げた直後に (f) の見張りを起こす。

   **(a) 近道と `loopable: N`**: 手順4の `CLAIMED` の行末が `direct=Y` で `tw plan-check T-xxx` が `PLAN_REGISTERED` なら、
   委譲せず `${CLAUDE_SKILL_DIR}/direct-run.md` を読んで従う（手順5〜6bを飛ばし、`tw verify` → 手順7 → 8）。
   `loopable: N` のタスク（ユーザーが直接呼んだとき）は委譲せずメインで行う。
   `${CLAUDE_SKILL_DIR}/main-run.md` を読んで従う。

   **(b)〜(f)**: `${CLAUDE_SKILL_DIR}/delegate.md` を読んで従う（(b) `plan-check` で形を決める、(c) 計画どおりに委譲する・書き直しから委譲する、
   (d) 委譲先の選び方と依頼、(e) 並列の段、(f) 無音の見張り。手順5c の実装の依頼文もここ）。

5b. **返りを見る**（委譲先の hand-back か、Agent ツールの完了の通知が届くたびに）:
   `${CLAUDE_SKILL_DIR}/handback-read.md` を読んで従う（返却の1行目で分ける表と、`plan-check` の行を読む表）。

5c. **実装の依頼文**: 手順5(d)の依頼に足す項目は `${CLAUDE_SKILL_DIR}/delegate.md` の手順5c にある。

5d. **委譲先の無応答に対応する**（手順5で投げたあと、報告が来ないまま長く止まったとき。完了の通知が
   「背景の作業を待っている」で届いたときも。手順5(f)の見張りの `SILENT` の通知もここに含める）: `${CLAUDE_SKILL_DIR}/unresponsive.md` を読んで従う。

6. **受け入れる**: 作業ツリーを直し始める前に、委譲先の最後の段（`段 N/N`）の hand-back が届いていることを
   確かめる（並列の段の担当を起こしたときは、どの担当も最後に渡した段を返していることを確かめる）。
   確かめた直後に手順5(f)の見張りを `TaskStop` で畳み、`tw lap T-xxx delegate end` を打ち、作業ツリーを cwd にした残りのプロセスが無いかを `ps` で見て（あれば止める）、`tw accept T-xxx` を打つ（出力の読み方は下の `accept.md`）。
   届いていなければ `TaskStop` か `SendMessage` で止めてから触る。報告をそのまま信用しない。

   受け入れの続き（目視待ちの報告・完了条件の目視・`tw accept` の出力の表・受け入れ役の起こし方と判定の表・
   差し戻し・別のリポジトリのとき・`COMMITS_SINCE_CLAIM`）は `${CLAUDE_SKILL_DIR}/accept.md` を読んで従う。
   受け入れのあとの `tw verify-check` の出力は `${CLAUDE_SKILL_DIR}/verify-check.md` で読む。

6a. **振り返る**（`/loop` から回っているときも。委譲せずメインで行う）: `${CLAUDE_SKILL_DIR}/retro-step.md` を読んで従う。

6b. **合流する**（手順6で `tw verify` を背景で起こしたときだけ。`VERIFIED_SAME` で起こさなかったときは手順7へ）:
   `${CLAUDE_SKILL_DIR}/verify-merge.md` を読んで従う（背景の `tw verify` の完了を待ち、出力の最後の行で分ける表）。

7. **完了にしてコミットして送る**: `tw finish` が `tw done`・コミット・`tw ship` を順に打つ。
   `${CLAUDE_SKILL_DIR}/finish.md` の手順7を読んで従う（`tw finish` の打ち方・`## 結果` に写す規約の2行・`COMMITS_SINCE_CLAIM`・`OPEN_SPAN`）。

8. **送ったあとを読む**: 手順7の `tw finish` の出力の最後の行（`tw ship` の判定）で分ける。
   `${CLAUDE_SKILL_DIR}/finish.md` の手順8の表に従う（`SHIPPED`・`NOTHING` は完了、それ以外は `ship-stopped.md`）。push はしない。

## 完了報告のフォーマット

手順8のあと（手順8より前で止まったときも）、`${CLAUDE_SKILL_DIR}/completion-report.md` を読んで報告を書く。

**`/loop` の続行判断**: 続けるかは `${CLAUDE_SKILL_DIR}/completion-report.md` の「`/loop` の続行判断」で決める。
