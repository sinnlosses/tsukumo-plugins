#!/bin/sh
# install.sh と uninstall.sh の自己テスト。本物の ~/.claude と ~/.local/bin には触れない。
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fails=0
check() {
  if [ "$2" -eq 0 ]; then
    echo "  ok   $1"
  else
    echo "  FAIL $1"
    fails=$((fails + 1))
  fi
}

export HOME="$tmp/home"
export CLAUDE_CONFIG_DIR="$tmp/cfg"
dest="$tmp/dest"
bin="$tmp/bin"
mkdir -p "$HOME" "$dest" "$bin" "$CLAUDE_CONFIG_DIR/agents"
inst() { "$here/install.sh" --dest "$dest" --bin-dir "$bin" "$@"; }
uninst() { "$here/uninstall.sh" --dest "$dest" --bin-dir "$bin" "$@"; }

# 張る先にあらかじめ置く実ディレクトリと他所を指すリンク
mkdir "$dest/retrospect"
ln -s "$tmp" "$dest/setup-tasks"
mkdir "$tmp/elsewhere"
ln -s "$tmp/elsewhere" "$dest/list-tasks"

inst >/dev/null 2>&1
uninst >"$tmp/out" 2>"$tmp/err"
check "全件で外す: 終了コード 0" $?
left=$(find "$dest" "$bin" "$CLAUDE_CONFIG_DIR/agents" -type l -exec readlink {} \; | grep -c "^$here/" || true)
check "全件で外す: このリポジトリを指すリンクが残らない" "$left"
[ -d "$dest/retrospect" ] && [ ! -L "$dest/retrospect" ]
check "実ディレクトリが残る" $?
[ "$(readlink "$dest/setup-tasks")" = "$tmp" ] && [ "$(readlink "$dest/list-tasks")" = "$tmp/elsewhere" ]
check "他所を指すリンクが残る" $?
grep -q "skipped retrospect" "$tmp/err" && grep -q "skipped setup-tasks" "$tmp/err"
check "残したものは警告される" $?

rm -rf "$dest" "$bin"
mkdir "$dest"
inst >/dev/null 2>&1
uninst list-tasks >"$tmp/out" 2>"$tmp/err"
[ ! -e "$dest/list-tasks" ] && [ -L "$dest/setup-tasks" ] && [ -L "$dest/task-workflow" ] && [ -L "$bin/tw" ]
check "名前を渡すとそのスキルだけ消える（tw は残る）" $?
ls "$CLAUDE_CONFIG_DIR/agents"/*.md >/dev/null 2>&1
check "名前を渡してもエージェント定義は残る" $?

uninst task-workflow >"$tmp/out" 2>"$tmp/err"
[ ! -e "$dest/task-workflow" ] && [ ! -e "$bin/tw" ]
check "task-workflow を渡すと tw も消える" $?
grep -q "warning: retrospect は task-workflow に依存" "$tmp/err"
check "依存元が残るときに警告が出る" $?

rm -rf "$dest" "$bin"
mkdir "$dest"
inst >/dev/null 2>&1
uninst retrospect task-workflow >"$tmp/out" 2>"$tmp/err"
! grep -q "warning: retrospect" "$tmp/err"
check "依存元も一緒に外せば、その依存元の警告は出ない" $?

[ "$fails" -eq 0 ] || { echo "$fails 件落ちた"; exit 1; }
