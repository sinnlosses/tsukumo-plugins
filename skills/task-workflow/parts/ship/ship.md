# 送り出し

| 入力 | 返すもの | 守る関門 |
| --- | --- | --- |
| タスクの ID | `tw finish` の最後の行 | `tw ship` |

手順7（完了にしてコミットして送る）と手順8（送ったあとを読む）。`SKILL.md` の手順7・8から読まれる。
止まったときの表と `COMMITS_SINCE_CLAIM` の扱いは同じ `parts/ship/` にある。

7. **完了にしてコミットして送る**: `tw finish` が `tw done`・コミット・`tw ship` を順に打つ。
   `## 結果`（正典「結果の書き方と知見の置き場」。3行程度、自分のコミットのハッシュは書かない）を標準入力で渡す:

   ```bash
   tw finish T-xxx --reflection none --result-file - --message '<何をしたか>' <<'EOF'
   - 検証コマンド: 1204 pass / 0 fail（+9）
   - 規約: coding-standards「テスト」
   - 規約の指摘: 0
   - 振り返り: 兆候なし
   EOF
   ```

   台帳の `reflection` は `--reflection` が決める（`## 結果` の `- 振り返り:` の行は人向けの表示。`--reflection` を渡せば機械はこの行を見ない。無いときだけ従来どおりこの行から読む）。
   振り返りが兆候なしなら `none`、ドラフトを積んだか兆候に当たったなら `some`、振り返りを通らなかった（近道・並びに振り返りの段が無い）なら `skipped`。
   並びに振り返りの段が無く手順6aを通らなかったときは、`--reflection skipped` を打ち、`- 振り返り:` の行を `- 振り返り: 設定で外した` にする。

   完了条件に印 `[目視]` の項目があるときは 受け入れ役の `- 目視:` の行を写した `- 目視: <見比べた画像のパス> を <完了条件の行> と見比べて可` の1行を足す。

   **必須行**: `--dropped` でない `tw done`・`tw finish` は、`## 結果` に `- 検証コマンド:`・`- 規約:`・`- 規約の指摘:`・`- 振り返り:`
   （完了条件の項目の頭に印 `[目視]` があれば `- 目視:` も。語「目視」を含むだけでは要らない）の行が無いと、終了コード 2 で何も書かずに欠けた行の名前を出して拒む。近道でも同じ。
   `- 検証コマンド:` だけは、行が無く台帳の直近の `tw verify` が `VERIFIED`・`VERIFIED_UNSTAMPED` なら tw が `- 検証コマンド: 通過（直近の tw verify が通過）` と補う（件数を書くなら自分で書く）。
   `- 預けること:`・`- 取り下げた指摘:` は、あるときだけ書く。`--dropped` は理由だけでよい。

   返却は Beads に残らないので、規約の2行を `## 結果` に写す（欠けると上の検査が拒む）。
   実装役の最後の段の返却の `- 規約:` の行をそのまま `- 規約: <読んだ節の名前>`（読まなかったら `- 規約: なし`）として写し、
   受け入れ役の `- 指摘:` のうち `規約:` で始まる行を差し戻しの回をまたいで数えて `- 規約の指摘: <件数>` として写す。
   実装役の返却が無い経路（メインで行う `loopable: N` のタスク `flows/main.md`・近道 `flows/direct.md`）では `- 規約: 委譲なし` と書き、
   受け入れ役を通らない近道では `- 規約の指摘: なし` と書く。

   `dropped` にするときは `--dropped` を付け、理由を結果に書く。`DONE` は
   stage せず（`## 結果` は Beads の comment に入る）、コミットは作業のファイルだけ
   （振り返りのドラフトは Beads の課題なので入れない）。`--add` を省くと、作業ツリーの変更のうち通常のファイル
   （`.tw/local` と symlink は除く）が**個別に** `git add` され（`git add -A` は使わない）、**1コミット**になる。
   一覧が意図と違うときだけ `--add` をファイルごとに繰り返して明示する（空白・コンマで1つにまとめない）。
   件名の `T-xxx:` は tw が一度だけ付けるので、`--message` は `<何をしたか>` だけを渡す（旧形式の `T-xxx: <何をしたか>` を渡しても二重にならない）。
   末尾の署名は正典「コミットメッセージ」に従って `--message` に入れる。差分が無ければ
   `NO_COMMIT` を出してコミットせず、そのまま ship へ進む（`tw ship` が `NOTHING` を返して閉じる）。
   コミットのあとに作業ツリーへ残った変更は `UNADDED\t<パス>` の行で出る（`--add` で渡さなかったもの）。省略時に除いた symlink は
   `SKIPPED_LINK\t<パス>` で出て、作業ツリーに残るので続く `tw ship` が `DIRTY` で止まる（symlink は自動ではコミットしない。
   `.gitignore` に入れるか消すかを人が決める）。
   知見は `## 結果`・`## 注意`・正典の docs・新しいタスクへ置く。
   `tw finish` は `tw done`・コミット・`tw ship` の行をそのまま並べ、止まる行（`NOT_OWNER`・`CONFLICT` など）が出たら
   以降を打たず、`${CLAUDE_SKILL_DIR}/parts/ship/ship-stopped.md` の表で読む。`DONE` の次に `COMMITS_SINCE_CLAIM` が続いたら、`tw finish` はそこで（`tw done` は済んだまま）
   コミットも `tw ship` も打たず終了コード12で止まる。`${CLAUDE_SKILL_DIR}/parts/ship/commits-since-claim.md` に従って判断し、
   同じ `--message` で `tw finish T-xxx --resume --message '<何をしたか>'` を打って続ける（`--result-file` は要らない。
   `done` を飛ばして `RESUMED` を出し、コミット→`tw ship` まで進む）。`--resume` なしで `tw finish`・`tw done` を打ち直すと、
   `ALREADY_DONE`（終了コード4）で止まる。
   `tw finish` は終わりに、`start` だけで `end` の無い段を `OPEN_SPAN\tT-xxx\t<段>` の行で出す（出力のみ。終了コードは変わらない）。
   手順の打ち漏れの印なので、完了報告に写す。`sync` の start・end は `tw finish`／`tw ship` が自分で出す。

8. **送ったあとを読む**: 手順7の `tw finish`（`COMMITS_SINCE_CLAIM` で止まったあとは `tw finish --resume`）の出力の最後の行（`tw ship` の判定）で分ける。

   | 出力 | すること |
   | --- | --- |
   | `SHIPPED`・`NOTHING` | 完了。`kept=feature/T-xxx` が付いていたら、枝を消せなかったことを報告して人に預ける（消さない）。続く行に `NOT_CLOSED`・`TRACKER\tFAILED`・`BACKUP\tFAILED` があれば、行を添えて報告する（送れてはいる。`TRACKER` は `tw sync` で打ち直せる） |
   | それ以外 | `${CLAUDE_SKILL_DIR}/parts/ship/ship-stopped.md` の表に従う |

   `/loop` を続けるかは `${CLAUDE_SKILL_DIR}/completion-report.md` の「`/loop` の続行判断」で決める。push はしない。
