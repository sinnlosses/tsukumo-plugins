#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 宣言が無いと Python 3.9 が、長い日本語の行の読み込み位置によって「Non-UTF-8 code」で落ちる。
"""Beads に置いたタスクの運用を操作する入口コマンド。

使い方: tw <status|new|claim|release|done|ship|finish|land|config|config-doctor|show|edit|plan-check|plan-phases|verify|verify-check|accept|pause|step|lap|metrics|adopt|sync|backup|jira-closed|commit-guard|handback-guard|ask-guard> ...

`install.sh` が PATH 上に張る `tw` と、plugin の `bin/tw` から呼ぶ。`commit-guard`・`handback-guard` の
`--agent-scoped` は plugin の `hooks/hooks.json` が付け、入力に `agent_id` があるとき（サブエージェント）だけ関門を掛ける。
`ask-guard` は `--agent-scoped` を持たず、主のセッションにもサブエージェントにも掛かる。
スキル側の呼び方の正典は task-model の tw-reference.md「`tw` コマンドの参照」。

出力は常に stdout（先頭語で種類を判定する TSV）、stderr は使い方の誤りだけ、
終了コードは tw-reference.md の表のとおり。データの不備で traceback を出さない
（traceback は「環境の故障」の合図として取っておく）。ここは引数を解釈して、
`tw_*.py` のサブコマンドへ振り分けるだけ。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "core"))

import ask_guard
import beads
import commit_guard
import handback_guard
import layout
import gitinfo
import metrics
import taskfile
import tracker
import tw_accept
import tw_base
import tw_claim
import tw_config
import tw_edit
import tw_finish
import tw_handback
import tw_maint
import tw_new
import tw_relay
import tw_ship
import tw_status
import tw_verify


HOOK_NOTE = """commit-guard・handback-guard・ask-guard は Claude Code の hook の取り決めに従う: 出力は TSV でなく JSON で、終了コードはいつも0。
読めない入力も何も出さずに通す。タスク運用を始めていないリポジトリ・git の外でも打てる。

この3つの hook を `tools`・`permissions.deny` で置き換えられない理由と、3つが拒むもの・掛かる口は task-model の tw-reference.md「hook の関門」節。"""

SUBCOMMAND_HELP: dict[str, tuple[str, str]] = {
    "status": (
        "一覧（読むだけ。done/dropped は件数だけ。`--all` で行も）。",
        r"""一覧（読むだけ。done/dropped は件数だけ。`--all` で行も）。`--check` は検証コマンド向けの判定で、Beads の課題がこの運用の形（状態・`difficulty`・`loopable`・`direct:` の label・題）に読めないものがあれば `INVALID`、無ければ `OK`（本文の枠は見ない。枠は `new`・`adopt`・`edit` が書くときに検査する）

出力: 行 `id/status/difficulty/loopable/dependencies/着手可否/印/summary`（ドラフトは status が `draft`・着手可否が `DRAFT`）、`---` の後に `counts`（ドラフトの件数は `draft=`）・`ready`（ドラフトは数えない）・`todo_loopable`・`stale`・`invalid`・`triage\t<件数>\t<ID>`（振り分け前の課題。TRACKER.md の「サイクルの各段で Beads に起きること」）・（`jira` なら）`jira_close`・（横断の振り返りの時期なら）`retrospect_due`。`retrospect_due\t<最後の記録の日付>\t<経過日数>d` は `docs/history/retrospect.md` の見出しの日付（主ブランチの版と作業ツリーの版の遅いほう）から7日以上経ったときに出る。記録が無いあいだは台帳の `flow/` の最も古い出来事から7日以上で `retrospect_due\t-\t<経過日数>d`（`flow/` が無ければ出ない）""",
    ),
    "new": (
        "採番して Beads に課題を作る（コミットしない）。",
        r"""採番して Beads に課題を作る（コミットしない。採番は TRACKER.md の「タスクの要素と Beads の置き場」）。`--draft` はドラフト（WORKFLOW.md の「ドラフトの課題」）を作る: 本文は「タスクの本文」のドラフトの緩い検査で見て、`deferred` と metadata `draft`=`Y` で作り、`task_plan_base` は控えない。`--hold` と同時・`--direct` と同時は何も作らずに終了コード2。以下は `--draft` でないとき。`--difficulty`・`--loopable` は metadata `difficulty`・`loopable` に書く（label は付けない）。`--direct` は委譲しない近道の印（label `direct:Y`）を付け、「difficulty とモデルの切り替え」の基準の表で `tw` が見る条件を外れれば何も作らずに終了コード2。`## やること` の中身が要る（空にできるのは `--hold` だけ。段の形に沿わない・`### 名指すファイル` が無い・形が違う・パスが木に無い・`### 作業先` が絶対パス1行でないか git のリポジトリの根でなければ、何も作らずに終了コード2）。中身があれば、計画のリポジトリ（`### 作業先` があればそこ）の `HEAD` と主ブランチの分かれ目の SHA を metadata `task_plan_base` に控える

出力: `CREATED`・`LOCKED`（20回打ち直しても番号が取れない）""",
    ),
    "claim": (
        "clean・未送りなしを確かめ、主ブランチへ追い付き、印を立てる。",
        r"""clean・未送りなしを確かめ、主ブランチへ追い付き（主ブランチが `HEAD` の祖先なら `merge` を打たない）、印を立て（`.tw/local/task-open-claims/T-xxx` の控えも置く）、設定の `branch` が `既定`・`作業ブランチを切る`（無ければ `既定`）なら枝を切る。追い付くか枝を切るのに `.git` へ書けなければ、印を立てる前に `GIT_READ_ONLY` で止まる。印は `bd update --claim`（actor は作業ツリーの名前）で、`claim` した時点の `HEAD` と枝を metadata `task_claim_head`・`task_branch` に控える。登録時の計画の控えがあれば、名指したファイルが控えから主ブランチの先端までに変わったかを判定し、比べた先端を metadata `task_plan_tip` に控え、変わっていなければ印に `registered` を残す（出力は変えない）。近道の印（label `direct:Y`）があれば基準を測り直し、`CLAIMED` の行末に `direct=Y`（基準に当たり `registered`）か `direct=N:<理由>`（外れた条件か `登録時の計画が古い`）の列を足す。印が無ければ列を出さない。ドラフトは何もせずに `NOT_READY\tT-xxx\tDRAFT`（終了コード4）

出力: `CLAIMED`・`TAKEN`・`NOT_READY`・`DIRTY`・`UNSHIPPED`・`GIT_READ_ONLY`""",
    ),
    "release": (
        "印（`bd unclaim`）と `task-open-claims/T-xxx` の控えを消すだけ（本文は戻さない）。",
        r"""印（`bd unclaim`）と `task-open-claims/T-xxx` の控えを消すだけ（本文は戻さない）。`--force` は人が取り残しを片付けるときで、控えは印の持ち主の作業ツリーのものを消す（その作業ツリーが消えていれば何もしない）。担当者が無い `in_progress` の印も `--force` で外せる（status を `open` に戻す）。着手の印の控え（`bd kv` の `task-workflow.claim.<Beads ID>`。TRACKER.md の「GitHub との双方向」）も消すので、外した印は控えから戻らない

出力: `RELEASED`・`NOT_CLAIMED`・`NOT_OWNER`""",
    ),
    "done": (
        "`## 結果` を comment に入れ、label `ship:done`／`ship:dropped` を立てる。",
        r"""`## 結果` を comment に入れ、label `ship:done`／`ship:dropped` を立てる（stage もコミットもしない・印は残す。`bd close` は `ship` が打つ）。`task-open-claims/T-xxx` の控えは消すので、このあとのコミットを `commit-guard` は拒まない。着手の印の控え（`bd kv` の `task-workflow.claim.<Beads ID>`。TRACKER.md の「GitHub との双方向」）も消すので、`ship` までの間に取り込みが印を消しても控えからは戻らない。本文が枠（`## 結果` を除く7節）と違えば書かずに `INVALID`（終了コード3）。`claim` した時点の `HEAD` より後に、主ブランチに無いコミットがあれば `DONE` に続けて知らせる（`commit-guard` をすり抜けたコミットの事後の知らせ。控えの無い古い印では出さない）。`.git` に書かないので、読み取り専用の `.git` でも通る。ドラフト（印は立たない）に `--dropped` を打つと、`## 結果` を comment に入れ、label `cancelled` を足して `bd close` までその場で打つ（`claim`・コミット・`ship` は要らず、着手の控えと `COMMITS_SINCE_CLAIM` は見ない）。ドラフトに `--dropped` なしで打つと `NOT_READY\tT-xxx\tDRAFT`（終了コード4）

