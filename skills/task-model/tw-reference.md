## `tw` コマンドの参照

`tw <サブコマンド>` で打つ。`tw` は plugin の `bin/tw`（plugin が有効なあいだ Bash の PATH に入る）か、`install.sh` が PATH 上
（既定は `~/.local/bin`）に張る `scripts/tw/task.py` へのシンボリックリンクで、どちらもロジックを持たない。
PATH に無ければ plugin を入れるか、`./install.sh` を打ち直す（`task-model` を対象に含める）。どのディレクトリから打ってもリポジトリの根で動く。
出力は常に stdout の TSV で、1行目の先頭語が種類。
表は「いつ打つか」と出力の先頭語だけを持ち、引数・内部の動き・例外・出力の列は `tw <サブコマンド> --help` が持つ。

| サブコマンド | すること | 主な出力 |
| --- | --- | --- |
| `status [--all] [--check]` | 一覧と判定を読むだけで見るとき（`--check` は課題の形の判定） | 行 `id/status/…`、`---` の後に `counts`・`ready`・`todo_loopable`・`stale`・`invalid`・`triage`・`jira_close`・`retrospect_due`。`--check` は `OK`・`INVALID` |
| `new --summary … --difficulty … --loopable Y\|N [--deps …] [--hold\|--draft] [--direct] [--kind <種類>] --body-file <path\|->` | タスクやドラフトを登録するとき（`opus` で `## 解くべき論点` が空か「なし」、ドラフトの `- 観点:` が語彙の外なら終了コード2。`adopt` も論点は同じ） | `CREATED`・`LOCKED` |
| `claim T-xxx` | 作業を始めるとき（着手の印を立て、git の hook `ref-guard` を置く。置けなければ標準エラーに `ref-guard:` の1行） | `CLAIMED`・`TAKEN`・`NOT_READY`・`DIRTY`・`UNSHIPPED`・`GIT_READ_ONLY` |
| `release T-xxx [--force]` | 着手の印を外すとき | `RELEASED`・`NOT_CLAIMED`・`NOT_OWNER` |
| `done T-xxx [--dropped] [--reflection none\|some\|skipped] --result-file <path\|->` | 結果を書いて作業を閉じるとき（コミットも送りもしない）。ドラフトの却下にも使う。`--dropped` でなければ `## 結果` に必須行（`- 検証コマンド:`・`- 規約:`・`- 規約の指摘:`・`- 振り返り:`、完了条件に目視があれば `- 目視:`）が要り、欠ければ終了コード 2 で何も書かない（`- 検証コマンド:` だけは、台帳の直近の `verify` が通っていれば件数なしで tw が補う。`finish` も同じ） | `DONE`（`COMMITS_SINCE_CLAIM` が続くことがある）・`NOT_OWNER`・`NOT_READY`・`INVALID` |
| `ship` | コミットを主ブランチへ送るとき（rebase・検証・ff-only・送った範囲を metadata `task_shipped` に書いて印を閉じる・トラッカーへ送る） | `SHIPPED`・`NOTHING`（`NOT_CLOSED`・`TRACKER`・`BACKUP`・`SUBMODULE_NOT_SYNCED` が続くことがある）・`MAIN_DIRTY`・`CONFLICT`・`VERIFY_FAILED`・`RACE`・`GIT_READ_ONLY` |
| `land <枝>` | 作業先が別のリポジトリのタスクの受け入れで、その枝を主ブランチへ ff で合流し、作業ツリーと枝を消すとき | `LANDED`（`SUBMODULE_NOT_SYNCED`・`REMOVED`・`DELETED` が続く）・`NOT_REMOVED`・`NOT_LANDED`・`GIT_READ_ONLY` |
| `show T-xxx` | タスクを読むだけで見るとき | 本文。無ければ `NOT_READY` |
| `find [<語>…] [--kind <種類>]` | ID の無い頼み文の語と種類で `READY` を1件に絞るとき（読むだけ。当て方は WORKFLOW.md「kind（種類）」） | `FOUND`・`MANY`（`CANDIDATE`・`MORE` が続く）・`NONE`（どれも終了コード0） |
| `edit T-xxx [--body-file <path\|->] [--section <節名>]（組は繰り返せる） [--after-work] [--change-frame] [--summary …] [--difficulty …] [--loopable Y\|N] [--status todo\|hold] [--add-deps …] [--remove-deps …] [--direct Y\|N] [--kind <種類>\|-]` | 本文・属性・依存を直すとき（`--section` で節だけ。`--section`・`--body-file` の組を繰り返すと複数の節が1回で書け、反映も1回。ドラフトを `todo` に進めるのも `--status`。変えた節だけを見て、`opus` に上げる編集か論点を変える編集で `## 解くべき論点` が空か「なし」、`## 目的・背景` を変える編集で `- 観点:` が語彙の外なら終了コード2） | `EDITED`（`PLAN_AFTER_WORK`・`DIRECT_OFF` が続くことがある）・`NOT_READY`・`WORK_BEFORE_PLAN`・`FRAME_CHANGED` |
| `adopt <ID> --difficulty … --loopable … [--summary …] [--direct] [--kind <種類>] --body-file …` | 振り分け前の課題をタスクにするとき | `ADOPTED` |
| `sync` | トラッカーと同期するとき | `TRACKER\tOK\|FAILED`・`NOTHING`・`BUSY`（終了コード5） |
| `backup` | Beads のバックアップを取るとき | `BACKUP\tOK\|FAILED` |
| `jira-closed T-xxx…` | `jira:close` を外すとき | `CLEARED` |
| `plan-check T-xxx` | `## やること` を作業より先に書いたかを読むだけで確かめるとき（受け入れと返却の前） | `PLAN_FIRST`・`PLAN_REGISTERED`・`PLAN_NOT_FIRST\t…\t<理由>`・`PLAN_STALE\tT-xxx\t<変わったファイル>\t<理由>`（理由は `legacy`・`uncovered`・`path-missing`・`no-identifier`・`touched:<語,…>`・`unreadable`。どれも終了コード0）、段が `- 満たす完了条件:` の欄で完了条件を覆わなければ続けて `PLAN_UNCOVERED\tT-xxx\t<n,…>`、並列の計画では続けて `PARALLEL`・`SERIAL`・`STEP`。`NOT_OWNER` |
| `plan-phases T-xxx [--through <段>] [--also <n,…>] [--skipped <段,…>]` | tsukumo の `work_plan` に渡す段の並びと位置を読むだけで出すとき（実装のあとの段は `steps` の並びの2つ目以降のスキルから組む。既定の並びなら「受け入れ」「振り返り」「送り出し」。`--skipped` は飛ばした段で、出力の `skippedPhases` に入る。使い方はオーケストレーターの「進み具合の帯（`work_plan`）」） | `PHASES\tT-xxx\t<JSON 1行>`。`NO_PHASE`（終了コード2）・`NOT_OWNER` |
| `verify [T-xxx]` | 検証コマンドを打つとき（主ブランチの取り込み・整形・中身の鍵の控えも担う。ID は基本付けない） | `FOLDED`（取り込んだとき先頭）・`VERIFIED`・`VERIFIED_UNSTAMPED`・`VERIFY_NOT_PASSED`・`FORMAT_FAILED`・`PLAN_MISSING`・`CONFLICT`・`GIT_READ_ONLY`・`NOTHING`。末尾40行のあとに判定行をもう一度出すので、`tail` で切っても最後の行（取り込んだときは最後の2行）で判定が取れる |
| `verify-check` | 中身が `verify` の控えと同じかを読むだけで見るとき（同じなら検証を省く） | `VERIFIED_SAME`・`NOT_VERIFIED\t<理由>`（終了コード0）・`NOTHING` |
| `accept T-xxx [--after-review]` | 受け入れの機械的な並び（機械的な判定と `verify-check`）を打つとき。判定の `REVIEW\tcode` は、差分に文書の拡張子でないパス、または設定 `review_code_globs`（WORKFLOW.md の設定の表）の glob に当たるパスがあるとき。全部文書なら `SKIP\tdocs-only` | `REVIEW`・`SKIP`・`NOTHING`・`NO_RANGE`・`SNAPSHOT`・`FORMATTED`・`FORMAT_FAILED`・`MISSING`・`INVALID`・`NOT_CLAIMED`・`NOT_OWNER` |
| `finish T-xxx [--dropped] [--reflection none\|some\|skipped] --result-file <path\|-> --message <件名> [--add <ファイル>]…` | 結果・コミット・送りを一度に打つとき（`done` → `git add` → コミット → `ship`） | `done` の行、`COMMITTED`・`NO_COMMIT`、`ship` の行。`COMMITS_SINCE_CLAIM` が出れば終了コード12で止まる |
| `metrics [--days N] [--stages]` | 流れの数と段ごとの所要時間を読むだけで見るとき（振り返り） | `PERIOD`、続けて `shipped`・`lead_median_seconds`・`verify_per_task` など。`--stages` は `STAGE`・`OPEN`。`SKIPPED`・`EMPTY` |
| `config` | 解けた設定を読むだけで見るとき | `CONFIG`、続けて `<キー>\t<値>\t<config\|default>`（`steps` の値は詰めた JSON の配列）。設定が無ければ `MISSING`（終了コード6） |
| `config --json` | 解けた設定を JSON で読むとき（`config` と同じキー・値・出どころ。呼び手が表の文字列を割らずに読む口） | 1行の JSON（TSV の例外）`{"config": "<パス>", "keys": [{"key", "value", "source"}, …]}`。`steps`・`review_code_globs` の `value` は配列。`MISSING`（終了コード6）・`INVALID`（終了コード3）は JSON にせず `config` と同じ行。`--schema`・`set` とは一緒に打てない（終了コード2）。欄の中身は `task.py config --help` |
| `config --schema` | 設定の欄の形（並び `steps` の既定と候補）を読むだけで見るとき（設定画面が欄を描く口。設定にはよらない） | 1行の JSON（TSV の例外）`{"schema": 2, "keys": [...]}`。`--version` は知らない引数として終了コード2。`values[]` は `{value, label, title, description}` で、`title` は日本語の段の名前（SKILL.md の `metadata` の `workflow-step-title`。無ければ `label` と同じ）。欄の中身は `task.py config --help` |
| `config set <キー> <値>` | `steps` を検査してから `.tw/config.toml` に書くとき（ほかの行・並び・コメントは変えない。値は1行の JSON の文字列の配列） | `SET\t<キー>\t<値>`。知らないキー（廃止した `flow`・`parts.<枠>` を含む）・空や重複や候補にも今の値にも無い名前の並びは終了コード2（ファイルは変えない）。`MISSING`（終了コード6）・`INVALID`（終了コード3） |
| `config-doctor` | リポジトリが今の読み取りに合っているかを読むだけで点検するとき（`--fix` は無い） | `base_branch`・`config`・`python`・`git`・`bd`・`dolt`・`gh`・`tw`・`store`・`beads`・`tracker` の行（2語目が `OK`・`MISSING`・`INVALID`、前提の行は `OPTIONAL` も） |
| `commit-guard [--agent-scoped]` | hook が呼ぶ（手で打たない）。拒むものは下の「hook の関門」節 | 拒むときだけ deny の JSON 1行。通すときは何も出さない |
| `pause [T-xxx [<n>]]` | 判断が要って止める・目視待ち・計画を作業の後に書いた返却の直前に打つ（委譲先。並列の段の担当は ID と段の番号を付ける） | `PAUSED`・`NOT_OWNER` |
| `step T-xxx <n>` | 最後でない段を済ませて返す直前に打つ（委譲先。着手した作業ツリーで打つ。別のリポジトリのタスクでは、出力の鍵は着手側のもの。作業先の作業ツリーの控えは作業先の中身の鍵で別に書かれる）。設定に `step_check` があれば、控えを書く前にタスクの各作業ツリー（着手した木と、作業先の別のリポジトリの木）でその木の設定の `step_check` を打ち、落ちたら控えを書かない | `STEPPED`・`LAST_STEP`（終了コード4。最後の段は `tw verify` を通して返す）・`STEP_CHECK_FAILED`（終了コード4。続けて出力の末尾20行。直してから打ち直す）・`NOT_OWNER` |
| `lap T-xxx <段> [start\|end]` | オーケストレーターの段の頭と終わりを記録するとき（メイン。段は `direct`・`delegate`・`accept`・`review`・`retro`・`land`・`sync`） | `LAPPED`・`NOT_CLAIMED` |
| `relay T-xxx <鍵> [--body-file <path\|->]` | 段の結果を後の段へ申し送りとして書くとき（`--body-file` 付き。着手の印の持ち主だけ）と、前の段の申し送りを鍵で引くとき（欄と鍵は WORKFLOW.md「段の申し送り」） | `RELAYED`・`RELAY`（次の行から本文）・`NONE`・`NOT_OWNER`・`NOT_READY` |
| `handback-guard [--agent-scoped]` | hook が呼ぶ（手で打たない）。拒むものは下の「hook の関門」節 | 拒むときだけ JSON 1行（`SubagentStop` は `block`、`PreToolUse` は deny）。通すときは何も出さない |
| `ask-guard` | hook が呼ぶ（手で打たない）。拒むものは下の「hook の関門」節 | 拒むときだけ deny の JSON 1行（理由はタスクIDと次の一手）。通すときは何も出さない |
| `ref-guard <state>` | git の `reference-transaction` hook が呼ぶ（手で打たない）。拒むものは下の「hook の関門」節 | 拒むときだけ標準エラーに理由の1行と終了コード1（git の hook の取り決め）。通すときは何も出さず0 |

