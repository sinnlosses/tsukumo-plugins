# install.sh と uninstall.sh が読む関数群。読み込んだだけでは何も動かさない。
# 呼ぶ側は `here` を決めてから `. "$here/scripts/links.sh"` で読む。

# 張る先を決める。`--dest`・`--bin-dir` は parse_dest_args が上書きする
resolve_dests() {
  if [ -n "$CLAUDE_CONFIG_DIR" ]; then
    dest="$CLAUDE_CONFIG_DIR/skills"
    agents_dest="$CLAUDE_CONFIG_DIR/agents"
  else
    dest="$HOME/.claude/skills"
    agents_dest="$HOME/.claude/agents"
  fi
  bin_dest="$HOME/.local/bin"
}

# オプションを読み、消費した引数の数を parsed_args に置く（呼ぶ側が shift "$parsed_args" する）。
# 不正な指定は exit 2
parse_dest_args() {
  parsed_args=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --dest)
        shift
        if [ $# -eq 0 ]; then
          echo "--dest には値が要ります" >&2
          exit 2
        fi
        dest="$1"
        shift
        parsed_args=$((parsed_args + 2))
        ;;
      --dest=*)
        dest="${1#--dest=}"
        shift
        parsed_args=$((parsed_args + 1))
        ;;
      --bin-dir)
        shift
        if [ $# -eq 0 ]; then
          echo "--bin-dir には値が要ります" >&2
          exit 2
        fi
        bin_dest="$1"
        shift
        parsed_args=$((parsed_args + 2))
        ;;
      --bin-dir=*)
        bin_dest="${1#--bin-dir=}"
        shift
        parsed_args=$((parsed_args + 1))
        ;;
      --)
        parsed_args=$((parsed_args + 1))
        return 0
        ;;
      -*)
        echo "unknown option: $1" >&2
        exit 2
        ;;
      *)
        return 0
        ;;
    esac
  done
}

# 第1引数が、続く引数のどれかと等しければ 0
name_in() {
  _want="$1"
  shift
  for _n in "$@"; do
    [ "$_n" = "$_want" ] && return 0
  done
  return 1
}

# link_refuse LINK NAME PREFIX NOTE: LINK を触ってはいけないなら警告して 0（warned=1）、
# 触ってよい（無い・PREFIX で始まるところを指すリンク）なら 1。実ファイルの警告の補足が NOTE
link_refuse() {
  _link="$1"
  _name="$2"
  _prefix="$3"
  _note="$4"
  if [ -e "$_link" ] && [ ! -L "$_link" ]; then
    echo "skipped $_name （$_link が実ファイル/実ディレクトリ。${_note}）" >&2
    warned=1
    return 0
  fi
  if [ -L "$_link" ]; then
    _cur=$(readlink "$_link")
    case "$_cur" in
      "$_prefix"*) ;;
      *)
        echo "skipped $_name （$_link は別の場所 $_cur を指している）" >&2
        warned=1
        return 0
        ;;
    esac
  fi
  return 1
}

# DIR 直下の、PREFIX で始まるところを指していたのに解決できなくなったリンクを消す
prune_links() {
  for _link in "$1"/*; do
    [ -L "$_link" ] || continue
    [ -e "$_link" ] && continue
    case "$(readlink "$_link")" in
      "$2"*)
        rm "$_link"
        echo "pruned $(basename "$_link") （リンク先が無くなっていた）"
        ;;
    esac
  done
}