出力: `DONE`（`COMMITS_SINCE_CLAIM` が続くことがある。ドラフトは `DONE\tT-xxx\tbeads:<id>\tドラフトを閉じた`）・`NOT_OWNER`・`NOT_READY`・`INVALID`""",
    ),
    "ship": (
        "rebase → （付け替えたら）検証 → ff-only で送り、自分の印を `bd close` する。",
        r"""rebase → （付け替えたら）検証 → （`verify_before_ship` があれば）付け替えた・借りがある・控えが今の中身と違うときだけそれを（付け替えた回は検証の代わりに） → ff-only で送る（主ブランチを出している別の作業ツリー〔本体〕があれば、そこに `.gitmodules` があるとき `git submodule update --init` を本体で打って、サブモジュールを記録した版へ揃える。揃えるのは本体のサブモジュールだけで、ほかの作業ツリーには触らない。揃えられなくても送りは取り消さず、結果は変えない） → `ship:*` の立った自分の印を `bd close` し、`task-open-claims/T-xxx` の控えを消す → 作業ブランチから降りる → トラッカーへ送り、バックアップを取る。送るものがあって `.git` に書けなければ、rebase の前に `GIT_READ_ONLY` で止まる

出力: `SHIPPED`・`NOTHING`（どちらも `NOT_CLOSED`・`TRACKER`・`BACKUP` の行が続くことがある。`SHIPPED` の次に `SUBMODULE_NOT_SYNCED\t<本体の作業ツリーのパス>\t<理由の1行>` が出ることがある。本体のサブモジュールを揃えられなかった警告で、終了コードは変わらない）・`MAIN_DIRTY`・`CONFLICT`・`VERIFY_FAILED`・`RACE`・`GIT_READ_ONLY`""",
    ),
    "land": (
        "主ブランチを出している作業ツリーで `<枝>` を `git merge --ff-only` し、作業ツリーと枝を消す。",
        r"""主ブランチを出している作業ツリーで `<枝>` を `git merge --ff-only` し、`git merge-base --is-ancestor` で主ブランチに入ったことを確かめてから、`<枝>` を出している作業ツリーを `git worktree remove` し（サブモジュールを持つ作業ツリーは git が断るので、作業ツリーと各サブモジュールの `git status --porcelain` が空のときだけ `--force` で消す。汚れがあれば消さずに `NOT_REMOVED`）、枝を `git branch -d` する（合流のあと、作業ツリーを消す前に、主ブランチを出している作業ツリーに `.gitmodules` があれば `git submodule update --init` をそこで打って、サブモジュールを記録した版へ揃える。揃えるのは本体のサブモジュールだけ。作業先が別のリポジトリのタスクの受け入れで、作業先の作業ツリーから打つ。消す作業ツリーの中から打ってもよい）。ff できない（主ブランチに入っていない）・主ブランチを出している作業ツリーか `<枝>` の作業ツリーに未コミットの変更か未追跡のファイルがある・どちらかの作業ツリーが無い（ディレクトリが消えているときも）ときは、何も変えずに `NOT_LANDED` で止まる。主ブランチ・無い枝を渡すと終了コード2。タスク運用を始めていないリポジトリでも打て、Beads と台帳に何も書かない。`.git` に書けなければ合流の前に `GIT_READ_ONLY` で止まる

出力: `LANDED\t<枝>\t<主ブランチ>\t<合流後の SHA>` に続けて、サブモジュールを揃えられなかったときだけ `SUBMODULE_NOT_SYNCED\t<本体の作業ツリーのパス>\t<理由の1行>`（警告で、終了コードは変わらない）、そのあとに `REMOVED\t<作業ツリー>`・`DELETED\t<枝>`。合流のあとに消せなかったものは `NOT_REMOVED\t<作業ツリーか枝>\t<git の理由>`（終了コード4。合流は済んでいる）。`NOT_LANDED\t<枝>\t<理由>`（終了コード4）・`GIT_READ_ONLY`""",
    ),
    "finish": (
        "`done` → `--add` のファイルを個別に `git add` して `--message` で1コミット → `ship` を順に打つ。",
        r"""`done` → `--add` のファイルを個別に `git add` して `--message` で1コミット → `ship` を順に打つ。`--add` が無いか、追加しても差分が無ければコミットせず `NO_COMMIT` を出して `ship` へ進む。`done` が `NOT_OWNER` で止まればコミットも `ship` も打たない。`git` が失敗したら標準エラーに出して終了コード1

出力: `done` の行（`DONE`・`COMMITS_SINCE_CLAIM` など）、`COMMITTED\t<sha>`／`NO_COMMIT\t(理由)`、`ship` の行。最後の行が `ship` の判定で、`SHIPPED`・`NOTHING` 以外は `ship` の説明のとおり止まる。`done` の出力に `COMMITS_SINCE_CLAIM` の行があれば、`done` は済んだまま、コミットも `ship` も打たずにその行までを出して終了コード12で止まる（続きは個別の `git add`・`git commit`・`tw ship`。`finish` や `done` を打ち直すと `## 結果` の comment が二重になる）""",
    ),
    "edit": (
        "本文と属性と依存を直す。",
        r"""本文と属性と依存を直す（`--difficulty`・`--loopable` は metadata を書き換える。コミットも `ship` も要らない。`## 完了条件`・`## やること` はそれぞれの欄へ分けて入れ、版は `bd history` に残る。`## 結果` は拒む）。`--status` は着手前（`open`・`deferred`）だけ。ドラフトに `--status todo|hold` を打つと、書いたあとの本文（同じ呼び出しの `--body-file` を当てたもの）を `new` と同じ検査（`hold` なら `--hold` の検査）にかけ、通らなければ何も書かずに `NOT_READY\tT-xxx\tDRAFT\t<理由>`（終了コード4）。通れば状態と印 `draft` の取り外しを同じ `bd update` で書き、`## やること` に中身があれば `new` と同じく `task_plan_base` を控える。ドラフトの本文の編集は WORKFLOW.md の「タスクの本文」のドラフトの緩い検査で見る。`--direct Y|N`（`--body-file` が無くてもよい）は label `direct:Y` を付け外しする。`Y` で基準を外れれば何も書かずに終了コード2。印のあるタスクの本文か `--difficulty` を変えて基準を外れたら、書き込んで印を外し、`EDITED`（と `PLAN_AFTER_WORK`）の次に `DIRECT_OFF\tT-xxx\t<理由>` を出す。`--add-deps`・`--remove-deps`（`--body-file` が無くてもよく、その場合は本文に触れない）は Beads の依存を `bd dep add`・`bd dep remove` で1辺ずつ直す（`bd` の循環検査の前に `tw` が先に拒む）。削除してから追加し、すでにある依存の追加は通る。自分自身・Beads に無い ID・循環になる辺・形の違う ID・依存にない ID の削除・追加と削除に同じ ID は、何も書かずに終了コード2。以下は本文について。本文を丸ごと書き換える。`--section` を付けると、その節の中身だけを渡した中身に置き換え、ほかの節は1バイトも変えない（節名は `やること`・`## やること` のどちらでも受ける。枠に無い見出し・渡した中身に行頭の `## ` の行があれば、書き込まずに終了コード2。置き換えた全文に以降の検査が効く）。`--section` と `--body-file` の組は繰り返せて、先頭から順に同じ呼び出しの中で置き換える（組の数が違う・同じ節を2回指す・`--body-file -` を2回使うのは書き込まずに終了コード2）。検査と、Beads・トラッカーへの反映は結合した本文に対して1回だけ通るので、何節も書くときは繰り返して1回で打つ（`--status`・`--summary` なども同じ呼び出しに付けられる）。渡した本文の `## 目的・背景`・`## 完了条件` がいまの本文と違えば、書き込まずに `FRAME_CHANGED\tT-xxx\t<違う節>` で止まる（終了コード4。行末の空白と空行の数の差は違いに数えない。いまの本文に節が無いもの・中身が空か「なし」のものは比べない）。変えてよいときだけ `--change-frame` を付けて打ち直す。ほかの節だけの書き換えは今までどおり通る。`claim` の後に、着手の印の持ち主が `## やること` に初めて中身を入れたとき、その時点で作業が始まっていたか（`claim` した時点の `HEAD` より後のコミットか、渡した本文のファイル以外の変更があるか。submodule のずれは数えない）を1回だけ metadata `task_plan` に残す（値は `first`／`after-work`）。`after-work` になるときは書き込まずに `WORK_BEFORE_PLAN\tT-xxx\t<次の一手>` で止まる（終了コード4）。`--after-work` を付けると書き込んで `after-work` を残し、`EDITED` の次に `PLAN_AFTER_WORK\tT-xxx\t作業の後に書いた` の行を足す（作業が始まっていなければ `--after-work` は何も変えず `first`）。front matter 付きの本文は拒む（終了コード2。除いて渡す）

