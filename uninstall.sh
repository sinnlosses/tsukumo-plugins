#!/bin/sh
# install.sh が張ったリンクを外す。使い方は `uninstall.sh [--dest DIR] [--bin-dir DIR] [スキル名...]`。
#
# - 張る先の決め方は install.sh と同じ
# - 外すのは**このリポジトリを指すリンクだけ**。実ファイル・実ディレクトリ・他所を指すリンクは
#   触らず警告する
# - スキル名を省けば、全スキルと `agents/` のリンクと `tw` を外し、このリポジトリを指していて
#   解決できなくなったリンクも消す。名前を渡せばそのスキルだけを外し（`task-workflow` を含む
#   ときは `tw` も）、エージェント定義には触らない
# - 名前を渡したとき、外したスキルに `REQUIRES` で依存するスキルが張る先に残っていれば警告する
#   （自動では外さない）
set -e
here=$(cd "$(dirname "$0")" && pwd)
. "$here/scripts/links.sh"

resolve_dests
parse_dest_args "$@"
shift "$parsed_args"

filtered=0
[ $# -gt 0 ] && filtered=1
warned=0

for d in "$here"/skills/*/; do
  [ -d "$d" ] || continue
  n=$(basename "$d")
  if [ "$filtered" -eq 1 ]; then
    if ! name_in "$n" "$@"; then
      continue
    fi
  fi
  link="$dest/$n"
  if [ ! -e "$link" ] && [ ! -L "$link" ]; then
    continue
  fi
  if link_refuse "$link" "$n" "$here/skills/" "触らない"; then
    continue
  fi
  rm "$link"
  echo "unlinked $n"
done

if [ "$filtered" -eq 0 ]; then
  prune_links "$dest" "$here/skills/"
else
  for d in "$here"/skills/*/; do
    [ -d "$d" ] || continue
    n=$(basename "$d")
    req="$here/skills/$n/REQUIRES"
    [ -f "$req" ] || continue
    [ -e "$dest/$n" ] || continue
    while IFS= read -r dep || [ -n "$dep" ]; do
      [ -n "$dep" ] || continue
      if name_in "$dep" "$@" && [ ! -e "$dest/$dep" ]; then
        echo "warning: $n は $dep に依存するが $dep を外した（$dest/$n は残っている）" >&2
        warned=1
      fi
    done < "$req"
  done
fi

unlink_tw=1
if [ "$filtered" -eq 1 ]; then
  unlink_tw=0
  if name_in task-workflow "$@"; then
    unlink_tw=1
  fi
fi
if [ "$unlink_tw" -eq 1 ]; then
  link="$bin_dest/tw"
  if [ -e "$link" ] || [ -L "$link" ]; then
    if link_refuse "$link" tw "$here/skills/task-workflow/scripts/task.py" "触らない"; then
      :
    else
      rm "$link"
      echo "unlinked tw"
    fi
  fi
fi

if [ "$filtered" -eq 0 ]; then
  for f in "$here"/agents/*.md; do
    [ -f "$f" ] || continue
    n=$(basename "$f")
    link="$agents_dest/$n"
    if [ ! -e "$link" ] && [ ! -L "$link" ]; then
      continue
    fi
    if link_refuse "$link" "$n" "$here/agents/" "触らない"; then
      continue
    fi
    rm "$link"
    echo "unlinked $n"
  done
  prune_links "$agents_dest" "$here/agents/"
fi

[ "$warned" -eq 0 ] || echo "※ skipped または warning があります。上の理由を確認してから手で片付けてください。" >&2