| 終了コード | 先頭語 | 意味 | スキルがすること |
| --- | --- | --- | --- |
| 0 | 各成功語（`plan-check` の `PLAN_NOT_FIRST`・`PLAN_STALE`・`PLAN_UNCOVERED`・`verify-check` の `NOT_VERIFIED` を含む） | 成功（`PLAN_NOT_FIRST`・`PLAN_STALE`・`PLAN_UNCOVERED`・`NOT_VERIFIED` は止める合図ではなく、着手・受け入れで扱う知らせ） | 次へ |
| 1 | （traceback） | 環境の故障 | エラー出力を報告して止まる。**手で代用しない** |
| 2 | （stderr） | 渡した引数・本文の誤り | 直して打ち直す |
| 3 | `INVALID` | データの不備（読めない課題〔label の語彙の外など〕、設定が読めない、主ブランチが決まらない、`--check` の重複、トラッカーの付け替え先が既にある） | 理由をそのまま報告して止まる。**直しに行かない** |
| 4 | `TAKEN`・`NOT_READY`・`DIRTY`・`MAIN_DIRTY`・`UNSHIPPED`・`LOCKED`・`NOT_OWNER`・`WORK_BEFORE_PLAN`・`FRAME_CHANGED`・`LAST_STEP`・`STEP_CHECK_FAILED`・`NOT_LANDED`・`NOT_REMOVED` | いまの状態では進めない | 各スキルの表のとおり（別の1件を選ぶか止まる） |
| 5 | `BUSY`（`sync` だけ） | 別の `tw sync` が台帳の錠を持っている（待たずに返った。何も書き換えていない）。持ち主のプロセスが無い古い錠なら、行にその旨が添えてある | 少し置いて打ち直す。古い錠の注記があれば、行を添えて人に預ける（錠のファイルは人が確かめて消す） |
| 6 | `MISSING`・`MISSING\t<.beads のパス>` | タスク運用を始めていない（`.tw/config.toml` が無い）／設定はあるのに `.beads` が無い | 用意の入口（WORKFLOW.md の用語）を案内して止まる |
| 7 | `CONFLICT` | `ship` の rebase が衝突した（`--abort` 済み）か、`verify` の主ブランチの取り込みが衝突した（何も書き換えていない） | 衝突したファイルを添えて人に預ける。`verify` の衝突を解いたあとは `tw verify` を打ち直す |
| 8 | `VERIFY_FAILED` | 付け替えのあとの検証が落ちた（送っていない）。行は `VERIFY_FAILED\t<コマンド>\t<ログのパス>` で、続けて出力の末尾40行。ログは stdout と stderr を出た順に残した全文で、落ちた回のものを時刻つきの別名（`task-ship-verify.failed-<時刻>.log`）に写したもの。打ち直しても上書きされない | ログを読み、落ちた箇所を添えて人に預ける |
| 9 | `RACE` | `--ff-only` が3回続けて落ちた | 人に預ける |
| 10 | `VERIFY_NOT_PASSED`・`FORMAT_FAILED`・`PLAN_MISSING`・`TRACKER\tFAILED`・`BACKUP\tFAILED`（後ろ2つは `sync`・`backup` だけ） | `verify` の検証コマンドが落ちた／整形コマンドが落ちた（検証は打っていない）／`## やること` が空のまま作業があるので打たなかった（どちらも控えは消えた）／トラッカー・バックアップに届かない | `VERIFY_NOT_PASSED`・`FORMAT_FAILED` は出力の末尾（全体はログ）を読んで直し、`PLAN_MISSING` は `## やること` を書いて `tw edit T-xxx --after-work` で渡し、打ち直す。`TRACKER`・`BACKUP` は行を添えて報告する（タスクの操作は済んでいる） |
| 11 | `GIT_READ_ONLY` | `.git` に書けない（`EACCES`・`EPERM`・`EROFS`）のに、git に書く操作が要る（`ship`・`land`、追い付くか枝を切る `claim`、取り込む `verify`）。行は `GIT_READ_ONLY\t<書けない git dir>\t<次の一手>`。打つ前に確かめるので、作業ツリーも Beads も変わっていない | sandbox の外で同じコマンドを打ち直す（`README.md`「`.git` が読み取り専用の sandbox で打つとき」節）。打ち直せなければ行を添えて人に預ける |
| 12 | `COMMITS_SINCE_CLAIM`（`finish` だけ） | `done` は済んだが、`claim` のあとに委譲先が作ったコミットの合図が出たので、コミットと `ship` を打たずに止めた | 送り出しの段の `COMMITS_SINCE_CLAIM` の扱いに従って判断し、個別に `git add`・`git commit`・`tw ship` を打つ |