出力: `EDITED`（`PLAN_AFTER_WORK`・`DIRECT_OFF` が続くことがある）・`NOT_READY`・`WORK_BEFORE_PLAN`・`FRAME_CHANGED`""",
    ),
    "adopt": (
        "振り分け前の課題に metadata `difficulty`・`loopable` と本文を付ける。",
        r"""振り分け前の課題（TRACKER.md の「サイクルの各段で Beads に起きること」）に metadata `difficulty`・`loopable` と本文を付ける（`jira` はキーの無い課題に番号も）。本文の検査は `new` と同じで `## やること` の中身が要り（`--hold` は無い）、`notes` に入れて metadata `task_plan_base` を控える。`--direct` は label `direct:Y` を付け、基準を外れれば `new` と同じく終了コード2

出力: `ADOPTED\t<元のID>\t<ID>`""",
    ),
    "accept": (
        "受け入れの機械的な並びを打つ。",
        r"""受け入れの機械的な並びを打つ。`lap T-xxx accept start` → `plan-check T-xxx` → `scripts/review_needed.py`（タスクの `difficulty` で判定）の順で、`REVIEW` なら `lap T-xxx review start` を打って木の控えを出して区切る。`SKIP`・`NOTHING`・`NO_RANGE` なら続けて設定の `format`（あれば）→ `verify-check`。`--after-review` は `lap accept`・`plan-check`・判定を飛ばし、`format` → `verify-check` だけ打つ。部品の出力の行をそのまま打った順に並べ、止まる行が出たらそこで以降を打たない

出力: 部品の行に加え、`REVIEW\topus`・`REVIEW\tcode\t<数>`・`SKIP\tdocs-only`・`NOTHING`・`NO_RANGE`（判定。`NO_RANGE` は範囲なしで作業ツリーの変更が空）、`SNAPSHOT\t<木の SHA>`（レビューが要るとき。最後の行）、`FORMATTED`（整形が通った）、`FORMAT_FAILED\t<コマンド>`（続けて出力の末尾40行。終了コード10）、`MISSING\t<パス>`（`review_needed.py` が無い。終了コード6）、`INVALID\t…`（判定の失敗。終了コード3）。`NOT_CLAIMED`・`NOT_OWNER` は終了コード4で止まる。それ以外の終了コードは部品のもの""",
    ),
    "verify": (
        "タスクIDは基本付けない。主ブランチを取り込んでから検証コマンドを打つ。",
        r"""タスクIDは基本付けない。付けるなら着手の印が立った自分のタスク1つだけで、引数なしと同じに動く（違えば何も打たず `NOT_OWNER`。`.beads` の無い作業先の作業ツリーでは付けられず usage）。主ブランチを取り込んでから検証コマンドを打つ。取り込むのは、主ブランチが `HEAD` より先へ進んでいて `HEAD` がその祖先のとき（`claim` のあとに自分のコミットがあれば取り込まず、`ship` が付け替える）で、未コミットの中身を一時のコミットにして `git merge-tree` で主ブランチと合わせ、衝突が無ければ作業ツリーに当てて `HEAD` を主ブランチへ進める（作業は未コミットのまま残る。`git add` 済みの区別は消える）。衝突すれば何も書き換えず、検証コマンドを打たずに控えを消して `CONFLICT` で止まる。取り込みが要るのに `.git` へ書けなければ、検証コマンドを打たずに控えを消して `GIT_READ_ONLY` で止まる（取り込みが要らなければ読み取り専用の `.git` でも通る）。取り込みのあと・検証の前に、設定に `format` があれば打つ（無ければ打たない。整形で変わった中身を鍵に取るので、続く `verify-check` は `VERIFIED_SAME` になる。整形が落ちたら検証コマンドを打たずに控えを消して `FORMAT_FAILED` で止まる）。打つ前後で作業ツリーの中身の鍵（`HEAD` の SHA・一時の index に `git add -A` して `.tw/local/` を外してから `write-tree` した木の SHA・検証コマンドの文字列。本物の index は変えず、新しい object は一時ディレクトリに書くので `.git` に書かない。`.tw/local/` の中身は木に入らない）を取り、通って前後で同じなら `.tw/local/task-verify-stamp` に控える。落ちたら控えを消す。全出力は同じ場所の `task-verify.log`（整形の出力のあとに検証の出力が続く。検証が落ちた回は `task-verify.failed-<時刻>.log` にも写して直近3本を残し、判定行のパスはこちら）。この作業ツリーが着手の印を持つタスク（`done`・`dropped` にしたものを除く）の `## やること` が空か「なし」のまま作業が始まっていれば（`edit` と同じ判定）、検証コマンドを打たずに控えを消して `PLAN_MISSING` で止まる

出力: 取り込んだときは先頭に `FOLDED\t<前の HEAD>..<主ブランチ>` の1行。`CONFLICT\t<ファイル,…>`（終了コード7）。`VERIFIED\t<木の SHA>\t<ログのパス>`（控えた）／`VERIFIED_UNSTAMPED\t<ログのパス>`（通ったが検証のあいだに中身が変わったので控えない）／`VERIFY_NOT_PASSED\t<ログのパス>`（終了コード10）／`FORMAT_FAILED\t<ログのパス>`（整形コマンドが落ちた。終了コード10）。どれも出力の末尾40行が続き、そのあとに同じ判定行をもう一度出す（取り込んだときは `FOLDED` の行も判定行の前にもう一度出す）。出力を `tail` で切るときは、最後の行（取り込んだときは最後の2行）だけで判定と `FOLDED` の有無が取れる。`PLAN_MISSING\tT-xxx\t<次の一手>`（終了コード10。該当するタスクごとに1行）。`GIT_READ_ONLY`（終了コード11）。検証コマンドが無ければ `NOTHING`""",
    ),
    "pause": (
        "いまの中身の鍵を `.tw/local/task-pause-stamp` に控え、`flow/` に `pause` を1行足す。",
        r"""タスクIDだけを付けたときは、着手の印が立った自分のタスクなら引数なしと同じに動く（違えば `NOT_OWNER`。基本は引数なし）。いまの中身の鍵（`verify` と同じ取り方）を `.tw/local/task-pause-stamp` に控え、`flow/` に `pause` を1行足す。着手中のタスクの `### 作業先` が別のリポジトリで、枝の名前がタスクIDの小文字の作業ツリーがあれば、その作業ツリーの `.tw/local/task-pause-stamp` にもその中身の鍵を控える。`handback-guard` は、この控えがいまの中身と同じなら計画・検証が欠けていても返却を通す（目視待ち・判断が要って止める・計画を作業の後に書いた回の委譲先が、返す直前に打つ）。タスクIDと段の番号を付けると、`step` と同じ置き場・同じ鍵の取り方で種類 `pause` の段の印を控える（並列の段の担当が打つ。最後の段にも打てる。段の読めない計画・段の外の番号・自分の着手でなければ `NOT_OWNER`）

