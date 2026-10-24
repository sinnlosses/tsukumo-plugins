"""`tw commit-guard`: Claude Code の PreToolUse hook の入力（stdin の JSON）を読み、次の2つの実効の作業先に、
`done` 前の着手の控え（`ledger.open_claims`）があれば拒む。
コミットを作る git のサブコマンドと、シェルでのファイルの書き換え（`sed -i`・`perl -i`・`>`・`>>`・`tee`・
python の書き込み）と、設定の `verify`・`verify_before_ship` と同じコマンドの直打ち
（段を流さない読むだけの旗 `--plan`・`--help`・`-h` を含む打ち方は通す）と、`ref-guard` を外す書き方
（`TW_REF_GUARD_ALLOW=`・`export TW_REF_GUARD_ALLOW`・`core.hooksPath` を書き換える形。読むだけのコマンドは通す）と、委譲先の `git stash`・`git add -A`・`git push`・`git worktree remove`・`git branch -d`、
書き込みの `bd`（主のセッションも）、受け入れ役の `tw` と検証コマンド。判定しきれない書き方は通す。
コミットは、作業先で `ref-guard` が効いていれば（`ref_guard.inactive_reason` が `None`）拒まず git の hook に任せる。
書き換えと直打ちは、入力の `cwd` の控えのタスクの `### 作業先` の作業ツリー（`tw_handback.task_trees`）にも控えがあるとみなして拒む。
コミットはそこでは拒まない。

拒むときだけ stdout に deny の JSON を1行出す。通すときは何も出さない。終了コードはいつも0
（hook の取り決め。`tw` の「stdout は先頭語の TSV」の例外）。
"""

from __future__ import annotations

import json
import os
import re
from typing import IO, NamedTuple

import agent_scope
import gitcmd
import layout
import gitinfo
import ledger
import ref_guard

COMMIT_SUBCOMMANDS = ("commit", "merge", "pull", "cherry-pick", "revert", "am", "rebase")
READ_ONLY_FLAGS = ("--plan", "--help", "-h")
BD_WRITES = frozenset(
    "assign close comment create create-form delete edit gate heartbeat label link note priority promote q reclaim "
    "reopen set-state tag todo unclaim update duplicate supersede import init sync batch compact flatten gc prune "
    "purge rename-prefix restore recompute-blocked migrate remember forget".split()
)
BD_SUBWRITES = {
    "dep": frozenset({"add", "remove", "relate", "unrelate"}),
    "kv": frozenset({"set", "clear"}),
    "config": frozenset({"set", "unset"}),
    "dolt": frozenset({"push", "pull", "commit"}),
    "github": frozenset({"sync", "push", "pull"}),
    "jira": frozenset({"sync", "push", "pull"}),
    "linear": frozenset({"sync", "push", "pull"}),
    "ado": frozenset({"sync", "push", "pull"}),
}
_BD_VALUE_FLAGS = ("--actor", "--db", "--database", "-C", "--directory", "--dolt-auto-commit", "--mem-profile")

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
_DISARMS_REF_GUARD = re.compile(
    rf"\b{gitcmd.REF_GUARD_ALLOW_ENV}="
    rf"|\bexport\s+{gitcmd.REF_GUARD_ALLOW_ENV}\b"
    r"|(?:^|\s)-c\s*['\"]?core\.hookspath="
    r"|--config-env[=\s]['\"]?core\.hookspath="
    r"|\bGIT_CONFIG_KEY_\d+=['\"]?core\.hookspath"
    r"|\bGIT_CONFIG_PARAMETERS=\S*core\.hookspath",
    re.IGNORECASE,
)
_CONFIG_HOOKS_PATH = re.compile(
    r"\bgit\s+(?:[^;&|\n]*?\s)?config\b(?P<opts>[^;&|\n]*?)\bcore\.hookspath\b(?P<rest>[^;&|\n]*)", re.IGNORECASE
)
_CONFIG_READS = re.compile(r"(?:^|\s)(?:--get\S*|get|--list|-l|--unset\S*|unset)(?=\s|$)")


