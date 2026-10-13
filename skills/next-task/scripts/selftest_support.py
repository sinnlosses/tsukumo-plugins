"""next-task の自己テストが共有する、一時リポジトリの下ごしらえ。"""

from __future__ import annotations

import os
import subprocess


def git(repo: str, *args: str) -> str:
    """`repo` で git を打ち、標準出力を返す。失敗したら例外。"""
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def write(repo: str, path: str, body: str) -> None:
    """`repo` の下の `path` に `body` を書く（途中のディレクトリも作る）。"""
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(body)
