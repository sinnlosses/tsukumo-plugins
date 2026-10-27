# COMMITS_SINCE_CLAIM の扱い

   `claim` した時点より後に、委譲先が hook（`tw commit-guard`）をすり抜けて作ったコミットがあった合図。
   すり抜けるのは、Bash の文字列に `git` が出ないコミットだけ。
   控えの無い古い印では出ない:

   | 出力 | すること |
   | --- | --- |
   | `COMMITS_SINCE_CLAIM` が無い | 次へ（`claim` の後に委譲先がコミットしていない） |
   | `COMMITS_SINCE_CLAIM\tT-xxx\t<sha,…>` | `tw finish` は `tw done` を済ませたまま、コミットも `tw ship` も打たずに終了コード12で止まっている。`git show <sha>` で中身を見る。「コミットしない」の指示に反した委譲先のコミットなら、`${CLAUDE_SKILL_DIR}/parts/acceptor/accept.md` の手順6の「人が止めた・直させた・やり直させたときも、そのことだけを一言メモしておく」と同じくメモを残し、打ち消す（`git revert`）か中身ごと活かすかを決める。そのあと、`tw finish T-xxx --resume --message '<何をしたか>'` を1回打つ（`--result-file` は要らない。`done` を飛ばして `RESUMED` を出し、手順7と同じ対象の1コミット→ `tw ship` まで進む）。出力は `${CLAUDE_SKILL_DIR}/parts/ship/ship.md` の手順8の表で読む。`--resume` なしの `tw finish`・`tw done` は `ALREADY_DONE`（終了コード4）で止まる |
