#!/usr/bin/env python3
"""いまの作業ツリー（未追跡のファイルを含み、.gitignore で無視されるものを除く）の木の SHA を1行で出す。

使い方: python3 review_snapshot.py
実の索引・作業ツリー・stash の山は変えない。一時の索引ファイルで木を作る。
2つの出力を `git diff <前の木> <いまの木>` に渡すと、未追跡のファイルの書き換えも差分に出る。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def git(env: dict[str, str], *args: str) -> str:
    out = subprocess.run(["git", *args], env=env, capture_output=True, text=True)
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
