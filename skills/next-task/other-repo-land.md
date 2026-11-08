# 作業先が別のリポジトリのときの合流

`### 作業先` が別のリポジトリのタスクで、送り出しの前に作業先の枝を主ブランチへ合流させる。

作業先の作業ツリーが無ければ `MISSING\t作業先 <本体> の作業ツリー（枝 <枝>）`（終了コード6）なので、委譲先の報告のパスを確かめて人に預ける。

**検証**: `NOT_VERIFIED` なら作業先の作業ツリーで `tw verify` を打ち直す。
作業先に設定が無い（`tw verify-check` の行が `NOTHING`）と控えが無いので、
委譲先の報告を信用せず検証コマンドを打ち、下の合流は `--fold` を付けずに打つ（`tw land` はタスク運用を始めていない作業先でも打てる。`--fold` は検証コマンドの設定が要る）。

**合流**: 検証が済んだら、着手した作業ツリーで `tw lap T-xxx land start` を打ち、作業先の作業ツリーで
`cd <パス> && tw land <枝> --fold` を打ち、出力を読んだ直後に着手した作業ツリーで `tw lap T-xxx land end` を打つ（`tw land` は台帳が別なので lap を書かない）。
`tw land --fold` は、委譲先の検証のあとに作業先の主ブランチが進んでいれば枝の作業ツリーへマージコミットで取り込み、
検証を多くて1回打ち（取り込みが要らず控えが今の中身と同じなら省く）、通ったら作業先の本体で `<枝>` を ff-only で主ブランチへ合流し、
入ったことを確かめてから作業ツリーと枝を消す。検証を打つと作業先の検証コマンドの所要（数分〜10分）がかかるので、
Bash の timeout を上限の 600000 にするか背景で起こし、`LANDED` か `NOT_LANDED` の行まで待つ。
`git merge`・`git worktree remove`・`git branch -d` を素で打たない（委譲先が打つと hook `tw commit-guard` が拒む。`tw land` は Python から打つので通る）。

| `tw land --fold` の先頭語 | すること |
| --- | --- |
| `FOLDED`・`VERIFIED_SAME …verify=skipped`・`VERIFIED` | 取り込み・検証の段の行。読み進める |
| `LANDED` に続けて `REMOVED`・`DELETED` | 次へ |
| `CONFLICT` に続けて `NOT_LANDED`（何も変えていない） | 主ブランチの取り込みが衝突した。その作業ツリーで `git merge <主ブランチ>` を打ち、両方の意図を残して解いてコミットしてから、`tw land <枝> --fold` を打ち直す。解けなければ行を添えて人に預けて終了する |
| 検証の落ち（`VERIFY_NOT_PASSED`・`FORMAT_FAILED`）に続けて `NOT_LANDED`（合流していない。`FOLDED` が出ていれば、取り込んだマージコミットは枝に残る） | 作業先の作業ツリーで直してコミットしてから `tw land <枝> --fold` を打ち直す |
| `FOLDED` のあと `SUBMODULE_NOT_SYNCED` に続けて `NOT_LANDED`（合流していない。取り込んだマージコミットは枝に残る） | 取り込んだサブモジュールの版を揃えられなかった。その作業ツリーで `git submodule update --init` を通してから `tw land <枝> --fold` を打ち直す。通らなければ行を添えて人に預けて終了する |
| ほかの `NOT_LANDED`（主ブランチ・作業ツリー・枝は消していない） | 理由が「ff-only で合流できない」なら、`--fold` を付け忘れたか、検証の間に主ブランチが進んだ。`--fold` を付けて打ち直す。ほかの理由は行を添えて人に預けて終了する |
| `NOT_REMOVED`（合流は済んでいる） | 残った作業ツリーか枝を報告に書いて次へ進む（消すのは人に預ける） |
| `GIT_READ_ONLY` | `task-model` の tw-reference.md の終了コード表のとおり |

**許可規則**: auto mode の分類器は、作業先の本体の主ブランチへ合流するコマンドを [Modify Shared Resources]
として拒むことがある。`tw land` も許可規則が無ければ拒まれうるので、利用者が `settings.json` の
`permissions.allow` に `Bash(tw land:*)` を置く。エージェントは置かない。拒まれたら回り道をせず、
作業ツリーのパスと枝を添えて人に預けて終了する。
