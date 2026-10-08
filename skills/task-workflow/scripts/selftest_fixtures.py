"""自己テストのファイル方式の足場と、複数のテストファイルが使う下ごしらえ。"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from selftest_support import git, write  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402

# 利用者の値のままだと、一時リポジトリの台帳がその置き場に積もる。
os.environ.pop(ledger.STATE_DIR_ENV, None)


TASK_PY = os.path.join(HERE, "task.py")

BODY = task_body()


# 登録の既定の本文。`make_repo` が主ブランチに置く `shared.txt` を名指す。
PLANNED_BODY = task_body([("書く", "x")], ["shared.txt"])

# `make_repo` の既定（`.tw/config.toml`）のときの置き場。
TASK_REL = ".tw/task"

DRAFT_REL = ".tw/draft"


def run_task(
    cwd: str, *args: str, stdin: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, TASK_PY, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        input=stdin,
        env={**os.environ, **env} if env is not None else None,
    )


def start_task(cwd: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, TASK_PY, *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _toml_value(value: str) -> str:
    """`` `cmd` `` か `なし` を TOML の文字列にする。"""
    inner = value[1:-1] if value.startswith("`") and value.endswith("`") else value
    return '"' + inner.replace("\\", "\\\\").replace('"', '\\"') + '"'


def make_repo(
    tmp: str,
    branch: str | None = "既定",
    verify: str | None = None,
    base: str = "main",
    config_filename: str | None = None,
    format_command: str | None = None,
    preship: str | None = None,
    section: bool = True,
) -> tuple[str, str, str]:
    """`(本体, 作業ツリー1, 作業ツリー2)`。本体だけが主ブランチを出す。

    設定は `.tw/config.toml` に書く。`verify`・`format_command`・`preship` は `` `cmd` `` か `なし` の形で渡し、
    `verify` を省略すると `verify = "なし"`。`branch` が `None` なら `branch` を書かない。`base` は主ブランチの
    名前——リモートを持たない足場なので `ledger.base_branch` の順3で決まる。
    `config_filename`（`CLAUDE.md`・`AGENTS.md`）を渡すと、代わりに旧い「## タスク運用」節をそのファイルに書く
    （互換の読み。値はそのまま行に書き、`verify` を省略すると行を書かない）。`section` が偽なら設定をどこにも書かない。
    `direction.md` は `.tw/config.toml` を書くときは `.tw/` に、そうでなければ `develop/` に置く。
    """
    main_path = os.path.join(tmp, "base")
    os.makedirs(main_path)
    git(main_path, "init", "-q", "-b", base)
    git(main_path, "config", "user.email", "test@example.com")
    git(main_path, "config", "user.name", "test")
    write(
        os.path.join(main_path, ".tw" if config_filename is None and section else "develop", "direction.md"),
        "# 未対応の指示メモ\n\n## ユーザーから\n\n## エージェントのドラフト\n",
    )
    write(os.path.join(main_path, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
    if config_filename is not None:
        config_md = "# x\n\n## タスク運用\n\n"
        if verify is not None:
            config_md += f"- 検証コマンド: {verify}\n"
        if format_command is not None:
            config_md += f"- 整形コマンド: {format_command}\n"
        if preship is not None:
            config_md += f"- 送る前の検証コマンド: {preship}\n"
        if branch is not None:
            config_md += f"- ブランチ: {branch}\n"
        write(os.path.join(main_path, config_filename), config_md if section else "# x\n")
    elif section:
        toml = f"verify = {_toml_value(verify or 'なし')}\n"
        if format_command is not None:
            toml += f"format = {_toml_value(format_command)}\n"
        if preship is not None:
            toml += f"verify_before_ship = {_toml_value(preship)}\n"
        if branch is not None:
            toml += f'branch = "{branch}"\n'
        write(os.path.join(main_path, ".tw", "config.toml"), toml)
    write(os.path.join(main_path, "shared.txt"), "line1\n")
    git(main_path, "add", "-A")
    git(main_path, "commit", "-q", "-m", "init")

    wt1 = os.path.join(tmp, "wt1")
    wt2 = os.path.join(tmp, "wt2")
    git(main_path, "worktree", "add", "-q", "-b", "wt1-branch", wt1, base)
    git(main_path, "worktree", "add", "-q", "-b", "wt2-branch", wt2, base)
    return main_path, wt1, wt2


def body_file(dirpath: str, name: str = "body.md") -> str:
    return write(os.path.join(dirpath, name), PLANNED_BODY)


def task_rel(repo: str) -> str:
    """`repo` の置き場（`.tw/config.toml` があれば `TASK_REL`、無ければ互換の `develop/task`）。"""
    return TASK_REL if os.path.exists(os.path.join(repo, ".tw", "config.toml")) else "develop/task"


def commit_task(main_path: str, task: taskfile.Task) -> None:
    write(os.path.join(main_path, task_rel(main_path), f"{task.id}.md"), taskfile.render(task))
    git(main_path, "add", "-A")
    git(main_path, "commit", "-q", "-m", f"{task.id}を足す")


def _make_legacy_repo(tmp: str, tasks: list[dict] | None = None, progress: str | None = None) -> str:
    """旧形式（`develop/tasks.json` あり）の一時リポジトリを1つ作る。`develop/direction.md` は
    実在のプロジェクトと同じく最初から置く（無いと移行後に `NEW` ではなく `MISSING` になる）。
    """
    repo = os.path.join(tmp, "legacy")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    write(
        os.path.join(repo, "develop", "direction.md"),
        "# 未対応の指示メモ\n\n## ユーザーから\n\n## エージェントのドラフト\n",
    )
    write(os.path.join(repo, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
    if tasks is not None:
        write(os.path.join(repo, "develop", "tasks.json"), json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    if progress is not None:
        write(os.path.join(repo, "develop", "progress.md"), progress)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def run_handback_guard(tmp: str, where: str, event: str = "SubagentStop", tool: str | None = None) -> dict | None:
    """`tw handback-guard` を git の外（`tmp`）から打つ。拒んだら出した JSON、通したら `None`。"""
    fields: dict = {"hook_event_name": event, "cwd": where}
    if tool is not None:
        fields["tool_name"] = tool
    r = run_task(tmp, "handback-guard", stdin=json.dumps(fields))
    if r.returncode != 0:
        raise RuntimeError(f"handback-guard が {r.returncode} で終わった: {r.stderr}")
    return json.loads(r.stdout) if r.stdout else None


@contextlib.contextmanager
def readonly_git(main_path: str):
    """本体の `.git` を丸ごと書けなくする（`chmod -R a-w` と同じ）。抜けるときに書けるよう戻す。"""
    common = os.path.join(main_path, ".git")
    _chmod_tree(common, writable=False)
    try:
        yield
    finally:
        _chmod_tree(common, writable=True)


def _chmod_tree(top: str, writable: bool) -> None:
    for d, _dirs, files in os.walk(top):
        for p in [d, *(os.path.join(d, f) for f in files)]:
            mode = os.lstat(p).st_mode
            if not stat.S_ISLNK(mode):
                os.chmod(p, mode | stat.S_IWUSR if writable else mode & ~0o222)


def snapshot(*paths: str) -> dict[str, bytes]:
    """`paths` の下のファイル（`.git` の中は除く）のパスと中身。打つ前後で何も変わらないことを見る。"""
    out: dict[str, bytes] = {}
    for top in paths:
        for d, dirs, files in os.walk(top):
            dirs[:] = [x for x in dirs if x != ".git"]
            for f in files:
                p = os.path.join(d, f)
                if f != ".git":
                    with open(p, "rb") as fh:
                        out[p] = fh.read()
    return out


def _claim_work_and_done(wt: str, task_id: str, note: str = "") -> None:
    """`claim` 済みのタスクに1件コミットぶんの作業をして `done` にする（コミットはしない）。"""
    write(os.path.join(wt, f"work{note}.txt"), "x")
    git(wt, "add", "-A")
    result_path = write(os.path.join(wt, f"result{note}.md"), "検証OK\n")
    r = run_task(wt, "done", task_id, "--result-file", result_path)
    if r.returncode != 0:
        raise RuntimeError(f"task done が失敗: {r.stdout}{r.stderr}")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", f"{task_id}: 完了")


def flow_rows(wt: str) -> list[dict]:
    """台帳の `flow/` の記録を、ファイル名の順に読む。"""
    d = ledger.flow_dir(ledger.ledger_root(cwd=wt))
    out: list[dict] = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        with open(os.path.join(d, name), encoding="utf-8") as f:
            out += [json.loads(line) for line in f if line.strip()]
    return out
