# COMMITS_SINCE_CLAIM の扱い

   手順7の `tw finish` の出力に、`tw done` の行として `COMMITS_SINCE_CLAIM` の行が出たら、次のとおり
   扱ってから先へ進む（`claim` した時点より後に、委譲先が hook（`tw commit-guard`）をすり抜けて作った
   コミットがあった合図。すり抜けるのは `general-purpose` への委譲と、Bash の文字列に `git` が出ない
   コミットだけ。控えの無い古い印では出ない）:

   | 出力 | すること |
   | --- | --- |
   | `COMMITS_SINCE_CLAIM` が無い | 次へ（`claim` の後に委譲先がコミットしていない） |
   | `COMMITS_SINCE_CLAIM\tT-xxx\t<sha,…>` | `tw finish` は `tw done` を済ませたまま、コミットも `tw ship` も打たずに終了コード12で止まっている。`git show <sha>` で中身を見る。「コミットしない」の指示に反した委譲先のコミットなら、この手順6の「人の差し戻し」と同じくメモを残し、打ち消す（`git revert`）か中身ごと活かすかを決める。そのあと、手順7のコミット（作業のファイルを個別に `git add` して1コミット）→ `tw ship` を個別に打ち、手順8の表で読む。`tw finish` や `tw done` は打ち直さない（`## 結果` の comment が二重になる） |
