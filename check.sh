#!/bin/sh
# このリポジトリの検証コマンド。`tw verify` が打ち、受け入れの判定に使う。
#
# 使い方: ./check.sh [--full | --plan]
#
# 自己テストの段は、変えたファイルに当たるものだけを流す
# （main との merge-base からの差分と、未コミット・未追跡のファイル）。
# 構文とリポジトリの整合は、いつも流す。check.sh 自身が変わったときは全段を流す。
# `--full` のとき、main の上にいるとき、detached HEAD のとき、差分が取れないときも全段を流す。
# `agents/*.md` の中身だけの変更（追加・削除でない）は、どの段にも当てない。
# `--plan` は段を流さず、流す段（== 見出し ==）と飛ばす段、task コマンドの節では絞りの先（`-- 絞り: <モジュール>` か `ALL`）だけを出す。`--full` と並べても段を流さない。
# 自己テストは一時ディレクトリを RAM ディスクに置く（作れなければ今の場所）。
# 低優先度（taskpolicy -c utility / nice）で流すのは `CHECK_LOWPRI=1` のときだけ。
#
# 1. リポジトリ全体の整合（frontmatter・README の索引・参照先の実在・殻 bin/tw-hook の自己テスト・公式の claude plugin validate）（いつも）
# 2. task-model のスクリプトの自己テスト（review_needed.py・review_snapshot.py を含む。台帳と Beads 方式は並べて走らせ、出力は流し終えてから順に出す）
#    絞れるときは tests/selftest_scope.py の対応の表が決め、selftest_task.py に当たる selftest_t_* だけを流す。表に載らないファイルは全件
#
# 当たる段は `./check.sh --plan` で見る。
set -e
here=$(cd "$(dirname "$0")" && pwd)
LOWPRI=
if [ "${CHECK_LOWPRI:-}" = 1 ]; then
  if command -v taskpolicy >/dev/null 2>&1; then
    LOWPRI="taskpolicy -c utility"
  elif command -v nice >/dev/null 2>&1; then
    LOWPRI="nice -n 10"
  fi
fi

mode=scoped
for arg in "$@"; do
  case "$arg" in
    --full) [ "$mode" = plan ] || mode=full ;;
    --plan) mode=plan ;;
    "") ;;
    *) echo "使い方: ./check.sh [--full | --plan]" >&2; exit 2 ;;
  esac
done

scoped=no
changed=
if [ "$mode" != full ]; then
  branch=$(git -C "$here" branch --show-current 2>/dev/null || true)
  if [ -n "$branch" ] && [ "$branch" != main ] \
    && base=$(git -C "$here" merge-base HEAD main 2>/dev/null) \
    && diffed=$(git -C "$here" diff --name-only --no-renames "$base" 2>/dev/null) \
    && untracked=$(git -C "$here" ls-files --others --exclude-standard 2>/dev/null); then
    changed=$(printf '%s\n%s\n' "$diffed" "$untracked")
    scoped=yes
    bodyonly=$(git -C "$here" diff --name-only --no-renames --diff-filter=M "$base" -- 'agents/*.md' 2>/dev/null)
    [ -z "$bodyonly" ] || changed=$(printf '%s\n' "$changed" | grep -vxF "$bodyonly" || true)
  fi
fi

touched() {
  [ "$scoped" = yes ] || return 0
  printf '%s\n' "$changed" | while IFS= read -r path; do
    [ -n "$path" ] || continue
    [ "$path" = check.sh ] && { echo hit; break; }
    for pat in "$@"; do
      case "$path" in $pat) echo hit; break 2 ;; esac
    done
  done | grep -q hit
}

# パターンに当たる変更のパスを出す（check.sh 自身が変わっていれば、それも出す）
matching() {
  printf '%s\n' "$changed" | while IFS= read -r path; do
    [ -n "$path" ] || continue
    [ "$path" = check.sh ] && { echo "$path"; continue; }
    for pat in "$@"; do
      case "$path" in $pat) echo "$path"; break ;; esac
    done
  done
}

# task-model の自己テストの段に当たるパターンを、コマンド（stage・matching）の後ろへ渡す
task_stage_patterns() {
  "$@" 'skills/task-model/scripts/*' 'agents/*' 'hooks/*' 'bin/*'
}