def _disarms_ref_guard(command: str) -> bool:
    """`ref-guard` を外す書き方か。`hooksPath` を読むだけのコマンド（`grep`・`git config --get`）は含めない。"""
    if _DISARMS_REF_GUARD.search(command):
        return True
    for m in _CONFIG_HOOKS_PATH.finditer(command):
        value = m.group("rest").strip().strip("'\"").strip()
        if value and not re.match(r"-|\d*[<>]", value) and not _CONFIG_READS.search(m.group("opts")):
            return True
    return False


def run(stdin: IO[str], stdout: IO[str], agent_scoped: bool = False, acceptor: bool = False) -> None:
    try:
        payload = json.loads(stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    if agent_scope.applies(payload, agent_scoped):
        _note_agent(payload)
        verdict = decide(payload, acceptor)
    else:
        verdict = decide_main(payload)
    if verdict is None:
        return
    rule, reason = verdict
    agent_scope.record_denial(payload, "tw-commit-guard", rule)
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


def _note_agent(payload: dict) -> None:
    """サブエージェントの呼び出しなら、入力の `cwd` の着手の控えのタスクとの対応を台帳に残す（失敗は握る）。"""
    agent_id = agent_scope.agent_id(payload)
    session_id = agent_scope.session_id(payload)
    cwd = payload.get("cwd")
    if agent_id is None or session_id is None or not isinstance(cwd, str) or not os.path.isdir(cwd):
        return
    try:
        ledger.record_agent_task(agent_id, session_id, cwd=cwd)
    except (gitinfo.GitCommandError, OSError):
        return


def decide_main(payload: dict) -> tuple[str, str] | None:
    """主のセッション（`agent_id` なし）の判定。書き込みの `bd` だけを見る。`bd` の語が無いコマンドは解析せず、落ちても通す。"""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    start = payload.get("cwd")
    if payload.get("tool_name") != "Bash" or not isinstance(command, str) or not isinstance(start, str):
        return None
    if "bd" not in command:
        return None
    try:
        return _bd_verdict(command, start)
    except Exception:  # noqa: BLE001 主の Bash 全部に掛かるので、判定の不具合で作業を止めない
        return None


def _bd_verdict(command: str, start: str) -> tuple[str, str] | None:
    for where, how in _bd_writes(command, start):
        claims = _open_claims_at(where)
        if claims:
            return "BD_WRITE", (
                f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                f"{how} を直に打たない。Beads への書き込みは `tw` が打つ（`bd show`・`bd list` など読む形は打てる）"
            )
    return None


def _bd_writes(command: str, start: str) -> list[tuple[str, str]]:
    """`(作業先, 打った形)` の並び。書き込みの `bd` だけで、`--readonly` 付きと読む形は含めない。"""
    found: list[tuple[str, str]] = []
    for simple in _simple_commands(command, start):
        if simple.where is None:
            continue
        args, _targets = _split_redirects(simple.words)
        args = _without_prefix(args)
        if not args or os.path.basename(args[0]) != "bd" or "--readonly" in args:
            continue
        rest = args[1:]
        words: list[str] = []
        i = 0
        while i < len(rest):
            if rest[i] in _BD_VALUE_FLAGS:
                i += 2
            elif rest[i].startswith("-"):
                i += 1
            else:
                words.append(rest[i])
                i += 1
        if not words:
            continue
        sub = words[0]
        second = words[1] if len(words) > 1 else ""
        if sub in BD_WRITES or second in BD_SUBWRITES.get(sub, ()):
            found.append((simple.where, f"bd {sub}" + (f" {second}" if sub in BD_SUBWRITES else "")))
    return found


def decide(payload: dict, acceptor: bool = False) -> tuple[str, str] | None:
    """`(規則のキー, 拒む理由)`。通すなら `None`。`acceptor` は受け入れ役（旗 `--acceptor` か `agent_type`）。"""
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    start = payload.get("cwd")
    if not isinstance(command, str) or not isinstance(start, str):
        return None
    acceptor = acceptor or _is_acceptor(payload)
    if acceptor:
        for where in _tw_calls(command, start):
            claims = _open_claims_at(where)
            if claims:
                return "ACCEPTOR_TW", (
                    f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では、"
                    "受け入れ役は tw を打たない。依頼文が渡した出力を読んで、判定だけを返す"
                )
    if _disarms_ref_guard(command):
        for where in dict.fromkeys([start, *(s.where for s in _simple_commands(command, start) if s.where)]):
            claims = _open_claims_at(where)
            if claims:
                return "DISARM_REF_GUARD", (
                    f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                    f"ref-guard を外す書き方（{gitcmd.REF_GUARD_ALLOW_ENV} を付ける・core.hooksPath を指し直す）をしない。"
                    "コミットせず、変更は作業ツリーに残したまま報告で返す"
                )
    for where, subcommand in _git_commits(command, start):
        claims = _open_claims_at(where)
        if claims and not ref_guard.active(where):
            return "GIT_COMMIT", (
                f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                f"git {subcommand} を拒む。コミットせず、変更は作業ツリーに残したまま報告で返す"
            )
    for where, sub, rest in _git_invocations(command, start):
        danger = _dangerous_git(sub, rest)
        claims = _open_claims_at(where) if danger else []
        if danger and claims:
            rule, how, next_step = danger
            return rule, (
                f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                f"{how} を打たない。{next_step}"
            )
    bd_verdict = _bd_verdict(command, start) if "bd" in command else None
    if bd_verdict is not None:
        return bd_verdict
    work_claims = _WorkTreeClaims(start)
    for path, how in _shell_writes(command, start):
        where, claims = _claims_for_write(path, work_claims)
        if claims and not _project_owns_shell_write(where):
            return "SHELL_WRITE", (
                f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                f"{how} でファイルを書き換えない。Edit・Write で書く（作業ツリーの外のログへの出力は通る）"
            )
    for where, configured in _verify_commands(command, start):
        claims = _open_claims_at(where) or work_claims.at(where)
        if claims:
            next_step = (
                "検証はメインが持つ。対象を絞った単体テストの実行は打てる"
                if acceptor
                else "`tw verify` で打つ（対象を絞った単体テストの実行は打てる）"
            )
            return "DIRECT_VERIFY", (
                f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{where}）では "
                f"検証コマンド（{configured}）を直に打たない。{next_step}"
            )
    return None


def _project_owns_shell_write(where: str) -> bool:
    """書き先の作業ツリー `where` の設定が `shell_write_guard = "project"` か。根が引けない・設定が読めないときは `False`（tw が見る側）。"""
    if not os.path.isdir(where):
        return False
    try:
        config = layout.read_config(gitinfo.git_toplevel(where))
    except (gitinfo.GitCommandError, layout.ConfigError, OSError):
        return False
    return config.shell_write_guard == layout.SHELL_WRITE_GUARD_PROJECT


def _is_acceptor(payload: dict) -> bool:
    """入力の `agent_type`（plugin では `tsukumo-workflow:acceptor` の形）の最後の `:` より後ろが `acceptor` か。"""
    agent_type = payload.get("agent_type")
    return isinstance(agent_type, str) and agent_type.rsplit(":", 1)[-1] == "acceptor"


def _tw_calls(command: str, start: str) -> list[str]:
    """`tw` を打つ単純コマンドの実効の作業先の並び。"""
    found: list[str] = []
    for simple in _simple_commands(command, start):
        if simple.where is None:
            continue
        args, _targets = _split_redirects(simple.words)
        args = _without_prefix(args)
        if args and os.path.basename(args[0]) == "tw":
            found.append(simple.where)
    return found


def _verify_commands(command: str, start: str) -> list[tuple[str, str]]:
    """`(作業先, 設定の検証コマンド)` の並び。設定の `verify`・`verify_before_ship` と同じコマンドだけ。"""
    found: list[tuple[str, str]] = []
    configs: dict[str, tuple[str, list[str]]] = {}
    for simple in _simple_commands(command, start):
        where = simple.where
        if where is None or not os.path.isdir(where):
            continue
        args, _targets = _split_redirects(simple.words)
        args = _without_prefix(args)
        if not args:
            continue
        if where not in configs:
            try:
                top = gitinfo.git_toplevel(where)
                config = layout.read_config(top)
            except (gitinfo.GitCommandError, layout.ConfigError):
                configs[where] = ("", [])
            else:
                configs[where] = (top, [c for c in (config.verify, config.verify_before_ship) if c is not None])
        top, commands = configs[where]
        for configured in commands:
            if _same_command(args, where, top, configured):
                found.append((where, configured))
    return found


def _same_command(words: list[str], where: str, toplevel: str, configured: str) -> bool:
    """`words` が設定のコマンドと同じか。設定のコマンドが単純コマンド1つのときだけ判定し、続く語は旗だけなら同じとする。
    続く語に読むだけの旗（`READ_ONLY_FLAGS`）が1つでもあれば、段を流さないので同じとしない。"""
    tokens = _tokens(configured)
    if not tokens or any(t.op for t in tokens):
        return False
    cw = [t.text for t in tokens]
    if any(w in READ_ONLY_FLAGS for w in words[len(cw) :]):
        return False
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
        args = _without_prefix(args)
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
            tree = os.path.realpath(gitinfo.git_toplevel(where))
        except gitinfo.GitCommandError:
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
        toplevel = gitinfo.git_toplevel(start)
        for task_id in claims:
            for tree in tw_handback.task_trees(toplevel, task_id)[1:]:
                by_tree.setdefault(os.path.realpath(tree), []).append(task_id)
    except (gitinfo.GitCommandError, beads.BeadsError, OSError):
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
        r = gitcmd.run(["check-ignore", "-q", "--", path], directory)
    except OSError:
        return False
    return r.returncode == 0


def _git_commits(command: str, start: str) -> list[tuple[str, str]]:
    """`(作業先, サブコマンド)` の並び。コミットを作る git のサブコマンドだけ。割れなければ空。"""
    return [(where, sub) for where, sub, _rest in _git_invocations(command, start) if sub in COMMIT_SUBCOMMANDS]


def _git_invocations(command: str, start: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """`(作業先, サブコマンド, 後ろの語)` の並び。割れなければ空。"""
    found = []
    for simple in _simple_commands(command, start):
        hit = _git_subcommand([t.text for t in simple.words if not t.op], simple.where)
        if hit is not None:
            found.append(hit)
    return found


def _is_flag_bundle(word: str, letters: str) -> bool:
    return word.startswith("-") and not word.startswith("--") and any(c in letters for c in word[1:])


def _dangerous_git(sub: str, rest: tuple[str, ...]) -> tuple[str, str, str] | None:
    """`(規則のキー, 打った形, 次の一手)`。読むだけの形と対象外のサブコマンドは `None`。"""
    first = rest[0] if rest else ""
    if sub == "stash":
        if first in ("list", "show", "-h", "--help"):
            return None
        return "GIT_STASH", "git stash", "stash の山は作業ツリーの間で共有される。変更の前後は `git show HEAD:<path>` で見る"
    if sub == "add":
        if "-n" in rest or "--dry-run" in rest:
            return None
        before = rest[: rest.index("--")] if "--" in rest else rest
        if any(w == "--all" or _is_flag_bundle(w, "A") for w in before) or any(w in (".", "./") for w in rest):
            return "GIT_ADD_ALL", "git add -A / git add .", "触ったファイルを個別に `git add <file>` する（コミットは委譲先はしない）"
        return None
    if sub == "push":
        if "-n" in rest or "--dry-run" in rest:
            return None
        return "GIT_PUSH", "git push", "push はメインが頼まれたときに打つ。変更は作業ツリーに残して返す"
    if sub == "worktree" and first in ("remove", "prune"):
        return "GIT_WORKTREE_REMOVE", f"git worktree {first}", "作業ツリーの片付けは `tw land` がする"
    if sub == "branch" and any(w == "--delete" or _is_flag_bundle(w, "dD") for w in rest):
        return "GIT_BRANCH_DELETE", "git branch -d", "枝の片付けは `tw land` がする"
    return None


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


def _git_subcommand(simple: list[str], where: str | None) -> tuple[str, str, tuple[str, ...]] | None:
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
    return where, simple[i], tuple(simple[i + 1 :])


def _without_prefix(args: list[str]) -> list[str]:
    """先頭の `env`・`command`・`VAR=値` の前置きを除いた語。"""
    while args and (args[0] in ("env", "command") or _is_assignment(args[0])):
        args = args[1:]
    return args


def _is_assignment(word: str) -> bool:
    name, sep, _value = word.partition("=")
    return sep == "=" and name.isidentifier()


def _open_claims_at(where: str) -> list[str]:
    if not os.path.isdir(where):
        return []
    try:
        return ledger.open_claims(cwd=where)
    except gitinfo.GitCommandError:
        return []
