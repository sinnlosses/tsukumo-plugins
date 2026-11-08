# 委譲しない近道で進める

   条件・通すもの・降りる条件は正典「difficulty とモデルの切り替え」の「委譲しない近道」。
   委譲せずメインで次を順に打つ:

   1. `tw lap T-xxx direct`
   2. `tw show T-xxx` の `## やること` の段どおりに、`### 名指すファイル` だけを直す。
      読むのは `tw show` と名指すファイルだけ
   3. 整形コマンド → `tw verify`（出力の末尾だけを読む。1件ごとの振り返りは通らない）。最後の行で分ける。背景に回ったら完了を待つ（上限を付ける）:

      | 最後の行 | すること |
      | --- | --- |
      | `VERIFIED`・`VERIFIED_UNSTAMPED` | 通った（取り込んだときは最後から2行目が `FOLDED`）。手順4へ |
      | `VERIFY_NOT_PASSED`・`FORMAT_FAILED` | 出力の末尾とログを読んで直し、`tw verify` を打ち直す。1回の直しで通らなければ近道を降りる |
      | `CONFLICT` | 取り込みが衝突した（何も書き換えず、検証コマンドは打っていない）。`ship-stopped.md` の `CONFLICT` と同じく、衝突したファイルを添えて人に預けて終了する |
      | `PLAN_MISSING` | `## やること` が空のまま作業がある。差分から中身を書き起こして `tw edit T-xxx --after-work` で渡してから `tw verify` を打ち直す |
      | `GIT_READ_ONLY` | 取り込みが要るのに `.git` に書けない（何も書き換えず、検証コマンドは打っていない）。`task-model` の tw-reference.md の終了コード表の11のとおり（`tw verify` を打ち直す） |
   4. `ship.md` 手順 1 の `tw done T-xxx --reflection skipped --result-file -`。`- 振り返り:` の行は `- 振り返り: 近道（省いた）`。規約の行は `- 規約: 委譲なし`・`- 規約の指摘: なし`。`- 検証コマンド:` を含む必須行が欠けると `tw done` が拒む
   5. `ship.md` 手順 1 のコミット → 手順 2

   プログレスバーの並びは、既定の並びなら「直す」「検証」「送り出し」の3段（受け入れ・振り返りはプログレスバーに無いので、`skipped` でも `--skipped` に入れない。`tw plan-phases T-xxx` が出す）。
   手順2で直し終えたあと手順3の前に `--through 直す`、手順3の `tw verify` が通ったあと手順4の前に `--through 検証` を呼び、送り出しだけが残る位置にする。

   近道の基準を広げるかは、`retrospect` の週ごとの出力の節「近道と通常の比較」（`DIRECT_GATE` が20件に届いてから、`fix_within_7d` で判定）で見直す。`DIRECT_SHARE` が `below` なら、20件を待たずに広げるか外すかを決めるドラフトが積まれる。

   `tw lap` の `delegate`・`accept`・`review`・`retro` は打たない。委譲・返り・受け入れのレビュー・振り返り・合流は通らない。

   **近道を降りる**（直しが名指すファイルの外へ広がる・判断が要る・`VERIFY_NOT_PASSED` が1回の直しで通らない）:
   直したファイルを `git restore` で戻し、`tw edit T-xxx --direct N` を打ってから、`SKILL.md` の手順5へ戻って `script-flow.md` に従う。
