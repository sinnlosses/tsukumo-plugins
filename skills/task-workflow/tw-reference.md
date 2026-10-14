## `tw` コマンドの参照

`tw <サブコマンド>` で打つ。`tw` は plugin の `bin/tw`（plugin が有効なあいだ Bash の PATH に入る）か、`install.sh` が PATH 上
（既定は `~/.local/bin`）に張る `scripts/task.py` へのシンボリックリンクで、どちらもロジックを持たない。
PATH に無ければ plugin を入れるか、`./install.sh` を打ち直す（`task-workflow` を対象に含める）。どのディレクトリから打ってもリポジトリの根で動く。
出力は常に stdout の TSV で、1行目の先頭語が種類。
表は「いつ打つか」と出力の先頭語だけを持ち、引数・内部の動き・例外・出力の列は `tw <サブコマンド> --help` が持つ。

| サブコマンド | すること | 主な出力 |
| --- | --- | --- |
| `status [--all] [--check]` | 一覧と判定を読むだけで見るとき（`--check` は課題の形の判定） | 行 `id/status/…`、`---` の後に `counts`・`ready`・`todo_loopable`・`stale`・`invalid`・`triage`・`jira_close`・`retrospect_due`。`--check` は `OK`・`INVALID` |
| `new --summary … --difficulty … --loopable Y\|N [--deps …] [--hold\|--draft] [--direct] --body-file <path\|->` | タスクやドラフトを登録するとき | `CREATED`・`LOCKED` |
| `claim T-xxx` | 作業を始めるとき（着手の印を立てる） | `CLAIMED`・`TAKEN`・`NOT_READY`・`DIRTY`・`UNSHIPPED`・`GIT_READ_ONLY` |
| `release T-xxx [--force]` | 着手の印を外すとき | `RELEASED`・`NOT_CLAIMED`・`NOT_OWNER` |
| `done T-xxx [--dropped] --result-file <path\|->` | 結果を書いて作業を閉じるとき（コミットも送りもしない）。ドラフトの却下にも使う | `DONE`（`COMMITS_SINCE_CLAIM` が続くことがある）・`NOT_OWNER`・`NOT_READY`・`INVALID` |
| `ship` | コミットを主ブランチへ送るとき（rebase・検証・ff-only・印を閉じる・トラッカーへ送る） | `SHIPPED`・`NOTHING`（`NOT_CLOSED`・`TRACKER`・`BACKUP`・`SUBMODULE_NOT_SYNCED` が続くことがある）・`MAIN_DIRTY`・`CONFLICT`・`VERIFY_FAILED`・`RACE`・`GIT_READ_ONLY` |
| `land <枝>` | 作業先が別のリポジトリのタスクの受け入れで、その枝を主ブランチへ ff で合流し、作業ツリーと枝を消すとき | `LANDED`（`SUBMODULE_NOT_SYNCED`・`REMOVED`・`DELETED` が続く）・`NOT_REMOVED`・`NOT_LANDED`・`GIT_READ_ONLY` |
| `show T-xxx` | タスクを読むだけで見るとき | 本文。無ければ `NOT_READY` |
| `edit T-xxx [--body-file <path\|->] [--section <節名>] [--after-work] [--change-frame] [--summary …] [--difficulty …] [--loopable Y\|N] [--status todo\|hold] [--add-deps …] [--remove-deps …] [--direct Y\|N]` | 本文・属性・依存を直すとき（`--section` で節だけ、ドラフトを `todo` に進めるのも `--status`） | `EDITED`（`PLAN_AFTER_WORK`・`DIRECT_OFF` が続くことがある）・`NOT_READY`・`WORK_BEFORE_PLAN`・`FRAME_CHANGED` |
| `adopt <ID> --difficulty … --loopable … [--summary …] [--direct] --body-file …` | 振り分け前の課題をタスクにするとき | `ADOPTED` |
| `sync` | トラッカーと同期するとき | `TRACKER\tOK\|FAILED`・`NOTHING`・`BUSY`（終了コード5） |
| `backup` | Beads のバックアップを取るとき | `BACKUP\tOK\|FAILED` |
| `jira-closed T-xxx…` | `jira:close` を外すとき | `CLEARED` |
| `plan-check T-xxx` | `## やること` を作業より先に書いたかを読むだけで確かめるとき（受け入れと返却の前） | `PLAN_FIRST`・`PLAN_REGISTERED`・`PLAN_NOT_FIRST\t…\t<理由>`・`PLAN_STALE`（どれも終了コード0）、並列の計画では続けて `PARALLEL`・`SERIAL`・`STEP`。`NOT_OWNER` |
| `verify [T-xxx]` | 検証コマンドを打つとき（主ブランチの取り込み・整形・中身の鍵の控えも担う。ID は基本付けない） | `FOLDED`（取り込んだとき先頭）・`VERIFIED`・`VERIFIED_UNSTAMPED`・`VERIFY_NOT_PASSED`・`FORMAT_FAILED`・`PLAN_MISSING`・`CONFLICT`・`GIT_READ_ONLY`・`NOTHING`。末尾40行のあとに判定行をもう一度出すので、`tail` で切っても最後の行（取り込んだときは最後の2行）で判定が取れる |
| `verify-check` | 中身が `verify` の控えと同じかを読むだけで見るとき（同じなら検証を省く） | `VERIFIED_SAME`・`NOT_VERIFIED\t<理由>`（終了コード0）・`NOTHING` |
| `accept T-xxx [--after-review]` | 受け入れの機械的な並び（機械的な判定と `verify-check`）を打つとき | `REVIEW`・`SKIP`・`NOTHING`・`NO_RANGE`・`SNAPSHOT`・`FORMATTED`・`FORMAT_FAILED`・`MISSING`・`INVALID`・`NOT_CLAIMED`・`NOT_OWNER` |
| `finish T-xxx [--dropped] --result-file <path\|-> --message <件名> [--add <ファイル>]…` | 結果・コミット・送りを一度に打つとき（`done` → `git add` → コミット → `ship`） | `done` の行、`COMMITTED`・`NO_COMMIT`、`ship` の行。`COMMITS_SINCE_CLAIM` が出れば終了コード12で止まる |
| `metrics [--days N] [--stages]` | 流れの数と段ごとの所要時間を読むだけで見るとき（振り返り） | `PERIOD`、続けて `shipped`・`lead_median_seconds`・`verify_per_task` など。`--stages` は `STAGE`・`OPEN`。`SKIPPED`・`EMPTY` |
| `config` | 解けた設定を読むだけで見るとき | `CONFIG`、続けて `<キー>\t<値>\t<config\|default>`。設定が無ければ `MISSING`（終了コード6） |
| `config-doctor` | リポジトリが今の読み取りに合っているかを読むだけで点検するとき（`--fix` は無い） | `base_branch`・`config`・`python`・`git`・`bd`・`dolt`・`gh`・`tw`・`store`・`beads`・`tracker` の行（2語目が `OK`・`MISSING`・`INVALID`、前提の行は `OPTIONAL` も） |
| `commit-guard [--agent-scoped]` | hook が呼ぶ（手で打たない）。着手の印が立った作業ツリーで、委譲先のコミット・シェルでのファイルの書き換え・検証コマンドの直打ちを拒む | 拒むときだけ deny の JSON 1行。通すときは何も出さない |
| `pause [T-xxx [<n>]]` | 判断が要って止める・目視待ち・計画を作業の後に書いた返却の直前に打つ（委譲先。並列の段の担当は ID と段の番号を付ける） | `PAUSED`・`NOT_OWNER` |
| `step T-xxx <n>` | 最後でない段を済ませて返す直前に打つ（委譲先。着手した作業ツリーで打つ） | `STEPPED`・`LAST_STEP`（終了コード4。最後の段は `tw verify` を通して返す）・`NOT_OWNER` |
| `lap T-xxx <段> [start\|end]` | `/next-task` の段の頭と終わりを記録するとき（メイン。段は `direct`・`delegate`・`accept`・`review`・`retro`・`land`・`sync`） | `LAPPED`・`NOT_CLAIMED` |
| `handback-guard [--agent-scoped]` | hook が呼ぶ（手で打たない）。作業があるのに計画・検証が欠けた委譲先の返却を拒む。段が2つ以上の計画で `tw step` の控えが1つも無い返却も拒む。最後の段の返却の本文（`PreToolUse` の `SubagentHandback`）に `## 構造の変更` の節が無いときも拒む。控えた `tw` と照らす `tw` の版が違うと `NOT_VERIFIED scheme` で止まるので、第3列のチェックアウトの `bin/tw` で `tw verify` を打ち直す | 拒むときだけ JSON 1行（`SubagentStop` は `block`、`PreToolUse` は deny）。通すときは何も出さない |
| `ask-guard` | hook が呼ぶ（手で打たない）。`loopable: Y` のタスクの着手中に、振り返り以外で `AskUserQuestion` を拒む | 拒むときだけ deny の JSON 1行（理由はタスクIDと次の一手）。通すときは何も出さない |

