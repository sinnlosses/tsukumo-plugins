# tsukumo-plugins

タスクを段に分けて委譲し、受け入れて送り出すワークフローの Claude Code プラグイン `tsukumo-workflow` と、
それを配るマーケットプレイス `tsukumo-plugins`。

タスクは Beads（`bd`）に置き、`/plan-tasks` で指示をタスクにし、`/next-task` で
1件ずつ difficulty に応じたモデルのサブエージェントへ委譲して、受け入れてから主ブランチへ送る。手順はコマンド `tw`
が持ち、スキルは「どのサブコマンドをいつ打つか」だけを書く（正典は `skills/task-model/WORKFLOW.md`、サブコマンドの表は `skills/task-model/tw-reference.md`）。

tsukumo のワークフローの契約に型の合う1つの例だが、tsukumo がいなくても素の Claude Code で動く。

## マーケットプレイスから入れる（主な入れ方）

```
/plugin marketplace add sinnlosses/tsukumo-plugins
/plugin install tsukumo-workflow@tsukumo-plugins
```

スキルは `tsukumo-workflow:<name>`、エージェント定義は `tsukumo-workflow:no-delegate`・
`tsukumo-workflow:acceptor` の名前で入る。`bin/tw` はプラグインが有効なあいだ Bash の PATH に入る。
hook は `hooks/hooks.json` だけが掛け、エージェント定義の frontmatter に `hooks` は置かない。
hook の掛かり方は `skills/task-model/tw-reference.md`「hook の関門」節。

入れ方はこの1つだけにする。スキルを `~/.claude/skills/` へ、エージェント定義を `~/.claude/agents/` へリンクで置くと、
同じスキルが二重に見え、`hooks/hooks.json` が読まれないぶん `ask-guard`・`lap-hook`・主のセッションの `bd` 書き込みの拒否・受け入れ役の拒否が掛からず、見張りの鼓動（`heartbeat`）も書かれない。

### このリポジトリの作業ツリーで直しながら使う

公開リポジトリではなく、手元の作業ツリーのパスでマーケットプレイスを足す。

```
/plugin marketplace add /絶対パス/tsukumo-plugins
/plugin install tsukumo-workflow@tsukumo-plugins
```

ローカルパスで足したマーケットプレイスの plugin は、キャッシュへコピーされず作業ツリーから直に読まれる。
直したものは次のセッションの開始か `/reload-plugins` で効き、版を上げる必要は無い（自動更新の設定とも関係しない）。
公開リポジトリから入れたときは、第三者のマーケットプレイスの自動更新は既定でオフなので、
`/plugin` の Marketplaces タブで自動更新を有効にするか `claude plugin update tsukumo-workflow@tsukumo-plugins` を打つ。

### 端末で `tw` を打つ

`bin/tw` が PATH に入るのは Claude Code の Bash ツールの中だけで、人が端末で打つ `tw` には届かない。
端末でも打つなら、`bin/tw` を呼ぶ 2 行を `~/.local/bin/tw` に置いて実行権を付ける。
`bin/tw` は自分の場所から根を引くので、シンボリックリンクでは動かない。

```sh
#!/bin/sh
exec /絶対パス/tsukumo-plugins/bin/tw "$@"
```

### リンクで入れていた環境の切り替え

人がやる（グローバルな設定の書き換えにあたる）。

1. `~/.claude/skills/` と `~/.claude/agents/` の、このリポジトリを指すリンクを外す（`ls -l` で指す先を見て、`tsukumo-plugins` を指すものだけ）
2. `~/.local/bin/tw` の、このリポジトリを指すリンクを外し、上の 2 行に置き換える
3. 上の 2 コマンドで plugin を入れ、`claude plugin list` に `tsukumo-workflow` が出ることと、`/reload-plugins` のあとにスキルが `tsukumo-workflow:<name>` で並ぶことを確かめる

Claude Code 以外の環境（Codex など）へスキルを置く入口は、このリポジトリでは持たない。

開発中は `claude --plugin-dir .` でも読ませられ（その 1 セッションだけ）、`claude plugin validate .` で検査する。

## もう1つのプラグイン `tsukumo-figure`

タスク運用とは関係なく、図を載せるためのプラグイン（`plugins/tsukumo-figure/`）。同じマーケットプレイスから入れる。

```
/plugin install tsukumo-figure@tsukumo-plugins
```

スキルは `tsukumo-figure:figure-capture` で、図の構造（JSON）を検査して配色と配置を決め、HTML にして図の部分を PNG に撮る。
いまの型はサンキーで、型は `scripts/figure/` に足せる（手順はスキルの `SKILL.md`）。標準ライブラリの Python だけで動き、
撮る段だけ playwright と chromium が要る（無ければ HTML までで止まる）。タスク運用の `tw`・Beads・hook には触らない。
`tsukumo-workflow` とは別の名前空間で、`vendor/tsukumo-plugins` として tsukumo に取り込まれるのは `tsukumo-workflow` だけ。

