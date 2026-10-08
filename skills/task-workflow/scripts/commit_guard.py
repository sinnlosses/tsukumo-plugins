"""`tw commit-guard`: Claude Code の PreToolUse hook の入力（stdin の JSON）を読み、コミットを作る git の
サブコマンドの実効の作業先に、`done` 前の着手の控え（`ledger.open_claims`）があれば拒む。

拒むときだけ stdout に deny の JSON を1行出す。通すときは何も出さない。終了コードはいつも0
（hook の取り決め。`tw` の「stdout は先頭語の TSV」の例外）。
"""

from __future__ import annotations

import json
import os
import shlex
from typing import IO

import agent_scope
import ledger

COMMIT_SUBCOMMANDS = ("commit", "merge", "pull", "cherry-pick", "revert", "am", "rebase")

_PUNCTUATION = ";&|()\n"
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
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    found: list[tuple[str, str]] = []
    where = start
    for simple in _split_commands(words):
        if not simple:
            continue
        if simple[0] == "cd":
            target = simple[1] if len(simple) > 1 else "-"
            where = start if target == "-" else os.path.normpath(os.path.join(where, os.path.expanduser(target)))
            continue
        hit = _git_subcommand(simple, where)
        if hit is not None and hit[1] in COMMIT_SUBCOMMANDS:
            found.append(hit)
    return found


def _split_commands(words: list[str]) -> list[list[str]]:
    commands: list[list[str]] = [[]]
    for w in words:
        if w and all(c in _PUNCTUATION for c in w):
            commands.append([])
        else:
            commands[-1].append(w)
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
