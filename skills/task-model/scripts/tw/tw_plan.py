"""`## やること` の判定の部品（近道の印・登録時の計画の控えと着手時の比べ・作業より先に書いたか）。"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

import gitinfo
import taskfile
import tw_base

PLAN_FIRST = "first"
PLAN_AFTER_WORK = "after-work"
PLAN_REGISTERED = "registered"


def warn_report_names(body: str) -> None:
    """計画に basename が `report` で始まるパスがあれば、標準エラーに `WARN_REPORT_NAME` を1行出す（拒まない）。"""
    paths = taskfile.report_named_paths(body)
    if paths:
        print(
            f"WARN_REPORT_NAME\t{','.join(paths)}\t委譲先は report で始まる名のファイルを書けない。名前を変えるか、メインが置く段にする",
            file=sys.stderr,
        )


def refuse_direct(difficulty: str, body: str) -> None:
    reason = taskfile.direct_refusal(difficulty, body)
    if reason is not None:
        print(f"usage: {reason}（--direct を外す）", file=sys.stderr)
        raise SystemExit(2)


def direct_column(direct: str, difficulty: str, body: str, registered: bool) -> str:
    """`CLAIMED` の行末に足す近道の列。印が無ければ空。"""
    if direct != "Y":
        return ""
    reason = taskfile.direct_refusal(difficulty, body) or (None if registered else "登録時の計画が古い")
    return "\tdirect=Y" if reason is None else f"\tdirect=N:{reason}"


def registered_plan_base(toplevel: str, body: str) -> str:
    """登録時に控える SHA（計画のリポジトリの `HEAD` と主ブランチの分かれ目）。

    名指すファイルがその木に無い、または `### 作業先` が git のリポジトリの根でなければ終了コード2。
    """
    repo = _plan_repo(toplevel, body)
    if repo is None:
        print(
            f"usage: {taskfile.PLAN_WORK_REPO_HEADING} の {taskfile.plan_work_repo(body)[0]} が git のリポジトリの根でない",
            file=sys.stderr,
        )
        raise SystemExit(2)
    base = gitinfo.base_branch(repo)
    r = tw_base.run_git(repo, ["merge-base", "HEAD", base])
    if r.returncode != 0:
        print(f"INVALID\t{base} との分かれ目が引けない（{r.stderr.strip()}）")
        raise SystemExit(3)
    sha = r.stdout.strip()
    paths, _ = taskfile.plan_files(body)
    missing = [p for p in paths if tw_base.run_git(repo, ["cat-file", "-e", f"{sha}:{p.rstrip('/')}"]).returncode != 0]
    if missing:
        where = base if repo == toplevel else f"{repo} の {base}"
        print(
            f"usage: {taskfile.PLAN_FILES_HEADING} の {', '.join(missing)} が {where} に無い"
            "（新しいファイルは置くディレクトリを名指す）",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return sha


def _plan_repo(toplevel: str, body: str) -> str | None:
    """計画の名指すファイルを読むリポジトリ。`### 作業先` が無ければ `toplevel`、あってもリポジトリの根でなければ `None`。"""
    path, _ = taskfile.plan_work_repo(body)
    if path is None:
        return toplevel
    if not os.path.isdir(path):
        return None
    r = tw_base.run_git(path, ["rev-parse", "--show-toplevel"])
    if r.returncode != 0 or os.path.realpath(r.stdout.strip()) != os.path.realpath(path):
        return None
    return path


def registered_plan_drift(toplevel: str, base_sha: str | None, tip: str | None, body: str) -> tuple[list[str], str] | None:
    """登録時の計画が古いか。控えが無ければ `None`、あれば `(変わったファイル, 理由)`。ファイルが空なら古くない（理由も空）。

    名指すファイルが控えた `base_sha` から `tip` までに変わっていても、覆いが保たれ（`PLAN_UNCOVERED` が出ない）、
    名指すパスが `tip` の木に残り、差分の追加・削除行に段の本文の識別子（`taskfile.plan_identifiers`）が出なければ古くない。
    理由は `touched:<語,…>`・`legacy`（満たす完了条件の欄が無い旧い計画）・`uncovered`・`path-missing`・`no-identifier`・`unreadable`。
    """
    if base_sha is None:
        return None
    repo = _plan_repo(toplevel, body)
    if repo is None:
        return ["(作業先が読めない)"], "unreadable"
    if tip is None or tw_base.run_git(repo, ["cat-file", "-e", f"{base_sha}^{{commit}}"]).returncode != 0:
        return ["(控えた SHA が無い)"], "unreadable"
    paths, _ = taskfile.plan_files(body)
    if not paths:
        return ["(名指すファイルが読めない)"], "unreadable"
    r = tw_base.run_git(repo, ["diff", "--name-only", base_sha, tip, "--", *paths])
    if r.returncode != 0:
        return ["(差分が引けない)"], "unreadable"
    changed = [line for line in r.stdout.splitlines() if line]
    if not changed:
        return [], ""
    specs, error = taskfile.plan_step_specs(body)
    if error is not None or all(s.covers is None for s in specs):
        return changed, "legacy"
    if any(taskfile.plan_coverage(body)):
        return changed, "uncovered"
    if any(tw_base.run_git(repo, ["cat-file", "-e", f"{tip}:{p.rstrip('/')}"]).returncode != 0 for p in paths):
        return changed, "path-missing"
    words = taskfile.plan_identifiers(body)
    if not words:
        return changed, "no-identifier"
    diff = tw_base.run_git(repo, ["diff", "-U0", base_sha, tip, "--", *paths])
    if diff.returncode != 0:
        return changed, "unreadable"
    shown = diff.stdout.splitlines()
    lines = [x[1:] for x in shown if x[:1] in ("+", "-") and not x.startswith(("+++", "---"))]
    if not lines or any(x.startswith("Binary files ") for x in shown):
        return changed, "unreadable"
    touched = [w for w in words if any(w in line for line in lines)]
    return (changed, f"touched:{','.join(touched)}") if touched else ([], "")


def plan_tip(toplevel: str, body: str) -> str | None:
    """着手時に比べる、計画のリポジトリの主ブランチの先端。"""
    repo = _plan_repo(toplevel, body)
    return _base_tip(repo) if repo is not None else None


def main_touched(tree: str, body: str) -> list[str]:
    """着手のあとに主ブランチが変えたファイルのうち、計画の名指すファイルか `tree` が変えたファイルに当たるもの（辞書順）。

    主ブランチ側は `merge-base HEAD <主ブランチ>` から主ブランチの先端まで、`tree` 側は同じ分かれ目からの
    作業ツリーの差分（コミット済み・未コミット・未追跡）。名前の変更は元と先を別のファイルとして数える。`tree` が計画の作業先のリポジトリの木でないか、
    git が引けなければ空（知らせなので落とさない）。
    """
    repo = _plan_repo(tree, body)
    try:
        if repo is None or gitinfo.git_common_dir(repo) != gitinfo.git_common_dir(tree):
            return []
    except gitinfo.GitCommandError:
        return []
    base = gitinfo.base_branch(tree)
    mb = tw_base.run_git(tree, ["merge-base", "HEAD", base])
    if mb.returncode != 0:
        return []
    fork = mb.stdout.strip()
    theirs = tw_base.run_git(tree, ["diff", "--name-only", "--no-renames", "-z", fork, base])
    mine = tw_base.run_git(tree, ["diff", "--name-only", "--no-renames", "-z", fork])
    fresh = tw_base.run_git(tree, ["ls-files", "--others", "--exclude-standard", "-z"])
    if theirs.returncode != 0 or mine.returncode != 0 or fresh.returncode != 0:
        return []
    named = [p.rstrip("/") for p in taskfile.plan_files(body)[0]]
    ours = set(mine.stdout.split("\0")) | set(fresh.stdout.split("\0"))
    return sorted(
        f for f in set(theirs.stdout.split("\0")) if f and (f in ours or any(f == p or f.startswith(p + "/") for p in named))
    )


def _base_tip(toplevel: str) -> str | None:
    r = tw_base.run_git(toplevel, ["rev-parse", gitinfo.base_branch(toplevel)])
    return r.stdout.strip() if r.returncode == 0 else None


def commits_since_claim(toplevel: str, head: str | None) -> list[str]:
    """`head`（claim 時の HEAD）から今の HEAD までにできた、主ブランチに無いコミット（古い順、短縮ハッシュ）。

    `head` が無い（metadata の `task_claim_head` が無い古い印）か `git log` が引けなければ空のまま返す（`task done` はそれを「委譲先の
    コミットは無い」と同じに扱い、落とさない）。
    """
    if not head:
        return []
    base = gitinfo.base_branch(toplevel)
    r = tw_base.run_git(toplevel, ["log", "--format=%h", "--reverse", "HEAD", f"^{head}", f"^{base}"])
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line]


def plan_state(toplevel: str, head: str | None, body_files: Sequence[str]) -> str:
    """いま `## やること` を書くと、作業より先（`first`）か作業が始まってから（`after-work`）か。

    作業が始まっているとは、claim した時点の `head` より後のコミットがあるか、`body_files`（標準入力の `-` は数えない）
    以外に `git status` の変更があること（submodule のずれは数えない）。
    """
    if commits_since_claim(toplevel, head):
        return PLAN_AFTER_WORK
    ignored: set[str] = set()
    for path in body_files:
        if path != "-":
            ignored.add(os.path.relpath(os.path.realpath(path), os.path.realpath(toplevel)))
    r = tw_base.run_git(toplevel, ["status", "--porcelain", "-z", "--untracked-files=all", "--ignore-submodules=all"])
    changed = _porcelain_paths(r.stdout) if r.returncode == 0 else []
    return PLAN_AFTER_WORK if any(p not in ignored for p in changed) else PLAN_FIRST


def _porcelain_paths(out: str) -> list[str]:
    """`git status --porcelain -z` の出力から変更のあるパスを取り出す（名前の変更は元の名前を読み飛ばす）。"""
    paths: list[str] = []
    entries = iter(out.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":
            next(entries, None)
    return paths
