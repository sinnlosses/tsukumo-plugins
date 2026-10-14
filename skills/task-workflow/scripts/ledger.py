"""共有の `.git` の中（か `TW_STATE_DIR`）に置く台帳の流れの記録（`flow/`）、`tw sync` の錠、作業ツリーごとの着手の一覧。

台帳はクローンに1つ（`git rev-parse --path-format=absolute --git-common-dir` の下）で、コミットしないので主ブランチを動かさない。
git の情報は `gitinfo.py`、作業ツリーごとの控えの置き場は `stamps.py`。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from collections.abc import Iterator
from datetime import datetime, timezone

import gitinfo
import stamps

LEDGER_DIR_NAME = "task-workflow"
STATE_DIR_ENV = "TW_STATE_DIR"


def ledger_root(cwd: str | None = None) -> str:
    """流れの記録（`flow/`）を置く台帳の置き場。`TW_STATE_DIR` があればその下の台帳（`_state_ledger_root`）、
    無ければ共有の git dir の `task-workflow/`。"""
    state = _state_ledger_root(cwd)
    return state if state is not None else os.path.join(gitinfo.git_common_dir(cwd), LEDGER_DIR_NAME)


def _state_ledger_root(cwd: str | None) -> str | None:
    """`$TW_STATE_DIR/<本体の作業ツリーの名前>-<共有の git dir の realpath の sha1 の先頭12桁>`
    （環境変数は全リポジトリに掛かるのでクローンごとに分ける）。`TW_STATE_DIR` が無ければ `None`。"""
    state = os.environ.get(STATE_DIR_ENV)
    if not state:
        return None
    real = os.path.realpath(gitinfo.git_common_dir(cwd))
    digest = hashlib.sha1(real.encode("utf-8")).hexdigest()[:12]
    name = os.path.basename(os.path.dirname(real))
    return os.path.join(os.path.abspath(os.path.expanduser(state)), f"{name}-{digest}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- flow（着手・検証・送り出し・完了の出来事。月ごとの JSONL）-------------

FLOW_DIR_NAME = "flow"


def flow_dir(root: str) -> str:
    return os.path.join(root, FLOW_DIR_NAME)


def record_event(
    cwd: str | None, event: str, task_id: str, difficulty: str, **fields: str | int | bool
) -> None:
    """1出来事を `flow/<YYYY-MM>.jsonl` に1行足す。会話・コマンド・パスは入れない。

    失敗しても例外を外へ出さない（呼ぶサブコマンドの出力と終了コードを変えない）。標準エラーに1行だけ出す。
    """
    try:
        at = now_iso()
        row = {"t": at, "event": event, "task": task_id, "difficulty": difficulty, **fields}
        d = flow_dir(ledger_root(cwd))
        os.makedirs(d, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(os.path.join(d, f"{at[:7]}.jsonl"), "a", encoding="utf-8") as f:
            f.write(line)
    except (OSError, gitinfo.GitCommandError) as e:
        print(f"flow: 記録を書けなかった（{type(e).__name__}）", file=sys.stderr)


# --- sync の錠（台帳に1つ。作業ツリーをまたいで `tw sync` を1本にする）------------

SYNC_LOCK_NAME = "sync.lock"


class SyncBusy(RuntimeError):
    """別の `tw sync` が錠を持っている。文面は `BUSY` の行の持ち主の情報（古い錠なら、その旨を添える）。"""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def sync_lock(cwd: str | None = None) -> Iterator[None]:
    """`tw sync` の錠（台帳の `sync.lock`。作成に失敗した側が負け）。待たずに `SyncBusy` を上げる。
    持ち主のプロセスが消えた古い錠は壊さず、`SyncBusy` の文面に添えて人に預ける。"""
    root = ledger_root(cwd)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, SYNC_LOCK_NAME)
    owner = f"{os.getpid()}\t{gitinfo.git_toplevel(cwd)}\t{now_iso()}"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            with open(path, encoding="utf-8") as f:
                held = f.read().strip()
        except OSError:
            held = "(読めない)"
        pid_text = held.split("\t", 1)[0]
        stale = pid_text.isdigit() and not _pid_alive(int(pid_text))
        note = f"\t古い錠（持ち主のプロセスが無い。確かめて {path} を消す）" if stale else ""
        raise SyncBusy(f"{held}{note}") from None
    try:
        os.write(fd, owner.encode())
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


# --- open-claim（`claim` から `done` までの作業ツリー固有の控え）------------


def mark_open_claim(task_id: str, cwd: str | None = None) -> None:
    """**作業ツリーごと**に置く（共有の台帳に置くと、別の作業ツリーのコミットまで拒む）。"""
    d = os.path.join(stamps.worktree_state_dir(cwd), stamps.OPEN_CLAIMS_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, task_id), "w", encoding="utf-8"):
        pass


def clear_open_claim(task_id: str, cwd: str | None = None) -> None:
    path = os.path.join(stamps.worktree_state_dir(cwd), stamps.OPEN_CLAIMS_DIR_NAME, task_id)
    if os.path.exists(path):
        os.remove(path)


def open_claims(cwd: str | None = None) -> list[str]:
    d = os.path.join(stamps.worktree_state_read_dir(cwd), stamps.OPEN_CLAIMS_DIR_NAME)
    if not os.path.isdir(d):
        return []
    return sorted(os.listdir(d))
