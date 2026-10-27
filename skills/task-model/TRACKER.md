# Beads とトラッカー（同期の実装の記録）

WORKFLOW.md「Beads とトラッカー」から切り出した。トラッカーの同期（`tracker.py`・`beads.py`）を直すときに読む。
断りのない「…」の節名は WORKFLOW.md の節を指す。

**用意**: 用意の入口（WORKFLOW.md の用語）が打つ `scripts/core/init.py` が、主ブランチを出している作業ツリー（本体）で
`bd init --stealth -p gh`（トラッカーが `github`）か `-p t`（それ以外）を打つ。`hold` は組み込みの
状態 `deferred` に置く（独自の状態は GitHub との往復で `open` に戻るので使わない）。ドラフトも `deferred` に置き、
metadata `draft` で `hold` と見分ける（独自の状態 `draft` は `status::` の label を持たない `open` の Issue として出て、
GitHub で本文を直したあとの取り込みで `open` に戻る。`deferred` は label `status::deferred` で往復して残るが、
取り込みは metadata を消すので、`tw` が取り込みのあとに `draft` を戻す。`bd` 1.3.1 で確かめた）。`.beads` は本体の根に1つで、
どの作業ツリーの `bd` も同じデータベースを読む。`--stealth` は `.git/info/exclude` で `.beads` を外し、
コミットも `AGENTS.md`・`CLAUDE.md` への書き足しもしない（**`--stealth` なしの `bd init` を打たない**。
両方を書き足して自動でコミットする）。ただし `--stealth` は利用者の `~/.config/bd/config.yaml` に `no-git-ops: true` を
書き足す（端末全体の設定）。`bd` を同時に何本も `init` すると、この設定ファイルの
ほかの値（使用状況の送信を止めた `metrics.disabled`）まで書き戻したことがあるので、`init` は1本ずつ打つ
（自己テストは `HOME` を一時ディレクトリへ向けて利用者の設定に触れない）。`github` なら人が `gh auth login`（`project` スコープ）と
`bd config set github.repository <owner>/<repo>` を済ませる。

**タスクの要素と Beads の置き場**（`beads.py` の docstring に抜粋がある）:

