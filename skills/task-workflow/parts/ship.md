# 送り出し

| 入力 | 返すもの | 守る関門 |
| --- | --- | --- |
| タスクの ID | `tw finish` の最後の行 | `tw ship` |

手順7（完了にしてコミットして送る）と手順8（送ったあとを読む）。`SKILL.md` の手順7・8から読まれる。
止まったときの表と `COMMITS_SINCE_CLAIM` の扱いは `parts/ship/` にある。

7. **完了にしてコミットして送る**: `tw finish` が `tw done`・コミット・`tw ship` を順に打つ。
   `## 結果`（正典「結果の書き方と知見の置き場」。3行程度、自分のコミットのハッシュは書かない）を標準入力で渡す:

   ```bash
   tw finish T-xxx --result-file - --message 'T-xxx: <何をしたか>' --add <ファイル1> --add <ファイル2> <<'EOF'
   - 検証コマンド: 1204 pass / 0 fail（+9）
   - 規約: coding-standards「テスト」
   - 規約の指摘: 0
   - 振り返り: 兆候なし
   EOF
   ```

   完了条件に目視があったときは 受け入れ役の `- 目視:` の行を写した `- 目視: <見比べた画像のパス> を <完了条件の行> と見比べて可` の1行を足す。

   返却は Beads に残らないので、規約の2行を必ず `## 結果` に写す。
   実装役の最後の段の返却の `- 規約:` の行をそのまま `- 規約: <読んだ節の名前>`（読まなかったら `- 規約: なし`）として写し、
   受け入れ役の `- 指摘:` のうち `規約:` で始まる行を差し戻しの回をまたいで数えて `- 規約の指摘: <件数>` として写す。
   実装役の返却が無い経路（メインで行う `loopable: N` のタスク `flows/main.md`・近道 `flows/direct.md`）では `- 規約: 委譲なし` と書き、
   受け入れ役を通らない近道では `- 規約の指摘: なし` と書く。

   `dropped` にするときは `--dropped` を付け、理由を結果に書く。`DONE` は
   stage せず（`## 結果` は Beads の comment に入る）、コミットは `--add` で渡した作業のファイルだけ
   （振り返りのドラフトは Beads の課題なので入れない）。`--add` はファイルごとに繰り返す（空白・コンマで1つにまとめない）。
   `--add` のファイルは**個別に** `git add` され（`git add -A` は使わない）、
   **1コミット**になる。件名は `T-xxx: <何をしたか>`
   （正典「コミットメッセージ」）、末尾の署名も同じ節に従って `--message` に入れる。`--add` を付けないか差分が無ければ
   `NO_COMMIT` を出してコミットせず、そのまま ship へ進む（`tw ship` が `NOTHING` を返して閉じる）。
   知見は `## 結果`・`## 注意`・正典の docs・新しいタスクへ置く。
   `tw finish` は `tw done`・コミット・`tw ship` の行をそのまま並べ、止まる行（`NOT_OWNER`・`CONFLICT` など）が出たら
   以降を打たず、`${CLAUDE_SKILL_DIR}/parts/ship/ship-stopped.md` の表で読む。`DONE` の次に `COMMITS_SINCE_CLAIM` が続いたら、`tw finish` はそこで（`tw done` は済んだまま）
   コミットも `tw ship` も打たず終了コード12で止まる。`${CLAUDE_SKILL_DIR}/parts/ship/commits-since-claim.md` に従って判断し、
   個別の `git add`・`git commit`・`tw ship` で続ける（`tw finish`・`tw done` は打ち直さない。`## 結果` が二重になる）。
   `tw finish` は終わりに、`start` だけで `end` の無い段を `OPEN_SPAN\tT-xxx\t<段>` の行で出す（出力のみ。終了コードは変わらない）。
   手順の打ち漏れの印なので、完了報告に写す。`sync` の start・end は `tw finish`／`tw ship` が自分で出す。

8. **送ったあとを読む**: 手順7の `tw finish`（`COMMITS_SINCE_CLAIM` で止まったあとは個別の `tw ship`）の出力の最後の行（`tw ship` の判定）で分ける。

   | 出力 | すること |
   | --- | --- |
   | `SHIPPED`・`NOTHING` | 完了。`kept=feature/T-xxx` が付いていたら、枝を消せなかったことを報告して人に預ける（消さない）。続く行に `NOT_CLOSED`・`TRACKER\tFAILED`・`BACKUP\tFAILED` があれば、行を添えて報告する（送れてはいる。`TRACKER` は `tw sync` で打ち直せる） |
   | それ以外 | `${CLAUDE_SKILL_DIR}/parts/ship/ship-stopped.md` の表に従う |

   `/loop` を続けるかは `${CLAUDE_SKILL_DIR}/completion-report.md` の「`/loop` の続行判断」で決める。push はしない。