## `.git` が読み取り専用の sandbox で打つとき

Codex のように、書けるのが作業ツリーと一時ディレクトリだけの環境:
`status`・`show`・`find`・`edit`・`done`・`plan-check`・`verify`（取り込みが要らないとき）・`verify-check`・`pause`・`step`・`metrics` と、
追い付きも枝の切り出しも要らない `claim`（`branch = "切らない"` で、主ブランチが `HEAD` の祖先）は通る。作業ツリーごとの控えは
作業ツリーの根の `.tw/local/` に、中身の鍵の object は一時ディレクトリに書くので、`.git` には書かない。git に書くコマンドは
`GIT_READ_ONLY`（終了コード11）で止まるので、sandbox の外で同じコマンドを打ち直す。Claude Code なら `settings.json` に
`"sandbox": {"excludedCommands": ["tw"]}` を足すと `tw` が sandbox の外で動く。Codex なら sandbox の外での実行を承認して打ち直す。
共有の台帳（共有の git dir の `task-workflow/`）も書けないので、`flow/` の記録を残すなら `TW_STATE_DIR` を書ける場所（Codex の writable roots、
Claude Code の `sandbox.filesystem.allowWrite`）に向ける（向けなくても記録が落ちるだけで、サブコマンドは止まらない。
`tw metrics` と横断の振り返りが同じ記録を読むよう、同じリポジトリを使うセッションは `TW_STATE_DIR` をそろえる）。
`.beads`（本体の根）も書ける場所に足す
（`bd` 1.3.1 は `.git` が読み取り専用でも動くが、`.beads` が読み取り専用だと読むだけの `bd list` も錠が取れずに落ち、`tw` は終了コード1になる）。

## 要るもの

| 前提 | 必須・任意 | 最低版 | 用途・無いとき |
| --- | --- | --- | --- |
| `python3` | 必須 | 3.9 | `tw` とスキルのスクリプト（標準ライブラリだけ）。`bin/tw` が足りなければ案内して終了コード78で止まる |
| `git` | 必須 | - | 作業ツリーと主ブランチの取り込み |
| `bd`（Beads） | 必須 | 1.3.0 | タスクの置き場。無いと `tw` は動かない |
| `dolt` | 必須 | - | `bd` の置き場の実体とバックアップ |
| `gh` | トラッカーが `github` のときだけ必須 | - | Issue と Project の同期（`skills/task-model/TRACKER.md`「トラッカー」） |
| `tw`（PATH 上） | 任意 | - | スキルや hook がサブエージェントの中から `tw` と打つとき |

`tw config-doctor` が前提ごとに `OK`・`MISSING`・`OPTIONAL` を出す（必須が欠ければ終了コード1）。

使うプロジェクトの側は、検証コマンドを書いた `.tw/config.toml` を置く。用意するのは `/setup-tasks`。

## あれば使う外のスキル

次のスキルはこのリポジトリに無い。スキルの一覧にあれば使い、無ければ本文の代わりの手順で進む
（`scripts/check_repo.py` の `OPTIONAL_SKILLS`）。

| 外のスキル | 使う場所 | 無いとき |
| --- | --- | --- |
| verifying-before-completion | task-workflow の受け入れの関門 | 主張ごとに差分とコマンドの出力で確かめる |
| writing-for-agents | retro の物差し、retrospect の「空振り」の試金石 | retro の「ファイルの使い分け」を物差しにする |
| code-review | retro が読むレビューの観点 | 読まない |
| resolving-merge-conflicts | task-workflow の `CONFLICT` の案内 | 人に預ける |
| token-usage-diet | retrospect の「道具の経済」の根拠 | 使用量の数を根拠にしない |

## 由来

全13スキル。`skills/` にあるものが全てで、この一覧がその索引。

- 自作（12件）: `task-model`（タスクの規則の正典と `tw` の実装・参照専用）、`setup-tasks` `plan-tasks` `list-tasks`、
  `next-task`（タスクを1件に決めて着手の印を立てる入口）、
  `task-workflow`（印の立ったタスクを委譲→受け入れ→振り返り→送り出しの段で進めるオーケストレーター。流れの変種は `flows/`、各段の手順は `parts/<段の役>/`）、
  `retrospect`（`task-workflow` の中で1件ごと・`next-task` の頭で7日おきに振り返り、改善候補をドラフトの課題〔`tw new --draft`〕として積む。正典・参照専用）、
  `comment-audit`（コメントの種類を判定して消す・縮める・正典へ移す。`task-workflow` の受け入れ役に渡す）、
  `implement-step` `accept-step` `retrospect-step` `ship-step`（tsukumo の契約の並び〔schema 2 の `steps`〕に置く段。
  frontmatter の `metadata` に `workflow-step: "true"` を持ち、入口と出口だけを持って手順は `task-workflow` の `parts/` を読む）