出力: `PAUSED\t<木の SHA>`・`NOT_OWNER`""",
    ),
    "step": (
        "`## やること` の段 `n` を済ませた印として、いまの中身の鍵を控える。",
        r"""自分の着手の印のタスクの `## やること` の段 `n` を済ませた印として、いまの中身の鍵（`verify` と同じ取り方から、段 `n` と並列の組になる段の触るファイルも外す）とタスクID・段の番号を `.tw/local/task-step-stamps/<ID>.<n>` に控え（段ごとに1つで、ほかの段の控えを上書きしない。着手中でないタスクの控えは消す）、`flow/` に `step` を1行足す。`pause` と同じく、作業先の作業ツリーがあればそこにもその中身の鍵で控える（作業先の作業ツリーでは打たず、着手した作業ツリーで打つ）。`handback-guard` は、そのタスクの段の控えのどれかが同じ外し方で取ったいまの中身と同じで、段がいまの計画の最後より前なら、`plan-check` が通っている限り検証が欠けていても返却を通す（最後でない段を済ませた委譲先が、返す直前に打つ）。段の読めない計画・1〜段の数の外の番号は終了コード2。最後の段は控えずに `LAST_STEP`（終了コード4。`tw verify` を通して返す）

出力: `STEPPED\tT-xxx\t<n>/<段の数>\t<木の SHA>`・`LAST_STEP\tT-xxx\t<n>/<段の数>\t<次の一手>`・`NOT_OWNER`""",
    ),
    "lap": (
        "オーケストレーターの段の頭を、`flow/` に `lap` の1行として足す。",
        r"""自分の着手の印のタスクのオーケストレーターの段の頭を、`flow/` に `{"event":"lap","stage":"<段>"}` の1行として足す（メインが打つ。第3引数を付けると `"edge":"start"`／`"end"` も入り、段の所要時間は同じ段の `end − start`。省くと `edge` の欄は書かず、旧い呼び方のまま、その出来事から同じタスクの次の出来事までの差。第3引数が `start`・`end` のどちらでもなければ終了コード2）。`<段>` は `direct`（委譲しない近道でメインが直し始めた）・`delegate`（委譲を投げた）・`accept`（最後の段の返りを受けて受け入れに入った）・`review`（受け入れ役を起こした。差し戻しのあとも打つ）・`retro`（振り返りに入った）・`land`（別のリポジトリへの合流。台帳は着手側と作業先で別なので `tw land` は記録せず、着手した作業ツリーでメインが `start`／`end` を打つ）・`sync`（`tw ship` のあとのトラッカー反映とバックアップ）。ほかの段は終了コード2。着手の印が無ければ記録しない。記録に失敗しても終了コードは 0

出力: `LAPPED\tT-xxx\t<段>`（第3引数を付けたときは末尾に `\t<start|end>`）・`NOT_CLAIMED\tT-xxx`""",
    ),
    "relay": (
        "段の結果を、後の段が読む申し送り（Beads の comment）として鍵ごとに書く・読む。",
        r"""`--body-file` を付けると、本文を鍵 `<鍵>` の申し送りとして、`## 申し送り: <鍵>` で始まる comment に書く（WORKFLOW.md「段の申し送り」。書けるのは自分の着手の印のタスクだけで、違えば何も書かずに `NOT_OWNER`、終了コード4）。空の本文は消した扱いになる。付けなければ、その鍵の最後の comment の本文を出す（着手の印によらない）。鍵は自由な語で、空かタブ・改行を含めば終了コード2。comment は `release`・`claim` で消えず、GitHub に出さない。無い ID は `NOT_READY`（終了コード4）

出力: `RELAYED\tT-xxx\t<鍵>`・`RELAY\tT-xxx\t<鍵>`（次の行から本文）・`NONE\tT-xxx\t<鍵>`（無いか空）・`NOT_OWNER\tT-xxx`・`NOT_READY\tT-xxx\t存在しない`""",
    ),
    "show": (
        "タスク1件を front matter と本文の形で出す（読むだけ）。",
        r"""タスク1件を front matter と本文の形で出す（読むだけ。WORKFLOW.md の「タスクの本文」）。枠の7節を空でもこの順に出す

出力: 本文。無ければ `NOT_READY`""",
    ),
    "plan-check": (
        "自分の着手の印について、`## やること` を作業より先に `tw edit` で書いたかを出す（読むだけ）。",
        r"""自分の着手の印について、`## やること` を作業より先に `tw edit` で書いたかを出す（読むだけ）

出力: `PLAN_FIRST\tT-xxx`、そうでなければ `PLAN_NOT_FIRST\tT-xxx\t<理由>`（`missing`＝空か「なし」／`after-work`＝作業が始まってから書いた／`unrecorded`＝`tw edit` が印を残さなかった。`claim` の前に書いた・着手の印の持ち主でない作業ツリーから書いた・`tw edit` を通さずに書いた／`steps`＝中身はあるが段の形（WORKFLOW.md の「タスクの本文」の「段の形」）に沿わない。印によらずこれを先に出す）。登録時に書いた計画（`new`・`adopt`）は、名指したファイルが変わっていなければ `PLAN_REGISTERED\tT-xxx\t<控えた SHA>`（`PLAN_FIRST` と同じに扱ってよい）、変わっていて書き直していなければ `PLAN_STALE\tT-xxx\t<変わったファイル,…>`（書き直すと `PLAN_FIRST`）。比べる先は `claim` が控えた着手時の主ブランチの先端なので、受け入れで打っても変わらない。段が読めれば2行目から、並列の組（「タスクの本文」の「段の形」）ごとに `PARALLEL\tT-xxx\t<a>,<b>`、触るファイルが重なって1本道にした組ごとに `SERIAL\tT-xxx\t<a>,<b>\t<重なったパス,…>` を番号の順に出す（欄の無い計画では出ない。最後の段は合流するので並列の組に入らない）。`PARALLEL` が1行でもあれば続けて段の順に `STEP\tT-xxx\t<n>\t<待つ段,…|なし>`（`前の段`・`SERIAL`・合流で決まる前後のうち、ほかの待つ段より先に済むものを除いた直の待ち）を出す。どれも終了コード0。`NOT_OWNER`""",
    ),
    "plan-phases": (
        "自分の着手の印について、`work_plan` に渡す段の並びと位置を出す（読むだけ）。",
        r"""自分の着手の印について、`work_plan` の引数（`phases`・`current`・`finishedInGroup`・あれば `delegatedRange`・`skippedPhases`）をそのまま写せる形で出す（読むだけ）。帯の段は `tw config` の `steps` の並びの順に組む（並びが空なら既定の4つの順）: 全名の `:` より後ろが `implement-step` なら下の実装の段、`accept-step` は「受け入れ」、`retrospect-step` は「振り返り」（`parts.retrospect` が `なし` なら省く）、`ship-step` は「送り出し」、それ以外はその名前の1段。既定の並びなら受け入れ以降は「受け入れ」「振り返り」「送り出し」。実装の段は `plan-check` と同じ印で決める: `PLAN_REGISTERED` は計画の段なしで各段、`PLAN_FIRST` は先頭に済みの「計画」を足し、それ以外は仮の「実装」1段と `delegatedRange` の `{first}` だけ（段の数が未定）。並びに `implement-step` が無ければ実装の段も `delegatedRange` も無い。`flow` が `main` か `loopable: N` なら `delegatedRange` なし。近道（`direct:Y` かつ `PLAN_REGISTERED`）は実装の段が「直す」「検証」になり、「受け入れ」「振り返り」を省く（既定の並びなら「直す」「検証」「送り出し」）。`--skipped <段,…>` は済んだ段のうち飛ばした段の名前で、出力の `skippedPhases` に入る（並びに無い名前と、その位置で済んでいない段は `NO_PHASE`）。連続する段のすべての組が並列の組なら1つの配列にまとめる（`current` は配列を1要素に数える）。`--through <段>` は、その段までと、それ以前を済ませた位置（段は `計画`・`実装`（全段）・`受け入れ`・`振り返り`・`直す`・`検証`・段の番号・既定の4つ以外のスキルの段の名前。省略は何も済んでいない位置）。`--also <n,…>` は、並列のまとまりの中で `--through` の後ろにあるが先に済んだ段の番号。全部済みにする指定（並びの最後の段まで）と、並びに無い段は何も出さず `NO_PHASE`

