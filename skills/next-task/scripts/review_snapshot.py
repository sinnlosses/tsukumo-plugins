#!/usr/bin/env python3
"""いまの作業ツリー（未追跡のファイルを含み、.gitignore で無視されるものを除く）の木の SHA を1行で出す。

使い方: python3 review_snapshot.py
実の索引・作業ツリー・stash の山は変えない。一時の索引ファイルで木を作る。
2つの出力を `git diff <前の木> <いまの木>` に渡すと、未追跡のファイルの書き換えも差分に出る。
"""

from __future__ import annotations

import os
import sys
import tempfile

_TASK_WORKFLOW_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "task-model", "scripts")
)
if _TASK_WORKFLOW_SCRIPTS not in sys.path:
    sys.path.insert(0, _TASK_WORKFLOW_SCRIPTS)

import gitcmd  # noqa: E402


def git(env: dict[str, str], *args: str) -> str:
    out = gitcmd.run(list(args), env=env)
    if out.returncode != 0:
        sys.stderr.write(out.stderr)
        sys.exit(out.returncode)
    return out.stdout.strip()


def main() -> int:
    top = git(dict(os.environ), "rev-parse", "--show-toplevel")
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(d, "index")}
        os.chdir(top)
        git(env, "read-tree", "HEAD")
        git(env, "add", "-A")
        print(git(env, "write-tree"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