# 当たらなければ飛ばす行を出して偽を返す。当たれば見出しを出し、--plan でなければ真を返す
stage() {
  title=$1; shift
  echo
  if touched "$@"; then
    echo "== $title =="
    [ "$mode" != plan ] && ran_stages="$ran_stages$title
"
    [ "$mode" != plan ]
  else
    echo "== $title: 変更に当たらないので飛ばす =="
    [ "$mode" = plan ] || printf 'STAGE\t%s\tunobserved\n' "$title"
    return 1
  fi
}

# 走らせた段を、終わるときに STAGE 行（tw verify が台帳の stages に読む）で出す。
# 段は順に走るので、失敗で終わるなら最後に走らせた段が fail、それ以前は pass
ran_stages=
emit_stages() {
  rc_exit=$?
  [ -n "$ran_stages" ] || return 0
  printf '%s' "$ran_stages" | {
    prev=
    while IFS= read -r title; do
      [ -z "$prev" ] || printf 'STAGE\t%s\tpass\n' "$prev"
      prev=$title
    done
    if [ "$rc_exit" -eq 0 ]; then
      printf 'STAGE\t%s\tpass\n' "$prev"
    else
      printf 'STAGE\t%s\tfail\n' "$prev"
    fi
  }
  ran_stages=
}
trap emit_stages EXIT

echo "== リポジトリの整合 =="
[ "$mode" = plan ] || ran_stages="リポジトリの整合
"
if [ "$mode" != plan ]; then
  $LOWPRI python3 "$here/scripts/selftest_check_repo.py"
  $LOWPRI python3 "$here/scripts/selftest_tw_hook.py"
  python3 "$here/scripts/check_repo.py"
  if command -v claude >/dev/null 2>&1; then
    claude plugin validate "$here"
    claude plugin validate "$here/plugins/tsukumo-figure"
  else
    echo "claude コマンドが無いので claude plugin validate を飛ばす"
  fi
fi

pid_task=
pid_beads=
out_task=
out_beads=
stop_background() {
  emit_stages
  for pid in $pid_task $pid_beads; do
    kill "$pid" 2>/dev/null || true
  done
  rm -f "$out_task" "$out_beads"
}
trap stop_background EXIT
trap 'exit 130' INT TERM
task_scope() {
  scope=ALL
  [ "$scoped" = yes ] || return 0
  scope=$(task_stage_patterns matching | python3 "$here/skills/task-model/scripts/tests/selftest_scope.py") || scope=ALL
  [ -n "$scope" ] || scope=ALL
}

if task_stage_patterns stage "task-model: scripts・task コマンドの自己テスト"; then
  task_modules=
  task_scope
  [ "$scope" = ALL ] || for m in $scope; do task_modules="$task_modules --module $m"; done
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest.py"
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_review_needed.py"
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_review_snapshot.py"
  # テストの selftest_t_*.py だけが変わったときは、Beads 方式のテスト（約 160 秒）は流さない（本体は変わっていない）
  run_beads=yes
  if [ "$scoped" = yes ] && ! task_stage_patterns matching | grep -qv '^skills/task-model/scripts/tests/selftest_t_[a-z_]*\.py$'; then
    run_beads=no
  fi
  out_task=$(mktemp)
  out_beads=$(mktemp)
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_task.py" $task_modules >"$out_task" 2>&1 &
  pid_task=$!
  if [ "$run_beads" = yes ]; then
    $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_beads.py" >"$out_beads" 2>&1 &
    pid_beads=$!
  else
    echo "Beads 方式は本体が変わっていないので飛ばした" >"$out_beads"
  fi
  rc=0
  wait "$pid_task" || rc=1
  pid_task=
  [ -z "$pid_beads" ] || wait "$pid_beads" || rc=1
  pid_beads=
  echo
  echo "-- task コマンド（develop/task/ + 台帳） --"
  cat "$out_task"
  echo
  echo "-- task コマンド（Beads 方式。bd が無ければ飛ばす） --"
  cat "$out_beads"
  [ "$rc" -eq 0 ] || exit 1
elif [ "$mode" = plan ] && task_stage_patterns touched; then
  task_scope
  echo "-- 絞り: $(printf '%s' "$scope" | tr '\n' ' ')"
fi

