"""作業先の登録簿（ホームの `.tw/config.toml` の `[repos.<名前>]`）と、`### 作業先` から作業先の根を解く口。

正典は WORKFLOW.md「作業先の登録簿」。名前の絶対パスは利用者ごとの `repos.toml`、無ければホームの本体の作業ツリーの親の `<名前>`。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

import gitinfo
import layout
import stamps
import taskfile

WORK_HOME_FILE = "work-home"


class RepoPathMissing(RuntimeError):
    """`### 作業先` の名前のパスが引けない（呼ぶ側が `MISSING\\t作業先 <名前>`・終了コード6にする）。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"作業先 {name}")
        self.name = name


@dataclass(frozen=True)
class WorkRepo:
    """解けた作業先。`name` は `### 作業先` に書いた登録簿の名前（絶対パスで書いたなら `None`）。"""

    name: str | None
    root: str
    entry: layout.RepoEntry | None


def repos_file() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "tw", "repos.toml")


def read_repos_file() -> dict[str, str]:
    """`repos_file()` の最上位の `<名前> = "<絶対パス>"`。ファイルが無ければ空。読めなければ `layout.ConfigError`。"""
    path = repos_file()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    paths: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{path}:{lineno}"
        m = layout._LINE_PATTERN.fullmatch(line)
        if m is None or layout.REPO_NAME_PATTERN.fullmatch(m.group(1)) is None:
            raise layout.ConfigError(f"{where}: `<名前> = \"<絶対パス>\"` の形でない（名前は {layout.REPO_NAME_FRAGMENT}）: {line}")
        name = m.group(1)
        if name in paths:
            raise layout.ConfigError(f"{where}: 名前 {name!r} が2度ある")
        value = layout._parse_string(m.group(2), where)
        if not value.startswith("/"):
            raise layout.ConfigError(f"{where}: {name} のパスは絶対パスにする: {value}")
        paths[name] = value
    return paths


def _sibling_of_home(toplevel: str, name: str) -> str | None:
    """ホームの本体の作業ツリーの親の `<名前>`。git のリポジトリの根でなければ `None`。"""
    try:
        trees = gitinfo.list_worktrees(cwd=toplevel)
    except gitinfo.GitCommandError:
        return None
    if not trees:
        return None
    path = os.path.join(os.path.dirname(os.path.normpath(trees[0].path)), name)
    if not os.path.isdir(path):
        return None
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True, text=True, check=False)
    if r.returncode != 0 or os.path.realpath(r.stdout.strip()) != os.path.realpath(path):
        return None
    return path


def _path_of(toplevel: str, name: str) -> str | None:
    return read_repos_file().get(name) or _sibling_of_home(toplevel, name)


def resolve(toplevel: str, spec: str | None) -> WorkRepo | None:
    """`### 作業先` の名前か絶対パスを解く。`spec` が `None` なら `None`。

    名前は登録簿 → `repos.toml` → ホームの本体の作業ツリーの親の `<名前>` の順に引き、登録簿に無いかパスが引けなければ
    `RepoPathMissing`。絶対パスはそのまま根にし、名前は引かない（`name`・`entry` は `None`。名前が要る呼び出し側が `name_of` で引く）。
    """
    if spec is None:
        return None
    if spec.startswith("/"):
        return WorkRepo(None, spec, None)
    entry = layout.read_config(toplevel).repo(spec)
    path = _path_of(toplevel, spec) if entry is not None else None
    if path is None:
        raise RepoPathMissing(spec)
    return WorkRepo(spec, path, entry)


def work_repo_root(toplevel: str, body: str) -> str | None:
    """本文の `### 作業先` の根。小見出しが無いか形が違えば `None`、名前のパスが引けなければ `RepoPathMissing`。"""
    spec, _ = taskfile.plan_work_repo(body)
    work = resolve(toplevel, spec)
    return work.root if work is not None else None


def work_repo_mark(toplevel: str, body: str) -> str | None:
    """metadata `work_repo` に書く名前。`### 作業先` が無ければ `None`、絶対パスは登録簿の名前に解け、解けなければ根のディレクトリ名。"""
    spec, _ = taskfile.plan_work_repo(body)
    if spec is None:
        return None
    if not spec.startswith("/"):
        return spec
    return name_of(toplevel, spec) or os.path.basename(spec.rstrip("/"))


def name_of(toplevel: str, path: str) -> str | None:
    """`path` が登録簿の名前のどれかのパスに解ければその名前。"""
    real = os.path.realpath(path)
    for entry in layout.read_config(toplevel).repos:
        found = _path_of(toplevel, entry.name)
        if found is not None and os.path.realpath(found) == real:
            return entry.name
    return None


def registry_refusal(toplevel: str, body: str) -> str | None:
    """`### 作業先` が登録簿に無い名前なら理由。絶対パス・小見出しが無い・登録済みの名前なら `None`。"""
    spec, _ = taskfile.plan_work_repo(body)
    if spec is None or spec.startswith("/") or layout.read_config(toplevel).repo(spec) is not None:
        return None
    return f"{taskfile.PLAN_WORK_REPO_HEADING} の名前 {spec} が登録簿（{layout.CONFIG_PATH} の [{layout.REPOS_TABLE}.{spec}]）に無い"


def work_home(tree: str) -> tuple[str, str] | None:
    """作業先の作業ツリー `tree` の `.tw/local/work-home` の（ホームの本体の根, 登録簿の名前）。名前が無ければ空。

    ファイルが無いか1行目が空なら `None`。
    """
    try:
        with open(os.path.join(tree, layout.LOCAL_DIR, WORK_HOME_FILE), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    if not lines or not lines[0]:
        return None
    return lines[0], lines[1] if len(lines) > 1 else ""


def write_work_home(tree: str, home_root: str, name: str | None) -> None:
    """`tree` の `.tw/local/work-home` に、ホームの本体の根と登録簿の名前（無ければ空行）を1行ずつ書く。"""
    path = os.path.join(stamps.worktree_state_dir(cwd=tree), WORK_HOME_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{home_root}\n{name or ''}\n")


_WORK_LANDED_PATTERN = re.compile(rf"({layout.REPO_NAME_FRAGMENT}):([^@\s]+)@([0-9a-f]{{40}})")


def format_work_landed(name: str, branch: str, sha: str) -> str:
    """metadata `work_landed` の値 `<名前>:<枝>@<SHA>`。"""
    return f"{name}:{branch}@{sha}"


def parse_work_landed(text: str) -> tuple[str, str, str] | None:
    """`format_work_landed` の値を `(名前, 枝, SHA)` に戻す。形が違えば `None`。"""
    m = _WORK_LANDED_PATTERN.fullmatch(text.strip())
    return (m.group(1), m.group(2), m.group(3)) if m else None


def registry_verify(tree: str) -> str | None:
    """`tree` の `work-home` が指すホームの登録簿の `[repos.<名前>]` の `verify`。

    `work-home` が無い・名前が空・ホームの設定が読めない・表か `verify` が無い（`なし` を含む）なら `None`。
    """
    home = work_home(tree)
    if home is None or not home[1]:
        return None
    try:
        entry = layout.read_config(home[0]).repo(home[1])
    except (layout.ConfigError, OSError):
        return None
    if entry is None or entry.verify in (None, layout.NO_COMMAND):
        return None
    return entry.verify
