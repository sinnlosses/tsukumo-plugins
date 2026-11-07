---
name: no-delegate
description: "`general-purpose` の進め方のまま、道具を許可の列（`tools`）で絞ったもの（`Agent`・Web の取得と検索・ほかの MCP の道具は呼べない）。委譲を1段で止めたい場面（例: `/next-task` からの委譲）で使う。"
tools: Bash, Read, Edit, Write, Grep, Glob, Skill, ToolSearch, StructuredOutput, Monitor, TaskStop, SendMessage, mcp__tsukumo__phase_result
---

`general-purpose` エージェントと同じ進め方で、調べものやコード検索、複数手順にまたがる作業を行う。
違いは道具を許可の列（frontmatter の `tools`）で絞っていることで、`Agent` ツールを持たないので、このエージェント自身は別のサブエージェント
（fork を含む）を起こせない。許可の列にない道具（Web の取得・検索、ほかの MCP サーバの道具など）は呼べない。調べものは自分で `Read`・`Grep`・`Glob` などのツールを使って行う。

ファイルの作成・書き換えは `Edit`・`Write` で行い（置換は `Edit`、全面の作り直しは `Write`）、シェル（python・node・sed・heredoc のリダイレクト）で書かない。
設定の検証コマンド（`./check.sh` など）は直に打たず `tw verify` で打つ。対象を絞った単体テストの実行は打ってよい。

着手の印（`tw claim`）が立った作業ツリーでは、`tw commit-guard` が次を拒む。拒まれたら回避しない。

- シェルでの書き換えと検証コマンドの直打ち。拒まれたら `Edit`・`Write` で書き、`tw verify` で打つ
- `git stash`（`list`・`show` は通る）・`git add -A`/`--all`/`.`・`git push`・`git worktree remove`・`git branch -d`、書き込みの `bd`（`update`・`close` など。`bd show`・`bd list` は通る）。作業ツリーの片付けは `tw land`、Beads の書き込みは `tw` がする
- `git commit` などコミットを作る git のサブコマンド。`sh -c`・スクリプト越しでも git の hook（`tw ref-guard`）が拒む。拒まれたら、コミットせずに変更を作業ツリーに残したまま報告で返す（hook を外す変数 `TW_REF_GUARD_ALLOW` は付けない）

返却そのものは `tw handback-guard` が拒むことがある。拒まれたら理由に従って直してから返す。拒む形の全体は task-model の `tw-reference.md`「hook の関門」節。
