# tsukumo-plugins

タスクを段に分けて委譲し、受け入れて送り出すワークフローの Claude Code プラグイン `tsukumo-workflow` と、
それを配るマーケットプレイス `tsukumo-plugins`。

タスクは `.tw/task/` の1件1ファイルか Beads に置き、`/plan-tasks` で指示をタスクにし、`/next-task` で
1件ずつ difficulty のモデルのサブエージェントへ委譲して、受け入れてから主ブランチへ送る。手順はコマンド `tw`
が持ち、スキルは「どのサブコマンドをいつ打つか」だけを書く（正典は `skills/task-workflow/WORKFLOW.md`）。

tsukumo のワークフローの契約に型の合う1つの例だが、tsukumo がいなくても素の Claude Code で動く。

## マーケットプレイスから入れる（主な入れ方）

```
/plugin marketplace add sinnlosses/tsukumo-plugins
/plugin install tsukumo-workflow@tsukumo-plugins
```

スキルは `tsukumo-workflow:<name>`、エージェント定義は `tsukumo-workflow:no-delegate`・
`tsukumo-workflow:reviewer` の名前で入る。`bin/tw` はプラグインが有効なあいだ Bash の PATH に入る。
プラグインに入れたエージェント定義の frontmatter の `hooks` は無視されるので、`no-delegate` の hook
（`tw commit-guard`・`tw handback-guard`）は `hooks/hooks.json` が `"${CLAUDE_PLUGIN_ROOT}/bin/tw"` を
`--agent-scoped` 付きで呼び、`agent_type` の末尾が `no-delegate` のときだけ掛かる。

開発中は `claude --plugin-dir .`（このリポジトリの根）で読ませ、`claude plugin validate .` で検査する。

## リンクで入れる（`install.sh`）

マーケットプレイスの代わりに、`./install.sh` で `~/.claude/skills/<name>` へスキルを、`~/.claude/agents/` へ
エージェント定義を、`~/.local/bin/tw` へ `tw` をシンボリックリンクで張る。入れ方はどちらか一方だけにする
（両方で入れると同じスキルが二重に見える）。

- 張る先は `--dest DIR` → `CLAUDE_CONFIG_DIR`（設定されていればその下の `skills/`）→ `$HOME/.claude/skills`
  の順で決まる。`tw` の張る先は `--bin-dir DIR`（無ければ `~/.local/bin`）
- 実ディレクトリや他所を指すリンクは触らず警告し、消したスキルの残骸は掃除する
- 引数にスキル名を渡すと対象を絞れる。絞ったときは `REQUIRES`（依存する兄弟スキル名を1行ずつ書いた
  ファイル）を読み、依存先が張る先に無ければ警告する
- `./uninstall.sh [--dest DIR] [--bin-dir DIR] [スキル名...]` は、このリポジトリを指すリンクだけを外す

## 要るもの

- `python3`（`tw` とスキルのスクリプト。標準ライブラリだけ）と `git`
- Beads 方式（`.tw/config.toml` に `store = "beads"`）で使うなら `bd`（Beads）と Dolt。
  トラッカーが `github` なら `gh` も要る（`skills/task-workflow/WORKFLOW.md`「Beads 方式」）。
  既定のファイル方式では要らない

使うプロジェクトの側は、`.tw/direction.md` と、検証コマンドを書いた `.tw/config.toml` を置く。用意するのは `/setup-tasks`。

## あれば使う外のスキル

次のスキルはこのリポジトリに無い。スキルの一覧にあれば使い、無ければ本文の代わりの手順で進む
（`scripts/check_repo.py` の `OPTIONAL_SKILLS`）。`comment-audit` だけは同梱のスクリプトのパスが要るので、
一覧ではなく、リンクで入れて兄弟（`${CLAUDE_SKILL_DIR}/../comment-audit/`）に在るときだけ使う。
マーケットプレイスから入れるとプラグインごとに別のキャッシュへ写されるので、別のプラグインで入れた
`comment-audit` は使われない。

| 外のスキル | 使う場所 | 無いとき |
| --- | --- | --- |
| verifying-before-completion | next-task の受け入れの関門 | 主張ごとに差分とコマンドの出力で確かめる |
| comment-audit（兄弟に在るときだけ） | next-task のレビュアーに渡すコメントの判定 | コメント行の判定を依頼文から外す |
| writing-for-agents | retro の物差し、retrospect の「空振り」の試金石 | retro の「ファイルの使い分け」を物差しにする |
| code-review | retro が読むレビューの観点 | 読まない |
| resolving-merge-conflicts | next-task の `CONFLICT` の案内 | 人に預ける |
| token-usage-diet | retrospect の「道具の経済」の根拠 | 使用量の数を根拠にしない |

## 由来

全7スキル。`skills/` にあるものが全てで、この一覧がその索引。

- 自作（6件）: `task-workflow`（運用の正典・参照専用）、`setup-tasks` `plan-tasks` `next-task` `list-tasks`、
  `retrospect`（`/next-task` の中で1件ごと・7日おきに振り返り、改善候補をドラフトに積む。正典・参照専用）
- [mattpocock/skills](https://github.com/mattpocock/skills) を日本語化したもの（1件）: `retro`
  （改善の7観点の一覧を `retrospect` へ移してそこを指し、選ばれた改善案をドラフトの置き場に積む）

どれも `sinnlosses/claude-skills` で育ったもので、そこから切り出した（下の「決めたこと」の2）。

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
   - claude-skills に残る `maintenance-docs` は `task-workflow` の `scripts/layout.py` を import している。
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
./check.sh --plan   # 段を流さず、流す段と飛ばす段だけを出す
```

1. `install.sh`・`uninstall.sh`・`scripts/links.sh` の構文
2. `install.sh`・`uninstall.sh` の自己テスト（`scripts/selftest_links.sh`）
3. `task-workflow` のスクリプトの自己テスト（`selftest.py`・`selftest_task.py`・`selftest_beads.py`。
   `selftest_beads.py` は `bd` が無ければ飛ばす）
4. `retrospect` の自己テスト
5. `plan-tasks` の自己テスト
6. `next-task` の自己テスト
7. リポジトリの整合（`scripts/check_repo.py`。frontmatter、この README の由来一覧と `skills/` の一致、
   参照先の実在、プラグインの名前と `bin/tw`・`hooks/hooks.json`、エージェント定義）

由来の一覧が索引なので、スキルを足したり消したりしたらここも直す（直し忘れは `./check.sh` が落とす）。

## 制約

- ユーザー単位スキルはプロジェクト単位の同名スキルより優先される。プロジェクト差分は
  `.tw/config.toml` で表す
- SKILL.md 内の `` !`コマンド` `` はスキル読み込み時に実行され、非0で終わるとスキル全体が失敗する。
  `/next-task` と `/plan-tasks` は設定を `` !`tw config 2>&1 || true` `` で読む。
  設定が無い（`MISSING`、終了コード6）・git の外（終了コード1）でも非0で終わらないようにしてある
