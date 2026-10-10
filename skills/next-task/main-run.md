# loopable: N のタスクをメインで行う

   `loopable: N` のタスク（ユーザーが直接呼んだとき）は委譲せずメインで行う（`## やること` を作業より
   先に `tw edit --section 'やること'` で書くのは委譲先と同じ。`tw edit`・`tw verify` の関門も同じく掛かる）。着手して初めて
   ユーザーの判断が要ると分かったら、`tw edit T-xxx --loopable N` と `tw release T-xxx`
   を打って預ける（コミットは要らない）。判断が重いと分かったら
   `difficulty` を上げてから委譲し直す。委譲し直すときは、前の担当を `TaskStop` で止めてから
   新しい担当を立てる（`SendMessage` で呼び戻さない）。
   委譲しないので `tw lap T-xxx delegate` は打たない（`accept`・`review`・`retro` は手順6・6aのとおり打つ）。
