# メインで行う（loopable: N のタスク）

   `loopable: N` のタスク（ユーザーが直接呼んだとき）は委譲せずメインで行う（`## やること` を作業より
   先に `tw edit --section 'やること'` で書くのは委譲先と同じ。`tw edit`・`tw verify` の関門も同じく掛かる）。着手して初めて
   ユーザーの判断が要ると分かったら、人がいれば `AskUserQuestion` で聞いて答えを
   `## 決まっていること` に焼く。聞けない（`/loop`）なら `tw release T-xxx` → `tw edit T-xxx --status hold`
   → `tw edit T-xxx --section 注意 --body-file -` で論点を1行足す、の順で外す（着手中は `--status` が効かないので
   `release` が先。`--loopable N` は打たない。コミットは要らない）。判断が重いと分かったら
   `difficulty` を上げてから委譲し直す。委譲し直すときは、前の担当を `TaskStop` で止めてから
   新しい担当を立てる（`SendMessage` で呼び戻さない）。

   並びで進めるとき（`SKILL.md` の手順4b）は、implement-step がこの手順で `## やること` の段を進め、後の段は並びのとおりに呼ぶ。

   委譲しないので手順7（`${CLAUDE_SKILL_DIR}/parts/ship/ship.md`）の `## 結果` の規約の行は `- 規約: 委譲なし` と書く（`- 規約の指摘:` は受け入れ役の指摘を数える。必須行は ship.md 手順7）。
   帯の並びは `## やること` の段のあとに並びの後の段（既定なら「受け入れ」「振り返り」「送り出し」）で、`delegatedRange` は付かない
   （`SKILL.md`「進み具合の帯（`work_plan`）」。`tw plan-phases T-xxx` が出す）。
   `tw lap T-xxx delegate` は打たない（`accept`・`review`・`retro` は手順6・6aのとおり打つ）。
