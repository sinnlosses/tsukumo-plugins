---
name: setup-tasks
description: "タスク運用に要る develop/direction.md（## ユーザーから）をプロジェクトに用意し、検証コマンド・整形コマンド・ブランチの3行を CLAUDE.md の「## タスク運用」節に書く。ユーザーが「タスク運用を始めたい」「develop/ を用意して」「このプロジェクトでもタスク管理を使いたい」と言ったとき、/next-task・/plan-tasks・/list-tasks が MISSING を返したときに使う。既にあるファイルは上書きしない。旧形式（develop/tasks.json）なら作らずに移行を案内する。"
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

1. **作る**。骨組みは決まりきっているので手で書かない（見出し以外の行が混ざると `/plan-tasks` が
   「未対応の指示がある」と誤判定する）:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../task-workflow/scripts/init.py develop
   ```

   | 行 | 意味 |
   | --- | --- |
   | `LEGACY`（終了コード5） | **旧形式**（`develop/tasks.json` がある）。何も作っていない。「正典「旧形式からの移行」の手順で `tw migrate --dry-run` から移す（全作業ツリーの手を止めてから）」と案内して終了する |
   | `CREATED` | `develop/direction.md` を骨組みで作った |
   | `KEPT` + `OK:` | 既にあり、筋が通っている。触っていない |
   | `KEPT` + `PENDING:` | セットアップとしては完了。未タスク化の指示が残っているので `/plan-tasks` が先と報告する |
   | `MISSING`/`NO_SECTION`/`MISSING_LINE`/`BAD_BRANCH`/`OK` の行 | CLAUDE.md の点検結果。手順2で使う |
   | `CREATED\t<…/.beads>`・`KEPT\t<…/.beads>` | 節が Beads 方式（`- タスクの置き場: beads`）なので `.beads` を用意した（トラッカーが `github` なら `bd init --stealth -p gh` で ID は Issue 番号、それ以外は `-p t`）・既にあった |
   | `NOT_MAIN_WORKTREE`（終了コード4） | `.beads` は主ブランチを出している作業ツリー（本体）の根に置く。パスの作業ツリーで打ち直すよう案内する |

   `develop/task/` は最初の `tw new` が、`develop/draft/` は最初のドラフトが作る（空のディレクトリは
   git に載らない。新形式の目印は `develop/direction.md`）。`docs/history/` も掘らない。

2. **CLAUDE.md の「## タスク運用」節を用意する**。プロジェクトごとに変わる値は3行だけ。まず値を
   決める。**推測で書かない**:

   - 検証コマンド・整形コマンドは `package.json` の `scripts`、`Makefile`、`justfile`、`pyproject.toml`、
     **既にある CLAUDE.md の記述**から探し、**実際に走らせて通ることを確かめてから**書く（受け入れと
     `tw ship` の付け替え後に毎回打たれる）。決め手が無い・見つからないときはユーザーに聞く。
     無いと決まったら `なし` と書き、行ごと消さない
   - `- 送る前の検証コマンド:` は任意行（雛形には書かない）。重い検証（全件の E2E など）を、受け入れの
     検証とは別に `tw ship` が主ブランチへ入れる直前に毎回打つようにしたいときだけ、`- 検証コマンド:` の
     次に足す。`tw verify` の控えでは省かれず、落ちれば送らない。正典「ファイル配置と設定ファイル」
   - `- ブランチ:` は語彙の先頭語で書く（正典「ファイル配置と設定ファイル」の表）。既定は `既定`
     （タスクごとに `feature/T-xxx` を切る）。`main` に直接積む・作業ツリーの枝のまま送るなら
     `切らない`。先頭語の後ろは説明を自由に書いてよい

   ```markdown
   ## タスク運用

   - 検証コマンド: `pnpm check`（変更後は必ずこれを通す。受け入れ判定に使う）
   - 整形コマンド: `pnpm format`
   - ブランチ: 既定

   タスクは `develop/task/` に1件1ファイル、指示は `develop/direction.md` に溜め、
   `/plan-tasks` でタスク化して `/next-task` で進める。
   ```

   | 点検結果 | すること |
   | --- | --- |
   | `MISSING` | CLAUDE.md ごと作る。**タスク運用の節だけ**を書き、プロジェクトの説明を書き足さない |
   | `NO_SECTION` | **既存の記述を先に読む**（検証コマンドが別の節にあることが多い）。節は末尾に足し、内容をユーザーに見せて確認を取ってから書く。別の節は消さない |
   | `MISSING_LINE` | 足りない行だけ足す。既にある行は書き換えない |
   | `BAD_BRANCH` | `- ブランチ:` の先頭語が語彙に無い（`tw` が `INVALID` で止まる）。どの語にするかユーザーに聞いてから直す |
   | `OK` | 触らない |

   **Beads 方式にする**（ユーザーが Beads を使うと決めたときだけ。既定はファイル方式で、行を足さない）:
   節に `- タスクの置き場: beads` と、トラッカーを使うなら `- トラッカー: github`（と
   `- GitHub Project: `<owner>/<番号>``）か `- トラッカー: jira` を足し（正典「Beads 方式」の設定の表）、
   手順1の `init.py` を本体で打ち直して `.beads` を作る。**`bd init` を手で打たない**（`--stealth` なしだと
   `AGENTS.md`・`CLAUDE.md` に書き足して自動でコミットする）。`github` なら `gh auth login`（`project`
   スコープ）と `bd config set github.repository <owner>/<repo>`、`jira` なら `bd jira` の設定を
   ユーザーに頼む（認証情報をスキルが書かない）。既存の `develop/task/` から移すのは別の作業（人が決める）

3. **通しで確かめる**: `tw status`。
   まっさらなら `---` と末尾の集計行だけが出る（終了コード0）。`MISSING` なら手順1が効いていない。
   Beads 方式なら `tw config-doctor` の `store`・`beads`・`tracker` の行も `OK` を確かめる。

4. **コミットする**（件名にタスクIDは付けない。push はしない）。作業ツリーの枝に居るなら
   `tw ship` で `main` へ送る。

5. **報告する**: 作ったファイル、CLAUDE.md に書いた値（と、そのコマンドが実際に通ったこと）、
   CLAUDE.md の扱い（新規／末尾に追記／触らず）、点検で見つかった問題。最後に次の一歩を1行:
   やりたいことを `develop/direction.md` の `## ユーザーから` に書いて `/plan-tasks` を呼ぶとタスクになる。

## やらないこと

- タスクの登録・実行（`/plan-tasks`・`/next-task`）。旧形式の移行（`tw migrate` は人が打つ）
- `develop/` を `.gitignore` に足す（タスクの正典はコミットして共有する）
- CLAUDE.md の「## タスク運用」節より外の書き換え
- `~/.claude/skills/` へのリンク（スキル自体の導入は tsukumo-plugins の `install.sh`）
