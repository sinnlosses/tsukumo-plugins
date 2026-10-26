# hook を tools・deny・git の hook で置き換えられない理由

hook を足すか、別の仕組みに置き換えられるかを迷ったときだけ読む。hook の仕様（拒むもの・掛かる口）は `tw-reference.md`「hook の関門」節。

#### tools・permissions.deny で置き換えられない理由

**この3つの hook は、サブエージェント定義の `tools`・`disallowedTools` や設定の `permissions.deny` では置き換えられない**
（公式の説明: https://code.claude.com/docs/en/sub-agents ・ https://code.claude.com/docs/en/permissions ）。
`disallowedTools` に `Bash(git push *)` のような指定子を書いても、Bash というツールごと外れ、コマンド単位の拒否にならない。
コマンド単位で拒むには `permissions.deny` の `Bash(...)` か hook になるが、`permissions.deny` は設定にパターンで静的に書く規則で、
着手の印や `loopable` のような状態によって拒むかを変えられず、`sh -c '…'`・フルパスの呼び出しは素通しする。
plugin のサブエージェントは frontmatter の `hooks`・`permissionMode` を無視する。
すでに `disallowedTools` でやっているのは、同梱の実装役のエージェント定義の `Agent` の除去と、同梱の受け入れ役のエージェント定義の `Agent`・`Edit`・`Write`・`NotebookEdit` の除去だけ。

| 関門 | tools・`permissions.deny` で防げるか | 理由 |
| --- | --- | --- |
| `commit-guard`・`ref-guard` のコミット拒否 | 一部だけ | `Bash(git commit *)` の deny は書けるが、`sh -c` とフルパスは素通しし、着手の印の有無で出し分けられない（`ref-guard` は git の側で書き方を問わずに拒む） |
| `commit-guard` の Bash での書き換え拒否 | 不可 | 委譲先は `tw verify` と git の読み取りに Bash が要り、Bash を外せない |
| `commit-guard` の検証コマンドの直打ち拒否 | 不可 | 検証コマンドはプロジェクトの設定にあり静的に書けない。`tw verify` の内部実行との区別もできない |
| `handback-guard` の返却の関門 | 不可 | 作業の有無と `plan-check`・`verify-check` の結果という状態を見る検査 |
| `ask-guard` | 不可 | 着手したタスクの `loopable` で拒むかが変わる |

#### git の hook で置き換えられないもの

コミットの拒否は `ref-guard` へ移したが、`commit-guard` の残りの2つは git の hook で置き換えられないので残す。

| 関門 | 置き換えられない理由 |
| --- | --- |
| シェルでのファイルの書き換え拒否 | git の hook はコミット・参照の更新・checkout などの git の操作のときにしか走らず、ファイルを書いた時点では何も起こらない。事後の差分検査（`handback-guard`・`plan-check`・`verify-check`）は中身を見るが書いた手段を見ないので、「`Edit`・`Write` で書く」は差分から判定できない |
| 検証コマンドの直打ち拒否 | 検証コマンドは git を通らないので、git の hook は打ったことを知らない。打った結果は `verify-check` の控えの有無として事後に分かるが、控えを残さない直打ちそのものは止められない |

#### `handback-guard` が構造の節の欠けを `additionalContext` で知らせる件

`## 構造の変更` の節の欠けは `graph` の見栄えにしか効かず、作業の安全に効かないので、拒まず通して `PreToolUse` の `additionalContext` で知らせ、`hook-denials/` に `mode: "log-only"` の行で残す（`ledger.record_denial` の `mode`。tsukumo の `hook-denial-tally` は数えず `hook-denial-replay` が拾い、`material.py` と `intervention_effect.py` の `read_denials` も除く）。
確かめた範囲: 公式 docs（https://code.claude.com/docs/en/hooks ）は `PreToolUse` が `additionalContext` を受けると書く。入るのは `SubagentHandback` を呼んだエージェントの文脈と読める（docs が明記しているわけではない）。
確かめていないこと: 生きた環境で主に届くか、`SubagentHandback` は呼ぶと走りが終わるので呼んだ側が読めるか、`permissionDecision` を省いた `PreToolUse` 単独で通るか。
