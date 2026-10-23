手順5（委譲する）の(b)〜(f)と、手順5c（実装の依頼文）。`SKILL.md` の手順5から読まれる。

   **(b) `plan-check` で形を決める**: 手順4のあとに打った `tw plan-check T-xxx` の出力で決める
   （`## やること` は登録時に書く。正典「タスクの本文」）:

   | 出力 | すること |
   | --- | --- |
   | `PLAN_REGISTERED\tT-xxx\t<SHA>` | 登録時に書いた `## やること` が名指すファイルは変わっていないか、変わっても覆いが保たれ段の本文の識別子が差分に出ず古くない（条件は task-model の WORKFLOW.md「登録時に書く `## やること`」）。`PLAN_UNCOVERED\tT-xxx\t<n,…>` が続いていれば、その行（`n?` は完了条件に無い番号）が覆われていない。出ていなければ、メインが `tw show T-xxx` の `## やること` を読み、`## 完了条件` の各行がどの段のどのファイルのどこで満たされるかが分かるかを確かめる（`- 満たす完了条件:` の欄が無い旧い計画は `PLAN_UNCOVERED` が出ないので、この読みが覆いの確かめのすべて。欄がある計画でも、その段で本当に満たされるかはここで読む）。分かれば下の「計画どおりに委譲する」。`PLAN_UNCOVERED` が出た・分からなければ「書き直しから委譲する」にし（`PLAN_UNCOVERED` なら `tw brief` が自分で書き直しに倒す。読みで分からないだけなら `--rewrite`）、足りない点を依頼文に足す |
   | `PLAN_STALE\tT-xxx\t<変わったファイル>\t<理由>` | 名指したファイルが登録のあとに変わり、古くないと言える条件を満たさない（理由は 4 列目。種類は task-model の WORKFLOW.md「登録時に書く `## やること`」）。下の「書き直しから委譲する」。変わったファイルは `tw brief` が `古い計画` の節で依頼文に添える |
   | `PLAN_NOT_FIRST\tT-xxx\tmissing`・`unrecorded`・`steps` | 登録時の計画が無い（`--hold` で登録して todo に戻したもの・旧い登録）か、着手の前に `tw edit` で書いたか、段の形（正典「タスクの本文」の「段の形」）に沿わない。下の「書き直しから委譲する」 |

   **メインが書いてよいとき**: 「書き直しから委譲する」に当たり、同じセッションでこのタスクの設計を
   利用者と決めていて、`## やること` に要る正典・コードをメインがすでに読んでいるときだけ、
   `${CLAUDE_SKILL_DIR}/parts/implementer/rewrite-plan.md` の「メインが書いてよいとき」に従う。

   **(c) 計画どおりに委譲する・書き直しから委譲する**: どちらの形かは `tw brief implementer T-xxx` が `plan-check` と同じ印から決め、
   依頼文（手順5cの場合分けを含む）を出す。上の表の「書き直しから委譲する」に当たるのに `PLAN_REGISTERED` のまま
   （`## 完了条件` の覆いが読みで分からない）なら `--rewrite` を付けて書き直しに倒す。
   計画どおりなら段1の依頼、書き直しなら計画だけの回の依頼（`書き直し` の節。段に数えず、
   返却の先頭は計画の要点を1文、2行目は `tw plan-phases T-xxx --through 計画` の出力の `PHASES\t…` の行の写しで、`phase_result` は呼ばない）になり、メインが手順5bで読み、
   `PHASES` の行を `work_plan` に写してから段1を渡す。
   書き直しの扱いは `${CLAUDE_SKILL_DIR}/parts/implementer/rewrite-plan.md`。

   どちらの形でも、途中で `difficulty` を上げるときは、段の返却を受けた切れ目で前の担当を `TaskStop` で
   止め、上のモデルで起こし直して、済んだ段（作業ツリーに残っている）の次の段から `tw brief implementer T-xxx --step <n>` の依頼文で渡す。
   分業（下の (d)）の `sonnet` の委譲先が判断が要ると止めて返したときも同じ手で、`difficulty` は書き換えずに
   `opus` で起こし直し、起こす直前に `tw lap T-xxx delegate start --model opus` を打つ。

   **(d) 委譲先の選び方と依頼**: Agent ツールで、正典「difficulty とモデルの切り替え」の対応表のモデル（分業に
   当たれば `sonnet`）のサブエージェントへ（メインのモデルは判断材料にしない。一致していても委譲する）。
   分業: (c) で `計画どおり` に決まり `difficulty: opus` なら、Agent を呼ぶ直前に
   `tw lap T-xxx delegate start --model sonnet` を打ち、`model: sonnet` で起こす（範囲と理由は正典の「分業」）。
   `subagent_type` は、Agent ツールの説明に並ぶエージェント末尾が `no-delegate` の行
   （`no-delegate`（リンクで入れたとき）か `tsukumo-workflow:no-delegate`（plugin で入れたとき）。`Agent` を
   外したエージェント定義で、tsukumo-plugins の `agents/no-delegate.md`）があれば、一覧に出ている名前のまま使う。
   末尾一致が複数当たったら選ばずに止め、行を添えて人に預け `tw release T-xxx` する。
   一覧に無ければ、まだ入れていない（人がやること）とみなして、これまでどおり `general-purpose` を使う。hook の関門は、リンクで入れたときは
   `no-delegate` の定義が、plugin で入れたときは `hooks/hooks.json` が名前を問わずサブエージェントに掛ける（正典の `tw-reference.md`「hook の関門」節）。一覧に無くて `general-purpose` に
   落ちたときは Claude Code の hook の関門が掛からず（コミットだけは git の hook `ref-guard` が呼び手を問わず拒む）、
   手順6の `COMMITS_SINCE_CLAIM` と手順5bの `plan-check`・手順6の `verify-check` だけが頼りになる。
   決まりの本文も依頼文も手で組まず、`tw brief implementer T-xxx` の出力の2行目以降をそのまま渡す
   （タスクID・`implementer-brief.md` の絶対パス・`tw verify`／`tw pause` の打ち方・手順5cの場合分けが入る。
   `no-delegate` でも `general-purpose` でも同じ依頼文）。メインが足すのは判断の部分（この作業で何を重く見るか、
   手順5bで `PLAN_UNCOVERED` などから分かった足りない点）だけ。1行目 `BRIEF\tT-xxx\timplementer\t<節,…>` は渡さず、
   選ばれた節（tw が決めた場合分け）を読む。`NOT_OWNER`・`MISSING`・`INVALID` が出たら行を添えて人に預け `tw release T-xxx` する。

   `loopable: Y` で着手したあと利用者に聞く必要が出たら、`AskUserQuestion` は hook（`tw ask-guard`）に拒まれる。
   論点を1行にして `tw hold T-xxx --reason-file -` に渡して外す。続行は `${CLAUDE_SKILL_DIR}/completion-report.md` の「`/loop` の続行判断」に従う。

   **(e) 並列の段**: `tw plan-check T-xxx` が `PARALLEL` の行を出したら、続く `STEP\tT-xxx\t<n>\t<待つ段,…|なし>` の
   行に従い、待つ段がすべて返った段から同時に起こす（並列にするかは計画の欄と `plan-check` の出力だけで決め、
   着手時に判断しない。最後の段は欄によらずほかの段がすべて返ってから走り、その担当だけが `tw verify` を打つ）。
   段の担当は、待つ段のうち番号の小さい段を済ませた担当へ `SendMessage` で渡し、その担当がほかの段を
   持っているとき・待つ段が無いときは、`tw brief implementer T-xxx --step <n>` の依頼文で新しいサブエージェントを起こす（1つのメッセージで
   並べて起こす。段が並列の組に入れば `並列の段` の節が入る）。どの担当がどの段を持っているかを控えておく。`PARALLEL` の行が無ければ1本道に順に渡す。

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

