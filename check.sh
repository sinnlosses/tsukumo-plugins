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
# ただし agents/no-delegate.md の frontmatter（先頭の `---` から次の `---` まで）の変更は、
# テストが hook 行を読むので、当てる。
# `--plan` は段を流さず、流す段（== 見出し ==）と飛ばす段だけを出す。`--full` と並べても段を流さない。
# 自己テストは一時ディレクトリを RAM ディスクに置く（作れなければ今の場所）。
#
# 1. 構文: install.sh・uninstall.sh・scripts/links.sh（いつも）
# 2. install.sh・uninstall.sh の自己テスト
# 3. task-model のスクリプトの自己テスト（review_needed.py・review_snapshot.py を含む。台帳と Beads 方式は並べて走らせ、出力は流し終えてから順に出す）
# 4. task-model の WORKFLOW.md・tw-reference.md を読む検査（それだけが変わったときの軽い段）
# 5. retrospect のスクリプトの自己テスト（3 の段と並べて走らせ、出力はこの段で出す）
# 6. plan-tasks のスクリプトの自己テスト
# 7. comment-audit のスクリプトの自己テスト
# 8. task-workflow のスクリプトの自己テスト
# 9. リポジトリ全体の整合（frontmatter・README の索引・参照先の実在）（いつも）
#
# 当たる段は `./check.sh --plan` で見る。
set -e
here=$(cd "$(dirname "$0")" && pwd)
LOWPRI=
if command -v taskpolicy >/dev/null 2>&1; then
  LOWPRI="taskpolicy -c utility"
elif command -v nice >/dev/null 2>&1; then
  LOWPRI="nice -n 10"
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
    # agents/*.md のうち自己テストが実物を読むのは、no-delegate.md の frontmatter の hook 行だけ
    bodyonly=$(git -C "$here" diff --name-only --no-renames --diff-filter=M "$base" -- 'agents/*.md' 2>/dev/null)
    fm_end=$(git -C "$here" show "$base:agents/no-delegate.md" 2>/dev/null \
      | { n=0; d=0; while IFS= read -r l; do n=$((n+1)); [ "$l" = --- ] && d=$((d+1)); [ "$d" -eq 2 ] && { echo "$n"; break; }; done; })
    if [ -n "$bodyonly" ] && git -C "$here" diff -U0 "$base" -- agents/no-delegate.md 2>/dev/null \
      | grep '^@@' | while read -r _ old _; do old=${old#-}; [ "${old%%,*}" -le "${fm_end:-999999}" ] && { echo hit; break; }; done | grep -q hit; then
      bodyonly=$(printf '%s\n' "$bodyonly" | grep -vx agents/no-delegate.md || true)
    fi
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

# 当たらなければ飛ばす行を出して偽を返す。当たれば見出しを出し、--plan でなければ真を返す
stage() {
  title=$1; shift
  echo
  if touched "$@"; then
    echo "== $title =="
    [ "$mode" != plan ]
  else
    echo "== $title: 変更に当たらないので飛ばす =="
    return 1
  fi
}

echo "== install.sh・uninstall.sh・scripts/links.sh の構文 =="
if [ "$mode" != plan ]; then
  sh -n "$here/install.sh" && sh -n "$here/uninstall.sh" && sh -n "$here/scripts/links.sh" && echo "  ok"
fi

if stage "install.sh・uninstall.sh の自己テスト" \
  install.sh uninstall.sh scripts/links.sh scripts/selftest_links.sh 'agents/*' 'bin/*' \
  'skills/*/SKILL.md' 'skills/*/REQUIRES'; then
  $LOWPRI sh "$here/scripts/selftest_links.sh"
fi

pid_retro=
pid_task=
pid_beads=
out_retro=
out_task=
out_beads=
stop_background() {
  for pid in $pid_retro $pid_task $pid_beads; do
    kill "$pid" 2>/dev/null || true
  done
  rm -f "$out_retro" "$out_task" "$out_beads"
}
trap stop_background EXIT
trap 'exit 130' INT TERM
if [ "$mode" != plan ] && touched 'skills/retrospect/*' 'skills/task-model/scripts/*'; then
  out_retro=$(mktemp)
  $LOWPRI python3 "$here/skills/retrospect/scripts/selftest.py" >"$out_retro" 2>&1 &
  pid_retro=$!
fi

if stage "task-model: scripts・task コマンドの自己テスト" \
  'skills/task-model/scripts/*' 'agents/*' 'hooks/*' 'bin/*' 'skills/retrospect/scripts/*'; then
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest.py"
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_review_needed.py"
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_review_snapshot.py"
  out_task=$(mktemp)
  out_beads=$(mktemp)
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_task.py" >"$out_task" 2>&1 &
  pid_task=$!
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_beads.py" >"$out_beads" 2>&1 &
  pid_beads=$!
  rc=0
  wait "$pid_task" || rc=1
  pid_task=
  wait "$pid_beads" || rc=1
  pid_beads=
  echo
  echo "-- task コマンド（develop/task/ + 台帳） --"
  cat "$out_task"
  echo
  echo "-- task コマンド（Beads 方式。bd が無ければ飛ばす） --"
  cat "$out_beads"
  [ "$rc" -eq 0 ] || exit 1
fi

if stage "task-model: WORKFLOW.md・tw-reference.md を読む検査" skills/task-model/WORKFLOW.md skills/task-model/tw-reference.md; then
  $LOWPRI python3 "$here/skills/task-model/scripts/tests/selftest_task.py" test_readonly_commands_stay_out_of_git
fi

if stage "retrospect scripts の自己テスト" \
  'skills/retrospect/*' 'skills/task-model/scripts/*'; then
  rc=0
  wait "$pid_retro" || rc=1
  pid_retro=
  cat "$out_retro"
  [ "$rc" -eq 0 ] || exit 1
fi

if stage "plan-tasks scripts の自己テスト" 'skills/plan-tasks/*'; then
  $LOWPRI python3 "$here/skills/plan-tasks/scripts/selftest_removed_lines.py"
fi

if stage "comment-audit scripts の自己テスト" 'skills/comment-audit/*'; then
  $LOWPRI python3 "$here/skills/comment-audit/scripts/selftest_diff_added_comment_lines.py"
fi

if stage "task-workflow scripts の自己テスト" 'skills/task-workflow/scripts/*' 'skills/task-model/scripts/core/gitcmd.py' 'skills/comment-audit/*'; then
  $LOWPRI python3 "$here/skills/task-workflow/scripts/selftest_self_review.py"
fi

echo
echo "== リポジトリの整合 =="
if [ "$mode" != plan ]; then
  $LOWPRI python3 "$here/scripts/selftest_check_repo.py"
  python3 "$here/scripts/check_repo.py"
fi
