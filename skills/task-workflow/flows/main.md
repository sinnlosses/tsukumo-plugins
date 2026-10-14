# メインで行う（loopable: N のタスクと、flow が main のとき）

   `loopable: N` のタスク（ユーザーが直接呼んだとき）は委譲せずメインで行う（`## やること` を作業より
   先に `tw edit --section 'やること'` で書くのは委譲先と同じ。`tw edit`・`tw verify` の関門も同じく掛かる）。着手して初めて
   ユーザーの判断が要ると分かったら、`tw edit T-xxx --loopable N` と `tw release T-xxx`
   を打って預ける（コミットは要らない）。判断が重いと分かったら
   `difficulty` を上げてから委譲し直す。委譲し直すときは、前の担当を `TaskStop` で止めてから
   新しい担当を立てる（`SendMessage` で呼び戻さない）。

   `tw config` の `flow` が `main` のときは、`loopable` によらず近道に当たらないタスクをすべてこの手順で進める。
   そのときは判断が重いと分かっても委譲し直さず、`difficulty` を上げて `tw release T-xxx` し、人に預ける。
   委譲しないので手順7（`${CLAUDE_SKILL_DIR}/parts/ship.md`）の `## 結果` の規約の行は `- 規約: 委譲なし` と書く（`- 規約の指摘:` は受け入れ役の指摘を数える）。
   帯の並びは `## やること` の段のあとに「受け入れ」「振り返り」「送り出し」で、`delegatedRange` は付かない
   （`SKILL.md`「進み具合の帯（`work_plan`）」。`tw plan-phases T-xxx` が出す）。
   `tw lap T-xxx delegate` は打たない（`accept`・`review`・`retro` は手順6・6aのとおり打つ）。
