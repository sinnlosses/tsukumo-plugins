"""`tw commit-guard`: Claude Code の PreToolUse hook の入力（stdin の JSON）を読み、次の2つの実効の作業先に、
`done` 前の着手の控え（`ledger.open_claims`）があれば拒む。
コミットを作る git のサブコマンドと、シェルでのファイルの書き換え（`sed -i`・`perl -i`・`>`・`>>`・`tee`・
python の書き込み）と、設定の `verify`・`verify_before_ship` と同じコマンドの直打ち。判定しきれない書き方は通す。
書き換えと直打ちは、入力の `cwd` の控えのタスクの `### 作業先` の作業ツリー（`tw_handback.task_trees`）にも控えがあるとみなして拒む。
コミットはそこでは拒まない。

拒むときだけ stdout に deny の JSON を1行出す。通すときは何も出さない。終了コードはいつも0
（hook の取り決め。`tw` の「stdout は先頭語の TSV」の例外）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import IO, NamedTuple

import agent_scope
import layout
import ledger

COMMIT_SUBCOMMANDS = ("commit", "merge", "pull", "cherry-pick", "revert", "am", "rebase")

_OPERATOR_CHARS = ";&|()\n<>"
_SHELL_KEYWORDS = ("do", "then", "else", "elif", "if", "while", "until", "!", "{")
_HEREDOC = re.compile(r"<<(?!<)-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_WRITE_OPERATORS = (">", ">>", ">|", "&>", "&>>")
_IN_PLACE = re.compile(r"^-[nEszrupal]*i")
_PYTHON = re.compile(r"^python[\d.]*$")
_PY_WRITE = re.compile(
    r"open\([^)]*,\s*(?:mode\s*=\s*)?['\"][rbt]*[wax+][rbtwax+]*['\"]|\.write_text\(|\.write_bytes\("
)
_PY_OUTSIDE = re.compile(r"['\"](?:/tmp|/private|/var|/dev|~)|tempfile")
_GIT_OPTIONS_WITH_VALUE =("-c", "--git-dir", "--work-tree", "--namespace")


def run(stdin: IO[str], stdout: IO[str], agent_scoped: bool = False) -> None:
    try:
        payload = json.loads(stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict) or not agent_scope.applies(payload, agent_scoped):
        return
    reason = decide(payload)
    if reason is None:
        return
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


def decide(payload: dict) -> str | None:
    """拒む理由。通すなら `None`。"""
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    start = payload.get("cwd")
    if not isinstance(command, str) or not isinstance(start, str):
        return None
    for where, subcommand in _git_commits(command, start):
        claims = _open_claims_at(where)
        if claims:
            return (
                f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                f"git {subcommand} を拒む。コミットせず、変更は作業ツリーに残したまま報告で返す"
            )
    work_claims = _WorkTreeClaims(start)
    for path, how in _shell_writes(command, start):
        where, claims = _claims_for_write(path, work_claims)
        if claims:
            return (
                f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                f"{how} でファイルを書き換えない。Edit・Write で書く（作業ツリーの外のログへの出力は通る）"
            )
    for where, configured in _verify_commands(command, start):
        claims = _open_claims_at(where) or work_claims.at(where)
        if claims:
            return (
                f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                f"検証コマンド（{configured}）を直に打たない。`tw verify` で打つ（対象を絞った単体テストの実行は打てる）"
            )
    return None


def _verify_commands(command: str, start: str) -> list[tuple[str, str]]:
    """`(作業先, 設定の検証コマンド)` の並び。設定の `verify`・`verify_before_ship` と同じコマンドだけ。"""
    found: list[tuple[str, str]] = []
    configs: dict[str, tuple[str, list[str]]] = {}
    for simple in _simple_commands(command, start):
        where = simple.where
        if where is None or not os.path.isdir(where):
            continue
        args, _targets = _split_redirects(simple.words)
        while args and (args[0] in ("env", "command") or _is_assignment(args[0])):
            args = args[1:]
        if not args:
            continue
        if where not in configs:
            try:
                top = ledger.git_toplevel(where)
                config = layout.read_config(top)
            except (ledger.GitCommandError, layout.ConfigError):
                configs[where] = ("", [])
            else:
                configs[where] = (top, [c for c in (config.verify, config.verify_before_ship) if c is not None])
        top, commands = configs[where]
        for configured in commands:
            if _same_command(args, where, top, configured):
                found.append((where, configured))
    return found


def _same_command(words: list[str], where: str, toplevel: str, configured: str) -> bool:
    """`words` が設定のコマンドと同じか。設定のコマンドが単純コマンド1つのときだけ判定し、続く語は旗だけなら同じとする。"""
    tokens = _tokens(configured)
    if not tokens or any(t.op for t in tokens):
        return False
    cw = [t.text for t in tokens]
    return (
        len(words) >= len(cw)
        and words[1 : len(cw)] == cw[1:]
        and _same_program(words[0], where, cw[0], toplevel)
        and all(w.startswith("-") for w in words[len(cw) :])
    )


def _same_program(word: str, where: str, configured: str, toplevel: str) -> bool:
    if "/" not in word and "/" not in configured:
        return word == configured
    a = _join(where, word)
    b = _join(toplevel, configured)
    return a is not None and b is not None and os.path.realpath(a) == os.path.realpath(b)


def _shell_writes(command: str, start: str) -> list[tuple[str, str]]:
    """`(書き先のパス〔python は作業先のディレクトリ〕, 書き方の名前)` の並び。見えたものだけで、判定しきれないものは含めない。"""
    bodies = _split_heredocs(command)[1]
    found: list[tuple[str, str]] = []
    for simple in _simple_commands(command, start):
        args, targets = _split_redirects(simple.words)
        for target in targets:
            path = _resolve(simple.where, target)
            if path is not None:
                found.append((path, f"リダイレクト（> {target}）"))
        while args and (args[0] in ("env", "command") or _is_assignment(args[0])):
            args = args[1:]
        if not args:
            continue
        name = os.path.basename(args[0])
        rest = args[1:]
        if name in ("sed", "gsed", "perl"):
            if any(_IN_PLACE.match(a) or a.startswith("--in-place") for a in rest):
                found.extend((p, f"{name} -i") for p in _resolved(simple.where, _file_arguments(rest)))
        elif name == "tee":
            found.extend((p, "tee") for p in _resolved(simple.where, [a for a in rest if a and not a.startswith("-")]))
        elif simple.where is not None and _PYTHON.match(name) and _python_writes(rest, bodies):
            found.append((simple.where, f"{name} の書き込み"))
    return found


def _split_redirects(words: tuple[Token, ...]) -> tuple[list[str], list[str]]:
    """リダイレクトを除いた語と、`>` 系の書き先の語。"""
    args: list[str] = []
    targets: list[str] = []
    i = 0
    while i < len(words):
        t = words[i]
        following = words[i + 1] if i + 1 < len(words) else None
        if t.op and ("<" in t.text or ">" in t.text):
            has_target = following is not None and not following.op
            if t.text in _WRITE_OPERATORS and has_target and following is not None:
                targets.append(following.text)
            i += 2 if has_target else 1
        elif not t.op and t.text.isdigit() and following is not None and following.op and (
            "<" in following.text or ">" in following.text
        ):
            i += 1
        elif t.op:
            i += 1
        else:
            args.append(t.text)
            i += 1
    return args, targets


def _file_arguments(args: list[str]) -> list[str]:
    """`sed`・`perl` の引数のうち、書き換えられるファイル。"""
    files: list[str] = []
    script_given = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "-f"):
            script_given = True
            i += 2
            continue
        if a == "" or (a.startswith("-") and a != "-"):
            i += 1
            continue
        files.append(a)
        i += 1
    return files if script_given else files[1:]


def _resolved(where: str | None, words: list[str]) -> list[str]:
    return [p for p in (_resolve(where, w) for w in words) if p is not None]


def _resolve(where: str | None, word: str) -> str | None:
    """絶対パス。変数・コマンド置換・グロブを含むなど決められなければ `None`。"""
    if not word or any(c in word for c in "$`*?[") or word.startswith("/dev/") or word == "-":
        return None
    return _join(where, word)


def _join(where: str | None, word: str) -> str | None:
    """`where` からの相対を絶対パスにする。`where` が不明で相対なら `None`。"""
    expanded = os.path.expanduser(word)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return None if where is None else os.path.normpath(os.path.join(where, expanded))


def _python_writes(args: list[str], bodies: list[str]) -> bool:
    if "-c" in args and args.index("-c") + 1 < len(args):
        source = args[args.index("-c") + 1]
    elif all(a.startswith("-") for a in args) or args == ["-"]:
        source = "\n".join(bodies)
    else:
        return False
    return _PY_WRITE.search(source) is not None and _PY_OUTSIDE.search(source) is None


class _WorkTreeClaims:
    """`start` の控えのタスクの作業先の作業ツリーごとの控え。初めて要るときに1回だけ引く。"""

    def __init__(self, start: str) -> None:
        self._start = start
        self._by_tree: dict[str, list[str]] | None = None

    def at(self, where: str) -> list[str]:
        """`where` を含む作業ツリーが作業先の作業ツリーなら、その作業ツリーを作業先に持つタスクのID。"""
        if not os.path.isdir(where):
            return []
        try:
            tree = os.path.realpath(ledger.git_toplevel(where))
        except ledger.GitCommandError:
            return []
        if self._by_tree is None:
            self._by_tree = _work_tree_claims(self._start)
        return self._by_tree.get(tree, [])


def _work_tree_claims(start: str) -> dict[str, list[str]]:
    """`{作業先の作業ツリーの realpath: タスクID の並び}`。`start` に控えが無いか、引けなければ空。"""
    claims = _open_claims_at(start)
    if not claims:
        return {}
    import beads
    import tw_handback

    by_tree: dict[str, list[str]] = {}
    try:
        toplevel = ledger.git_toplevel(start)
        for task_id in claims:
            for tree in tw_handback.task_trees(toplevel, task_id)[1:]:
                by_tree.setdefault(os.path.realpath(tree), []).append(task_id)
    except (ledger.GitCommandError, beads.BeadsError, OSError):
        return {}
    return by_tree


def _claims_for_write(path: str, work_claims: _WorkTreeClaims) -> tuple[str, list[str]]:
    """書き先を含む作業ツリーの `(ディレクトリ, 着手の控え)`。無視されるパスと、控えの無い所は控え無し。"""
    directory = path if os.path.isdir(path) else os.path.dirname(path)
    while directory != os.path.dirname(directory) and not os.path.isdir(directory):
        directory = os.path.dirname(directory)
    claims = _open_claims_at(directory) or work_claims.at(directory)
    if claims and not os.path.isdir(path) and _is_ignored(path, directory):
        return directory, []
    return directory, claims


def _is_ignored(path: str, directory: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-q", "--", path], cwd=directory, capture_output=True, check=False
        )
    except OSError:
        return False
    return r.returncode == 0


def _git_commits(command: str, start: str) -> list[tuple[str, str]]:
    """`(作業先, サブコマンド)` の並び。コミットを作る git のサブコマンドだけ。割れなければ空。"""
    found: list[tuple[str, str]] = []
    for simple in _simple_commands(command, start):
        hit = _git_subcommand([t.text for t in simple.words], simple.where)
        if hit is not None and hit[1] in COMMIT_SUBCOMMANDS:
            found.append(hit)
    return found


class Token(NamedTuple):
    text: str
    op: bool


class Simple(NamedTuple):
    where: str | None
    words: tuple[Token, ...]


def _simple_commands(command: str, start: str) -> list[Simple]:
    """単純コマンドと、`cd` を追った実効の作業先の並び。割れなければ空。リダイレクトの演算子と先の語は残す。