`/loop` を続けるかは、オーケストレーターの「`/loop` の続行判断」で決める（終了コードの一覧はここに持たない）。

`.git` が読み取り専用の sandbox で打つときは `README.md`「`.git` が読み取り専用の sandbox で打つとき」節。

**`config-doctor` だけは終了コード1の意味が違う**（「直すものがある」。環境の故障ではない）。検査の最悪値を返し、`INVALID` があれば3、直すもの（必須の前提の欠落・古さを含む）があれば1、全部OKで0。点検のコマンドなので、1で止まっても報告するだけでよい。

### hook の関門

hook 4種（Claude Code の hook 3種と git の hook `ref-guard`）の仕様の正典はこの節。判定の細部（何をコミット・書き換えとみなすか、通す書き方、読む控えの順）は `tw commit-guard --help`・`tw handback-guard --help`・`tw ask-guard --help`・`tw ref-guard --help` が持つ。

| hook | 掛かる場面 | 拒むもの | 拒まれた側の次の一手 |
| --- | --- | --- | --- |
| `commit-guard` | 着手の印（`.tw/local/task-open-claims/T-xxx`）が立ってから `done` を打つまでの作業ツリーで、サブエージェント（入力に `agent_id` がある。名前は問わない）の Bash | コミットを作る git のサブコマンド（その作業ツリーで `ref-guard` が効いていないときだけ。効いていれば git の hook に任せる）・`ref-guard` を外す書き方のコマンド（`TW_REF_GUARD_ALLOW=`・`export TW_REF_GUARD_ALLOW`・`core.hooksPath` を書き換える形。読むだけのコマンドは通す。形は下の「掛かる口」の段落）・シェルでのファイルの書き換え・設定の検証コマンドの直打ち。タスクの `### 作業先` が別のリポジトリなら、作業先の作業ツリー（枝の名前がタスクIDの小文字）での書き換えと検証コマンドの直打ちも拒む（そこでのコミットは拒まない） | 回避しない。書き換えは `Edit`・`Write` で、検証は `tw verify` で打つ。コミットはせず、変更を作業ツリーに残して返す |
| `handback-guard` | 同じ作業ツリーで、`Edit`・`Write`・`NotebookEdit` を一度でも使ったサブエージェント（名前は問わない。使ったことは `agent_id` ごとの印 `.tw/local/task-agent-writers/<agent_id>` に残り、着手の控えが無くなると消える）の返却（`SubagentStop` と、`PreToolUse` の `SubagentHandback`）。書き換えない受け入れ役の返却には掛からない | 作業（着手より後のコミットか、タスクのファイル以外の変更）があるのに、`plan-check` が `PLAN_FIRST`・`PLAN_REGISTERED` でないか、`verify-check` が `VERIFIED_SAME` でない返却。段が2つ以上の計画で `tw step` の控えが1つも無い返却。最後の段の返却の本文（`SubagentHandback`）に `## 構造の変更` の節が無いか、節が空か、中身が形（各行が `+|-|~ A -> B`、または全体が `なし: <理由>` の1行）に合わないもの。作業の無い返却（前提が誤り・`dropped`・計画だけの回）は通す。作業先が別のリポジトリなら、枝の名前がタスクIDの小文字の作業先の作業ツリーにも同じ判定を当てる（別の名前の枝は見ない。作業先で `tw verify` が `MISSING`・`INVALID` を返すか、設定に検証コマンドが無ければ検証を求めずに通す） | 理由に従って直してから返す（計画は `tw edit`、検証は `tw verify`、途中の段は `tw step`、止める返却は `tw pause`）。`NOT_VERIFIED scheme <パス>` なら、第3列（照らした側のチェックアウトの `tw_verify.py` のパス）のチェックアウトの `bin/tw`（またはそこへ解決される PATH の `tw`）で `tw verify` を打ち直す |
| `ask-guard` | 着手の印が立った作業ツリーでの `AskUserQuestion`（主のセッションにもサブエージェントにも掛かる） | 控えのタスクが `loopable: Y` で、作業の段の後でない質問。作業の段の後とは、主のセッション（入力に `agent_id` が無い）で、最後の `claim` より後の最後の `tw lap` が `accept` の `end` か、段 `retro`・`land`・`sync` のとき（振り返りを外した流れでも受け入れの後は通る）。サブエージェントの質問は段によらず拒む。`report` の `favor`・許可確認・道具を呼ばずにターンを終える止まり方は捕まえない（WORKFLOW.md「loopable（`/loop` に載せてよいか）」節の表で先に防ぐ） | `tw release T-xxx` で着手を戻し、`tw edit T-xxx --status hold` で `hold` にして、`## 注意` に論点を書く（着手中は `--status` を直せない。続行はオーケストレーターの `/loop` の続行判断） |
| `ref-guard`（git の `reference-transaction`） | 着手の印が立ってから `done`・`release`・`ship` で控えが消えるまでの作業ツリーで打たれた git の参照の更新。呼び手（主のセッションかサブエージェントか）もシェルの書き方（`sh -c`・スクリプト越し・`--no-verify`）も問わない | `HEAD` か今の枝を、主ブランチの祖先でないコミットへ動かす更新（`commit`・`commit --amend`・`merge`・`cherry-pick`・`rebase`・`reset` など）。`tw` 自身の git（`ship`・`verify` の取り込み・`claim` の枝切り・`finish` のコミット）は変数 `TW_REF_GUARD_ALLOW` を付けるので拒まない。控えの無い作業ツリー・ほかの枝の更新・主ブランチへの早送り・同じコミットへの更新（`stash` など）は通す | 回避しない。コミットはせず、変更を作業ツリーに残して返す。メインは `tw finish`（`done` で控えを消してからコミットする）で送る。控えがあるうちにメインが `tw` を通さずにどうしてもコミットするときだけ、`TW_REF_GUARD_ALLOW=1` を付けて打つ |