| タスクの要素 | Beads の置き場 |
| --- | --- |
| タスクID | `github`: 外は `GH-5`、Beads の中は `gh-5`（Issue 番号）。`なし`・`jira`: 外は `T-123`、Beads の中は `t-123`（接頭辞は小文字だけ）。`jira` で取り込んだ課題は外が `PROJ-123`、中が `proj-123` |
| 採番 | `github`: GitHub の採番（下の「GitHub との双方向」の登録）。`なし`・`jira`: `bd create --id t-<n>`（同じ番号は1つしか作れない。負けたら次の番号で打ち直し、20回で `LOCKED`）。最後の番号は `bd kv` の `task-workflow.last-id`。候補は Beads の番号・`bd kv`・主ブランチの旧いアーカイブの最大（[legacy.md](legacy.md)） |
| 着手の印 | `bd update --claim`（actor は作業ツリーの名前）。`claim` した時点の `HEAD` と枝は metadata `task_claim_head`・`task_branch` |
| `NOT_OWNER` | `done`・`release` の前に assignee が自分かを見る（`bd close` も actor が違えば拒む） |
| `todo`・`hold` | `open`・`deferred`（`bd ready` から外れる。GitHub では label `status::deferred`）。旧形式の `pending` も `hold` と読む（[legacy.md](legacy.md)） |
| `draft`（ドラフト） | `deferred` ＋ metadata `draft`（値 `Y`。GitHub には載らず、取り込みで消えたら取り込み前の値を戻す。GitHub で label を外されて `open` に戻っても、印があればドラフトと読む。ドラフトのまま閉じた課題でも戻す） |
| ドラフトから進めた課題の出自 | metadata `from_draft`（値は `- 観点:` の観点か `不明`。GitHub には載らず、取り込みで消えたら取り込み前の値を戻す。閉じた課題でも戻す） |
| `done`・`dropped` | `bd close`（`dropped` は label `cancelled` を足す。独自の状態 `cancelled` は依存を解決しないので使わない） |
| `difficulty`・`loopable` | metadata `difficulty`・`loopable`（GitHub には載らず、取り込みで消えたら取り込み前の値を戻す。label `difficulty:`・`loopable:` は読まない） |
| 委譲しない近道の印 | label `direct:Y`（印が無ければ label を置かない。`direct:` の label が `Y` 以外か2つ以上なら INVALID） |
| 種類（WORKFLOW.md「kind（種類）」） | label `kind:<種類>`（種類が無ければ label を置かない。`kind:` の label が語彙の外か2つ以上なら INVALID） |
| `dependencies` | `blocks` の依存（`bd create --deps`。後から変えるのは `tw edit --add-deps`・`--remove-deps` が打つ `bd dep add`・`bd dep remove`） |
| `summary` | `title` |
| `## 完了条件` | `acceptance_criteria` |
| `## やること` | `notes` |
| `## やること` を作業より先に書いたかの記録（`first`・`after-work`・`registered`） | metadata `task_plan` |
| 登録時に `## やること` を書いたときの SHA と、着手時に比べた主ブランチの先端 | metadata `task_plan_base`・`task_plan_tip` |
| `ship` が送ったコミットの範囲（`<送る前の主ブランチの先端>..<送ったコミット>`。`NOTHING` では書かない。閉じた課題にも残る） | metadata `task_shipped` |
| `## 目的・背景`・`## 決まっていること`・`## 解くべき論点`・`## 注意`・`## 参考情報`・`## 介入` | `description`（見出しのまま）。`tw show` は枠の7節を空でもこの順に出し、`## 介入` があればその後ろに出す |
| `## 結果`（`- 振り返り:` を含む） | `## 結果` で始まる comment（最後のものが正） |
| 段の申し送り（WORKFLOW.md「段の申し送り」） | `## 申し送り: <鍵>` で始まる comment（鍵ごとに最後のものが正。`tw relay` が書き・読む） |
| 登録から完了までの本文の差（振り返りの材料） | `bd history`（振り返りの段のスキルが最初と最後の版の差を出す） |
| git に残る過去の `develop/task/` と `docs/history/` | 動かさない（[legacy.md](legacy.md)） |

**サイクルの各段で Beads に起きること**（入口と流れの手順と tw-reference.md の「`tw` コマンドの参照」の補足）:

- **読む・書く**: `tw show T-xxx` で読み、`tw edit T-xxx` で直す（コミットも `ship` も要らない。引数は `tw edit --help`）
- **`done`・`ship`**: `done` は `## 結果` を comment に入れて label `ship:done`／`ship:dropped` を立てるだけで、
  `bd close` は `ship` が送り終えたあとに打つ（「送り出し」）
- **取り残し**: 「status と着手の印」の表
- **振り分け前**（番号でない ID か、`difficulty`・`loopable` が無い課題。トラッカーから
  取り込んだもの）: `status` の `着手可否` が `TRIAGE` で、末尾の `triage` 行に出る。`claim` は
  `NOT_READY\tT-xxx\tTRIAGE`。人の確認つきで `tw adopt <ID> --difficulty … --loopable … --body-file …`
  （metadata `difficulty`・`loopable` を付け、`## 完了条件` を書き起こす。`jira` はキーの無い課題にだけ番号を振り、キーのある課題は
  キーの ID のまま（まだなら付け替える。行き先が既にあれば `TRACKER\tINVALID` の行を出して終了コード3で、
  番号も振らない）。`github` は取り込みの時点で `gh-<Issue 番号>` になっているので番号は変えない）してから着手する
- **`status` の末尾**: `invalid` の次に `triage\t<件数>\t<ID>` が必ず、`jira` なら `jira_close` 行が付く

