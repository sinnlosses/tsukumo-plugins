---
name: list-tasks
description: "tw status の出力から、develop/task/ に登録されている未完了タスクの一覧をテーブル1つで表示し、着手できるものがあれば次の1件を推薦する。ユーザーが「タスク一覧を見せて」「今どのタスクが残ってる？」「次は何をやるべき？」「誰が何を作業中？」と言ったときに使う。読み取り専用で、タスクの実行も登録もしない。旧形式（develop/tasks.json）なら移行の案内だけ出す。"
---

未完了タスクを**テーブル1つに要約し、次の1件を推薦する**。**何も書き換えない・実行しない**（実行は
`/next-task`、登録は `/plan-tasks`）。ルールは `task-workflow` スキルの `WORKFLOW.md`（以下「正典」）。
タスクファイルは開かない（要約は `summary` にある）。

```bash
tw status
```

行の列は `id / status / difficulty / loopable / dependencies / 着手可否 / 印 / summary`（`id` は `T-xxx`。
Beads 方式でトラッカーが `github` なら Issue 番号の `GH-<n>` もあり、`T-xxx` の行のあとに並ぶ）。`---` の後ろに
`counts`・`ready`（READY 件数）・`todo_loopable`・`stale`・`invalid`、Beads 方式（正典「Beads 方式」）なら
`triage`（とトラッカーが `jira` なら `jira_close`）、残っていれば `legacy_progress`。

## 表示のしかた

0. **依存を確かめる**: `command -v tw` が何も返さなければ、`tw` が PATH に張られていないとして
   `MISSING` を報告して終了する（plugin で入れるなら `/plugin install sinnlos-skills@sinnlos-skills`、
   リンクで入れるなら `task-workflow` を含めて `./install.sh` を打ち直すよう案内する）。

1. 終了コードが0でなければ表を出さずに終わる: 5（`LEGACY`）は「旧形式（`develop/tasks.json`）。
   正典「旧形式からの移行」の手順で `tw migrate --dry-run` から移す」、6（`MISSING`）は「タスク運用を
   始めていない（`/setup-tasks`）」、3 は `INVALID` の理由、1 はエラー出力をそのまま添える。
   **タスクファイルを読んで代用しない。**

2. 未完了を**テーブル1つ**で。行の順は `READY` → `BLOCKED` → 作業中（`CLAIMED`）→ 判断待ち（`HOLD`）、
   同じ区分の中は ID 順:

   | ID | 状態 | 難易度 | loop | 内容 |
   | --- | --- | --- | --- | --- |
   | T-130 | 着手可 | haiku | 可 | speak のログに expression を残す |
   | T-125 | T-124 待ち | sonnet | 要判断 | 新しいキャラクターパックを画面から作れるようにする |
   | T-124 | 作業中（tsukumo-3 12m） | sonnet | 可 | キャラクターパックのスキーマを決める |
   | T-600 | 判断待ち | sonnet | 可 | 雑談の要約の上限を決める |

   | 出力 | 書き方 |
   | --- | --- |
   | `READY` | `着手可` |
   | `BLOCKED:T-124` | `T-124 待ち` |
   | `CLAIMED` | `作業中（<印の列>）` |
   | `HOLD` | `判断待ち` |
   | `TRIAGE` | `振り分け待ち`（Beads 方式。トラッカーから取り込んだもの） |
   | 印の列が `local` | 状態の後ろに `（未送り）` |
   | `loopable` が `Y` / `N` | `可` / `要判断` |

   - **1列1値**。`内容` は `summary` を切らずにそのまま貼る（端末幅で折り返してよい）。依存の列は
     作らない（`状態` が待ち先を持つ）。done/dropped の行は出さない

3. テーブルの下に**1行**: 件数（todo／作業中／判断待ち／done／dropped。`counts` から）、`ready` の
   件数、`todo_loopable` の `N` が1件以上なら「うち `/loop` では進まない N 件」。
   続けて、あるものだけ1行ずつ:
   - `stale` が1件以上: 「取り残しの印: T-xxx（STALE:gone …）。片付けるのは人（`tw release T-xxx --force`）」
   - `invalid` が1件以上: 「読めないタスクファイル: T-xxx」
   - `long_summary` に当たるもの（80桁を超える `summary`）: 「`summary` が長すぎる: T-xxx」
   - `legacy_progress`: 「移行の残り: `develop/progress.md`（未解決 n / 注意 m）。振り分けたら消す」
   - `triage` が1件以上: 「振り分け待ち: <ID>（`/plan-tasks` で `tw adopt` する）」
   - `jira_close` が1件以上: 「Jira で閉じてほしいもの: T-xxx（閉じたら `tw jira-closed T-xxx`）」

## オススメの提示

`READY` があれば**1件だけ**推薦する（着手はしない）。順位は上から見て、先に決着したところで確定:

1. 他のタスクをブロックしている数が多いもの（その ID を `dependencies` に持つ未完了の件数）
2. `difficulty` が低いもの（`haiku` → `sonnet` → `opus`）
3. ID が小さいもの

```
オススメ: T-012（difficulty: haiku）— T-015 と T-018 の依存を解くので、ここから開けると後が動く
着手するなら /next-task。
```

- 理由は決め手になったもの1行。推薦が `loopable: N` なら「ユーザーの判断が要るので `/loop` では
  進まない」と添える
- **`HOLD` は推薦しない。** `READY` が0件なら推薦の代わりに1行で、待ち先（「`T-xxx` が終わると
  `T-yyy` が開く」）か、`HOLD` があれば「人の判断待ち: T-xxx（`/next-task` を直接呼ぶと決められる）」

## 出さないもの

タスク本文と `## 結果`、2件目以降の実行計画、推薦したタスクの進め方（本文を読んでいないので中身の
話はできない）。特定のタスクを読みたいと言われたら `develop/task/T-xxx.md` を1つ開く（Beads 方式では `tw show T-xxx`。このスキルの範囲外）。