**掛かる口**: plugin で入れると、`hooks/hooks.json` が `"${CLAUDE_PLUGIN_ROOT}/bin/tw"` を呼ぶ（plugin のエージェント定義は frontmatter の `hooks` を無視するため）。
`commit-guard`・`handback-guard` は `--agent-scoped` 付きで、入力に `agent_id` があるときだけ掛かる（`agent_type` は `--agent` で起こした主のセッションにも入るので見ない）。`handback-guard` は `Edit`・`Write`・`NotebookEdit` の `PreToolUse` でも呼ばれ、書いた印を残す。`commit-guard` は、入力に `agent_id`・`session_id` があり `cwd` の作業ツリーの着手の控えがちょうど1件なら、拒否の判定とは別に、台帳の `flow/` へ `agent` の出来事（`agent`・`session`）を最初の1回だけ足す（`WORKFLOW.md`「ファイル配置と設定ファイル」）。`ask-guard` は付けずに呼び、主のセッションにも掛かる。
`install.sh` のリンクだけで入れると `hooks/hooks.json` は読まれず、同梱の実装役のエージェント定義（`agents/` の下）の frontmatter の hook が PATH の `tw` で `commit-guard`・`handback-guard` を掛ける（`ask-guard` は掛からない）。この口は同梱の実装役にしか掛からず、frontmatter に hook を持たない自作の実装役には掛からない（導入経路の一本化 GH-771 の結論に任せる）。
リンクと plugin を併用すると、同梱の実装役への委譲で両方の口が走り、PATH の `tw` と plugin の `bin/tw` の版が違うと `tw verify` のあとの関門が `NOT_VERIFIED scheme` で止まるので、2つを同じチェックアウトに揃える。
`ref-guard` は `tw claim` が共有の git dir の `hooks/reference-transaction` に置く薄い殻で、全作業ツリーに1つ。控えの無い作業ツリーでは何もしないので、`done`・`ship` では消さない（消してよい。次の `claim` が置き直す）。
Bash のコマンド文字列に `git` が出ないコミット（`sh -c`・スクリプト越し）も、`ref-guard` が効く作業ツリーでは git の側で拒む。
`commit-guard` は打つたびに `ref-guard` が効くかを確かめ（`ref_guard.inactive_reason`）、効かなければコミットを自分の文字列の判定で拒む。
効かない場面は次のすべてで、`tw claim` も置いたあとに同じ判定をして、効かなければ標準エラーに `ref-guard:` の1行を出す:
`core.hooksPath` がある・共有の hooks の `reference-transaction` が無い・印の無い利用者の hook である・殻がこの `tw` の `task.py` を指していない（指す `task.py` が無い・別のチェックアウトを指す）・殻が実行できない・git が 2.28 より古いか版が読めない・`python3` が PATH に無い・環境に `TW_REF_GUARD_ALLOW` がある。
このとき `sh -c`・スクリプト越しのコミットは、`commit-guard` の文字列の判定でも拾えない。
効いていても、コマンドの文字列に見える hook を外す書き方は `commit-guard` が拒む（`sh -c '…'` の中も見る）:
`TW_REF_GUARD_ALLOW=`・`export TW_REF_GUARD_ALLOW`・`-c core.hooksPath=…`・`--config-env=core.hooksPath=…`・`GIT_CONFIG_KEY_<n>=core.hooksPath`・`GIT_CONFIG_PARAMETERS` に `core.hooksPath` を含む代入・値を渡す `git config [<旗>] core.hooksPath <値>`（`set` を含む）。
`hooksPath` を読むだけのコマンド（`grep`・`rg`・`git config --get`・`get`・`--list`・値を渡さない `git config core.hooksPath`）と、既定に戻す `git config --unset core.hooksPath` は通す。
スクリプトの中で外した git・`git -C <控えの無い作業ツリー>`・今の枝でない参照の `update-ref` は拒まない。
これらと、`Edit`・`Write`・`NotebookEdit` を使わずに書き換えたサブエージェントの返却は、`finish`・`done` の `COMMITS_SINCE_CLAIM` と受け入れの `plan-check`・`verify-check` が事後に拾う。

**lap は並びのスキルではなくオーケストレーターが段の前後で打つ**（`tw lap`。段の名前は `direct`・`delegate`・`accept`・`review`・`retro`・`land`・`sync` で固定）。`ask-guard` はこの段で境を決め、並びのスキルの名前は見ない。

**`commit-guard`・`handback-guard`・`ask-guard` は Claude Code の hook の取り決めに従う**: 出力は TSV でなく JSON で、終了コードはいつも0（`ref-guard` は git の hook の取り決めに従い、拒むときだけ終了コード1）。
読めない入力も何も出さずに通す。タスク運用を始めていないリポジトリ・git の外でも打てる。
plugin の `hooks.json` は、`tw` 自体が落ちた（終了コード 78・127 以外の非 0）ときは標準エラーに理由を出して終了コード 1 で返し（作業は止めない）、78・127 は案内して 0 で返す。

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
