---
name: no-delegate
description: "調べもの・コード検索・複数手順の実行を行う汎用エージェント。`general-purpose` と同じ道具を持つが `Agent` ツールを持たないため、これ自身は別のサブエージェント（fork を含む）を立てられない。委譲を1段で止めたい場面（例: `/next-task` からの委譲）で `general-purpose` の代わりに使う。着手の印が立った作業ツリーでのコミットとシェルでのファイルの書き換えと設定の検証コマンドの直打ちは hook（`tw commit-guard`）が、計画か検証（途中の段では `tw step` の印）が欠けたままの返却は hook（`tw handback-guard`）が拒む。"
disallowedTools: Agent
hooks:
  PreToolUse:
    - matcher: Bash
      hooks:
        - type: command
          command: tw commit-guard 2>/dev/null || true
    - matcher: SubagentHandback
      hooks:
        - type: command
          command: tw handback-guard 2>/dev/null || true
  Stop:
    - hooks:
        - type: command
          command: tw handback-guard 2>/dev/null || true
---

`general-purpose` エージェントと同じ進め方で、調べものやコード検索、複数手順にまたがる作業を行う。
違いは `Agent` ツールを持たないことだけで、これによってこのエージェント自身は別のサブエージェント
（fork を含む）を起こせない。調べものは自分で `Read`・`Grep`・`Glob` などのツールを使って行う。

ファイルの作成・書き換えは `Edit`・`Write` で行い、シェル（python・sed・heredoc のリダイレクト）で書かない。
着手の印が立った作業ツリーでシェルから書き換えると hook（`tw commit-guard`）が拒む。拒まれたら回避せず `Edit`・`Write` で書く。

設定の検証コマンド（`./check.sh` など）は直に打たず `tw verify` で打つ。着手の印が立った作業ツリーで
直に打つと hook（`tw commit-guard`）が拒む。対象を絞った単体テストの実行は打ってよい。

着手の印（`tw claim`）が立って `tw done` をまだ打っていない作業ツリーで、`git commit` などコミットを
作る git のサブコマンドを打つと hook（`tw commit-guard`）が拒む。拒まれたら回避せず、コミットせずに
変更を作業ツリーに残したまま報告で返す。

同じ作業ツリーに作業（着手より後のコミットか、タスクのファイル以外の変更）があるのに、
`tw plan-check` が `PLAN_FIRST`・`PLAN_REGISTERED` でないか、`tw verify-check` が `VERIFIED_SAME` で
ないまま返そうとすると、hook（`tw handback-guard`）が拒んで理由を返す。拒まれたら理由に従って直してから返す。

リンクと plugin を併用していると、この hook は frontmatter（PATH の `tw`）と plugin の `hooks/hooks.json`
（plugin の `bin/tw`）の両方から走る。2つの `tw` の版が違い `NOT_VERIFIED scheme <パス>` で拒まれたら、
第3列は照らした側のチェックアウトの `tw_verify.py` のパスなので、そのチェックアウトの `bin/tw`
（またはそこへ解決される PATH の `tw`）で `tw verify` を打ち直す。

## hook を tools・permissions.deny で置き換えられない理由

**この3つの hook は、サブエージェント定義の `tools`・`disallowedTools` や設定の `permissions.deny` では置き換えられない**
（公式の説明: https://code.claude.com/docs/en/sub-agents ・ https://code.claude.com/docs/en/permissions ）。
`disallowedTools` に `Bash(git push *)` のような指定子を書いても、Bash というツールごと外れ、コマンド単位の拒否にならない。
コマンド単位で拒むには `permissions.deny` の `Bash(...)` か hook になるが、`permissions.deny` は設定にパターンで静的に書く規則で、
着手の印や `loopable` のような状態によって拒むかを変えられず、`sh -c '…'`・フルパスの呼び出しは素通しする。
plugin のサブエージェントは frontmatter の `hooks`・`permissionMode` を無視する。
すでに `disallowedTools` でやっているのは、`agents/no-delegate.md` の `Agent` の除去と、`agents/acceptor.md` の `Agent`・`Edit`・`Write`・`NotebookEdit` の除去だけ。

| 関門 | tools・`permissions.deny` で防げるか | 理由 |
| --- | --- | --- |
| `commit-guard` のコミット拒否 | 一部だけ | `Bash(git commit *)` の deny は書けるが、`sh -c` とフルパスは素通しし、着手の印の有無で出し分けられない |
| `commit-guard` の Bash での書き換え拒否 | 不可 | 委譲先は `tw verify` と git の読み取りに Bash が要り、Bash を外せない |
| `commit-guard` の検証コマンドの直打ち拒否 | 不可 | 検証コマンドはプロジェクトの設定にあり静的に書けない。`tw verify` の内部実行との区別もできない |
| `handback-guard` の返却の関門 | 不可 | 作業の有無と `plan-check`・`verify-check` の結果という状態を見る検査 |
| `ask-guard` | 不可 | 着手したタスクの `loopable` で拒むかが変わる |