**トラッカー**（錠と本文は Beads が持ち、トラッカーは写し。**失敗はタスクの操作を止めない**——
`new`・`ship`・`sync`（と、GitHub を読む取り込み）は `TRACKER\tFAILED\t…` の行を足して終了コードは
そのまま。打ち直しは `tw sync`。`claim`・`release`・`edit`・`adopt`・`done` は GitHub に書かない。
`bd github` は GitHub に届かなくても終了コード0を返すので、`tw` は
`--json` の `stats.errors` と `Warning: Failed` の行で失敗を見る。1件の `new` の反映と取り込みの読みは、始めから合わせて
25 秒で打ち切って `TRACKER\tFAILED` にする（`bd` の内側の再試行は外から切れないので時間で切る。Beads への
書き込みは残るので、`tw sync` で打ち直す。`ship`・`sync` の全件は期限なし。環境変数
`TASK_WORKFLOW_TRACKER_TIMEOUT`〔秒〕で1件の上限を変えられる）:

| 方式 | すること | しないこと |
| --- | --- | --- |
| `github` | `issue_prefix` が `gh` なら双方向（下の「GitHub との双方向」）、`t` なら Beads から送るだけ。書くのは `new`（`gh` なら Issue を立てて採番、`t` なら作った1件を `bd github push <ID>` で送る）と、`ship`・`sync` だけ。`ship`・`sync` は、双方向なら変えた課題（控えが無いか `updated_at` が控えより新しい）だけを `bd github sync --push-only --issues <ID,…>` で送り（`bd` の `github.last_sync` が無いと全件が更新対象になり遅いため）、`t` なら全件を `bd github sync --push-only` で送る。そのあと Project の Status 欄を `gh api graphql` で書く（`deferred` → `Pending`〔ドラフトも。`open` に戻っていても印 `draft` があれば `Pending`〕、`open` → `Todo`、`in_progress` → `In progress`、閉じた → `Done`、`cancelled` → `Cancel`。Project に無い Issue は足す）。`bd` は Status 欄を触らない。token は `GITHUB_TOKEN` が無ければ `gh auth token` | 引数なしの `bd github sync`・`--pull-only`（`bd` の増分の取り込みは取りこぼす）。`gh project` のコマンド（`item-list` は入れ子の上限で1回約101点かかり、どれも持ち主の照会を足す）。Status 欄を読んで合わせる（人が Project で変えた Status は、次にその課題の Beads の状態が変わるまで戻らない） |
| `jira` | `tw sync`（と各操作のあと）で `bd jira sync --pull` だけ。取り込んだ課題の ID は `issue_prefix` の hash（`t-aby`。数字だけにもなりうる）なので、`external_ref`（`https://<site>/browse/PROJ-123`）のキーと ID が違う振り分け前の課題を `bd rename` で `proj-123` にする（行き先が既にあれば付け替えず `TRACKER\tINVALID\t<ID>\t<行き先> が既にある（付け替えない）`。`bd rename` は接頭辞が `issue_prefix` と違っても通り、次の取り込みは `external_ref` で同じ課題に当てる）。ローカルで閉じた Jira の課題（`external_ref` あり）には label `jira:close` を付け、`status` の `jira_close` 行に出す。人が Jira で閉じたら `tw jira-closed T-xxx` で外す | Jira へ書く（`--push`・引数なしの `sync`）。状態は人が Jira で変える |
| `なし` | 何もしない（`tw sync` は `NOTHING`） | ― |

**Status 欄の控え**（GraphQL の枠は1時間に5000点・アカウント単位で、点数は要求の上限で数えられる）:
Project・Status 欄・選択肢の ID は `bd kv` の `task-workflow.project` に控え、控えで書いて落ちたら（欄や
選択肢を作り直したとき）1回だけ読み直す。Issue ごとの項目 ID と最後に書いた Status は
`task-workflow.project-item.<Issue 番号>`（課題ごとの1キー。値は `{"item", "status"}`。1つの JSON にまとめると
`bd kv` の1値の上限を超える）に控える。Beads の状態から決まる Status が控えと同じ課題には
触らない。項目 ID が控えに無い課題は Issue の `projectItems(first: 10)` だけを引き、控えの無い課題が10件を
超えるとき（最初の1回）は Project を `items(first: 100)` の `id`・`content.url`・`fieldValueByName("Status")`
だけで一巡して控えを埋める。Issue の読み書きと取り込む番号を選ぶ一覧は REST で、GraphQL の枠を食わない。

