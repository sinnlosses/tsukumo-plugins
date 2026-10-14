"""git の薄い包みと主ブランチの決め方、作業ツリーの一覧。"""

from __future__ import annotations

import errno
import os
import tempfile
from dataclasses import dataclass

import gitcmd
import layout

READ_ONLY_ERRNOS = (errno.EACCES, errno.EPERM, errno.EROFS)


GitCommandError = gitcmd.GitCommandError


class NoBaseBranch(RuntimeError):
    """主ブランチが決まらなかった。データの不備（呼ぶ側が `INVALID`・終了コード3 にする）。"""


class GitReadOnly(RuntimeError):
    """`.git` に書けない（呼ぶ側が `GIT_READ_ONLY`・終了コード11 にする）。"""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def _git(args: list[str], cwd: str | None = None) -> str:
    return gitcmd.out(args, cwd)


def git_common_dir(cwd: str | None = None) -> str:
    return _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)


def git_dir(cwd: str | None = None) -> str:
    """この作業ツリー**だけ**の git dir（共有の `git_common_dir` とは違う。連結した
    作業ツリーでは `<共通の git dir>/worktrees/<名前>`）。作業ツリーの控えの最も古い置き場。"""
    return _git(["rev-parse", "--path-format=absolute", "--git-dir"], cwd)


def require_git_writable(cwd: str | None = None) -> None:
    """git に書く操作の前に、作業ツリー固有の git dir と共有の git dir に書いて消せるかを確かめる。
    書けなければ `GitReadOnly`（打つ前に止めるので、作業ツリーも台帳も変わらない）。"""
    for d in dict.fromkeys([git_dir(cwd), git_common_dir(cwd)]):
        try:
            fd, probe = tempfile.mkstemp(prefix=".tw-probe-", dir=d)
            os.close(fd)
            os.remove(probe)
        except OSError as e:
            if e.errno in READ_ONLY_ERRNOS:
                raise GitReadOnly(d) from e
            raise


def git_toplevel(cwd: str | None = None) -> str:
    return _git(["rev-parse", "--show-toplevel"], cwd)


def current_branch(cwd: str | None = None) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def is_clean(cwd: str | None = None) -> bool:
    return _git(["status", "--porcelain"], cwd) == ""


def head_sha_or_none(cwd: str | None = None) -> str | None:
    """`git rev-parse HEAD`。引けなければ `None`（`claim` が `head` を控えずに済ませる）。"""
    r = gitcmd.run(["rev-parse", "HEAD"], cwd)
    return r.stdout.strip() if r.returncode == 0 else None


# --- 主ブランチ（`main`・`master`・`trunk` …） -----------------------------

BASE_BRANCH_CANDIDATES = ("main", "master", "trunk")
_base_branch_cache: dict[str, tuple[str, int]] = {}


def _resolve_base_branch(cwd: str | None) -> tuple[str, int]:
    """`(主ブランチ名, 決まった順)` を1回だけ git に問い合わせて決める（`base_branch`・
    `base_branch_order` の共有部分。呼ぶ側を増やしても判定は1箇所のまま）。

    決め方の順:

    1. 設定の `base_branch`（任意）
    2. `git symbolic-ref --short refs/remotes/origin/HEAD` の枝名。**`origin` だけを見る**
       （別名のリモートしか無いリポジトリは順3へ落ちる。唯一のリモートを `origin` 扱いすると、
       fork 元を指す `upstream` を主ブランチの出どころにしてしまう。逃げ道は順1の行）
    3. `main`・`master`・`trunk` のうち `rev-parse --verify` で実在するもの（この順）
    4. どれも無ければ `NoBaseBranch`。**黙って `main` を作らない**

    1プロセスで1回だけ git に問い合わせて覚える（`task status` は1回の実行で何度も要る）。
    """
    # 呼ぶ側はふつう toplevel を渡すので、覚えていればそのまま返す（git を呼ばない）。
    if cwd is not None and cwd in _base_branch_cache:
        return _base_branch_cache[cwd]
    toplevel = git_toplevel(cwd)
    cached = _base_branch_cache.get(toplevel)
    if cached is not None:
        return cached

    found = layout.read_config(toplevel).base_branch
    order = 1
    if found is None:
        order = 2
        r = gitcmd.run(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], toplevel)
        head = r.stdout.strip()
        if r.returncode == 0 and head.startswith("origin/"):
            found = head[len("origin/") :]
    if found is None:
        order = 3
        for name in BASE_BRANCH_CANDIDATES:
            r = gitcmd.run(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], toplevel)
            if r.returncode == 0:
                found = name
                break
    if found is None:
        raise NoBaseBranch(
            "主ブランチが決まらない（設定の base_branch も、origin/HEAD も、"
            f"{'・'.join(BASE_BRANCH_CANDIDATES)} の枝も無い）"
        )

    _base_branch_cache[toplevel] = (found, order)
    return found, order


def base_branch(cwd: str | None = None) -> str:
    """このリポジトリの主ブランチ名（`main` に固定しない）。決め方の順は `_resolve_base_branch`。"""
    return _resolve_base_branch(cwd)[0]


def base_branch_order(cwd: str | None = None) -> int:
    """主ブランチが決まった順（1〜3。`_resolve_base_branch` の docstring）。`task config-doctor` 向け。"""
    return _resolve_base_branch(cwd)[1]


def clear_base_branch_cache() -> None:
    """覚えた主ブランチ名を捨てる（同じプロセスで別のリポジトリを作り替えるテスト用）。"""
    _base_branch_cache.clear()


@dataclass(frozen=True)
class Worktree:
    path: str
    branch: str | None  # detached HEAD なら None


def list_worktrees(cwd: str | None = None) -> list[Worktree]:
    """`git worktree list --porcelain` を読む（4.3 の取り残し判定に使う）。"""
    out = _git(["worktree", "list", "--porcelain"], cwd)
    worktrees: list[Worktree] = []
    path: str | None = None
    branch: str | None = None
    for line in out.splitlines() + [""]:
        if line == "":
            if path is not None:
                worktrees.append(Worktree(path, branch))
            path, branch = None, None
            continue
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
            branch = None
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            branch = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
    return worktrees
