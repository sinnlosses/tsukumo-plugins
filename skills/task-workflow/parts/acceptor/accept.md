手順6（受け入れる）の続き。`SKILL.md` の手順6から読まれる。委譲先の最後の段の hand-back を確かめ、`tw accept T-xxx` を打つところまでは本文で済んでいる。

   **目視待ちの報告が来たとき**（描画を変えるタスクで、委譲先が組み立てた直後に返したもの。完了の
   hand-back ではないので、ここで受け入れを始めない）: `${CLAUDE_SKILL_DIR}/parts/acceptor/visual-review.md` を読んで従う。

   **完了条件に目視（画面・見た目）があるとき**: `${CLAUDE_SKILL_DIR}/parts/acceptor/visual-review.md` の「完了条件に目視があるとき（手順6）」に従う。
   受け入れ役の `- 目視:` が `可` になるまで手順7の `tw finish` に進まない。

   メインは差分を読まず、画像も開かない。差分のレビュー・目視・振り返りの材料は、下の受け入れ役が判断して判定の行で返す。
   `## やること` は手順5と5bで見た（計画どおりに委譲したときは `tw plan-check T-xxx` が
   `PLAN_REGISTERED` を返し、`PLAN_FIRST` と同じに読む）。

   **束ねた受け入れの準備**: `tw accept T-xxx` は `tw lap T-xxx accept start`・`tw plan-check T-xxx`・レビューの要否の判定
   （`review_needed.py`）を順に打ち、その行をそのまま並べる。出力の最後の行で分ける（止まる行が出たらそこで以降を打たず、
   行の終了コードで終わる。`NOT_CLAIMED`・`NOT_OWNER` は手順4の印が無い合図で、メインで原因を見る）。

   | 出力 | すること |
   | --- | --- |
   | 最後が `SNAPSHOT\t<木の SHA>`（その前に `REVIEW\topus`・`REVIEW\tcode\t<数>`） | レビューが要る。`lap review`（start）は打ってあり、木の SHA が1回目の控え。下の受け入れ役を起こす。判定を受けた直後に `tw lap T-xxx review end` を打つ（差し戻しで受け入れ役を起こし直すたびに、起こす直前の `tw lap T-xxx review start` と判定直後の `review end` を対で打つ）。直しのあとは `tw accept T-xxx --after-review`（終わりで `accept` の end を `tw` が出す） |
   | 最後が `tw verify-check` の行（その前に `SKIP\tdocs-only`・`NOTHING`・`NO_RANGE`、設定に `format` があれば `FORMATTED`） | レビューは要らない（`accept` の end は `tw` が出した）。下の受け入れ役を `SKIP`・`NOTHING`・`NO_RANGE` の依頼（目視と材料だけ）で起こす。`NO_RANGE` は範囲なしで作業ツリーの変更が空という意味で、作業先が別のリポジトリのタスクでは範囲の渡し忘れの合図なので、`other-repo.md` のとおり範囲つきで打ち直す |
   | `FORMAT_FAILED\t<コマンド>` | 整形が落ちた。続く末尾の行を読んで直し、`tw accept T-xxx --after-review` を打ち直す |
   | `MISSING`・`INVALID` | review_needed.py が見つからない・失敗した。行を添えて人に預けて終了する |

   `plan-check` の行（`PLAN_FIRST`・`PLAN_REGISTERED` など）は、手順5bの表で読んだものと同じに読む。
   着手より後のコミットも作業ツリーの変更も無い計測だけのタスクは、`tw accept` が `NO_RANGE` を出し、続く `tw verify-check` が `NOT_VERIFIED\tnone` になる（単一のリポジトリで確かめた）。検証する中身が無いので `tw verify` は起こさず、手順6aへ進む（`tw finish` は検証の控えを見ない。`tw ship` の戻りは枝の状態による: 主ブランチを出している作業ツリーでは `SHIPPED`、主ブランチより先のコミットが無い別の枝では `NOTHING`）。作業先が別のリポジトリのときは確かめていない。
   作業先が別のリポジトリのタスクでは `tw accept` を使わず、`other-repo.md` のとおり個別に打つ。

   受け入れ役は Agent ツールで起こす。`subagent_type` は、Agent ツールの説明に並ぶエージェント末尾が `acceptor` の行
   （`acceptor` か `tsukumo-workflow:acceptor`。tsukumo-plugins の `agents/acceptor.md`。モデルは Opus 固定で、
   読むだけの定義。書き換えないので返却は `tw handback-guard` に判定されない）があれば、一覧に出ている名前のまま `model` を渡さずに起こす。
   末尾一致が複数当たったら選ばずに止め、行を添えて人に預け `tw release T-xxx` する。
   無ければ手順5の `subagent_type` の選び方と同じにして `model: opus` を渡す。決まりの本文は貼らず、
   渡すのは次だけ（受け入れ役は `${CLAUDE_SKILL_DIR}` を持たないので、パスは解決した絶対パスにする）:
   - 作業ツリーのパス・差分を出すコマンド（`git diff HEAD` と、未追跡の新しいファイルを並べる
     `git ls-files --others --exclude-standard`）・`tw show T-xxx` の `## 完了条件` の本文・
     プロジェクトの規約のファイル（AGENTS.md・CLAUDE.md と、そこから引かれるコーディング規約）のパス
   - `${CLAUDE_SKILL_DIR}/parts/acceptor/acceptor-brief.md` を解決した絶対パスと「最初にそれを読んで従う」
   - `tw accept` の判定（`REVIEW\t…` か `SKIP`・`NOTHING`・`NO_RANGE`）と、`REVIEW` のときは木の SHA
   - 目視の完了条件があるときは、委譲先が報告した画像のパス（幅ごと。いちばん狭い幅も）
   - 振り返りの材料: 委譲先の段ごとの返却の friction log の行（そのまま）と、人の差し戻しの一言メモの数
   - 「`comment-audit` を Skill ツールで名前を指して読む」こと（一覧の末尾が `comment-audit` の行。リンクで入れたとき
     `comment-audit`、plugin で入れたとき `tsukumo-workflow:comment-audit`。一覧に出ている名前のまま使う。受け入れ役は
     Skill ツールを持つ）。差分で足されたコメント行は、受け入れ役がその置き場（Base directory）の
     `python3 <Base directory>/scripts/diff_added_comment_lines.py HEAD` で拾う

   委譲の依頼文・委譲先の報告の本文・`## やること`・`## 目的・背景` は渡さない。

   返却の1行目 `受け入れ <判定> | <1文>` の判定の語で分ける。1行目がこの形でなければ、受け入れ役へ `SendMessage` で
   形を直して返し直させる（メインは差分を読んで補わない）。

   | 判定 | すること |
   | --- | --- |
   | `受け入れ` | 次へ。`- 材料:` の行を手順6aに使う |
   | `差し戻し` | `- 指摘:`（`直し:` 付きを含む）と `- 目視: 否` の行をそのまま `SendMessage` で委譲先へ渡して差し戻す（メインは直さない。委譲先が終わっていても `SendMessage` で再開できる。並列の段の担当を起こしたときは、まとめて最後の段の担当へ渡す）。直ったら `${CLAUDE_SKILL_DIR}/parts/acceptor/review-rounds.md` に従って同じ受け入れ役に続けさせる |
   | `預ける` | 直す指摘が無く、`- 預けること:` だけが残った（直し方を選ぶ必要がある）。直さず、その行をそのまま手順7の `## 結果` と完了報告の「人に知らせること」に写し、`受け入れ` と同じく `tw accept T-xxx --after-review` 以降（整形・`verify-check`・手順6a・6b）を通って手順7へ進む（終了しない。`- 材料:` は手順6aに使う。3回目も同じ） |

   `差し戻し` の返却に `- 預けること:` が混ざるときは、委譲先へは `- 指摘:`・`- 目視: 否` の行だけを渡し、`- 預けること:` の行は手順7の `## 結果` と「人に知らせること」に写すために控える。

   差し戻しの `- 指摘: 正しさ` の行には、「満たすべき不変条件（例: どの経路でも同じ状況には同じ結果）を、同じ種類の状況をすべて挙げて満たすよう直し、それぞれに自己テストを足す」と頼む一文を添えて渡す（受け入れ役は正しさの指摘を不変条件の形で書く。`acceptor-brief.md` の返し方）。

   渡す前に、`- 指摘: 簡素化` の行が委譲先の返却か friction log に書かれた「試して退けた案」へ戻す内容でないかを見る（メインは差分を読まず、書かれた退けた案との一致だけを見る）。
   当たる行は渡さず取り下げ、取り下げた行を控えて、手順7の `## 結果` に `- 取り下げた指摘: <数>` と写す。
   渡す行が残らなければ `受け入れ` と同じに `tw accept T-xxx --after-review` 以降へ進む。
   取り下げた行は、2・3回目の受け入れ役にも `${CLAUDE_SKILL_DIR}/parts/acceptor/review-rounds.md` の定めで知らせる。

   **差し戻したあとの2・3回目**と、**3回目にも指摘が残ったとき**は、
   `${CLAUDE_SKILL_DIR}/parts/acceptor/review-rounds.md` を読んで従う（同じ受け入れ役に `SendMessage` で、直しの差分だけを見させる）。

   方針からズレた実装を見つけたら、受け入れ役が `- 指摘:` に入れて差し戻しになる。メインは直さない。
   受け入れのあとは、`tw accept T-xxx --after-review` で整形コマンド（`format` の行があれば）→ `tw verify-check` を打つ（`${CLAUDE_SKILL_DIR}/parts/acceptor/verify-check.md` の表。検証を省くか、`tw verify` を背景で起こして手順6aの振り返りと並べるかを決める）。
   背景で起こした `tw verify` は、手順6bで合流するまで止めない。
   検証の打ち直しと差し戻しの回数は、一言メモに取らない（`material.py --gate` が台帳から数える）。
   **人が止めた・直させた・やり直させた**ときも、そのことだけを一言メモしておく（手順6aの材料
   「人の差し戻し」。人の発言は写さない）。

   **作業先が別のリポジトリのとき**（タスクが直す対象が自分の作業ツリーの外のリポジトリ）:
   `${CLAUDE_SKILL_DIR}/other-repo.md` の「受け入れ（手順6）」に従う。

   受け入れで作業ツリーを直したら、`tw accept T-xxx --after-review` から打ち直す。`tw ship` が付け替えたときの
   検証は、この控えでは省かれない（付け替えが起きるのは、受け入れのあとに主ブランチが進んだときだけ）。

   手順7の `tw finish` の出力に（`tw done` の行として） `COMMITS_SINCE_CLAIM` の行が出たら（`tw finish` は終了コード12で止まる）、
   `${CLAUDE_SKILL_DIR}/parts/ship/commits-since-claim.md` を読んで扱ってから先へ進む。
