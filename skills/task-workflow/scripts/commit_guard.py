"""`tw commit-guard`: Claude Code の PreToolUse hook の入力（stdin の JSON）を読み、コミットを作る git の
サブコマンドの実効の作業先に、`done` 前の着手の控え（`ledger.open_claims`）があれば拒む。

拒むときだけ stdout に deny の JSON を1行出す。通すときは何も出さない。終了コードはいつも0
（hook の取り決め。`tw` の「stdout は先頭語の TSV」の例外）。
"""

from __future__ import annotations

import json
import os
import re
from typing import IO, NamedTuple

import agent_scope
import ledger

COMMIT_SUBCOMMANDS = ("commit", "merge", "pull", "cherry-pick", "revert", "am", "rebase")

_OPERATOR_CHARS = ";&|()\n<>"
_SHELL_KEYWORDS = ("do", "then", "else", "elif", "if", "while", "until", "!", "{")
_HEREDOC = re.compile(r"<<(?!<)-?\s*(['\"]?)([A-Za-z_]\w*)\1")
_GIT_OPTIONS_WITH_VALUE = ("-c", "--git-dir", "--work-tree", "--namespace")


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
    return None


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
    where: str
    words: tuple[Token, ...]


def _simple_commands(command: str, start: str) -> list[Simple]:
    """単純コマンドと、`cd` を追った実効の作業先の並び。割れなければ空。リダイレクトの演算子と先の語は残す。"""
    tokens = _tokens(_split_heredocs(command)[0])
    if tokens is None:
        return []
    found: list[Simple] = []
    where = start
    for words in _split_commands(tokens):
        while words and not words[0].op and words[0].text in _SHELL_KEYWORDS:
            words = words[1:]
        if not words:
            continue
        if words[0].text == "cd" and not words[0].op:
            target = words[1].text if len(words) > 1 else "-"
            where = start if target == "-" else os.path.normpath(os.path.join(where, os.path.expanduser(target)))
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


def _git_subcommand(simple: list[str], where: str) -> tuple[str, str] | None:
    i = 0
    while i < len(simple) and (simple[i] in ("env", "command") or _is_assignment(simple[i])):
        i += 1
    if i >= len(simple) or not (simple[i] == "git" or simple[i].endswith("/git")):
        return None
    i += 1
    while i < len(simple) and simple[i].startswith("-"):
        option = simple[i]
        if option == "-C" and i + 1 < len(simple):
            where = os.path.normpath(os.path.join(where, os.path.expanduser(simple[i + 1])))
            i += 2
        elif option in _GIT_OPTIONS_WITH_VALUE:
            i += 2
        else:
            i += 1
    if i >= len(simple):
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
