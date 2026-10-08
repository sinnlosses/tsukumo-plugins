#!/bin/sh
# skills/ 配下の各スキルと、agents/ 配下の各エージェント定義を、張る先にシンボリックリンクする。
#
# - スキルの張る先は `--dest DIR` → `CLAUDE_CONFIG_DIR`（設定されていればその下の `skills/`）→
#   `$HOME/.claude/skills` の順で決める。エージェント定義の張る先は `CLAUDE_CONFIG_DIR`
#   （設定されていればその下の `agents/`）→ `$HOME/.claude/agents` で、`--dest` とスキル名の
#   絞り込みの影響を受けない（`--dest` はスキル用の張る先を指す引数のため）。エージェント定義は
#   常に全件張り替える
# - `--dest` の後ろに残った引数はスキル名で、渡せば対象を絞る（無ければ従来どおり全件）
# - 既にこのリポジトリを指すリンクは張り替える
# - **実ディレクトリ・他所を指すリンクは触らず警告する**。`ln -sfn` は相手が実ディレクトリだと
#   エラーにならず *その中に* リンクを作り（`<name>/<name>`）、壊れたスキルが黙って生まれる
# - このリポジトリを指していたのに解決できなくなったリンク（スキルを消した・改名した跡、
#   エージェント定義を消した跡）は消す。**スキルは対象を絞ったときこの掃除を走らせない**
#   （対象外のスキルを消さないため）。エージェント定義の掃除は絞り込みの対象が無いので常に走る
# - **対象を絞ったときは依存も見る**（`skills/<name>/REQUIRES` に1行で書かれた兄弟スキル名）。
#   張る先に依存先が無ければ警告するだけで、自動では足さない（絞り込みの意図を守るため）
# - `task-workflow` が対象なら、`task.py` を指す1語のコマンド `tw` を `--bin-dir DIR`（無ければ
#   `$HOME/.local/bin`）に張る。`--dest`・`CLAUDE_CONFIG_DIR` の影響は受けない。実ファイル・他所を
#   指すリンクは触らず警告し、張る先が PATH に無い・別の `tw` が先に見つかるときも警告する
set -e
here=$(cd "$(dirname "$0")" && pwd)
. "$here/scripts/links.sh"

resolve_dests
parse_dest_args "$@"
shift "$parsed_args"

filtered=0
[ $# -gt 0 ] && filtered=1

mkdir -p "$dest"
warned=0
nested="中に入れ子のリンクを作らないため触らない"

for d in "$here"/skills/*/; do
  [ -d "$d" ] || continue
  n=$(basename "$d")
  if [ "$filtered" -eq 1 ]; then
    if ! name_in "$n" "$@"; then
      continue
    fi
  fi
  link="$dest/$n"
  if link_refuse "$link" "$n" "$here/skills/" "$nested"; then
    continue
  fi
  ln -sfn "${d%/}" "$link"
  echo "linked $n"
done

if [ "$filtered" -eq 0 ]; then
  prune_links "$dest" "$here/skills/"
else
  # 対象を絞ったときだけ、依存（`skills/<name>/REQUIRES` に1行で書かれた兄弟スキル名）が
  # 張る先に無ければ警告する。**自動では足さない**（黙って対象を増やすと `--dest` や
  # 絞り込みで決めた意図に反するため）。全件張るとき（絞り込みなし）は skills/*/ を丸ごと
  # 張るので依存は自然に満たされ、この検査は要らない。
  for n in "$@"; do
    req="$here/skills/$n/REQUIRES"
    [ -f "$req" ] || continue
    while IFS= read -r dep || [ -n "$dep" ]; do
      [ -n "$dep" ] || continue
      if [ ! -e "$dest/$dep" ]; then
        echo "warning: $n は $dep に依存するが $dest/$dep が無い（$dep も引数に渡すか、先に張ってください）" >&2
        warned=1
      fi
    done < "$req"
  done
fi

link_tw=1
if [ "$filtered" -eq 1 ]; then
  link_tw=0
  if name_in task-workflow "$@"; then
    link_tw=1
  fi
fi
if [ "$link_tw" -eq 1 ]; then
  tw_target="$here/skills/task-workflow/scripts/task.py"
  link="$bin_dest/tw"
  mkdir -p "$bin_dest"
  if link_refuse "$link" tw "$tw_target" "触らない"; then
    :
  else
    ln -sfn "$tw_target" "$link"
    echo "linked tw -> $link"
    case ":$PATH:" in
      *":$bin_dest:"*)
        found=$(command -v tw || true)
        if [ -n "$found" ] && [ "$found" != "$link" ]; then
          echo "warning: PATH で先に見つかる tw は $found （$link より前にある）" >&2
          warned=1
        fi
        ;;
      *)
        echo "warning: $bin_dest が PATH に無い。シェルの設定で PATH に足してください" >&2
        warned=1
        ;;
    esac
  fi
fi

# agents/ 配下の各エージェント定義（1ファイル1つ）を張る。スキル名の絞り込みは効かず常に全件。
mkdir -p "$agents_dest"

for f in "$here"/agents/*.md; do
  [ -f "$f" ] || continue
  n=$(basename "$f")
  link="$agents_dest/$n"
  if link_refuse "$link" "$n" "$here/agents/" "$nested"; then
    continue
  fi
  ln -sfn "$f" "$link"
  echo "linked $n"
done

prune_links "$agents_dest" "$here/agents/"

[ "$warned" -eq 0 ] || echo "※ skipped があります。上の理由を確認してから手で片付けてください。" >&2
