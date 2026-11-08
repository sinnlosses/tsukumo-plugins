# hook を tools・deny・git の hook で置き換えられない理由

hook を足すか、別の仕組みに置き換えられるかを迷ったときだけ読む。hook の仕様（拒むもの・掛かる口）は `tw-reference.md`「hook の関門」節。

#### tools・permissions.deny で置き換えられない理由

**この3つの hook は、サブエージェント定義の `tools`・`disallowedTools` や設定の `permissions.deny` では置き換えられない**
（公式の説明: https://code.claude.com/docs/en/sub-agents ・ https://code.claude.com/docs/en/permissions ）。
`disallowedTools` に `Bash(git push *)` のような指定子を書いても、Bash というツールごと外れ、コマンド単位の拒否にならない。
コマンド単位で拒むには `permissions.deny` の `Bash(...)` か hook になるが、`permissions.deny` は設定にパターンで静的に書く規則で、
着手の印や `loopable` のような状態によって拒むかを変えられず、`sh -c '…'`・フルパスの呼び出しは素通しする。
plugin のサブエージェントは frontmatter の `hooks`・`permissionMode` を無視する。
すでに `tools`（許可の列）でやっているのは、同梱の実装役・受け入れ役・振り返り役のエージェント定義で使う道具だけを許し、`Agent` と（受け入れ役・振り返り役は）`Edit`・`Write`・`NotebookEdit` を許可の列に書かないことだけ。

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

#### `commit-guard`・`handback-guard` の判定の細部

`tw-reference.md` の表の行から外した細部。正典は `tw commit-guard --help`・`tw handback-guard --help`。

- `commit-guard` が通す形: コミットを作る git のサブコマンドは、その作業ツリーで `ref-guard` が効いていれば git の hook に任せる。`ref-guard` を外す書き方でも読むだけのコマンドは通す。設定 `shell_write_guard = "project"` ならシェルでの書き換えは見ない。検証コマンドは `--plan`・`--help`・`-h` を含む打ち方（段を流さない読むだけの旗）なら通す。作業先が別のリポジトリのときは、作業先の作業ツリー（枝の名前がタスクIDの小文字）での書き換えと検証コマンドの直打ちも拒み、そこでのコミットは拒まない。
- 委譲先への拒否の例外: `git stash` は `list`・`show`・`--help` を通す。`git add` は `--` の後ろの `-A` をファイル名として通し、`-n`/`--dry-run` も通す。`git push` は `-n`/`--dry-run` を除いて拒む。`tw land`・`tw ship` は Python から git を打つので Bash の hook を通らず、対象外。
- 書き込みの `bd`（`update`・`close`・`create`・`reclaim`・`unclaim`・`reopen`・`delete`・`label`・`dep add|remove`・`kv set|clear`・`config set` など）は主のセッションにも拒む。`show`・`list`・`ready`・`kv get`・`dep list`・`--readonly` 付きは通し、コマンドに `bd` の語があるときだけ解析する。
- 受け入れ役（`agent_type` の最後の `:` より後ろが受け入れ役のエージェント名、または手で付ける `--acceptor`）には、`tw` を打つ単純コマンドと検証コマンドの直打ちを拒む。判定は書き方の文字列で、`sh -c '…'` の中は見ない。受け入れ役への拒否では `tw verify` は案内しない（出力を読んで判定だけを返させるため）。
- `agent_type` を見ない理由: `agent_type` は `--agent` で起こした主のセッションにも入る。だから掛かるかどうかは `agent_id` の有無で決め、`agent_type` は受け入れ役の規則を足すときだけ使う。
- `handback-guard` が `## 構造の変更` の節の欠けを見るのは、最後の段の返却の本文（`SubagentHandback`）だけ。
- `handback-guard` は、作業のある返却（途中の段も含む）の本文（`SubagentHandback`）に `friction log:` の箇条が1行も無いと `FRICTION_LOG_MISSING` で拒む。書き忘れを機械で落とすため。作業の無い返却と本文が読めない `SubagentStop` は通す。書き方は `implementer-brief.md` の `## friction log`。
- `handback-guard` の印は `agent_id` ごとの `.tw/local/task-agent-writers/<agent_id>` に残り、着手の控えが無くなると消える。外への書き込みは印を残さない。受け入れ役の返却には掛からない。
- `handback-guard` が通す返却: 作業の無い返却（前提が誤り・`dropped`・計画だけの回）。作業先で `tw verify` が `MISSING`・`INVALID` を返すか、設定に検証コマンドが無ければ、検証を求めずに通す。段の控えの数えは、作業先の作業ツリーの判定では着手した作業ツリーの段の控えも含める。

#### `accept-hook` で受け入れの準備と締めを打つ件

ワークフロースクリプト（`flow = "script"`）の受け入れの準備（`tw accept`・`tw brief acceptor`）と締め（`tw accept --after-review`）は判断の要らない決まった手順で、受け入れ役は `tw` を打てない（上の `commit-guard` の受け入れ役の規則）。そのため以前は周回ごとに準備と締めの agent を起こしていた。受け入れ役の `SubagentStart`・`SubagentStop` の hook で打てば、その起動が消える。仕様の正典は `tw accept-hook --help`。

- 確かめた範囲（2026-11-08）: `SubagentStart` の hook が返す `hookSpecificOutput.additionalContext` は、`claude -p` の `Agent` で起こした `general-purpose` の最初の文脈に届いた。ワークフローの agent の transcript の先頭には `SubagentStart` の `hook_success` が1件ずつある。受け入れ役の transcript では `StructuredOutput` の行が `SubagentStop` の行より前にあり、`SubagentStop` の入力には `agent_transcript_path`・`stop_hook_active` が入る。
- 確かめていないこと: ワークフローの agent に `additionalContext` が届くか（`Agent` の起動では届いた）、`SubagentStop` の起動時点で transcript の書き込みが済んでいるか（読めなければ 0.5 秒おきに最大4回読み直し、それでも読めなければ締めを打たない）、同じイベントの hook が `hooks.json` の順に走るか（`accept-hook` を `lap-hook` の前に置く）。
- 届かないときの形: 受け入れ役は判定の行を見つけられず `acceptKind` を `STOP` で返し、ワークフロースクリプトは `stopped` で止まる。
- `skills` の流れでは打たない: メインが同じ部品を打ち、同じ型の受け入れ役を起こすので、二重になる。
- やめる条件: hook の失敗で受け入れが止まる run が出続けるなら、準備・締めの agent に戻す。