作業先が変数やコマンド置換で決まる `cd` のあとのコマンドは作業先が `None` で、絶対パスで指し直すもの
（`git -C <絶対パス>`・絶対パスの書き先）だけが判定の対象になる。"""
    tokens = _tokens(_split_heredocs(command)[0])
    if tokens is None:
        return []
    found: list[Simple] = []
    where: str | None = start
    for words in _split_commands(tokens):
        while words and not words[0].op and words[0].text in _SHELL_KEYWORDS:
            words = words[1:]
        if not words:
            continue
        if words[0].text == "cd" and not words[0].op:
            target = words[1].text if len(words) > 1 else "-"
            if target == "-":
                where = start
            elif "$" in target or "`" in target:
                where = None
            elif where is not None or os.path.isabs(os.path.expanduser(target)):
                where = os.path.normpath(os.path.join(where or "", os.path.expanduser(target)))
            continue
        found.append(Simple(where, tuple(words)))
    return found


def _split_heredocs(command: str) -> tuple[str, list[str]]:
    """ヒアドキュメントの本文と終わりの語の行を除いたコマンドと、本文の並び。"""
    lines = command.split("\n")
    kept: list[str] = []
    bodies: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        i += 1
        for m in _HEREDOC.finditer(line):
            body: list[str] = []
            while i < len(lines) and lines[i].strip() != m.group(2):
                body.append(lines[i])
                i += 1
            i += 1
            bodies.append("\n".join(body))
    return "\n".join(kept), bodies


def _arithmetic_end(text: str, start: int) -> int | None:
    """`((` で始まる算術式の閉じ `))` の直後の位置。閉じなければ `None`。"""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1 if text[i - 1] == ")" else None
    return None


def _tokens(text: str) -> list[Token] | None:
    """語と演算子（引用符の外の `;&|()<>` と改行の連なり）に割る。引用符が閉じなければ `None`。"""
    tokens: list[Token] = []
    buf = ""
    in_word = False
    quote = ""
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = ""
            elif c == "\\" and quote == '"' and text[i + 1 : i + 2] in ('"', "\\", "$", "`"):
                buf += text[i + 1]
                i += 1
            else:
                buf += c
        elif c in "'\"":
            quote = c
            in_word = True
        elif c == "\\" and i + 1 < len(text):
            if text[i + 1] != "\n":
                buf += text[i + 1]
                in_word = True
            i += 1
        elif c == "#" and not in_word:
            end = text.find("\n", i)
            i = len(text) if end < 0 else end - 1
        elif text.startswith("((", i) and _arithmetic_end(text, i) is not None:
            if in_word:
                tokens.append(Token(buf, False))
                buf, in_word = "", False
            end = _arithmetic_end(text, i) or i
            tokens.append(Token(text[i:end], False))
            i = end - 1
        elif c in " \t\r" or c in _OPERATOR_CHARS:
            if in_word:
                tokens.append(Token(buf, False))
                buf, in_word = "", False
            if c in _OPERATOR_CHARS:
                j = i
                while j < len(text) and text[j] in _OPERATOR_CHARS:
                    j += 1
                tokens.append(Token(text[i:j], True))
                i = j - 1
        else:
            buf += c
            in_word = True
        i += 1
    if quote:
        return None
    if in_word:
        tokens.append(Token(buf, False))
    return tokens


def _split_commands(tokens: list[Token]) -> list[list[Token]]:
    commands: list[list[Token]] = [[]]
    for t in tokens:
        if t.op and "<" not in t.text and ">" not in t.text:
            commands.append([])
        else:
            commands[-1].append(t)
    return commands


def _git_subcommand(simple: list[str], where: str | None) -> tuple[str, str] | None:
    i = 0
    while i < len(simple) and (simple[i] in ("env", "command") or _is_assignment(simple[i])):
        i += 1
    if i >= len(simple) or not (simple[i] == "git" or simple[i].endswith("/git")):
        return None
    i += 1
    while i < len(simple) and simple[i].startswith("-"):
        option = simple[i]
        if option == "-C" and i + 1 < len(simple):
            where = _join(where, simple[i + 1])
            i += 2
        elif option in _GIT_OPTIONS_WITH_VALUE:
            i += 2
        else:
            i += 1
    if i >= len(simple) or where is None:
        return None
    return where, simple[i]


def _is_assignment(word: str) -> bool:
    name, sep, _value = word.partition("=")
    return sep == "=" and name.isidentifier()


def _open_claims_at(where: str) -> list[str]:
    if not os.path.isdir(where):
        return []
    try:
        return ledger.open_claims(cwd=where)
    except ledger.GitCommandError:
        return []
