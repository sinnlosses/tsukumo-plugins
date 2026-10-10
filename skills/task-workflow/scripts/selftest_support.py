"""`selftest_task.py` と `selftest_beads.py` が共有する自己テストの支え。"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

failures: list[str] = []
local = threading.local()

# prefix ごとに `bd init --stealth` した `.beads` と、そのとき書かれた `.git/info/exclude`。`beads_home` が作る。
_beads_templates: dict[str, tuple[str, str]] = {}

_BD_CONFIG = "metrics:\n    disabled: true\n    notice_shown: true\nno-git-ops: true\n"


@contextlib.contextmanager
def beads_home(prefixes: tuple[str, ...]) -> Iterator[str]:
    """`HOME`・`XDG_*` を一時ディレクトリへ向け、`prefixes` ごとに `.beads` の作り置きを作る。

    `bd init` は利用者の `~/.config/bd/config.yaml` を読み書きし、並行に打つと使用状況の送信の設定まで
    書き戻すことがあった。一時の家には送信を止めた設定を置く。抜けるときに環境変数を戻し、
    利用者の設定ファイルに触れていないことを確かめる（結果は `local.lines` に残す）。
    `bd` が無ければ非0で終わる（`tw` は `bd` 無しでは動かない）。
    """
    if shutil.which("bd") is None:
        print("bd が PATH に無い（tw は Beads（bd）で動くので、自己テストにも bd が要る）")
        raise SystemExit(1)
    real_config = os.path.join(os.path.expanduser("~"), ".config", "bd", "config.yaml")
    before = _stat(real_config)
    keys = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "BEADS_ACTOR", "GITHUB_TOKEN")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        with tempfile.TemporaryDirectory() as home:
            write(os.path.join(home, ".config", "bd", "config.yaml"), _BD_CONFIG)
            os.environ["HOME"] = home
            os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
            os.environ["XDG_DATA_HOME"] = os.path.join(home, ".local", "share")
            os.environ.pop("BEADS_ACTOR", None)
            os.environ.pop("GITHUB_TOKEN", None)
            with ThreadPoolExecutor(max_workers=max(1, len(prefixes))) as pool:
                list(pool.map(lambda prefix: _make_beads_template(home, prefix), prefixes))
            yield home
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    local.lines = []
    check("利用者の ~/.config/bd/config.yaml に触れていない", _stat(real_config) == before, real_config)


def copy_beads(repo: str, prefix: str) -> None:
    """`beads_home` が作った `prefix` の `.beads` を `repo` に写す（`bd init` より速い）。"""
    template, exclude = _beads_templates[prefix]
    shutil.copytree(template, os.path.join(repo, ".beads"))
    write(os.path.join(repo, ".git", "info", "exclude"), exclude)


def _make_beads_template(home: str, prefix: str) -> None:
    repo = os.path.join(home, f"beads-template-{prefix}")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "beads.role", "maintainer")
    r = subprocess.run(
        ["bd", "init", "--stealth", "-p", prefix, "--non-interactive", "--skip-hooks", "--quiet"],
        cwd=repo, capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"bd init -p {prefix} 失敗: {r.stdout}{r.stderr}")
    with open(os.path.join(repo, ".git", "info", "exclude"), encoding="utf-8") as f:
        _beads_templates[prefix] = (os.path.join(repo, ".beads"), f.read())


def _stat(path: str) -> tuple[float, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime, st.st_size


def say(line: str = "") -> None:
    local.lines.append(line)


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        say(f"  ok   {label}")
    else:
        say(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def write(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失敗: {r.stderr}")
    return r


def weight(n: int):
    """重いテストに付ける。大きいほど先に並列へ乗せる（後ろに残ると全体がその分延びる）。"""

    def mark(test):
        test.weight = n
        return test

    return mark


def run_one(test) -> list[str]:
    local.lines = []
    try:
        test()
    except Exception as e:  # noqa: BLE001  1件の故障で残りのテストを止めない
        check(f"{test.__name__} が落ちずに終わる", False, repr(e))
    return local.lines


def select(tests: tuple, only: list[str]) -> tuple:
    chosen = tuple(t for t in tests if not only or t.__name__ in only)
    chosen = tuple(sorted(chosen, key=lambda t: -getattr(t, "weight", 0)))
    if not chosen:
        print("該当するテストが無い: " + ", ".join(only))
        raise SystemExit(1)
    return chosen


def run_parallel(tests: tuple) -> list[list[str]]:
    with ThreadPoolExecutor(max_workers=min(len(tests), max(1, (os.cpu_count() or 2) // 2))) as pool:
        return list(pool.map(run_one, tests))


def finish(outputs: list[list[str]]) -> None:
    for lines in outputs:
        print("\n".join(lines))
    print()
    if failures:
        print(f"FAILED {len(failures)}件: " + ", ".join(failures))
        raise SystemExit(1)
    print("すべて通った")
