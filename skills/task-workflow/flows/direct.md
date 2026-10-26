# 委譲しない近道で進める

   条件・通すもの・降りる条件は正典「difficulty とモデルの切り替え」の「委譲しない近道」。
   委譲せずメインで次を順に打つ。並びで進めるとき（`SKILL.md` の手順4b）は、implement-step が1〜3を行い、
   accept-step・retrospect-step が `skipped` を返し、ship-step が4・5を行う:

   1. `tw lap T-xxx direct`
   2. `tw show T-xxx` の `## やること` の段どおりに、`### 名指すファイル` だけを直す。
      読むのは `tw show` と名指すファイルだけ
   3. 整形コマンド → `tw verify`（出力の末尾だけを読む。表は手順6bの表（`verify-merge.md`）と同じ扱いで、待って打つ。1件ごとの振り返りは通らないので、表の振り返りの扱いは読まない）
   4. 手順7の `tw done T-xxx --reflection skipped --result-file -`。`- 振り返り:` の行は `- 振り返り: 近道（省いた）`。規約の行は `- 規約: 委譲なし`・`- 規約の指摘: なし`。`- 検証コマンド:` を含む必須行が欠けると `tw done` が拒む（ship.md 手順7）
   5. 手順7のコミット → 手順8

   帯の並びは、既定の並びなら「直す」「検証」「送り出し」の3段（受け入れ・振り返りは帯に無いので、`skipped` でも `--skipped` に入れない。`SKILL.md`「進み具合の帯（`work_plan`）」。`tw plan-phases T-xxx` が出す）。
   手順2で直し終えたあと手順3の前に `--through 直す`、手順3の `tw verify` が通ったあと手順4の前に `--through 検証` を呼び、送り出しだけが残る位置にする。

   近道の基準を広げるかは、`retrospect` の週ごとの出力の節「近道と通常の比較」（`DIRECT_GATE` が20件に届いてから、`fix_within_7d` で判定）で見直す。`DIRECT_SHARE` が `below` なら、20件を待たずに広げるか外すかを決めるドラフトが積まれる。

   `tw lap` の `delegate`・`accept`・`review`・`retro` は打たない。委譲・返り・受け入れのレビュー・振り返り・合流（手順5〜5d・6・6a・6b）は通らない。

   **近道を降りる**（直しが名指すファイルの外へ広がる・判断が要る・`VERIFY_NOT_PASSED` が1回の直しで通らない）:
   直したファイルを `git restore` で戻し、`tw edit T-xxx --direct N` を打ってから、手順5の委譲へ進む
   （`delegate` の start の打ち分けは手順5のとおりで、plugin で入れたときは委譲先の起動で `tw lap-hook` が書く）。
