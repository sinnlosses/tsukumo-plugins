"""`tw verify` が検証コマンドを打つ前に、未コミットの中身ごと主ブランチを取り込む git 手順。

衝突の有無は `git merge-tree --write-tree` で先に確かめ、衝突したときは本物の index・HEAD・
作業ツリーのどれも書き換えない。
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field

import gitcmd
import gitinfo
import stamps


@dataclass(frozen=True)
class FoldOutcome:
    kind: str  # "UP_TO_DATE" | "FOLDED" | "CONFLICT"
    old_base: str = ""
    new_base: str = ""
    merged_tree: str = ""
    conflict_files: tuple[str, ...] = field(default_factory=tuple)


def can_fold(toplevel: str, base: str) -> bool:
    """主ブランチが HEAD より先へ進んでいて、HEAD がその祖先である（`claim` のあとに自分のコミットが無い）。"""
    if _is_ancestor(toplevel, base, "HEAD"):
        return False
    return _is_ancestor(toplevel, "HEAD", base)


def fold_base(toplevel: str, base: str) -> FoldOutcome:
    if not can_fold(toplevel, base):
        return FoldOutcome(kind="UP_TO_DATE")
    old_base = _git(toplevel, ["rev-parse", "HEAD"]).stdout.strip()
    new_base = _git(toplevel, ["rev-parse", base]).stdout.strip()
    outcome = _merge_tree(toplevel, new_base)
    if outcome.kind == "CONFLICT":
        return outcome
    _checked(toplevel, ["read-tree", "--reset", "-u", outcome.merged_tree])
    _checked(toplevel, ["reset", "-q", "--mixed", new_base])
    return FoldOutcome(kind="FOLDED", old_base=old_base, new_base=new_base)


def fold_committed(toplevel: str, base: str) -> FoldOutcome:
    """自分のコミットがある枝へ、主ブランチをマージコミットで取り込む。

    作業ツリーが汚れていないことは呼ぶ側が確かめる。主ブランチが `HEAD` の祖先なら `UP_TO_DATE`。
    衝突は `git merge-tree` で先に確かめ、衝突したときは参照・index・作業ツリーのどれも書き換えない。
    """
    if _is_ancestor(toplevel, base, "HEAD"):
        return FoldOutcome(kind="UP_TO_DATE")
    old_head = _checked(toplevel, ["rev-parse", "HEAD"])
    new_base = _checked(toplevel, ["rev-parse", base])
    outcome = _merge_commits(toplevel, old_head, new_base)
    if outcome.kind == "CONFLICT":
        return outcome
    branch = _checked(toplevel, ["rev-parse", "--abbrev-ref", "HEAD"])
    commit = _checked(
        toplevel,
        ["commit-tree", outcome.merged_tree, "-p", old_head, "-p", new_base, "-m", f"Merge {base} into {branch}"],
    )
    _checked(toplevel, ["merge", "--ff-only", "--quiet", commit])
    return FoldOutcome(kind="FOLDED", old_base=old_head, new_base=commit)


def folds_cleanly(toplevel: str, base: str) -> bool:
    """`can_fold` が真で、未コミットの中身ごと主ブランチと合わせても衝突しない。何も書き換えない。"""
    if not can_fold(toplevel, base):
        return False
    with tempfile.TemporaryDirectory() as scratch:
        env = stamps.scratch_object_env(toplevel, scratch)
        return _merge_tree(toplevel, _checked(toplevel, ["rev-parse", base]), env).kind != "CONFLICT"


def _merge_tree(toplevel: str, new_base: str, object_env: dict[str, str] | None = None) -> FoldOutcome:
    """`object_env` があれば object を `.git` でなくそこへ書く。"""
    tree = stamps.worktree_tree(toplevel, object_env=object_env)
    work = _checked(toplevel, ["commit-tree", tree, "-p", "HEAD", "-m", "tw verify"], object_env)
    return _merge_commits(toplevel, work, new_base, object_env)


def _merge_commits(toplevel: str, ours: str, theirs: str, object_env: dict[str, str] | None = None) -> FoldOutcome:
    merged = _git(toplevel, ["merge-tree", "--write-tree", "--name-only", "--no-messages", ours, theirs], object_env)
    lines = [line for line in merged.stdout.splitlines() if line]
    if merged.returncode == 1:
        return FoldOutcome(kind="CONFLICT", conflict_files=tuple(dict.fromkeys(lines[1:])))
    if merged.returncode != 0 or not lines:
        raise gitinfo.GitCommandError(f"git merge-tree が失敗（{merged.returncode}）: {merged.stderr.strip()}")
    return FoldOutcome(kind="FOLDED", merged_tree=lines[0])


def _is_ancestor(toplevel: str, ancestor: str, descendant: str) -> bool:
    return _git(toplevel, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


def _git(toplevel: str, args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return gitcmd.run(args, toplevel, env)


def _checked(toplevel: str, args: list[str], env: dict[str, str] | None = None) -> str:
    return gitcmd.out(args, toplevel, env)