出力: `PHASES\tT-xxx\t<JSON 1行>`。`NO_PHASE\tT-xxx\t<理由>`（終了コード2）。`NOT_OWNER`（終了コード4）""",
    ),
    "verify-check": (
        "いまの中身の鍵を `verify` の控えと照らす（読むだけ）。",
        r"""いまの中身の鍵を `verify` の控えと照らす（読むだけ）

出力: `VERIFIED_SAME\t<木の SHA>`（検証を省いてよい）、そうでなければ `NOT_VERIFIED\t<理由>`（`none`＝控えが無い／`base`＝主ブランチが進んでいて `verify` が取り込める。`verify_before_ship` があり取り込みが衝突しないときは、この理由にせず鍵で照らす／`head`／`scheme`＝別の版の `tw` が控えた。第3列にこの `tw` のチェックアウトの `tw_verify.py` の実パス／`content`／`command`）。どちらも終了コード0。検証コマンドが無ければ `NOTHING`""",
    ),
    "config": (
        "解けた設定を出す（読むだけ）。`--schema` で欄の形、`set <キー> <値>` で書く。",
        r"""解けた設定を出す（読むだけ）。`--schema` は欄の形を出し（読むだけ）、`set <キー> <値>` は検査してから `.tw/config.toml` に書く

`--schema`: 設定にはよらず（`--version 2` の候補の走査だけ、いまの git の作業ツリーの `.claude/skills/` を見る。git の外なら飛ばす）、1行の JSON を出す（`tw` の「stdout は先頭語の TSV」の、hook の口に続く例外）。形は `{"schema": <形の版>, "default_word": "既定", "none_word": "なし", "keys": [...]}` で、`keys` は画面に出す順の欄。各欄は `key`（`flow`・`parts.<枠>`）・`label`・`description`・`kind`（`choice`＝`values` の `{"value","label","description"}` から選ぶ／`part`＝パーツの全名を書く）・`default`、`part` の欄は加えて `candidates`（`agent`＝Agent ツールの一覧から／`skill`＝スキルの一覧から）と `allow_none`（`なし` を許すか）。`schema` は欄を消す・意味を変えるときだけ上がる。パーツの候補は出さない。`--schema --version <版>` は移行中だけの形で、`2` なら `{"schema": 2, "keys": [...]}`（並びの欄 `steps`＝`kind: steps`・`default`＝スキルの全名の配列で、この `tw` と同じチェックアウトの plugin の候補を `metadata` の `workflow-step-order`（整数）の小さい順に、無いものはその後ろに走査順で並べたもの・`values`＝候補 `{"value","label","description"}`。候補は SKILL.md の frontmatter の `metadata` に `workflow-step: "true"` を持つスキルだけで、作業ツリーの `.claude/skills/`・`~/.claude/skills/`・この `tw` と同じチェックアウトの plugin の `skills/` から拾う）。知らない版は終了コード2。

`set <キー> <値>`: 書けるキーは `flow`・`steps`・`parts.<枠>` だけ。`steps` の値は1行の JSON の文字列の配列（例 `'["a","b"]'`）で、1つ以上・重複なし・`--schema --version 2` の `values` か今の値にある名前だけ（外れると終了コード2）。`.tw/config.toml` には最上位の `steps = ["a", "b"]` として書き、`SET` の値は詰めた JSON。キーの行があれば値だけを差し替え（行末のコメントは残す）、無ければ `flow`・`steps` は最初の表より前、`parts.<枠>` は `[parts]` の表の最後（表が無ければ末尾に表ごと）へ足す。ほかの行と並びは変えない。出力 `SET\t<キー>\t<値>`。知らないキー・語彙の外の値は stderr に理由を出して終了コード2（ファイルは変えない）。設定が無ければ `MISSING`（終了コード6）、読めなければ `INVALID`（終了コード3）

出力: 1行目 `CONFIG\t.tw/config.toml`、続けて全キーを `<キー>\t<解けた値>\t<config|default>`（`config` は設定に書いてある、`default` は既定。コマンドのキーで値が無ければ `なし`、ほかは `-`。`base_branch` を書いていなければ git で決めた主ブランチ。`flow` は無ければ `delegate`。`steps` は詰めた JSON の配列で、無ければ既定の並び）、そのあと `[parts]` の枠ごとに `parts.implementer`・`parts.acceptor`・`parts.retrospect`・`parts.ship` の順で `parts.<枠>\t<既定|なし|全名>\t<config|default>`。設定が無ければ `MISSING`（終了コード6）。`.beads` が無くても打てる""",
    ),
    "config-doctor": (
        "このリポジトリが今の読み取りに合っているかを点検する（読むだけ。`--fix` は無い）。",
        r"""このリポジトリが今の読み取りに合っているかを点検する（**読むだけ。`--fix` は無い**）。主ブランチが何で決まったか・設定が読めるか、の2検査を必ず1行ずつ出し、続けて前提のコマンド（python・git・bd・dolt・gh・tw）の行を出し、設定が読めれば Beads の用意とトラッカーの行を足す

終了コード1の意味が他と違う（「直すものがある」。環境の故障ではない）。検査の最悪値を返し、`INVALID` があれば3、直すもの（必須の前提の欠落・古さを含む）があれば1、全部OKで0。点検のコマンドなので、1で止まっても報告するだけでよい。

出力: `base_branch`・`config` の2行。`config\t<OK|MISSING|INVALID>\t<ファイル>\t<詳細>`（読めない・語彙の外は `INVALID`、設定が無い・検証コマンドが無いは `MISSING`）。前提の行は `<python|git|bd|dolt|gh|tw>\t<OK|MISSING|OPTIONAL>\t<必須|任意>\t<版・詳細>`（必須の欠落と最低版未満が `MISSING`。`gh` はトラッカーが github のときだけ必須、`tw` は任意）。設定が読めれば続けて `store\tOK\tbeads`・`beads\t<OK|MISSING>\t<.beads のパス>`（`MISSING` は直すもの）・`tracker\tOK\t<なし|github|jira>`。各行の2語目が `OK`／`MISSING`／`INVALID`（前提の行は `OPTIONAL` も）""",
    ),
    "metrics": (
        "台帳の `flow/` の記録から、直近 N 日と前の同じ長さの期間の数を並べる（読むだけ）。",
        r"""台帳の `flow/` の記録（`claim`・`release`・`verify`・`pause`・`step`・`lap`・`ship`・`done` のたびに `tw` が1行足す。書けなくても元のサブコマンドの出力と終了コードは変わらず、標準エラーに1行出るだけ）から、直近 N 日（既定 7）と、その前の同じ長さの期間の数を並べる（読むだけ）。`verify` は着手の印を持つタスクごと、`ship` は送れたタスクごと（`NOTHING` も着手の印を持つタスクごとに1行足すが、出力の `shipped` の数は `SHIPPED` だけ。`VERIFY_FAILED`・`CONFLICT`・`RACE` は印を持つタスクごと）に1行

