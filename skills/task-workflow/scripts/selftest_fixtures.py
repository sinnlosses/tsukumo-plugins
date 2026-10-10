"""自己テストの足場（git の本体と作業ツリー2本と `.beads`）と、複数のテストファイルが使う下ごしらえ。"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from selftest_support import copy_beads, git, write  # noqa: E402
import beads  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402

# 利用者の値のままだと、一時リポジトリの台帳がその置き場に積もる。
os.environ.pop(ledger.STATE_DIR_ENV, None)


TASK_PY = os.path.join(HERE, "task.py")

BODY = task_body()


# 登録の既定の本文。`make_repo` が主ブランチに置く `shared.txt` を名指す。
PLANNED_BODY = task_body([("書く", "x")], ["shared.txt"])

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
    format_command: str | None = None,
    preship: str | None = None,
) -> tuple[str, str, str]:
    """`(本体, 作業ツリー1, 作業ツリー2)`。本体だけが主ブランチを出す。

    設定は `.tw/config.toml` に書く。`verify`・`format_command`・`preship` は `` `cmd` `` か `なし` の形で渡し、
    `verify` を省略すると `verify = "なし"`。`branch` が `None` なら `branch` を書かない。`base` は主ブランチの
    名前——リモートを持たない足場なので `ledger.base_branch` の順3で決まる。
    `.beads` は `beads_home` の `t` の作り置きを写す。
    """
    main_path = os.path.join(tmp, "base")
    os.makedirs(main_path)
    git(main_path, "init", "-q", "-b", base)
    git(main_path, "config", "user.email", "test@example.com")
    git(main_path, "config", "user.name", "test")
    git(main_path, "config", "beads.role", "maintainer")
    copy_beads(main_path, beads.PREFIX_LOCAL)
    write(
        os.path.join(main_path, ".tw", "direction.md"),
        "# 未対応の指示メモ\n\n## ユーザーから\n\n## エージェントのドラフト\n",
    )
    write(os.path.join(main_path, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
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


def commit_task(main_path: str, task: taskfile.Task) -> None:
    """`tw new` を通さずに、`task` の ID・状態・label・本文のまま Beads の課題を作る（コミットしない）。"""
    bd_id = beads.to_bd_id(task.id)
    parts = beads.split_body(task.body)
    labels = [f"{beads.DIFFICULTY_LABEL}{task.difficulty}", f"{beads.LOOPABLE_LABEL}{task.loopable}"]
    if task.direct == "Y":
        labels.append(beads.DIRECT_ON)
    if task.status == "dropped":
        labels.append(beads.CANCELLED_LABEL)
    cmd = ["create", "--id", bd_id, "--title", task.summary, "--body-file", "-", "-l", ",".join(labels), "--silent"]
    if parts.acceptance:
        cmd += ["--acceptance", parts.acceptance]
    if parts.notes:
        cmd += ["--notes", parts.notes]
    if task.status == "hold":
        cmd += ["-s", beads.HOLD_STATUS]
    if task.dependencies:
        cmd += ["--deps", ",".join(beads.to_bd_id(d) for d in task.dependencies)]
    _bd(main_path, cmd, parts.description)
    if parts.result is not None:
        _bd(main_path, ["comment", bd_id, "--stdin"], f"{beads.RESULT_HEADING}\n\n{parts.result}\n")
    if task.status in ("done", "dropped"):
        _bd(main_path, ["close", bd_id, "--reason", "cancelled" if task.status == "dropped" else "done"])


def shown(repo: str, task_id: str) -> str:
    """`tw show` の出力（front matter と本文）。"""
    return run_task(repo, "show", task_id).stdout


def shown_body(repo: str, task_id: str) -> str:
    """`tw show` の本文（front matter の後ろ）。"""
    return shown(repo, task_id).split("\n---\n", 1)[-1]


def issue_json(repo: str, task_id: str) -> dict:
    """`bd show --json` の課題（無ければ空）。"""
    r = subprocess.run(["bd", "show", beads.to_bd_id(task_id), "--json"], cwd=repo, capture_output=True, text=True)
    data = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    issue = data[0] if isinstance(data, list) and data else data
    return issue if isinstance(issue, dict) else {}


def metadata(repo: str, task_id: str) -> dict:
    """Beads の課題の metadata（無ければ空）。"""
    value = issue_json(repo, task_id).get("metadata")
    return value if isinstance(value, dict) else {}


def unset_metadata(repo: str, task_id: str, key: str) -> None:
    _bd(repo, ["update", beads.to_bd_id(task_id), "--unset-metadata", key])


def issue_count(repo: str) -> int:
    """Beads の課題の数（閉じたものも数える）。"""
    r = subprocess.run(["bd", "list", "--all", "-n", "0", "--flat", "--json"], cwd=repo, capture_output=True, text=True)
    data = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []
    return len(data) if isinstance(data, list) else 0


def set_plan_directly(repo: str, task_id: str, plan: str) -> None:
    """`tw edit` を通さずに `## やること` の中身（Beads の notes）を書き換える。"""
    _bd(repo, ["update", beads.to_bd_id(task_id), "--notes", plan])


def _bd(cwd: str, args: list[str], stdin: str | None = None) -> None:
    r = subprocess.run(["bd", *args], cwd=cwd, capture_output=True, text=True, input=stdin)
    if r.returncode != 0:
        raise RuntimeError(f"bd {' '.join(args)} 失敗: {r.stdout}{r.stderr}")


def hook_env(tmp: str, path: str) -> dict[str, str]:
    """hook の行を `sh -c` で打つときの環境。`path` に `bd` だけを置いたディレクトリを足し、家の向け先を引き継ぐ。"""
    bd_dir = os.path.join(tmp, "bd-only-bin")
    if not os.path.isdir(bd_dir):
        os.makedirs(bd_dir)
        os.symlink(shutil.which("bd") or "bd", os.path.join(bd_dir, "bd"))
    homes = {k: os.environ[k] for k in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME") if k in os.environ}
    return {"PATH": f"{path}:{bd_dir}", **homes}


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
