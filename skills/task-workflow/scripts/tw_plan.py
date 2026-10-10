"""`## やること` の判定の部品（近道の印・登録時の計画の控えと着手時の比べ・作業より先に書いたか）。"""

from __future__ import annotations

import os
import sys

import ledger
import taskfile
import tw_base

PLAN_FIRST = "first"
PLAN_AFTER_WORK = "after-work"
PLAN_REGISTERED = "registered"


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
    base = ledger.base_branch(repo)
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


def registered_plan_changes(toplevel: str, base_sha: str | None, tip: str | None, body: str) -> list[str] | None:
    """登録時の計画が名指すファイルのうち、控えた `base_sha` から `tip` までに変わったもの。控えが無ければ `None`。"""
    if base_sha is None:
        return None
    repo = _plan_repo(toplevel, body)
    if repo is None:
        return ["(作業先が読めない)"]
    if tip is None or tw_base.run_git(repo, ["cat-file", "-e", f"{base_sha}^{{commit}}"]).returncode != 0:
        return ["(控えた SHA が無い)"]
    paths, _ = taskfile.plan_files(body)
    if not paths:
        return ["(名指すファイルが読めない)"]
    r = tw_base.run_git(repo, ["diff", "--name-only", base_sha, tip, "--", *paths])
    if r.returncode != 0:
        return ["(差分が引けない)"]
    return [line for line in r.stdout.splitlines() if line]


def plan_tip(toplevel: str, body: str) -> str | None:
    """着手時に比べる、計画のリポジトリの主ブランチの先端。"""
    repo = _plan_repo(toplevel, body)
    return _base_tip(repo) if repo is not None else None


def _base_tip(toplevel: str) -> str | None:
    r = tw_base.run_git(toplevel, ["rev-parse", ledger.base_branch(toplevel)])
    return r.stdout.strip() if r.returncode == 0 else None


def commits_since_claim(toplevel: str, head: str | None) -> list[str]:
    """`head`（claim 時の HEAD）から今の HEAD までにできた、主ブランチに無いコミット（古い順、短縮ハッシュ）。

    `head` が無い（metadata の `task_claim_head` が無い古い印）か `git log` が引けなければ空のまま返す（`task done` はそれを「委譲先の
    コミットは無い」と同じに扱い、落とさない）。
    """
    if not head:
        return []
    base = ledger.base_branch(toplevel)
    r = tw_base.run_git(toplevel, ["log", "--format=%h", "--reverse", "HEAD", f"^{head}", f"^{base}"])
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line]


def plan_state(toplevel: str, head: str | None, body_file: str) -> str:
    """いま `## やること` を書くと、作業より先（`first`）か作業が始まってから（`after-work`）か。

    作業が始まっているとは、claim した時点の `head` より後のコミットがあるか、`body_file` 以外に
    `git status` の変更があること。
    """
    if commits_since_claim(toplevel, head):
        return PLAN_AFTER_WORK
    ignored: set[str] = set()
    if body_file != "-":
        ignored.add(os.path.relpath(os.path.realpath(body_file), os.path.realpath(toplevel)))
    r = tw_base.run_git(toplevel, ["status", "--porcelain", "-z", "--untracked-files=all"])
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