出力: 1行目 `PERIOD\t<N>d\tcurrent\tprevious`、続けて `shipped`（送り出した件数）・`lead_median_seconds`・`lead_max_seconds`（着手から送り出しまで）・`verify_per_task`（1件あたりの `verify` の回数の平均）・`verify_failed`（`FORMAT_FAILED`・`VERIFY_NOT_PASSED` の件数）・`ship_verify_failed`（`ship` の `VERIFY_FAILED`）・`reclaim`（`release` のあとの `claim`）・`reflection_none_ratio`（`done` のうち振り返りが兆候なしの割合。`dropped` と、近道か設定で振り返りを省いた `done`〔`reflection` が `skipped`〕は除く）の各行が `<名前>\t<今>\t<前>`（無ければ `-`）。壊れた行は読み飛ばし `SKIPPED\t<件数>`。記録が無ければ `EMPTY`。`--stages` を付けるとこの出力の代わりに、直近 N 日に送り出したタスク〔`ship` が `SHIPPED`〕と、`done` したが `ship` が `NOTHING` だったタスク〔作業先が別のリポジトリのもの〕の段ごとの所要時間を `STAGE\t<段>\t<difficulty>\t<道>\t<件数>\t<中央値秒>\t<最大秒>` の行で出す（道は、着手から送り出しまでに `direct` の `lap` があり `delegate` の `lap` が無ければ `direct`〔委譲しない近道〕、ほかは `normal`。段は計画〔`claim`〕・直し〔`direct` の `lap`〕・委譲〔`delegate` の `lap`・`step`〕・検証〔`verify`。記録された所要秒〕・受け入れ・レビュー・振り返り〔それぞれの `lap`〕・合流〔`land` の `lap`〕・送り出し〔`done`〕・反映〔`sync` の `lap`〕の順。`edge` 付きの `lap` の段は同じタスクの同じ段の `end` の時刻 − `start` の時刻（検証の秒は引かない）で、`end` の無い `start` は所要に入れず、`STAGE` の行のあとに `OPEN\t<段>\t<件数>` の行で数を出す（`edge` の無い旧い `lap` は次の規則）。検証でない段の時間は、次の検証でない出来事までの差から、検証の区間〔記録の時刻から記録の秒だけ前まで〕と重なった秒を引く。並列の段の返却が重なっても区間は時刻順に切れて重ならないので、委譲の時間は段ごとの和でなく壁時計の長さになる。前の期間との比較は付けない。材料が無ければ `EMPTY`）""",
    ),
    "sync": (
        "トラッカーと同期する（`github` は双方向、`jira` は取り込み、打ち直しも兼ねる）。",
        r"""トラッカーと同期する（`github` は双方向、`jira` は取り込み、打ち直しも兼ねる）

出力: `TRACKER\tOK|FAILED\t…`・`NOTHING`・`BUSY\tsync\t<持ち主の情報>`（別の `tw sync` が走っている。終了コード5）""",
    ),
    "backup": (
        "Beads のバックアップを取る。",
        r"""Beads のバックアップを取る

出力: `BACKUP\tOK|FAILED\t<置き場>`""",
    ),
    "jira-closed": (
        "`jira:close` を外す。",
        r"""`jira:close` を外す

出力: `CLEARED`""",
    ),
    "commit-guard": (
        "Claude Code の PreToolUse hook から呼ばれ、着手中の作業ツリーでのコミット・書き換え・検証コマンドの直打ちを拒む。",
        r"""Claude Code の PreToolUse hook（`agents/no-delegate.md` の frontmatter と、plugin の `hooks/hooks.json`）から呼ばれる。`--agent-scoped`（`hooks/hooks.json` だけが付け、`"${CLAUDE_PLUGIN_ROOT}/bin/tw"` の絶対パスで呼ぶ）があるときは、入力に `agent_id` があるとき（サブエージェント。名前は問わない）だけ掛け、無ければ（主のセッション）通す。stdin の hook の入力を読み、Bash のコマンドのうちコミットを作る git のサブコマンド（`commit`・`merge`・`pull`・`cherry-pick`・`revert`・`am`・`rebase`）の実効の作業先（入力の `cwd`・`cd <dir>`・`git -C <dir>`）に `.tw/local/task-open-claims/` の控え（読む順は WORKFLOW.md の「ファイル配置と設定ファイル」の控えの行のとおり、`.tw/local/` が無ければ `.tw/` 直下、それも無ければ作業ツリー固有の git dir）があれば拒む。コミットのほかに、シェルでのファイルの書き換え（`sed -i`・`perl -i`・`>`・`>>`・`tee`、`-c`・ヒアドキュメントに書き込みが見える python）も、書き先（python は実効の作業先）を含む作業ツリーに控えがあれば拒む（入力の `cwd` に控えが無くても、書き先がその作業ツリーの中なら拒む）。作業ツリーの外・`.gitignore` で無視されるパス・`/dev/`・判定しきれない書き方（変数展開・変数やコマンド置換で決まる `cd` のあと・`eval`・`bash -c`）は通す。さらに、実効の作業先に控えがあるとき、その作業ツリーの設定の `verify`・`verify_before_ship` と同じコマンドの直打ちも拒んで `tw verify` を案内する。同じと見なすのは、`&&`・`;`・`|` を含まない設定のコマンドの語で始まり、残りが `-` で始まる語（`--full` など）だけのとき。`&&`・`;`・`|` で割れた各コマンドを別々に見る（`./check.sh` を `tail` に渡すパイプも拒む）。先頭の `env`・`command`・`VAR=値` の前置きとリダイレクトは無視し、`cd` は実効の作業先として追う。コマンドがパスで書かれていれば、実効の作業先（設定は作業ツリーの根）から解いて同じファイルを指すかで比べる（`./check.sh` と絶対パスは同じ）。旗でない語が続く絞った実行（`./check.sh path/to.test.ts`）・`bash ./check.sh`・`timeout …`・`tw verify`・設定が読めないか `なし` のときは通す。`cwd` が作業ツリーの外のまま絶対パスで打つものも、作業先に設定が無いので通す（既知の取りこぼし）。入力の `cwd` に控えがあり、そのタスクの `### 作業先` が別のリポジトリなら、そのリポジトリの作業ツリーのうち枝の名前がタスクIDの小文字のもの（`handback-guard` と同じ）にも控えがあるとみなして、書き換えと検証コマンドの直打ちを拒む（コミットは拒まない）。入力の `cwd` が作業先の作業ツリーそのものなら、着手した作業ツリーを引けないので通す（既知の取りこぼし）。メインのセッションと、別の作業ツリー・別のリポジトリへのコミットには掛からない。リンクだけで入れたときの `no-delegate` 以外への委譲と、Bash のコマンド文字列に `git` が出ないコミット（`sh -c`・スクリプト越し）には効かず、`finish`・`done` の `COMMITS_SINCE_CLAIM` が事後に拾う。`bd` は、書き換えか直打ちの先が控えの無い作業ツリーで、入力の `cwd` に控えがあるときだけ読む

出力: 拒むときだけ PreToolUse の deny の JSON 1行（理由は「コミットせず…報告で返す」か「…`Edit`・`Write` で書く」か「検証コマンド…を直に打たない。`tw verify` で打つ」）。通すときは何も出さない

