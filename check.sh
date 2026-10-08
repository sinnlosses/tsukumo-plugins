#!/bin/sh
# このリポジトリの検証コマンド。`/next-task` が受け入れ判定に使う。
#
# 使い方: ./check.sh [--full | --plan]
#
# 自己テストの段は、変えたファイルに当たるものだけを流す
# （main との merge-base からの差分と、未コミット・未追跡のファイル）。
# 構文とリポジトリの整合は、いつも流す。check.sh 自身が変わったときは全段を流す。
# `--full` のとき、main の上にいるとき、detached HEAD のとき、差分が取れないときも全段を流す。
# `--plan` は段を流さず、流す段（== 見出し ==）と飛ばす段だけを出す。
#
# 1. 構文: install.sh・uninstall.sh・scripts/links.sh（いつも）
# 2. install.sh・uninstall.sh の自己テスト
# 3. task-workflow のスクリプトの自己テスト
# 4. retrospect のスクリプトの自己テスト
# 5. plan-tasks のスクリプトの自己テスト
# 6. next-task のスクリプトの自己テスト
# 7. リポジトリ全体の整合（frontmatter・README の索引・参照先の実在）（いつも）
#
# 当たる段は `./check.sh --plan` で見る。
set -e
here=$(cd "$(dirname "$0")" && pwd)

mode=scoped
case "${1:-}" in
  --full) mode=full ;;
  --plan) mode=plan ;;
  "") ;;
  *) echo "使い方: ./check.sh [--full | --plan]" >&2; exit 2 ;;
esac

scoped=no
changed=
if [ "$mode" != full ]; then
  branch=$(git -C "$here" branch --show-current 2>/dev/null || true)
  if [ -n "$branch" ] && [ "$branch" != main ] \
    && base=$(git -C "$here" merge-base HEAD main 2>/dev/null) \
    && diffed=$(git -C "$here" diff --name-only "$base" 2>/dev/null) \
    && untracked=$(git -C "$here" ls-files --others --exclude-standard 2>/dev/null); then
    changed=$(printf '%s\n%s\n' "$diffed" "$untracked")
    scoped=yes
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
  sh "$here/scripts/selftest_links.sh"
fi

if stage "task-workflow: scripts・task コマンド（ファイル方式・Beads 方式）の自己テスト" \
  'skills/task-workflow/*' 'agents/*' 'hooks/*' 'bin/*' 'skills/retrospect/scripts/*'; then
  python3 "$here/skills/task-workflow/scripts/selftest.py"
  echo
  echo "-- task コマンド（develop/task/ + 台帳） --"
  python3 "$here/skills/task-workflow/scripts/selftest_task.py"
  echo
  echo "-- task コマンド（Beads 方式。bd が無ければ飛ばす） --"
  python3 "$here/skills/task-workflow/scripts/selftest_beads.py"
fi

if stage "retrospect scripts の自己テスト" \
  'skills/retrospect/*' 'skills/task-workflow/scripts/*'; then
  python3 "$here/skills/retrospect/scripts/selftest.py"
fi

if stage "plan-tasks scripts の自己テスト" 'skills/plan-tasks/*'; then
  python3 "$here/skills/plan-tasks/scripts/selftest_removed_lines.py"
fi

if stage "next-task scripts の自己テスト" 'skills/next-task/*'; then
  python3 "$here/skills/next-task/scripts/selftest_review_needed.py"
  python3 "$here/skills/next-task/scripts/selftest_context_size.py"
  python3 "$here/skills/next-task/scripts/selftest_review_snapshot.py"
fi

echo
echo "== リポジトリの整合 =="
if [ "$mode" != plan ]; then
  python3 "$here/scripts/check_repo.py"
fi