**Jira の方式は本物の Jira で試していない**（サイトとトークンが要る）。取り込んだ課題の ID・`external_ref` の形と、
付け替えたあとの取り込みが同じ課題に当たることは、`jira.url` を手元の偽の HTTP サーバへ向けた `bd` で確かめた。`bd jira sync --pull` が
ローカルで閉じた課題をどう扱うか（開き直すか）は未確認で、試すときは人に用意を頼む。

**GitHub との双方向**（`github` のとき。`bd` 1.3.0 の実測に合わせた形）:

- **`bd` の増分の取り込みを使わない**。`bd` は前回の同期の時刻（push でも pull でも進む）より後に
  更新された Issue だけを読むので、その間の GitHub での変更と、立てた直後の Issue を取りこぼす。
  `tw sync` は GitHub の一覧（REST の `repos/<repo>/issues?state=all&since=…`）で見た最後の更新時刻を
  自分で覚え（`bd kv` の `task-workflow.github-seen`。無ければ開いている全件）、その1時間前より後に
  更新された Issue（立てた直後の Issue が一覧に遅れて出る分をさかのぼる）を番号で `bd github pull <番号>…` する。
  自分の送りの反響（課題ごとの送った時刻 + 10 秒以内の GitHub 側の更新）は取り込みに渡さない
  （送った時刻の控えが無い課題は渡す）
- **登録**（`tw new`）: `bd create --id gh-new-<作業ツリーの名前>-<時刻>`（仮の ID。`bd` の採番は
  数字だけの hash になりうるので使わない）→ `bd github push <仮の ID>` → `external_ref` の末尾の番号 →
  `bd rename <仮の ID> gh-<番号>`（依存・comment・履歴ごと付け替わる）→ もう一度 `bd github push`（付け替えも
  Beads の更新なので、前回の同期の時刻を進めておく）。push で落ちたら仮の ID のまま
  `TRACKER\tFAILED` を出し、`tw sync` が push と付け替えをやり直す。立てた直後に上限で落ちて番号が
  Beads に無い仮の ID は、送り直す前に題・本文が同じ Issue があればその番号を `external_ref` に控えるだけにし
  （Issue を2つ立てない）、取り込んだ新しい Issue が既存の課題と題・本文が同じなら
  `TRACKER\tINVALID\t<取り込んだ課題>\t<既にある課題> と重複している` の行で知らせる（閉じるのは人）
- **取り込み**: 取り込んだ課題の ID は `gh-<時刻>-1-<hash>` なので、`external_ref` の番号 `n` と ID が
  違う課題は `tw` が `bd rename` で `gh-<n>` にする（行き先が既にあれば付け替えず `INVALID` の行）。
  `difficulty`・`loopable` が無ければ振り分け前（上の「サイクルの各段で Beads に起きること」）
- **写る欄**: 題 ↔ `title`、本文 ↔ `description`、label ↔ label（`ship:*`・
  `cancelled` も）、開閉 ↔ `open`／`closed`、`status::in_progress`・`status::deferred` ↔ `in_progress`・
  `deferred`。`acceptance_criteria`・`notes`・comment（`## 結果`・`## 申し送り:`）は Beads だけに置き、GitHub には出さない