"""
        + HOOK_NOTE,
    ),
    "handback-guard": (
        "Claude Code の hook から呼ばれ、作業があるのに計画・検証が欠けた返却を拒む。",
        r"""Claude Code の hook（`agents/no-delegate.md` の frontmatter の `Stop`（委譲先では `SubagentStop` として効く）と、`SubagentHandback` の `PreToolUse`。plugin では `hooks/hooks.json` の `SubagentStop` と `PreToolUse`）から呼ばれる。`--agent-scoped` の扱いは `commit-guard` と同じで、さらに `Edit`・`Write`・`NotebookEdit` を一度でも使ったサブエージェントの返却だけを判定する（plugin では `hooks/hooks.json` が matcher `Edit|Write|NotebookEdit` の `PreToolUse` でも呼び、`cwd` の作業ツリーに着手の控えがあれば `agent_id` ごとの印 `.tw/local/task-agent-writers/<agent_id>` を書いて何も出さない。印は着手の控えが無くなると消える。書き換えない受け入れ役の返却は判定しない）。入力の `cwd` の作業ツリーに `.tw/local/task-open-claims/` の控え（`commit-guard` と同じ読む順。`verify`・`step`・`pause` の控えも同じ）があり、控えのタスクのどれかに作業（`claim` した時点の `HEAD` より後のコミットか、タスク自身のファイル以外の変更）があって、`plan-check` が `PLAN_FIRST`・`PLAN_REGISTERED` でないか、`verify-check` が `VERIFIED_SAME`・`NOTHING` でなく `step` の控えも効かず、`pause` の控え（段の印の `pause` を含む）もいまの中身と違えば拒む。段の印はどれか1つが効けば通すので、並列の段の担当が `tw step` を打ち忘れて返しても、ほかの並列の段の効いている印で通る（担当を見分けないため。メインが返却の1行目と差分で受け入れる）。タスクの `### 作業先` が別のリポジトリなら、枝の名前がタスクIDの小文字の作業ツリーにも同じ判定を当てる（作業は主ブランチとの分かれ目より後のコミットか変更で、`verify-check`・`step`・`pause` の控えはその作業ツリーのもの。作業先に `.tw/config.toml` を置けば、`.beads` が無くても作業先の作業ツリーで `tw verify` が打てて控えが効く。作業先の作業ツリーで `tw verify` が設定の判定（`MISSING`・`INVALID`）で打てないとき、または設定に検証コマンドが無いときは、`verify-check` を `NOTHING` と同じに扱って通す。その作業ツリーがまだ無ければ見ない）。`verify-check` が `VERIFIED_SAME` で通る返却でも、計画の段が2つ以上でその作業ツリーに `tw step` の控え（`.tw/local/task-step-stamps/<ID>.<n>`）が1つも無ければ、段を飛ばしたとして拒む（`1..N-1` の全部まではそろえさせない）。作業の無い返却（前提が誤り・`dropped`・計画だけの回）は通す。並列の段の印は段ごとに残り、並列の組になる段の触るファイルの書き換えでは崩れない（止める返却は `tw pause T-xxx <n>` で段の印にする）。この関門は2つの口から走る（エージェント定義の hook は PATH の `tw`、plugin の hook は取り込みの `bin/tw`）。口を1つにまとめないのは、plugin のエージェント定義は frontmatter の `hooks` を無視するので plugin だけの利用者には `hooks.json` が要り、`hooks.json` は PATH に `tw` があるとは限らず `${CLAUDE_PLUGIN_ROOT}/bin/tw` に頼るため。リンクと plugin を併用して `no-delegate`（リンクで入れた名前）へ委譲すると両方が走る（`tsukumo-workflow:no-delegate` なら `hooks.json` だけ）。控えた `tw` と照らす `tw` の版が違うと中身の鍵の `scheme` が合わず `NOT_VERIFIED scheme <照らした側のチェックアウトの `tw_verify.py` のパス>` で止まる。クローンと取り込みを同じチェックアウトに揃え、第3列のチェックアウトの `bin/tw`（またはそこへ解決される PATH の `tw`）で `tw verify` を打ち直す。リンクだけで入れたときは `no-delegate` 以外への委譲には効かない。`bd` を呼ぶ

出力: 拒むときだけ JSON 1行（`SubagentStop` は `{"decision":"block","reason":…}`、`PreToolUse` は deny。理由は欠けた `plan-check`・`verify-check` の行と次の一手。作業先の作業ツリーの行にはそのパスを添える）。通すとき・ほかのイベントやツールには何も出さない

"""
        + HOOK_NOTE,
    ),
    "ask-guard": (
        "Claude Code の PreToolUse hook から呼ばれ、`/loop` に載せたタスクの作業中の AskUserQuestion を拒む。",
        r"""Claude Code の `PreToolUse` hook（plugin の `hooks/hooks.json` の matcher `AskUserQuestion`。`"${CLAUDE_PLUGIN_ROOT}/bin/tw"` の絶対パスで呼ぶ）から呼ばれる。`--agent-scoped` を持たず、名前（`agent_type`）を見ないので主のセッションにもサブエージェントにも掛かる。`install.sh` のリンクだけで入れたときは `hooks/hooks.json` が読まれないので掛からない。入力の `tool_name` が `AskUserQuestion` で、`cwd` の作業ツリーに `.tw/local/task-open-claims/` の控え（`commit-guard` と同じ読む順）があり、控えのタスクのどれかが `loopable: Y` で、作業の段の後でなければ拒む。作業の段の後とは、主のセッション（入力に `agent_id` が無い）で、台帳の `flow/` のそのタスクの最後の `claim` より後の最後の `lap` が `accept` の `end` か、段 `retro`・`land`・`sync` のとき（振り返りの lap を打たない流れでも受け入れの後は通す）。サブエージェント（`agent_id` がある）の質問は段によらず拒む。`tw` にも `bd` にも書かない（`bd` は読む）

出力: 拒むときだけ PreToolUse の deny の JSON 1行（理由はタスクIDと次の一手を打つ順に: `tw release T-xxx`（着手中は `--status` を直せない）・`tw edit T-xxx --status hold`・`## 注意` に論点を1行足す `tw edit T-xxx --section 注意 --body-file -`、作業ツリーに作業が残っていなければ `tw status` を取り直して `READY` かつ `loopable: Y` の行が残る限り `/loop` は次の1件へ、残っていれば人に預けて終える）。通すとき・ほかのツールには何も出さない

