# 手順7・8で `SHIPPED`・`NOTHING` 以外が返ったとき

   | 出力 | すること |
   | --- | --- |
   | `NOT_OWNER`・`NOT_READY`（手順7の `tw finish`・`tw done` が返す。終了コード4） | 以降を打たず、行を添えて終了する。扱いは正典の終了コード表の 4 の行に従う |
   | `INVALID`（手順7の `tw finish`・`tw done` が返す。終了コード3） | 理由をそのまま報告して止まる。直しに行かない（正典の終了コード表の 3 の行） |
   | `MAIN_DIRTY`・`DIRTY` | 本体（か自分）が汚れている。パスを添えて終了する（`main` は動いていない） |
   | `CONFLICT` | 衝突したファイルを添えて人に預ける（スキルの一覧に `resolving-merge-conflicts` があれば、それで解ける） |
   | `VERIFY_FAILED` | 付け替え後の検証が落ちた（行は `VERIFY_FAILED\t<コマンド>\t<ログのパス>`。落ちた回を残した時刻つきのログ）。ログを読み、落ちた箇所を添えて人に預ける（送っていない） |
   | `RACE` | 3回先を越された。人に預ける |
   | `GIT_READ_ONLY` | `.git` に書けない（送っていない。何も変えていない）。`task-workflow` の WORKFLOW.md の終了コード表の11のとおり（`tw ship` を打ち直す） |
