---
name: next-task
description: "Beads に置いた未着手タスクを1件選び、tw コマンドで着手の印を立て、task-workflow（difficulty に応じたモデルのサブエージェントに委譲し、受け入れてから作業を1コミットにして main へ送る流れ）に渡す。ユーザーが「次のタスクを進めて」「タスクをやって」と言ったとき、または /loop と組み合わせて全タスク完了までの自動進行に使う。ID を名指して「タスク GH-123 を進めて（bd show GH-123 で読める）」「T-001 をやって」のように頼まれたときも使う（選ばずにその ID で着手する）。「不具合修正のどれか」「表示まわりを1つ」のように、ID は無いが対象の種類・範囲を言って頼まれたときも使う（`READY` を頼み文の語と種類で `tw find` に絞らせて1件に決める）。タスクの一覧を見たいだけのとき・対象の種類を言わない雑談には使わない（一覧は list-tasks）。"
metadata:
  workflow-entry: "true"
  workflow-entry-title: "タスクの実行"
---

タスクを1件に決めて着手の印を立て、`task-workflow` に渡して1サイクルぶん前に進める（全件進めたいときは
`/loop /next-task`。続行/停止は `/loop` が決める）。ルールは `task-model` スキルの `WORKFLOW.md`（以下「正典」）。`tw` は
`task-model` のタスク運用のコマンド（`tw-reference.md`「`tw` コマンドの参照」）。
**`tw` の代わりに `bd` や台帳を手で操作しない。**

## このプロジェクトの設定（`tw config`）

!`tw config 2>&1 || true`

タスクは Beads に置かれている（正典「Beads とトラッカー」）。`tracker` の行が
`github` なら、タスクID は Issue 番号の `GH-<n>` のことがある（`tw status` の1列目の形のまま使い、下の
`T-xxx` をそう読み替える）。
`MISSING` の行などで設定が読めないときは `task-model` スキルの `preflight.md` に従う。

## 手順

0. **依存を確かめる**: `task-model` スキルの `preflight.md` に従う。

1. **見渡す**: `tw status`（取り直した `tw status` が会話に残っていて、その後に状態を変える `tw` を打っていなければ打ち直さない。トラッカーが `jira`・`github` なら先に `tw sync`。
   `TRACKER\tFAILED` は報告に添えて先へ進む）。終了コードが 6 `MISSING`・1・3 なら、
   `${CLAUDE_SKILL_DIR}/start-stopped.md` の「手順1の表」に従って終了する。

1a. **週ごとの振り返り**: `retrospect_due` の行（`tw status` の `---` の後ろ）があれば、
   `${CLAUDE_SKILL_DIR}/weekly-retrospect.md` を読んで従う

1b. **`/loop` の停止規則**: `/loop` から呼ばれたときだけ `tw loop-check` を打つ。`STOP` なら着手せず、理由・残りの `READY` の数・`SHIPPED_TODAY` の送った一覧を報告して終える。

2. **取り残しを見る**: 行の `着手可否` が `CLAIMED` で、印の列の作業ツリー名が自分
   （`basename "$(git rev-parse --show-toplevel)"`）なら、`${CLAUDE_SKILL_DIR}/leftover-claim.md` を読んで従う。

3. **選ぶ**: `着手可否` が `READY` の行から1件。呼ばれ方と候補で次のとおり選ぶ:

   | 候補 | `/loop` から | ユーザーが直接呼んだとき |
   | --- | --- | --- |
   | `READY` で `loopable: Y` | 選ぶ | 選ぶ |
   | `READY` で `loopable: N` | 選ばない | 選んでよい（着手前に判断の要る点を確かめる） |
   | `HOLD` | 選ばない | ユーザーが ID か「hold を」と指したとき、または `READY` が無いときに、依存がすべて解決済み（正典「status と着手の印」）のものを選んでよい（選んだら手順4を飛ばし、印を立てずに手順5で `HOLD` と添えて渡す） |
   | 頼み文に ID がある（「タスク GH-123 を進めて」の形も含む） | （頼み文は無い） | `READY` から選ばずにその ID を対象にする |
   | 頼み文に ID は無いが、対象の種類・範囲を言う語がある（「不具合修正のどれか」「表示まわりを1つ」） | （頼み文は無い） | 下の「頼み文の語で絞るとき」に従う |
   | 候補が無い | `${CLAUDE_SKILL_DIR}/no-candidate.md` を読んで従う | 同左 |

   **頼み文の語で絞るとき**（ユーザーが直接呼んだときだけ）:

   頼み文から次の2つを取り出して `tw find [<語>…] [--kind <種類>]` を打つ（当て方と件数は `tw find` が決める。正典「kind（種類）」）。

   - 種類: 頼み文が正典「kind（種類）」の語彙表の行に当たるとき（「不具合修正」なら `bug`）だけ `--kind` に渡す
   - 語: 対象・範囲を言う語（「表示まわり」なら `表示`）。言い換えも並べてよい（どれか1つに当たれば当たり）。「どれか」「1つ」のように対象を言わない語は渡さない

   | 先頭語 | すること |
   | --- | --- |
   | `FOUND` | 2列目の ID で手順4へ（3列目の `loopable` が `N` なら上の表のとおり着手前に確かめる） |
   | `MANY` | 続く `CANDIDATE` の行（ID・`loopable`・summary）を `AskUserQuestion` で見せて聞く。`MORE` の行があれば「ほかにもある」と添える |
   | `NONE` | `${CLAUDE_SKILL_DIR}/no-candidate.md` を読んで従う。別のタスクへ勝手に切り替えない |

4. **着手の印を立てる**: `tw claim T-xxx`。

   | 出力 | すること |
   | --- | --- |
   | `CLAIMED` | 手順5へ。`branch=` が作業する枝。行末の `direct=Y`／`direct=N:<理由>` は委譲しない近道の印の判定（印が無ければ列が無い） |
   | `TAKEN`・`NOT_READY` | 先を越された・状態が変わった。手順1から別の1件を選び直す（3回続いたら止まる） |
   | `DIRTY`・`UNSHIPPED`・`INVALID`・`GIT_READ_ONLY` | `${CLAUDE_SKILL_DIR}/start-stopped.md` の「手順4の表の残り」に従って終了する |

5. **`task-workflow` を呼ぶ**: Skill ツールの一覧で末尾が `task-workflow` の行（リンクで入れたとき `task-workflow`、
   plugin で入れたとき `tsukumo-workflow:task-workflow`。一覧に出ている名前のまま使う）を呼び、次を渡す:
   タスクID、手順4の `CLAIMED` の行（`HOLD` を選んだときは `HOLD`）、呼ばれ方（`/loop` か直接か）、
   `/loop` ならその依頼文。ここから先（送り出し・完了報告・`/loop` の続行判断）は `task-workflow` に従う。

## 手順5より前で止まったとき

手順1〜4の表が終了とした行で止まったら、止まったことと先頭語を1文目に置き、表が「添える」としたもの
（`git status --short`・`git log` の行・理由・ID）を続けて報告する。`/loop` から回っていても続行しない。