"""
        + HOOK_NOTE,
    ),
}


class _Commands:
    """サブコマンドを足すとき、`SUBCOMMAND_HELP` の説明を必ず付ける。"""

    def __init__(self, sub: argparse._SubParsersAction) -> None:
        self._sub = sub

    def add_parser(self, name: str) -> argparse.ArgumentParser:
        line, description = SUBCOMMAND_HELP[name]
        return self._sub.add_parser(
            name,
            help=line,
            description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tw")
    sub = _Commands(parser.add_subparsers(dest="command", required=True))

    p_status = sub.add_parser("status")
    p_status.add_argument("--all", dest="show_all", action="store_true")
    p_status.add_argument("--check", action="store_true")

    p_new = sub.add_parser("new")
    p_new.add_argument("--summary", required=True)
    p_new.add_argument("--difficulty", required=True, choices=taskfile.DIFFICULTY_VALUES)
    p_new.add_argument("--loopable", required=True, choices=taskfile.LOOPABLE_VALUES)
    p_new.add_argument("--deps", default="")
    p_new.add_argument("--hold", action="store_true")
    p_new.add_argument("--draft", action="store_true")
    p_new.add_argument("--direct", action="store_true")
    p_new.add_argument("--body-file", required=True)

    p_claim = sub.add_parser("claim")
    p_claim.add_argument("task_id")

    p_release = sub.add_parser("release")
    p_release.add_argument("task_id")
    p_release.add_argument("--force", action="store_true")

    p_done = sub.add_parser("done")
    p_done.add_argument("task_id")
    p_done.add_argument("--dropped", action="store_true")
    p_done.add_argument("--result-file", required=True)

    sub.add_parser("ship")
    p_finish = sub.add_parser("finish")
    p_finish.add_argument("task_id")
    p_finish.add_argument("--dropped", action="store_true")
    p_finish.add_argument("--result-file", required=True)
    p_finish.add_argument("--message", required=True)
    p_finish.add_argument("--add", dest="files", action="append", default=[])
    sub.add_parser("land").add_argument("branch")

    sub.add_parser("config-doctor")
    p_config = sub.add_parser("config")
    p_config.add_argument("--schema", action="store_true")
    p_config.add_argument("--version")
    p_config.add_argument("action", nargs="?", choices=("set",))
    p_config.add_argument("key", nargs="?")
    p_config.add_argument("value", nargs="?")

    p_show = sub.add_parser("show")
    p_show.add_argument("task_id")

    p_plan_check = sub.add_parser("plan-check")
    p_plan_check.add_argument("task_id")

    p_plan_phases = sub.add_parser("plan-phases")
    p_plan_phases.add_argument("task_id")
    p_plan_phases.add_argument("--through")
    p_plan_phases.add_argument("--also")
    p_plan_phases.add_argument("--skipped")

    p_accept = sub.add_parser("accept")
    p_accept.add_argument("task_id")
    p_accept.add_argument("--after-review", dest="after_review", action="store_true")

    sub.add_parser("verify").add_argument("task_id", nargs="?")
    sub.add_parser("verify-check")
    p_lap = sub.add_parser("lap")
    p_lap.add_argument("task_id")
    p_lap.add_argument("stage")
    p_lap.add_argument("edge", nargs="?", default=None)
    p_relay = sub.add_parser("relay")
    p_relay.add_argument("task_id")
    p_relay.add_argument("key")
    p_relay.add_argument("--body-file", dest="body_file", default=None)
    p_metrics = sub.add_parser("metrics")
    p_metrics.add_argument("--days", type=int, default=metrics.DAYS_DEFAULT)
    p_metrics.add_argument("--stages", action="store_true")
    sub.add_parser("commit-guard").add_argument("--agent-scoped", dest="agent_scoped", action="store_true")
    p_pause = sub.add_parser("pause")
    p_pause.add_argument("task_id", nargs="?")
    p_pause.add_argument("step", nargs="?")
    p_step = sub.add_parser("step")
    p_step.add_argument("task_id")
    p_step.add_argument("step")
    sub.add_parser("handback-guard").add_argument("--agent-scoped", dest="agent_scoped", action="store_true")
    sub.add_parser("ask-guard")

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("task_id")
    p_edit.add_argument("--body-file", action="append", default=None)
    p_edit.add_argument("--section", action="append", default=None)
    p_edit.add_argument("--summary", default=None)
    p_edit.add_argument("--difficulty", default=None, choices=taskfile.DIFFICULTY_VALUES)
    p_edit.add_argument("--loopable", default=None, choices=taskfile.LOOPABLE_VALUES)
    p_edit.add_argument("--status", default=None, choices=("todo", "hold"))
    p_edit.add_argument("--add-deps", dest="add_deps", default=None)
    p_edit.add_argument("--remove-deps", dest="remove_deps", default=None)
    p_edit.add_argument("--after-work", dest="after_work", action="store_true")
    p_edit.add_argument("--change-frame", dest="change_frame", action="store_true")
    p_edit.add_argument("--direct", default=None, choices=taskfile.LOOPABLE_VALUES)

    p_adopt = sub.add_parser("adopt")
    p_adopt.add_argument("bd_id")
    p_adopt.add_argument("--difficulty", required=True, choices=taskfile.DIFFICULTY_VALUES)
    p_adopt.add_argument("--loopable", required=True, choices=taskfile.LOOPABLE_VALUES)
    p_adopt.add_argument("--summary", default=None)
    p_adopt.add_argument("--direct", action="store_true")
    p_adopt.add_argument("--body-file", required=True)

    sub.add_parser("sync")
    sub.add_parser("backup")

    p_jira_closed = sub.add_parser("jira-closed")
    p_jira_closed.add_argument("task_ids", nargs="+")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "config":
        if args.schema and args.action is not None:
            parser.error("config: --schema と set は一緒に打てない")
        if args.action == "set" and (args.key is None or args.value is None):
            parser.error("config set: <キー> と <値> が要る")
        if args.action is None and args.key is not None:
            parser.error("config: 引数は set <キー> <値> だけ")
        if args.version is not None and not args.schema:
            parser.error("config: --version は --schema と一緒に打つ")
        if args.schema:
            tw_config.cmd_config_schema(args.version)
            return

    if args.command == "commit-guard":
        commit_guard.run(sys.stdin, sys.stdout, args.agent_scoped)
        return
    if args.command == "handback-guard":
        handback_guard.run(sys.stdin, sys.stdout, tw_handback.handback_refusal, args.agent_scoped)
        return
    if args.command == "ask-guard":
        ask_guard.run(sys.stdin, sys.stdout)
        return

    try:
        toplevel = gitinfo.git_toplevel()
    except gitinfo.GitCommandError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)

    if args.command not in ("config-doctor", "land"):
        refusal = tw_base.format_refusal(toplevel)
        if refusal is not None:
            print(refusal[0])
            raise SystemExit(refusal[1])

    # 主ブランチは要る道でだけ問い合わせる（`gitinfo.base_branch`。決まらなければ INVALID）。
    try:
        if args.command == "land":
            tw_ship.cmd_land(toplevel, args.branch)
            return
        if args.command == "config-doctor":
            tw_maint.cmd_config_doctor(toplevel)
            return
        layout.read_config(toplevel)  # 読めない設定はどのサブコマンドでも先に INVALID にする
        if args.command == "config":
            if args.action == "set":
                tw_config.cmd_config_set(toplevel, args.key, args.value)
            else:
                tw_status.cmd_config(toplevel)
            return
        _run(toplevel, args)
    except (gitinfo.NoBaseBranch, layout.ConfigError) as e:
        print(f"INVALID\t{e}")
        raise SystemExit(3)
    except gitinfo.GitReadOnly as e:
        print(
            f"GIT_READ_ONLY\t{e.path}\tsandbox の外で同じコマンドを打ち直す"
            "（Claude Code は sandbox.excludedCommands に tw を足す）"
        )
        raise SystemExit(11)


def _run(toplevel: str, args: argparse.Namespace) -> None:
    """`.beads` を要るサブコマンドを振り分ける（`verify`・`verify-check` だけは `.beads` が無くても打てる）。"""
    if args.command in ("verify", "verify-check") and not beads.is_initialized(toplevel):
        if args.command == "verify":
            tw_verify.cmd_verify(toplevel, args.task_id)
        else:
            tw_verify.cmd_verify_check(toplevel)
        return
    if not beads.is_initialized(toplevel):
        print(f"MISSING\t{beads.beads_dir(toplevel)}")
        raise SystemExit(6)
    tracker.read_tracker(toplevel)  # 読めない設定はどのサブコマンドでも先に INVALID にする
    try:
        if args.command == "status":
            tw_status.cmd_status(toplevel, args.show_all, args.check)
        elif args.command == "new":
            tw_new.cmd_new(toplevel, args)
        elif args.command == "claim":
            tw_claim.cmd_claim(toplevel, args.task_id)
        elif args.command == "release":
            tw_claim.cmd_release(toplevel, args.task_id, args.force)
        elif args.command == "done":
            tw_claim.cmd_done(toplevel, args.task_id, args.dropped, args.result_file)
        elif args.command == "ship":
            tw_ship.cmd_ship(toplevel)
        elif args.command == "finish":
            tw_finish.cmd_finish(toplevel, args.task_id, args.dropped, args.result_file, args.message, args.files)
        elif args.command == "show":
            tw_status.cmd_show(toplevel, args.task_id)
        elif args.command == "edit":
            tw_edit.cmd_edit(toplevel, args)
        elif args.command == "plan-check":
            tw_edit.cmd_plan_check(toplevel, args.task_id)
        elif args.command == "plan-phases":
            tw_edit.cmd_plan_phases(toplevel, args.task_id, args.through, args.also, args.skipped)
        elif args.command == "accept":
            tw_accept.cmd_accept(toplevel, args.task_id, args.after_review)
        elif args.command == "verify":
            tw_verify.cmd_verify(toplevel, args.task_id)
        elif args.command == "verify-check":
            tw_verify.cmd_verify_check(toplevel)
        elif args.command == "pause":
            tw_handback.cmd_pause(toplevel, args.task_id, args.step)
        elif args.command == "step":
            tw_handback.cmd_step(toplevel, args.task_id, args.step)
        elif args.command == "lap":
            tw_handback.cmd_lap(toplevel, args.task_id, args.stage, args.edge)
        elif args.command == "relay":
            tw_relay.cmd_relay(toplevel, args.task_id, args.key, args.body_file)
        elif args.command == "metrics":
            (metrics.cmd_metrics_stages if args.stages else metrics.cmd_metrics)(toplevel, args.days)
        elif args.command == "adopt":
            tw_new.cmd_adopt(toplevel, args)
        elif args.command == "sync":
            tw_maint.cmd_sync(toplevel)
        elif args.command == "backup":
            tw_maint.cmd_backup(toplevel)
        elif args.command == "jira-closed":
            tw_maint.cmd_jira_closed(toplevel, args.task_ids)
    except beads.BeadsError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
