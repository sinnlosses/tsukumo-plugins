---
name: story-capture
description: Storybook のあるリポジトリでフロントの部品を直したとき、変えたファイルに当たる story だけを前後で撮り、最終レポートに前後の画を載せる。ユーザーが「見た目を直して」「部品を変えて」と頼み、リポジトリに Storybook（`.storybook/`・`*.stories.*`）があるときは、「story」という言葉が無くても着手時に使う。Storybook が無いリポジトリ・tsukumo 自身（疑似セッションの `capture-view` が撮る）・全 story の撮影・見た目の回帰テストには使わない。
---

# 変えた部品の story を前後で撮る

**story は部品を状態ごとに止めた場面で、1件ずつ `iframe.html?id=<story-id>&viewMode=story` で単独に描ける。**
このスキルは、変えたファイルに当たる story だけを選び、`#storybook-root` だけを PNG に撮る。
全 story は撮らず、外部サービスも使わない。撮った画と作業ファイルは `/tmp` に置き、対象のリポジトリには書かない。

## いつ使うか

| 場面 | 手段 |
| --- | --- |
| Storybook のあるリポジトリで部品を直す | このスキル。**着手時（変更の前）に `before` を撮る** |
| 画面全体・Storybook に無い場面を撮る | webapp-testing など。ここは story 単位だけ |
| 撮り忘れて変更が済んでいる | `base-worktree` で主ブランチの Storybook を build して `before` を撮る |
| Storybook が無い | 使わない |

## 手順

`SC=${CLAUDE_SKILL_DIR}/scripts/story_capture.mjs`。Playwright は対象のリポジトリの `node_modules` から読む。

1. **着手時に前を撮る。** Storybook が動いていればその URL（例 `http://localhost:6006`）、無ければ `storybook-static/` を使う。

   ```bash
   node $SC select --repo <根> [--base main] [--index http://localhost:6006/index.json]
   node $SC shoot --url http://localhost:6006 --ids <id,id> --out /tmp/story/<タスク> --label before
   ```

   着手時はまだ変えていないので、`select` の差分は空になる。直す予定のファイルが分かっていれば、その story の ID を
   `index.json` の `importPath` から選んで `--ids` に渡す。
2. **直したあとに後を撮る。** `select` を打ち直し（変えたファイルに当たる ID が出る）、同じ `--ids` で `--label after` を撮る。
   `NO_STORY\t<ファイル>` の行は、当たる story が無かったファイル。
3. **撮った PNG を開いて読む。** 空白・はみ出し・崩れを見てから載せる。
4. tsukumo の `report` で載せる。前後がそろえば `Compare`（`relation: before-after`、側は `kind: image`）、前が無ければ `Picture`。
   画像のパスは `/tmp` の下のまま渡せる。
5. 撮れなかった・当たる story が無かったときは、`checks` に `unobserved` の行（理由に `NO_STORY` や終了コードを書く）。

## 撮り忘れたとき

```bash
node $SC base-worktree --repo <根> --base main --out /tmp/story/base-static
node $SC shoot --url /tmp/story/base-static --ids <id,id> --out /tmp/story/<タスク> --label before
```

`base-worktree` は主ブランチを一時の `git worktree` に出し、元の `node_modules` を symlink して `storybook build` し、
終わると（中断でも）worktree を片付ける。依存の入れ直しはしない。

## 終了コード

| 終了コード | 意味 |
| --- | --- |
| 0 | 撮れた（`SHOT\t<label>\t<id>\t<パス>` が1件1行） |
| 2 | 引数の誤り |
| 3 | 撮れない環境（`playwright` が `node_modules` に無い・chromium が起きない・Storybook に届かない・`base-worktree` の build 失敗） |

3 のとき、`npx` での取得やグローバルな導入はしない。導入は人がやる。
