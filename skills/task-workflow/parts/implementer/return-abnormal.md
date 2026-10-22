# 手順5bの返りの異常系

手順5bの表（`${CLAUDE_SKILL_DIR}/parts/implementer/handback-read.md`）のうち、通常の返り（`PLAN_REGISTERED`・`PLAN_FIRST`）以外の行。並列の段の担当を起こしたときは、
先頭が `止めた:` の返却はその段の担当にだけ当て、その段 `n` を待つ段は渡さずに、ほかの担当の段は進める。
「前提が誤り」・`dropped` にすべき理由の報告はタスク全体に当て、ほかの担当を返却の切れ目で `TaskStop` で
止めてから表に従う。

   | 見たもの | すること |
   | --- | --- |
   | 「前提が誤り」の報告 | 手順7で `--dropped` にするか、`## 目的・背景` を直して手順4から着手し直すかを決める（直す `tw edit` には `--change-frame` を付ける。付けないと `FRAME_CHANGED` で拒まれる） |
   | `dropped` にすべき理由の報告 | 手順7へ（`--dropped`。理由を結果に書く） |
   | 先頭が `止めた:`（渡した段 `n` で止めた） | 理由を読み、メインで決められることなら決めて同じ委譲先へ `SendMessage` で段 `n` の続きを渡す。判断が `difficulty` を超えるなら`${CLAUDE_SKILL_DIR}/parts/implementer/delegate.md` の手順5(c)の「途中で `difficulty` を上げるときは」で段 `n` から渡し直し、人の判断が要るなら論点を `tw hold T-xxx --reason-file -` に渡して外し、`/loop` の続行は `${CLAUDE_SKILL_DIR}/completion-report.md` の「`/loop` の続行判断」に従う（`loopable: N` には直さない） |
   | 本文が済んだとも止めたとも読めない | 同じ委譲先へ `SendMessage` で、いま済んでいる段と、済んだか止めたかを先頭に書き直して返すよう尋ねる（作業ツリーには触らない。段の控えは `unresponsive.md` 項2で見られる） |
   | `PLAN_NOT_FIRST`（`missing`・`after-work`・`unrecorded`・`steps`）か `PLAN_STALE` | `## やること` を作業より先に書かなかった。差分が完了条件を満たすなら活かして手順6へ進み、`## やること` はメインが差分から書き起こして `tw edit T-xxx --section 'やること' --after-work` で渡す。満たさなければ差分を退けて（自分の作業ツリー内の `git restore`）「書き直しから委譲する」で頼み直す |
   | `tw accept` が `MAIN_DIRTY_FROM_DELEGATE\t<パス>` を出した（作業先の本体の作業ツリーに、委譲先の枝と同じ blob の未コミットの変更がある。`${CLAUDE_SKILL_DIR}/other-repo.md` の「作業先の本体の汚れ」の表） | 委譲先が本体を書き換えた。`SendMessage` で伝えて差し戻し、その行のファイルだけ `git restore` させる。`tw claim` の控えにあったファイル（`MAIN_DIRTY_BEFORE`）は別の作業なので消さずに待つか人に預け、枝と blob が違う・枝に無いファイル（`MAIN_DIRTY_UNKNOWN`）は消さずに人に預ける |
   | `NOT_OWNER` | 自分の作業ツリーの印が無い（人が `tw release --force` した、など）。ID を添えて終了する |
