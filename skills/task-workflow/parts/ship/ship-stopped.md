# 手順7・8で `SHIPPED`・`NOTHING` 以外が返ったとき

   | 出力 | すること |
   | --- | --- |
   | `NOT_OWNER`・`NOT_READY`（手順7の `tw finish`・`tw done` が返す。終了コード4） | 以降を打たず、行を添えて終了する。扱いは `task-model` の tw-reference.md の終了コード表の 4 の行に従う |
   | `ALREADY_DONE`（手順7の `tw finish`・`tw done` が返す。終了コード4。`done` は済んでいて、何も書き足していない） | コミットと送りが残っているなら `tw finish T-xxx --resume --message '<何をしたか>'` で続ける。`tw ship` まで済んでいるなら放っておく |
   | `WORK_NOT_LANDED`（手順7の `tw finish` が返す。終了コード4。行は `WORK_NOT_LANDED\tT-xxx\t<作業先>\t<枝>`。何も書いていない） | 作業先の枝が合流していない。`${CLAUDE_SKILL_DIR}/other-repo.md` の「合流」を打ち、`LANDED` を確かめてから同じ `tw finish` を打ち直す |
   | `INVALID`（手順7の `tw finish`・`tw done` が返す。終了コード3） | 理由をそのまま報告して止まる。直しに行かない（`task-model` の tw-reference.md の終了コード表の 3 の行） |
   | `MAIN_DIRTY`・`DIRTY` | 本体（か自分）が汚れている。パスを添えて終了する（`main` は動いていない） |
   | `CONFLICT` | 衝突したファイルを添えて人に預ける（スキルの一覧に `resolving-merge-conflicts` があれば、それで解ける） |
   | `VERIFY_FAILED` | 付け替え後の検証が落ちた（行は `VERIFY_FAILED\t<コマンド>\t<ログのパス>`。落ちた回を残した時刻つきのログ）。ログを読み、落ちた箇所を添えて人に預ける（送っていない） |
   | `RACE` | 3回先を越された。人に預ける |
   | `GIT_READ_ONLY` | `.git` に書けない（送っていない。何も変えていない）。`task-model` の tw-reference.md の終了コード表の11のとおり（`tw ship` を打ち直す） |
