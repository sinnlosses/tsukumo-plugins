# 委譲しない近道で進める

   条件・通すもの・降りる条件は正典「difficulty とモデルの切り替え」の「委譲しない近道」。手順4の `CLAIMED` の行末が
   `direct=Y` で、`tw plan-check T-xxx` が `PLAN_REGISTERED` のときだけ、委譲せずメインで次を順に打つ
   （どちらかが違えば手順5の委譲へ進む）:

   1. `tw lap T-xxx direct`
   2. `tw show T-xxx` の `## やること` の段どおりに、`### 名指すファイル` だけを直す。
      読むのは `tw show` と名指すファイルだけ
   3. 整形コマンド → `tw verify`（出力の末尾だけを読む。表は手順6bの表（`verify-merge.md`）と同じ扱い。待って打ち、振り返りは無いので直しの行は振り返りの扱いを読まない）
   4. 手順7の `tw done T-xxx --result-file -`。`- 振り返り:` の行は `- 振り返り: 近道（省いた）`
   5. 手順7のコミット → 手順8

   `tw lap` の `delegate`・`accept`・`review`・`retro` は打たない。手順5〜6b（委譲・返り・受け入れのレビュー・振り返り・合流）は通らない。

   **近道を降りる**（直しが名指すファイルの外へ広がる・判断が要る・`VERIFY_NOT_PASSED` が1回の直しで通らない）:
   直したファイルを `git restore` で戻し、`tw edit T-xxx --direct N` を打ってから、手順5の委譲へ進む
   （`tw lap T-xxx delegate` から）。