- **錠の持ち主**: 取り込みは assignee を GitHub の担当者で上書きし（作業ツリーの名前は GitHub の
  利用者でないので空になる）、metadata を消し、label `status::in_progress` が GitHub に無ければ
  `open` に戻す。`tw` は取り込みを1回ごとに一意の actor `<作業ツリーの名前>:github-pull:<pid>-<time_ns>`
  で打ち（前の取り込みの更新と取り違えない）、その更新の書く直前の版
  （`bd history --events` の `old_value`。取り込みの最中に別の作業ツリーが着手しても、その着手が入る）
  から、消えた assignee と metadata の印（`task_*`・`difficulty`・`loopable`。閉じた課題は後の2つだけ）を `bd update` で戻す。`open` に戻った着手は、
  着手の送りが GitHub に届く前の版で上書きされたときだけ `in_progress` に戻し、送ったあとで label が
  外れた・GitHub で担当者が付いたものは手放しとして戻さない。届いたかは課題ごとの送りの印
  （`bd kv` の `task-workflow.github-pushed.<番号>`。送りが届くたびに、送る直前に読んだ着手の時刻
  `started_at` を書く。着手中でなければ空）で見て、印が今の着手の時刻と（秒で）違えば届いていない。
  印の無い課題（印を書く前に送ったもの）だけは、`task-workflow.github-synced.<番号>` の時刻が着手より前なら届いていないとする。
  戻せなければ `TRACKER\tFAILED` の行を足し、ほかの行（`CLOSED` など）と送りは続ける。
  **取り込みはすべてこの包みを通す**
- **着手の印の控え**: 取り込みと上の戻しは別々の書き込みなので、間でプロセスが止まると書く直前の版は
  監査の出来事にしか残らず、次の取り込みからは戻せない。そこで `tw claim` は印（assignee・`started_at`・
  `task_*`・`difficulty`・`loopable` などの metadata）を `bd kv` の `task-workflow.claim.<Beads ID>` に
  JSON で控え、`tw edit` が自分の着手中の課題に metadata を書くたびに書き直す。`tw release`・`tw done`・`tw ship`
  （閉じたとき）が消す。控えから戻すのは3か所で、(1) 取り込みのあと、書く直前の版で戻せなかった分を補う、
  (2) `tw sync`・`tw ship` の送りの前に控えのある課題をすべて見る、(3) `plan-check`・`step`・`verify`・`pause`・
  handback-guard が `NOT_OWNER` を出す前に、その課題だけを見る（GitHub には触らず、戻した課題は次の送りで送る）。
  戻すかの判定は上と同じで、GitHub で手放された・閉じた・別の作業ツリーが取った課題は戻さずに控えを消す。
  戻したら `TRACKER\tRESTORED\tGH-<n>` の行を出す（(3) では標準エラー）。上の書く直前の版からの戻しはこの行を出さない
- **タスクの操作**（`claim`・`release`・`edit`・`adopt`・`done`・`hold`）: 触る課題だけを
  取り込み → 操作の順で打ち、**送らない**。Beads が正で、GitHub は `ship` か `tw sync` までずれる
  （`claim` の直後に label `status::in_progress` や担当者は付かず、Status 欄は間に `sync` を挟まなければ `In progress` を経ず `Done` へ1回で変わる）。
  ただし Beads が `in_progress` で担当者のある課題（着手中で未送り）は取り込まない。取り込みは `tw sync` と、着手前（`open`）の課題への操作だけが打つ。
  着手中に GitHub 側だけが変わっても `tw sync` まで入らず、両側が変わっていれば Beads が勝って `CONFLICT` を出す。
  担当者が空の `in_progress`（止めた取り込みが印を消した版）は取り込んで戻す。
  送るのは `new`・`ship`・`sync` だけ。取り込み（`tw sync` と着手前の取り込み）は前回の同期の時刻を進めるので、取り込みのあとの直し（assignee・label・付け替え）は
  その場で送る（送らないと、次の取り込みがその課題を「Beads で変えたもの」として飛ばし、GitHub での変更を送り返して消す）
