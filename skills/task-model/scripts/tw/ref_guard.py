"""`tw ref-guard`: git の `reference-transaction` hook として、着手の控え（`ledger.open_claims`）がある作業ツリーで
`HEAD`・今の枝を主ブランチに無いコミットへ動かす参照の更新を拒む。

hook は共有の git dir の `hooks/` に1つ置き（`install`。`tw claim` が呼ぶ）、控えの無い作業ツリーでは何もしない。
`tw` 自身の git（`gitcmd.run`）は `gitcmd.REF_GUARD_ALLOW_ENV` を付けるので拒まない。
"""

from __future__ import annotations

import os
import re
import shlex
from typing import IO

import gitcmd
import gitinfo
import ledger

HOOK_NAME = "reference-transaction"
MARKER = "# tw ref-guard"
_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _task_py() -> str:
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "task.py")


def hook_body(task_py: str) -> str:
    quoted = shlex.quote(task_py)
    return (
        "#!/bin/sh\n"
        f"{MARKER}（tw claim が置く。消してよい）\n"
        '[ "$1" = prepared ] || exit 0\n'
        f'[ -n "${gitcmd.REF_GUARD_ALLOW_ENV}" ] && exit 0\n'
        f"[ -f {quoted} ] || exit 0\n"
        f'exec python3 {quoted} ref-guard "$@"\n'
    )


def _hooks_path_setting(cwd: str) -> str | None:
    r = gitcmd.run(["config", "--get", "core.hooksPath"], cwd)
    value = r.stdout.strip()
    return value if r.returncode == 0 and value else None


def _hook_path(cwd: str) -> str:
    return os.path.join(gitinfo.git_common_dir(cwd), "hooks", HOOK_NAME)


def _is_ours(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return MARKER in f.read()
    except OSError:
        return False


def install(toplevel: str) -> str | None:
    """hook を置く。置けたか置いてあれば `None`、置かなかったらその理由。"""
    setting = _hooks_path_setting(toplevel)
    if setting is not None:
        return f"core.hooksPath（{setting}）が設定されているので置かない"
    path = _hook_path(toplevel)
    body = hook_body(_task_py())
    if os.path.lexists(path):
        if not _is_ours(path):
            return f"{path} に利用者の hook があるので置かない"
        with open(path, encoding="utf-8", errors="replace") as f:
            if f.read() == body and os.access(path, os.X_OK):
                return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, 0o755)
    return None


def active(where: str) -> bool:
    """`where` を含むリポジトリで hook が効いている（`core.hooksPath` が無く、印付きの hook が実行できる）。"""
    try:
        if _hooks_path_setting(where) is not None:
            return False
        path = _hook_path(where)
    except gitinfo.GitCommandError:
        return False
    return os.access(path, os.X_OK) and _is_ours(path)


def run(state: str, stdin: IO[str], stderr: IO[str]) -> int:
    """hook の終了コード。拒むときだけ1で、理由を `stderr` に1行出す。判定できないときは通す。"""
    if state != "prepared" or os.environ.get(gitcmd.REF_GUARD_ALLOW_ENV):
        return 0
    try:
        reason = _refusal(stdin.read())
    except (RuntimeError, OSError, ValueError):
        return 0
    if reason is None:
        return 0
    stderr.write(reason + "\n")
    return 1


def _refusal(updates: str) -> str | None:
    toplevel = gitinfo.git_toplevel(os.getcwd())
    claims = ledger.open_claims(cwd=toplevel)
    if not claims:
        return None
    branch = gitcmd.run(["symbolic-ref", "-q", "HEAD"], toplevel).stdout.strip()
    guarded = {"HEAD", branch} - {""}
    base: str | None = None
    for line in updates.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        old, new, ref = parts
        if ref not in guarded or new == old or not _OBJECT_ID.fullmatch(new) or set(new) == {"0"}:
            continue
        if base is None:
            base = gitinfo.base_branch(toplevel)
        if gitcmd.run(["merge-base", "--is-ancestor", new, base], toplevel).returncode == 0:
            continue
        return (
            f"着手の印（{','.join(claims)}）が立っていて tw done の前の作業ツリー（{toplevel}）では、"
            f"主ブランチに無いコミットへ {ref} を動かさない。コミットせず、変更は作業ツリーに残したまま報告で返す"
        )
    return None
