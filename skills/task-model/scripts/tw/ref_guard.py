"""`tw ref-guard`: git の `reference-transaction` hook として、着手の控え（`ledger.open_claims`）がある作業ツリーで
`HEAD`・今の枝を主ブランチに無いコミットへ動かす参照の更新を拒む。

hook は共有の git dir の `hooks/` に1つ置き（`install`。`tw claim` が呼ぶ）、控えの無い作業ツリーでは何もしない。
`tw` 自身の git（`gitcmd.run`）は `gitcmd.REF_GUARD_ALLOW_ENV` を付けるので拒まない。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from typing import IO

import gitcmd
import gitinfo
import ledger

HOOK_NAME = "reference-transaction"
MARKER = "# tw ref-guard"
MIN_GIT = (2, 28)
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
        "command -v python3 >/dev/null 2>&1 || exit 0\n"
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


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _git_too_old(cwd: str) -> str | None:
    m = re.search(r"(\d+)\.(\d+)", gitcmd.run(["--version"], cwd).stdout)
    if m is None or (int(m.group(1)), int(m.group(2))) < MIN_GIT:
        return f"git が {'.'.join(map(str, MIN_GIT))} より古いか版が読めない（reference-transaction が無い）"
    return None


def install(toplevel: str) -> str | None:
    """hook を置く。置いても効かなければ（置かなかった場合を含む）その理由、効いていれば `None`。"""
    setting = _hooks_path_setting(toplevel)
    if setting is not None:
        return f"core.hooksPath（{setting}）が設定されているので置かない"
    path = _hook_path(toplevel)
    body = hook_body(_task_py())
    if os.path.lexists(path) and not _is_ours(path):
        return f"{path} に利用者の hook があるので置かない"
    if _read(path) != body or not os.access(path, os.X_OK):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(path, 0o755)
    return inactive_reason(toplevel)


def inactive_reason(where: str) -> str | None:
    """`where` で打つ git に hook が効かない理由。効くなら `None`。"""
    if os.environ.get(gitcmd.REF_GUARD_ALLOW_ENV):
        return f"環境に {gitcmd.REF_GUARD_ALLOW_ENV} がある"
    if shutil.which("python3") is None:
        return "python3 が PATH に無い"
    try:
        too_old = _git_too_old(where)
        if too_old is not None:
            return too_old
        setting = _hooks_path_setting(where)
        if setting is not None:
            return f"core.hooksPath（{setting}）が設定されている"
        path = _hook_path(where)
    except gitinfo.GitCommandError:
        return "git dir が読めない"
    if not os.path.lexists(path):
        return f"{path} が無い"
    if not _is_ours(path):
        return f"{path} が利用者の hook"
    if _read(path) != hook_body(_task_py()):
        return f"{path} がこの tw の task.py を指していない"
    if not os.access(path, os.X_OK):
        return f"{path} が実行できない"
    return None


def active(where: str) -> bool:
    return inactive_reason(where) is None


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
    ledger.record_denial(os.getcwd(), "tw-ref-guard", "MOVE_REF", "main")
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