- **衝突**: 課題ごとの勝ち負けで、欄ごとには合わせない。取り込みは前回の同期より後に Beads で
  変えた課題を上書きしないので、**両側で変えたら Beads が勝ち**、GitHub の版は Issue の編集履歴に残る。
  `tw` はその課題を `TRACKER\tCONFLICT\tGH-<n>` の行で知らせる（人が履歴から拾い直す）。`bd` は飛ばしたことを
  教えないので、`tw` が課題ごとに最後に送った・取り込んだ時刻（`bd kv` の課題ごとのキー `task-workflow.github-synced.<番号>`）を
  控え、そのあとに Beads と GitHub の両方で更新された課題を衝突とする（触った番号のキーだけを書くので、
  別の作業ツリーが書いた別の番号の時刻を戻さない。旧い 1 つの表 `task-workflow.github-synced` は読むだけで、
  課題ごとのキーが無い番号だけそこから読む）
- **GitHub で閉じる**: 取り込むと Beads でも閉じ、依存が解ける。`ship:*` の無い課題が GitHub で
  閉じられたら見送りとして label `cancelled` を足し、`TRACKER\tCLOSED\tGH-<n>` の行を出す（着手中なら
  人に預ける）。GitHub で開き直したら `cancelled` と `ship:*` を外す
- **`tw sync` の順**（着手中の課題の取り込みはここで打つ）: 控える → 取り込む（反響を除く）→ 送りの印を読んで着手の印を戻し、付け替える（直したものを送る）→
  push（取り込みの前に選んだ、控えが無いか `updated_at` が控えより新しい課題だけを
  `bd github sync --push-only --issues <ID,…>` で。無ければ打たない）→ 登録で落ちた仮の ID を付け替える →
  送りの印と違う着手中の課題をまとめて送り直し（1回の `bd github push <ID>…`）、届いたら
  その課題の送りの印を書く → Status 欄を書く
- **重ねて打った `tw sync` は錠で1本にする**: 台帳の `sync.lock` を持つ1本だけが走り、後から来た `tw sync` は待たず `BUSY\tsync\t<持ち主の情報>` を出して終了コード5で返る（持ち主のプロセスが無い古い錠は壊さず、行に添えて人に預ける）。ほかのコマンド末尾の取り込み・送りは錠に包まない

**Jira の方式との違い**:

| | `github` | `jira` |
| --- | --- | --- |
| タスクID | Issue 番号（`GH-5`） | 取り込んだ課題は Jira のキー（`PROJ-123`。取り込みの直後に `external_ref` のキーへ付け替える）、ローカルで `tw new` した課題は `tw` の採番（`T-123`）で、混ざる |
| 登録 | Issue を先に作って番号を得る | Beads だけに作る（Jira に書かない） |
| 同期の向き | 双方向（取り込みは `tw` が番号で選ぶ） | 取り込みだけ（`bd jira sync --pull`） |
| 状態を変える場所 | どちらでも（GitHub で閉じたら見送り） | Beads。Jira で閉じるのは人（`jira_close`） |
| 衝突 | Beads が勝ち、`CONFLICT` の行 | 起きない（Jira に書かない） |

**バックアップ**: `.beads` は git の外なので、タスクの記録は git の履歴に残らない。`tw ship` の
最後と `tw backup` が、置き場へ `bd backup sync`（Dolt の履歴ごと。置き場が未設定なら
`bd backup init <置き場>/dolt`）と `bd export -o <置き場>/issues.jsonl` を取る（`BACKUP\tOK|FAILED\t<置き場>`）。
**置き場がリポジトリの中なら取らない**——`bd export` の JSONL には作成者のメールアドレス（`owner`）が
入るので、公開リポジトリにコミットされうる場所へ置かない。戻すのは `bd backup restore`（人が行う）。
置き場が `.beads` と同じディスクなら、`tw config-doctor` が `WARN_BACKUP_SAME_DISK` で知らせる。

**触らないもの**: `bd metrics`（端末全体の設定）、`bd reclaim`、`bd init` の `--stealth` なし。
