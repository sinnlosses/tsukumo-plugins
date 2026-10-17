# 作業先が別のリポジトリのとき

タスクが直す対象が自分の作業ツリーの外のリポジトリのときの、実装の依頼文の項目（手順5c）と受け入れ（手順6）。

## 実装の依頼文の項目（手順5c）

   作業先が別のリポジトリ（`## やること` の `### 作業先`）のタスクでは、`tw brief implementer` が
   `${CLAUDE_SKILL_DIR}/parts/implementer/implementer-request.md` の `別のリポジトリ` の節を依頼文に足す。中身は、作業先の別の作業ツリーで直してコミットしてから
   その作業ツリーで `tw verify` を打つこと、`tw step`・`tw pause` は着手した側で打つこと、作業先の本体を書き換えないこと、
   作業ツリーのパスでその回に Read してから Edit すること。枝（タスクIDの小文字。`T-012` なら `t-012`）・作業ツリーの道・
   作業先の主ブランチは tw が埋めるので、メインは書かない（道の決め方は `tw brief --help`）。枝の名前が違うと関門が作業先を
   見ない（正典の `tw-reference.md`「hook の関門」節）。関門が作業先の検証を求めずに通したとき（同じ節）は、受け入れで検証コマンドを打つ。

## 委譲の前に本体の汚れを控える（手順4aのあと）

   作業先が別のリポジトリのとき、最初の委譲の前に `git -C <作業先の本体> status --short` を打ち、出力を
   一言メモしておく（空なら「空」）。本体には、委譲先ではなく別のセッションが未コミットの変更を置いている
   ことがある。受け入れで見つけた汚れが委譲の前から在ったものかを、この控えで区別する。

## 受け入れ（手順6）

   **段の始まりと終わりを手で打つ**（`tw accept` を使わないので、`tw` は出さない。どれも着手した作業ツリーで、作業先の `cd` の外で打つ）:
   - 頭（本体の汚れを見る前）に `tw lap T-xxx accept start`、検証が通った（`VERIFIED_SAME`、または打ち直した `tw verify` が通った）直後に `tw lap T-xxx accept end`
   - 受け入れ役を起こす直前に `tw lap T-xxx review start`、判定を受けた直後に `tw lap T-xxx review end`（差し戻しのたびに対で打つ）
   - 下の `tw land` の直前に `tw lap T-xxx land start`、`tw land` の出力を読んだ直後に `tw lap T-xxx land end`

   **最初に本体の汚れを見る**（`git diff` や `tw verify-check` より前）: `git -C <作業先の本体> status --short`
   を打ち、委譲の前の控えと比べる。

   | 見たもの | すること |
   | --- | --- |
   | 空、または控えと同じ | 次へ |
   | 控えに無いファイルがあり、中身（`git hash-object <ファイル>`）が委譲先の枝の同じパス（`git rev-parse <枝>:<パス>`）の blob と同じ | 委譲先が本体に書き残したもの。報告と食い違うものとして `${CLAUDE_SKILL_DIR}/parts/implementer/return-abnormal.md` の表に従い、委譲先へ `SendMessage` で伝えて差し戻す。`git restore` は blob が同じと確かめたファイルだけに打つ |
   | 控えに無いファイルがあり、blob が枝と違う・枝に無いファイル | 委譲先か別の作業か分からない。消さずに、ファイルと `git diff` の要点を添えて人に預けて終了する |
   | 控えにあったファイル（委譲の前から在る） | 別の作業。消さない。本体が汚れているあいだは下の `tw land` が `NOT_LANDED` で止まるので、汚れが消えるのを待つか、人に預けて終了する |

   **作業先が別のリポジトリのとき**（タスクが直す対象が自分の作業ツリーの外のリポジトリ）: 控えは
   cwd の作業ツリーごとに別なので、`tw verify-check` は、委譲先が残した作業先の作業ツリー
   （報告のパス。枝はタスクIDを小文字にした `<枝>`）で `cd <パス> && tw verify-check` と打つ（自分の作業ツリーで打つと `NOT_VERIFIED none`
   になるだけ）。レビューの判定も同じ作業ツリーで、主ブランチから委譲先の枝までの範囲を渡して打ち
   （`python3 ${CLAUDE_SKILL_DIR}/../task-model/scripts/core/review_needed.py --difficulty <difficulty> main..<枝>`）、
   その出力を（着手した作業ツリーで）`tw brief acceptor T-xxx --accept-file -` に渡す。受け入れ役の依頼文には、tw が
   作業先の作業ツリーと差分のコマンド `git diff main...<枝>`・コメント行を拾う範囲 `main..<枝>` を埋め、
   `REVIEW` なら作業先の作業ツリーで木の SHA を取って添える。範囲を渡さず本体で打つと `NO_RANGE` が返る（`NOTHING` ではない）ので、出たら範囲を付けて打ち直す。表は同じに読む。`VERIFIED_SAME` なら、検証コマンドを打たずに同じ作業ツリーで
   `cd <パス> && tw land <枝>` を打つ。`tw land` は作業先の本体で `<枝>` を ff-only で主ブランチへ合流し、
   入ったことを確かめてから作業ツリーと枝を消す。`git merge`・`git worktree remove`・`git branch -d` を
   素で打たない。
   `NOT_VERIFIED` ならその作業ツリーで `tw verify` を打ち直してから同じ段取りへ進む。受け入れで直すなら
   その作業ツリーで直してコミットしてから打つ。設定が無く `tw` が `MISSING` を返す作業先では控えが無いので、
   委譲先の報告を信用せず検証コマンドを打つ（`tw land` はタスク運用を始めていない作業先でも打てる）。

   | `tw land` の先頭語 | すること |
   | --- | --- |
   | `LANDED` に続けて `REMOVED`・`DELETED` | 次へ |
   | `NOT_LANDED`（何も変えていない） | 理由が「ff-only で合流できない」なら、作業先の主ブランチが先へ進んでいる。その作業ツリーで主ブランチを取り込んで `tw verify` を打ち直してから、もう一度打つ。ほかの理由は行を添えて人に預けて終了する |
   | `NOT_REMOVED`（合流は済んでいる） | 残った作業ツリーか枝を報告に書いて次へ進む（消すのは人に預ける） |
   | `GIT_READ_ONLY` | `task-model` の tw-reference.md の終了コード表のとおり |

   **許可規則**: auto mode の分類器は、作業先の本体の主ブランチへ合流するコマンドを [Modify Shared Resources]
   として拒むことがある。`tw land` も許可規則が無ければ拒まれうるので、利用者が `settings.json` の
   `permissions.allow` に `Bash(tw land:*)` を置く。エージェントは置かない。拒まれたら回り道をせず、
   作業ツリーのパスと枝を添えて人に預けて終了する。