| 終了コード | 先頭語 | 意味 | スキルがすること |
| --- | --- | --- | --- |
| 0 | 各成功語（`plan-check` の `PLAN_NOT_FIRST`・`PLAN_STALE`・`verify-check` の `NOT_VERIFIED` を含む） | 成功（`PLAN_NOT_FIRST`・`PLAN_STALE`・`NOT_VERIFIED` は止める合図ではなく、着手・受け入れで扱う知らせ） | 次へ |
| 1 | （traceback） | 環境の故障 | エラー出力を報告して止まる。**手で代用しない** |
| 2 | （stderr） | 渡した引数・本文の誤り | 直して打ち直す |
| 3 | `INVALID` | データの不備（読めない課題〔label の語彙の外など〕、設定が読めない、主ブランチが決まらない、`--check` の重複、トラッカーの付け替え先が既にある） | 理由をそのまま報告して止まる。**直しに行かない** |
| 4 | `TAKEN`・`NOT_READY`・`DIRTY`・`MAIN_DIRTY`・`UNSHIPPED`・`LOCKED`・`NOT_OWNER`・`WORK_BEFORE_PLAN`・`FRAME_CHANGED`・`LAST_STEP`・`NOT_LANDED`・`NOT_REMOVED` | いまの状態では進めない | 各スキルの表のとおり（別の1件を選ぶか止まる） |
| 5 | `BUSY`（`sync` だけ） | 別の `tw sync` が台帳の錠を持っている（待たずに返った。何も書き換えていない）。持ち主のプロセスが無い古い錠なら、行にその旨が添えてある | 少し置いて打ち直す。古い錠の注記があれば、行を添えて人に預ける（錠のファイルは人が確かめて消す） |
| 6 | `MISSING`・`MISSING\t<.beads のパス>` | タスク運用を始めていない（`.tw/config.toml` が無い）／設定はあるのに `.beads` が無い | `/setup-tasks` を案内して止まる |
| 7 | `CONFLICT` | `ship` の rebase が衝突した（`--abort` 済み）か、`verify` の主ブランチの取り込みが衝突した（何も書き換えていない） | 衝突したファイルを添えて人に預ける。`verify` の衝突を解いたあとは `tw verify` を打ち直す |
| 8 | `VERIFY_FAILED` | 付け替えのあとの検証が落ちた（送っていない）。行は `VERIFY_FAILED\t<コマンド>\t<ログのパス>` で、続けて出力の末尾40行。ログは stdout と stderr を出た順に残した全文で、落ちた回のものを時刻つきの別名（`task-ship-verify.failed-<時刻>.log`）に写したもの。打ち直しても上書きされない | ログを読み、落ちた箇所を添えて人に預ける |
| 9 | `RACE` | `--ff-only` が3回続けて落ちた | 人に預ける |
| 10 | `VERIFY_NOT_PASSED`・`FORMAT_FAILED`・`PLAN_MISSING`・`TRACKER\tFAILED`・`BACKUP\tFAILED`（後ろ2つは `sync`・`backup` だけ） | `verify` の検証コマンドが落ちた／整形コマンドが落ちた（検証は打っていない）／`## やること` が空のまま作業があるので打たなかった（どちらも控えは消えた）／トラッカー・バックアップに届かない | `VERIFY_NOT_PASSED`・`FORMAT_FAILED` は出力の末尾（全体はログ）を読んで直し、`PLAN_MISSING` は `## やること` を書いて `tw edit T-xxx --after-work` で渡し、打ち直す。`TRACKER`・`BACKUP` は行を添えて報告する（タスクの操作は済んでいる） |
| 11 | `GIT_READ_ONLY` | `.git` に書けない（`EACCES`・`EPERM`・`EROFS`）のに、git に書く操作が要る（`ship`・`land`、追い付くか枝を切る `claim`、取り込む `verify`）。行は `GIT_READ_ONLY\t<書けない git dir>\t<次の一手>`。打つ前に確かめるので、作業ツリーも Beads も変わっていない | sandbox の外で同じコマンドを打ち直す（`README.md`「`.git` が読み取り専用の sandbox で打つとき」節）。打ち直せなければ行を添えて人に預ける |
| 12 | `COMMITS_SINCE_CLAIM`（`finish` だけ） | `done` は済んだが、`claim` のあとに委譲先が作ったコミットの合図が出たので、コミットと `ship` を打たずに止めた | `next-task/commits-since-claim.md` に従って判断し、個別に `git add`・`git commit`・`tw ship` を打つ |

`/loop` を続けるかは、`skills/next-task/SKILL.md` の「`/loop` の続行判断」で決める（終了コードの一覧はここに持たない）。

`.git` が読み取り専用の sandbox で打つときは `README.md`「`.git` が読み取り専用の sandbox で打つとき」節。

**`config-doctor` だけは終了コード1の意味が違う**（「直すものがある」。環境の故障ではない）。検査の最悪値を返し、`INVALID` があれば3、直すもの（必須の前提の欠落・古さを含む）があれば1、全部OKで0。点検のコマンドなので、1で止まっても報告するだけでよい。

**`commit-guard`・`handback-guard`・`ask-guard` は Claude Code の hook の取り決めに従う**: 出力は TSV でなく JSON で、終了コードはいつも0。
読めない入力も何も出さずに通す。タスク運用を始めていないリポジトリ・git の外でも打てる。

この3つの hook を `tools`・`permissions.deny` で置き換えられない理由は `agents/no-delegate.md`「hook を tools・permissions.deny で置き換えられない理由」節。
