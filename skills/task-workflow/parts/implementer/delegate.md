手順5（委譲する）の(b)〜(f)と、手順5c（実装の依頼文）。`SKILL.md` の手順5から読まれる。

   **(b) `plan-check` で形を決める**: 手順4のあとに打った `tw plan-check T-xxx` の出力で決める
   （`## やること` は登録時に書く。正典「タスクの本文」）:

   | 出力 | すること |
   | --- | --- |
   | `PLAN_REGISTERED\tT-xxx\t<SHA>` | 登録時に書いた `## やること` が名指すファイルは変わっていない。`PLAN_UNCOVERED\tT-xxx\t<n,…>` が続いていれば、その行（`n?` は完了条件に無い番号）が覆われていない。出ていなければ、メインが `tw show T-xxx` の `## やること` を読み、`## 完了条件` の各行がどの段のどのファイルのどこで満たされるかが分かるかを確かめる（`- 満たす完了条件:` の欄が無い旧い計画は `PLAN_UNCOVERED` が出ないので、この読みが覆いの確かめのすべて。欄がある計画でも、その段で本当に満たされるかはここで読む）。分かれば下の「計画どおりに委譲する」。`PLAN_UNCOVERED` が出た・分からなければ「書き直しから委譲する」にし、足りない点を依頼文に足す |
   | `PLAN_STALE\tT-xxx\t<変わったファイル>` | 名指したファイルが登録のあとに変わった。下の「書き直しから委譲する」。依頼文に「登録時の `## やること` は古い（変わったファイル: <行の3列目>）。いまの主ブランチで調べ直して丸ごと書き直す」を足す |
   | `PLAN_NOT_FIRST\tT-xxx\tmissing`・`unrecorded`・`steps` | 登録時の計画が無い（`--hold` で登録して todo に戻したもの・旧い登録）か、着手の前に `tw edit` で書いたか、段の形（正典「タスクの本文」の「段の形」）に沿わない。下の「書き直しから委譲する」 |

   **メインが書いてよいとき**: 「書き直しから委譲する」に当たり、同じセッションでこのタスクの設計を
   利用者と決めていて、`## やること` に要る正典・コードをメインがすでに読んでいるときだけ、
   `${CLAUDE_SKILL_DIR}/parts/implementer/rewrite-plan.md` の「メインが書いてよいとき」に従う。

   **(c) 計画どおりに委譲する**: (d) の依頼（brief のパス）と手順5cの依頼に、次を足して渡す（並列の段の担当には、
   段1の代わりに渡す段の番号を書く）:
   - 「`## やること` は書いてある。`tw show T-xxx` で読み、段1（`### 1.`）だけをそのとおり実装して返す。
     次の段はメインが `SendMessage` で渡す。判断が想定より要ると分かったら押し切らず、そこで止めて報告する。
     `## 目的・背景`・`## 完了条件` は変えない（変えると `FRAME_CHANGED` で拒まれる。`--change-frame` は
     付けず、変えたい理由を報告する）」

   **(c) 書き直しから委譲する**: (d) の依頼（brief のパス）と手順5cの依頼に、
   `${CLAUDE_SKILL_DIR}/parts/implementer/rewrite-plan.md` の「書き直しから委譲する」を足して渡す。最初の返却は計画だけの
   回（段に数えない。返却の先頭は計画の要点を1文。`phase_result` は呼ばない）で、メインが手順5bで読み、段の数を Beads から読んでから段1を渡す。

   どちらの形でも、途中で `difficulty` を上げるときは、段の返却を受けた切れ目で前の担当を `TaskStop` で
   止め、上のモデルで起こし直して、済んだ段（作業ツリーに残っている）の次の段から同じ形で渡す。

   **(d) 委譲先の選び方と依頼**: Agent ツールで `difficulty` と同じモデルのサブエージェントへ（メインのモデルは
   判断材料にしない。一致していても委譲する。正典「difficulty とモデルの切り替え」）。
   `subagent_type` は、Agent ツールの説明に並ぶエージェント末尾が `no-delegate` の行
   （`no-delegate`（リンクで入れたとき）か `tsukumo-workflow:no-delegate`（plugin で入れたとき）。`Agent` を
   外したエージェント定義で、tsukumo-plugins の `agents/no-delegate.md`）があれば、一覧に出ている名前のまま使う。
   末尾一致が複数当たったら選ばずに止め、行を添えて人に預け `tw release T-xxx` する。
   一覧に無ければ、まだ入れていない（人がやること）とみなして、これまでどおり `general-purpose` を使う。hook の関門は、リンクで入れたときは
   `no-delegate` の定義が、plugin で入れたときは `hooks/hooks.json` が名前を問わずサブエージェントに掛ける（正典の `tw-reference.md`「hook の関門」節）。一覧に無くて `general-purpose` に
   落ちたときは Claude Code の hook の関門が掛からず（コミットだけは git の hook `ref-guard` が呼び手を問わず拒む）、
   手順6の `COMMITS_SINCE_CLAIM` と手順5bの `plan-check`・手順6の `verify-check` だけが頼りになる。
   決まりの本文は貼らず、次を渡す（委譲先は `${CLAUDE_SKILL_DIR}` を持たないので、パスは解決した絶対パスにする）:
   - タスクID と「まず `tw show T-xxx` で読む」
   - `${CLAUDE_SKILL_DIR}/parts/implementer/implementer-brief.md` を解決した絶対パスと「最初にそれを読んで従う」
     （`no-delegate` でも `general-purpose` でも同じ brief を渡す）
   - 「`tw verify`・`tw pause` は引数なしで打つのが基本。ID を付けるなら着手中のタスクの ID 1つだけ
     （違う ID は `NOT_OWNER` で拒まれる）」

   `loopable: Y` で着手したあと利用者に聞く必要が出たら、`AskUserQuestion` は hook（`tw ask-guard`）に拒まれる。
   理由の文に従って `tw release T-xxx` → `tw edit T-xxx --status hold` → `## 注意` に論点を書く、の順で外す。続行は `${CLAUDE_SKILL_DIR}/completion-report.md` の「`/loop` の続行判断」に従う。

   **(e) 並列の段**: `tw plan-check T-xxx` が `PARALLEL` の行を出したら、続く `STEP\tT-xxx\t<n>\t<待つ段,…|なし>` の
   行に従い、待つ段がすべて返った段から同時に起こす（並列にするかは計画の欄と `plan-check` の出力だけで決め、
   着手時に判断しない。最後の段は欄によらずほかの段がすべて返ってから走り、その担当だけが `tw verify` を打つ）。
   段の担当は、待つ段のうち番号の小さい段を済ませた担当へ `SendMessage` で渡し、その担当がほかの段を
   持っているとき・待つ段が無いときは、同じ依頼文で新しいサブエージェントを起こす（1つのメッセージで
   並べて起こす）。どの担当がどの段を持っているかを控えておく。`PARALLEL` の行が無ければ1本道に順に渡す。

   **(f) 無音の見張り**: 委譲を待つあいだメインはターンを終えていて、委譲先が無音でも起こすイベントが無い。
   そこで委譲を投げた直後に `Monitor` で見張りを起こす。
   - 起こし方: `Agent` の戻りの `output_file` は symlink で、実体は委譲先の transcript。
     `stat` は `-L` を付けて実体の更新時刻を読む（付けないと symlink 自体の時刻で、更新が一度も見えない）。
     `T` には `${CLAUDE_SKILL_DIR}/parts/implementer/unresponsive.md` の項3が決める無音の秒数を渡す（数はここに書かない）:
     `f=<output_file>; T=<秒数>; l=$(date +%s); p=$(stat -L -f %m "$f"); g=0; while :; do n=$(date +%s); m=$(stat -L -f %m "$f"); [ "$m" != "$p" ] && { p=$m; l=$n; g=0; }; [ $((n-l)) -ge $T ] && [ $g -eq 0 ] && { echo "SILENT $((n-l))s"; g=1; }; sleep 5; done`
     更新のたびには出さず、無音を越えた1回だけ出す。`Monitor` の `timeout_ms` は上限（30分）にし、
     失効の通知が来たとき委譲先がまだ動いていれば起こし直す。並列の段では担当ごとに1つ起こす。
     見張るのは transcript の実体の更新だけで、作業ツリーの更新は補いにしない（読み取りと待ちの間は進まない）。
   - `SILENT` の通知が来たら、`${CLAUDE_SKILL_DIR}/parts/implementer/unresponsive.md` を読んで項3の尋ねる・止める・引き取るに従う。
   - 委譲先の最後の段の返却を受けたら、見張りを `TaskStop` で止める（手順6の頭。返却のあとも transcript が
     数秒更新されるので、畳まないと失効まで残る）。

5c. **実装の依頼文**（手順5のどちらの形にも、該当するときだけ足す。毎回渡す決まりは brief にある）:
   - 作業先が別のリポジトリのときだけ渡す（手順6の「作業先が別のリポジトリのとき」）:
     `${CLAUDE_SKILL_DIR}/other-repo.md` の「実装の依頼文に足す項目（手順5c）」の項目
   - 並列の段の担当（手順5の「並列の段」）にだけ渡す: 「同じ作業ツリーでほかの段の担当が同時に動いている。
     書き換えるのは渡した段の `- 触るファイル:` の欄のファイルだけにする。最後の段でなければ `tw verify` も
     検証コマンドの全段も打たない（対象を絞った単体テストは打ってよい）。止めて返すときは、基本の引数なしの
     `tw pause` の代わりに `tw pause T-xxx <段の番号>`（ID と段の番号の2つ）を打つ」
   - 本文が見本（画・ラフ）を名指しているか、完了条件に目視があるか、描画を変えるタスクのときだけ、
     `${CLAUDE_SKILL_DIR}/parts/acceptor/visual-review.md` の「実装の依頼文に足す項目（手順5c）」の項目を渡す