5c. **実装の依頼文の場合分け**（毎回渡す決まりは brief にある）: 文面は依頼文の型 `${CLAUDE_SKILL_DIR}/parts/implementer/implementer-request.md` にあり、
   `tw brief implementer` が場合に当たる節を足す。メインは型を読まず、手で写さない:
   - 作業先が別のリポジトリ（`### 作業先`）なら `別のリポジトリ` の節（枝・作業ツリーの道・作業先の主ブランチを埋める。道の決め方は `tw brief --help`）
   - 渡す段が並列の組（`plan-check` の `PARALLEL`）に入れば `並列の段` の節（段の番号つきの `tw pause`・触るファイル）
   - 型には無く、メインが依頼文へ足す一文: 「`tw step` は打たない・`tw verify` は流さない」と頼む段（検証を後でまとめて流す流れ）には、返す直前に引数なしの `tw pause` を打ってから返すよう一緒に頼む（並列の段なら段の番号つきの `tw pause <ID> <段の番号>`）。返却ゲートは、`tw verify` の控えが無い（または中身が変わった）と `NOT_VERIFIED` で、`tw verify` の控えが通っても段が2つ以上の計画で段の控えが1つも無いと「段の控えが無い」で拒み、中身が同じ `tw pause` の控えがあれば通る
   - `--visual` を付けたときだけ `目視` の節（tw は本文の語で決めない）。完了条件に画面・見た目を確かめる行があるか、本文が見本（画・ラフ）を名指しているか、描画を変えるタスクなら、メインが `--visual` を付ける。受け入れでも同じタスクには `目視` を渡す（`visual-review.md`）
