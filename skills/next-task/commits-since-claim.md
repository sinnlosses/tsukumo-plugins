# COMMITS_SINCE_CLAIM の扱い

   手順7で `tw done` を打ったときの出力に `COMMITS_SINCE_CLAIM` の行が続いたら、次のとおり
   扱ってから先へ進む（`claim` した時点より後に、委譲先が hook（`tw commit-guard`）をすり抜けて作った
   コミットがあった合図。すり抜けるのは `general-purpose` への委譲と、Bash の文字列に `git` が出ない
   コミットだけ。控えの無い古い印では出ない）:

   | 出力 | すること |
   | --- | --- |
   | `COMMITS_SINCE_CLAIM` が無い | 次へ（`claim` の後に委譲先がコミットしていない） |
   | `COMMITS_SINCE_CLAIM\tT-xxx\t<sha,…>` | `git show <sha>` で中身を見る。「コミットしない」の指示に反した委譲先のコミットなら、この手順6の「人の差し戻し」と同じくメモを残し、打ち消す（`git revert`）か中身ごと活かすかを決めてから手順7のコミットへ進む |
