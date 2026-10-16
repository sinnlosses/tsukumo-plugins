"""git を呼ぶ口。`run`（結果をそのまま）・`out`（失敗で例外、stdout を返す）・`lines`（stdout の行）。標準ライブラリだけ。"""

from __future__ import annotations

import os
import subprocess


REF_GUARD_ALLOW_ENV = "TW_REF_GUARD_ALLOW"


class GitCommandError(RuntimeError):
    """git を呼んで非0で終わった。環境の故障として扱う（データの不備ではない）。"""


def run(
    args: list[str], cwd: str | None = None, env: dict[str, str] | None = None, errors: str | None = None
) -> subprocess.CompletedProcess:
    """`git <args>` を打つ。`env` は `os.environ` に重ねる。失敗しても例外にしない。
    `tw` の打つ git は着手中の作業ツリーでも `ref-guard` に拒まれないよう、いつも `REF_GUARD_ALLOW_ENV` を付ける。"""
    merged = {**os.environ, **(env or {}), REF_GUARD_ALLOW_ENV: "1"}
    return subprocess.run(["git", *args], cwd=cwd, env=merged, capture_output=True, text=True, errors=errors)


def _checked(args: list[str], cwd: str | None, env: dict[str, str] | None) -> subprocess.CompletedProcess:
    r = run(args, cwd, env)
    if r.returncode != 0:
        raise GitCommandError(f"git {' '.join(args)} が失敗（{r.returncode}）: {r.stderr.strip()}")
    return r


def out(args: list[str], cwd: str | None = None, env: dict[str, str] | None = None) -> str:
    """stdout の前後の空白を落として返す。非0なら `GitCommandError`。"""
    return _checked(args, cwd, env).stdout.strip()


def lines(args: list[str], cwd: str | None = None, env: dict[str, str] | None = None) -> list[str]:
    """stdout の行リスト。非0なら `GitCommandError`。"""
    return _checked(args, cwd, env).stdout.splitlines()
