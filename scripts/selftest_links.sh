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
[ ! -e "$dest/list-tasks" ] && [ -L "$dest/setup-tasks" ] && [ -L "$dest/task-model" ] && [ -L "$bin/tw" ]
check "名前を渡すとそのスキルだけ消える（tw は残る）" $?
ls "$CLAUDE_CONFIG_DIR/agents"/*.md >/dev/null 2>&1
check "名前を渡してもエージェント定義は残る" $?

uninst task-model >"$tmp/out" 2>"$tmp/err"
[ ! -e "$dest/task-model" ] && [ ! -e "$bin/tw" ]
check "task-model を渡すと tw も消える" $?
grep -q "warning: retrospect は task-model に依存" "$tmp/err"
check "依存元が残るときに警告が出る" $?

rm -rf "$dest" "$bin"
mkdir "$dest"
inst >/dev/null 2>&1
uninst retrospect task-model >"$tmp/out" 2>"$tmp/err"
! grep -q "warning: retrospect は" "$tmp/err"
check "依存元も一緒に外せば、その依存元の警告は出ない" $?

rm -rf "$dest" "$bin"
mkdir "$dest" "$bin"
ln -s "$here/skills/task-workflow/scripts/task.py" "$bin/tw"
inst task-model >"$tmp/out" 2>"$tmp/err"
[ "$(readlink "$bin/tw")" = "$here/skills/task-model/scripts/tw/task.py" ] && ! grep -q "skipped tw" "$tmp/err"
check "改名前の置き場を指す tw を張り替える" $?

[ "$fails" -eq 0 ] || { echo "$fails 件落ちた"; exit 1; }
