---
name: setup-tasks
description: "タスク運用に要る .tw/ （direction.md・.gitignore）をプロジェクトに用意し、検証コマンドを確かめて .tw/config.toml に書く。ユーザーが「タスク運用を始めたい」「.tw/ を用意して」「このプロジェクトでもタスク管理を使いたい」と言ったとき、/next-task・/plan-tasks・/list-tasks が MISSING を返したときに使う。既にあるファイルは上書きしない。旧形式（develop/tasks.json）なら作らずに移行を案内する。"
---

`/next-task` `/plan-tasks` `/list-tasks` が読む**プロジェクト側のファイルを用意する**。置き場と役割は
`task-workflow` スキルの `WORKFLOW.md`（以下「正典」）「ファイル配置と設定ファイル」。
**タスクは登録しない。既にあるファイルは上書きしない**（点検結果を出し、直すかは下で決める）。

## 手順

0. **依存を確かめる**: `${CLAUDE_SKILL_DIR}/../task-workflow/scripts/task.py` が無ければ、
   `task-workflow` スキルが張られていないとして `MISSING` を報告して終了する（plugin で入れるなら
   `/plugin install tsukumo-workflow@tsukumo-plugins`、リンクで入れるなら `install.sh` で
   `task-workflow` も一緒に張るよう案内する。以下の手順で打つ `task-workflow` の各スクリプトは
   これが前提）。`command -v tw` が何も返さないときも同じく `MISSING` として、同じ2つの入れ方を案内する。

1. **`.tw/` を用意する**。骨組みは決まりきっているので手で書かない（見出し以外の行が混ざると `/plan-tasks` が
   「未対応の指示がある」と誤判定する）:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../task-workflow/scripts/init.py
   ```

   | 行 | 意味 |
   | --- | --- |
   | `LEGACY`（終了コード5） | **旧形式**（`develop/tasks.json` がある）。何も作っていない。「正典「旧形式からの移行」の手順で `tw migrate --dry-run` から移す（全作業ツリーの手を止めてから）」と案内して終了する |
   | `CREATED` | `.tw/direction.md`・`.tw/.gitignore` を作った |
   | `KEPT` + `OK:` | 既にあり、筋が通っている。触っていない |
   | `KEPT` + `PENDING:` | セットアップとしては完了。未タスク化の指示が残っているので `/plan-tasks` が先と報告する |
   | `config\tOK` | `.tw/config.toml` があり読める。手順2を飛ばす |
   | `config\tMISSING` | `.tw/config.toml` が無い（か `verify` が無い）。手順2で書く |
   | `config\tINVALID` | 設定が読めない。理由をユーザーに見せ、どう直すかを聞いてから直す |
   | `CREATED\t<…/.beads>`・`KEPT\t<…/.beads>` | 設定が Beads 方式（`store = "beads"`）なので `.beads` を用意した（トラッカーが `github` なら `bd init --stealth -p gh` で ID は Issue 番号、それ以外は `-p t`）・既にあった |
   | `NOT_MAIN_WORKTREE`（終了コード4） | `.beads` は主ブランチを出している作業ツリー（本体）の根に置く。パスの作業ツリーで打ち直すよう案内する |

   `.tw/task/` は最初の `tw new` が、`.tw/draft/` は最初のドラフトが作る（空のディレクトリは
   git に載らない）。`docs/history/` も掘らない。

2. **`.tw/config.toml` を書く**（`config\tMISSING` のときだけ）。聞くのは検証コマンドだけ。**推測で書かない**:

   - `package.json` の `scripts`、`Makefile`、`justfile`、`pyproject.toml`、既にある `AGENTS.md`・`CLAUDE.md` の
     記述から探し、**実際に走らせて通ることを確かめてから**書く（受け入れと `tw ship` の付け替え後に毎回打たれる）。
     決め手が無い・見つからないときはユーザーに聞く。打つものが無いと決まったら `"なし"` と書き、行ごと消さない
   - `verify` 以外のキーはコメントアウトした見本で置く。キーと語彙は正典「ファイル配置と設定ファイル」の表

   ```toml
   # タスク運用の設定（tw が読む）
   verify = "pnpm check"        # 受け入れ判定に使う。打つものが無ければ "なし"
   # verify_before_ship = ""    # 送る前に tw ship が打つ重い検証
   # format = "pnpm format"     # あれば tw verify が検証の前に打つ
   # branch = "既定"            # 既定 / 作業ブランチを切る / 切らない
   ```

   `AGENTS.md`・`CLAUDE.md` には書かない。

   **Beads 方式にする**（ユーザーが Beads を使うと決めたときだけ。既定はファイル方式で、キーを足さない）:
   `.tw/config.toml` に `store = "beads"` と、トラッカーを使うなら `tracker = "github"`（と
   `github_project = "<owner>/<番号>"`）か `tracker = "jira"` を足し（正典「Beads 方式」の設定の表）、
   手順1の `init.py` を本体で打ち直して `.beads` を作る。**`bd init` を手で打たない**（`--stealth` なしだと
   `AGENTS.md`・`CLAUDE.md` に書き足して自動でコミットする）。`github` なら `gh auth login`（`project`
   スコープ）と `bd config set github.repository <owner>/<repo>`、`jira` なら `bd jira` の設定を
   ユーザーに頼む（認証情報をスキルが書かない）。既存のファイル方式のタスクから移すのは別の作業（人が決める）

3. **通しで確かめる**: `tw status` と `tw config`。
   まっさらなら `---` と末尾の集計行だけが出る（終了コード0）。`MISSING` なら手順1・2が効いていない。
   Beads 方式なら `tw config-doctor` の `store`・`beads`・`tracker` の行も `OK` を確かめる。

4. **コミットする**（件名にタスクIDは付けない。push はしない）。作業ツリーの枝に居るなら
   `tw ship` で `main` へ送る。

5. **報告する**: 作ったファイル、`.tw/config.toml` に書いた値（と、そのコマンドが実際に通ったこと）、
   点検で見つかった問題。最後に次の一歩を1行:
   やりたいことを `.tw/direction.md` の `## ユーザーから` に書くか `/plan-tasks <パス>` でファイルを渡すと、タスクになる。

## やらないこと

- タスクの登録・実行（`/plan-tasks`・`/next-task`）。旧形式の移行（`tw migrate` は人が打つ）
- `.tw/` を `.gitignore` に足す（タスクの正典はコミットして共有する。`.tw/local/` だけは `.tw/.gitignore` が外す）
- `AGENTS.md`・`CLAUDE.md` の書き換え。整形コマンド・ブランチを聞くこと
- `~/.claude/skills/` へのリンク（スキル自体の導入は tsukumo-plugins の `install.sh`）