- [mattpocock/skills](https://github.com/mattpocock/skills) を日本語化したもの（1件）: `retro`
  （改善の7観点の一覧を `retrospect` へ移してそこを指し、選ばれた改善案をドラフトの課題として積む）

並びの段の4件を除き、どれも `sinnlosses/claude-skills` で育ったもので、そこから切り出した（下の「決めたこと」の2）。

## 決めたこと

切り出し（2026-10-08）で決めたこと。根拠の文書は Claude Code の公式文書（code.claude.com/docs）。

1. **リポジトリをまたぐ依存は、頼る箇所を外して「一覧にあれば使う」形にする。** 写しも、プラグイン間の
   依存（`plugin.json` の `dependencies`）も作らない。
   - 写しは中身がずれる
   - 依存先にしうる `sinnlos-skills`（`sinnlosses/claude-skills`）は、張り替えが済むまでこのリポジトリと同じ
     スキルを持っているので、依存にすると同じスキルが二重に入る。別のマーケットプレイスのプラグインへの
     依存は既定では入らず、根のマーケットプレイスの `allowCrossMarketplaceDependenciesOn` が要る
     （「Plugin dependencies」）
   - マーケットプレイスから入れたプラグインはキャッシュへ別々に写され、プラグインの根より上（`../`）は
     届かない（「Plugin loading reference」）。`${CLAUDE_SKILL_DIR}/../<外のスキル>` は在るときだけ使う
   - claude-skills に残る `maintenance-docs` は `task-workflow`（このリポジトリでは `task-model` に改名した）の `scripts/layout.py` を import している。
     claude-skills から `task-workflow` を消すときに、`tw` コマンド越しに読み、無ければその検査を飛ばす形に直す
2. **歴史は持ち込まず、新しい歴史で始める。** 最初のコミットに写し元（claude-skills の main の SHA）を書いた。
   それまでの歴史は claude-skills に残る。`git filter-repo` は使える外部コマンドに無く、持ち込むコミットの題は
   claude-skills のタスク番号を指す
3. **tsukumo の `token-usage-diet` は移さず tsukumo に残す。** 読むのは tsukumo の記録（`$TSUKUMO_HOME`）で、
   tsukumo の外では入力が無い。ワークフローの契約の外の機能で、このリポジトリからは「あれば使う」とだけ指す
4. **マーケットプレイスは `tsukumo-plugins`（リポジトリ名と同じ）、プラグインは `tsukumo-workflow`。**
   `sinnlos-skills` とも、tsukumo が同梱するプラグイン `tsukumo` とも重ならない。部品はプラグインの名前で
   名前空間に入り、`tsukumo-workflow:<名前>` になる（「Plugin manifest reference」の `name`）。
   `plugin.json` に `version` は置かず、コミットの SHA で更新を追う（「Plugin loading reference」の
   Versions and updates）

## 検証

```sh
./check.sh          # 変えたファイルに当たる段だけ
./check.sh --full   # 全段
./check.sh --plan   # 段を流さず、流す段と飛ばす段、task コマンドの節では絞りの先（`-- 絞り:` の行）だけを出す
```

1. `task-model` のスクリプトの自己テスト（`scripts/tests/` の `selftest.py`・`selftest_review_needed.py`・`selftest_review_snapshot.py`・`selftest_task.py`・`selftest_beads.py`。
   後ろの2つは入口で、テストの本体は `selftest_t_*.py`・`selftest_b_*.py` にある。
   `bd` が無ければ非0で終わる）
2. リポジトリの整合（`scripts/check_repo.py`。frontmatter、この README の由来一覧と `skills/` の一致、
   参照先の実在、スキル間の依存の辺と `task-model` が役の名で書かれていること、
   プラグインの名前と `bin/tw`・`hooks/hooks.json`、エージェント定義）

由来の一覧が索引なので、スキルを足したり消したりしたらここも直す（直し忘れは `./check.sh` が落とす）。

## 制約

- ユーザー単位スキルはプロジェクト単位の同名スキルより優先される。プロジェクト差分は
  `.tw/config.toml` で表す
- SKILL.md 内の `` !`コマンド` `` はスキル読み込み時に実行され、非0で終わるとスキル全体が失敗する。
  終了コードを潰していない埋め込みは `./check.sh` の `check_repo.py` が落とす。
  `/next-task`・`task-workflow`・`/plan-tasks` は設定を `` !`tw config 2>&1 || true` `` で読む（設定が無い・git の外でも非0で終わらない）
