"""作業ツリーの根の `.tw/local/` に置く作業ツリーごとの控え（検証の借り・検証・中断・段の鍵、着手の控えの置き場、ログ）。"""

from __future__ import annotations

import hashlib
import inspect
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

import gitcmd
import gitinfo
import layout

# --- 作業ツリーごとの控えの置き場（作業ツリーの根の `.tw/local/`）----------

WORKTREE_STATE_DIR_NAME = ".tw"
VERIFY_OWED_FILE_NAME = "task-ship-verify-owed"
VERIFY_STAMP_FILE_NAME = "task-verify-stamp"
VERIFY_LOG_FILE_NAME = "task-verify.log"
SHIP_VERIFY_LOG_FILE_NAME = "task-ship-verify.log"
PAUSE_STAMP_FILE_NAME = "task-pause-stamp"
STEP_STAMPS_DIR_NAME = "task-step-stamps"
OPEN_CLAIMS_DIR_NAME = "task-open-claims"
# `.tw/local/` を作るときに旧い置き場（`.tw/` 直下、無ければ `git_dir`）から移すもの。
_CARRIED_OVER = (
    VERIFY_OWED_FILE_NAME,
    VERIFY_STAMP_FILE_NAME,
    PAUSE_STAMP_FILE_NAME,
    STEP_STAMPS_DIR_NAME,
    OPEN_CLAIMS_DIR_NAME,
)
_LOG_STEMS = (VERIFY_LOG_FILE_NAME.removesuffix(".log"), SHIP_VERIFY_LOG_FILE_NAME.removesuffix(".log"))


WORKTREE_STATE_IGNORE = "*\n"


def _is_state_log(name: str) -> bool:
    return name.endswith(".log") and any(name == f"{s}.log" or name.startswith(f"{s}.failed-") for s in _LOG_STEMS)


def legacy_state_entries(tw_dir: str) -> list[str]:
    """`.tw/` 直下にある旧い控えとログの名前。"""
    if not os.path.isdir(tw_dir):
        return []
    return sorted(n for n in os.listdir(tw_dir) if n in _CARRIED_OVER or _is_state_log(n))


def worktree_state_dir(cwd: str | None = None) -> str:
    """書く・消す側の置き場 `.tw/local/`。無ければ、`.gitignore`（`*`）と旧い置き場の控え（名前ごとに `.tw/` 直下、
    無ければ `git_dir`）と `.tw/` 直下のログの写しを `.tw/` の下の一時ディレクトリに揃えてから `.tw/local` へ
    `rename` し、写した旧い側を両方の置き場から消す（消せなければ残す）。`.tw/.gitignore` は書かない。"""
    toplevel = gitinfo.git_toplevel(cwd)
    tw_dir = os.path.join(toplevel, WORKTREE_STATE_DIR_NAME)
    target = os.path.join(toplevel, layout.LOCAL_DIR)
    if os.path.isdir(target):
        ignore = os.path.join(target, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as f:
                f.write(WORKTREE_STATE_IGNORE)
        return target
    old = gitinfo.git_dir(toplevel)
    os.makedirs(tw_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".local-", dir=tw_dir)
    with open(os.path.join(tmp, ".gitignore"), "w", encoding="utf-8") as f:
        f.write(WORKTREE_STATE_IGNORE)
    in_tw = legacy_state_entries(tw_dir)
    names = [*_CARRIED_OVER, *(n for n in in_tw if n not in _CARRIED_OVER)]
    carried: list[str] = []
    for name in names:
        sources = [p for p in (os.path.join(tw_dir, name), os.path.join(old, name)) if os.path.lexists(p)]
        if name not in _CARRIED_OVER:
            sources = sources[:1]
        if not sources:
            continue
        src = sources[0]
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tmp, name))
        else:
            shutil.copy2(src, os.path.join(tmp, name))
        carried.extend(sources)
    try:
        os.rename(tmp, target)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        if not os.path.isdir(target):
            raise
        return target
    for src in carried:
        try:
            if os.path.isdir(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
        except OSError:
            pass
    return target


def worktree_state_read_dir(cwd: str | None = None) -> str:
    """読む側の置き場。`.tw/local/` → `.tw/` 直下（旧い控えがあれば）→ `git_dir` の順。"""
    toplevel = gitinfo.git_toplevel(cwd)
    target = os.path.join(toplevel, layout.LOCAL_DIR)
    if os.path.isdir(target):
        return target
    tw_dir = os.path.join(toplevel, WORKTREE_STATE_DIR_NAME)
    if any(n in _CARRIED_OVER for n in legacy_state_entries(tw_dir)):
        return tw_dir
    return gitinfo.git_dir(cwd)


# --- verify-owed（`VERIFY_FAILED` のあと打ち直すまでの検証の借り）----------


def mark_verify_owed(verify_command: str, cwd: str | None = None) -> None:
    """**作業ツリーごと**に置く（共有の台帳とは別）。
    検証を跨いだ枝を抱えているのはこの作業ツリーだけなので、共有すると
    無関係な作業ツリーの `ship` まで検証を強制してしまう。"""
    path = os.path.join(worktree_state_dir(cwd), VERIFY_OWED_FILE_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(verify_command + "\n")
    os.replace(tmp, path)


def is_verify_owed(cwd: str | None = None) -> bool:
    return os.path.exists(os.path.join(worktree_state_read_dir(cwd), VERIFY_OWED_FILE_NAME))


def clear_verify_owed(cwd: str | None = None) -> None:
    path = os.path.join(worktree_state_dir(cwd), VERIFY_OWED_FILE_NAME)
    if os.path.exists(path):
        os.remove(path)


# --- verify-stamp（検証コマンドが通った作業ツリーの中身の控え）------------


@dataclass(frozen=True)
class ContentKey:
    head: str
    tree: str
    verify_command: str
    scheme: str


def content_key(verify_command: str, cwd: str | None = None, excluded: tuple[str, ...] = ()) -> ContentKey:
    """いまの作業ツリーの中身の鍵。`excluded` のパスも木から外す。"""
    toplevel = gitinfo.git_toplevel(cwd)
    head = gitinfo.head_sha_or_none(toplevel) or "-"
    return ContentKey(head, content_tree(toplevel, excluded), verify_command, CONTENT_SCHEME)


def content_tree(toplevel: str, excluded: tuple[str, ...] = ()) -> str:
    """鍵に取る木の SHA。`worktree_tree` の木から `.tw/local/` と `excluded` を外す。`.git` に書かない。"""
    return worktree_tree(toplevel, objects_in_repo=False, excluded=(layout.LOCAL_DIR, *excluded))


def scratch_object_env(toplevel: str, scratch_dir: str) -> dict[str, str]:
    """新しい object を `scratch_dir` に書き、元の object は読むだけにする git の環境変数。`.git` には何も書かない。"""
    real_objects = gitcmd.out(["rev-parse", "--path-format=absolute", "--git-path", "objects"], toplevel)
    temp_objects = os.path.join(scratch_dir, "objects")
    os.mkdir(temp_objects)
    alternates = [real_objects, *filter(None, [os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES")])]
    return {"GIT_OBJECT_DIRECTORY": temp_objects, "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.pathsep.join(alternates)}


def worktree_tree(
    toplevel: str,
    objects_in_repo: bool = True,
    object_env: dict[str, str] | None = None,
    excluded: tuple[str, ...] = (),
) -> str:
    """いまの作業ツリーの中身の木の SHA。

    index を一時ファイルに写して `git add -A` → `git write-tree` するので、`.gitignore` の対象でない
    未追跡のファイルまで入り、本物の index は変わらない。`objects_in_repo` が偽なら、新しい object は
    一時ディレクトリに書き、元の object は `GIT_ALTERNATE_OBJECT_DIRECTORIES` で読むので、
    `.git` に書かずに済む（木の SHA は同じ。その木は `.git` には残らない）。`object_env` があれば
    その環境変数で書き、呼ぶ側が書いた木をそのあとも読める。`excluded` のパスは木から外す。
    """
    real_index = gitcmd.out(["rev-parse", "--path-format=absolute", "--git-path", "index"], toplevel)
    with tempfile.TemporaryDirectory() as tmp:
        temp_index = os.path.join(tmp, "index")
        if os.path.exists(real_index):
            # mtime を保たないと、同じ秒・同じ大きさの書き換えを git が無変更とみなす
            shutil.copy2(real_index, temp_index)
        env = {**os.environ, "GIT_INDEX_FILE": temp_index}
        if object_env is not None:
            env.update(object_env)
        elif not objects_in_repo:
            env.update(scratch_object_env(toplevel, tmp))
        steps = [["add", "-A"], ["write-tree"]]
        if excluded:
            steps.insert(1, ["rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", *excluded])
        tree = ""
        for args in steps:
            tree = gitcmd.out(args, toplevel, env)
        return tree


def _content_scheme() -> str:
    """鍵の取り方の版の目印。`content_tree`・`worktree_tree` のソースと `layout.LOCAL_DIR` から機械で取る。"""
    source = inspect.getsource(content_tree) + inspect.getsource(worktree_tree) + layout.LOCAL_DIR
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


CONTENT_SCHEME = _content_scheme()
SCHEME_LINE_PREFIX = "scheme:"


def verify_log_path(cwd: str | None = None) -> str:
    return os.path.join(worktree_state_dir(cwd), VERIFY_LOG_FILE_NAME)


def ship_verify_log_path(cwd: str | None = None) -> str:
    return os.path.join(worktree_state_dir(cwd), SHIP_VERIFY_LOG_FILE_NAME)


FAILED_LOGS_KEPT = 3


def keep_failed_log(log_path: str) -> str:
    """落ちた回のログを時刻つきの別名へ写し、同じ種類の古いものを直近3本だけ残して消す。写したパスを返す。"""
    directory, name = os.path.split(log_path)
    stem = name.removesuffix(".log")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    kept = os.path.join(directory, f"{stem}.failed-{stamp}.log")
    shutil.copyfile(log_path, kept)
    older = sorted(n for n in os.listdir(directory) if n.startswith(f"{stem}.failed-") and n.endswith(".log"))
    for old in older[:-FAILED_LOGS_KEPT]:
        os.remove(os.path.join(directory, old))
    return kept


def write_verify_stamp(key: ContentKey, cwd: str | None = None) -> None:
    _write_key(os.path.join(worktree_state_dir(cwd), VERIFY_STAMP_FILE_NAME), key)


def read_verify_stamp(cwd: str | None = None) -> ContentKey | None:
    return _read_key(os.path.join(worktree_state_read_dir(cwd), VERIFY_STAMP_FILE_NAME))


def clear_verify_stamp(cwd: str | None = None) -> None:
    path = os.path.join(worktree_state_dir(cwd), VERIFY_STAMP_FILE_NAME)
    if os.path.exists(path):
        os.remove(path)


def write_pause_stamp(key: ContentKey, cwd: str | None = None) -> None:
    """`tw pause` を打った時点の中身の鍵。"""
    _write_key(os.path.join(worktree_state_dir(cwd), PAUSE_STAMP_FILE_NAME), key)


def read_pause_stamp(cwd: str | None = None) -> ContentKey | None:
    return _read_key(os.path.join(worktree_state_read_dir(cwd), PAUSE_STAMP_FILE_NAME))


STEP_STAMP_KINDS = ("step", "pause")


@dataclass(frozen=True)
class StepStamp:
    """`tw step`（`kind` が `step`）か `tw pause <ID> <n>`（`pause`）を打った時点の中身の鍵と段。"""

    key: ContentKey
    task_id: str
    step: int
    kind: str = "step"


def write_step_stamp(stamp: StepStamp, cwd: str | None = None) -> None:
    """段ごとに1つ（`<タスクID>.<段の番号>`）書き、同じ段の前の控えを置き換える。"""
    d = os.path.join(worktree_state_dir(cwd), STEP_STAMPS_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    _write_key(os.path.join(d, f"{stamp.task_id}.{stamp.step}"), stamp.key, (stamp.task_id, str(stamp.step), stamp.kind))


def read_step_stamps(cwd: str | None = None) -> list[StepStamp]:
    """段の控えを全部。読めない控えは飛ばす。"""
    d = os.path.join(worktree_state_read_dir(cwd), STEP_STAMPS_DIR_NAME)
    if not os.path.isdir(d):
        return []
    stamps: list[StepStamp] = []
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        key = _read_key(path) if os.path.isfile(path) else None
        if key is None:
            continue
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        if len(lines) < 6 or not lines[4].isdigit() or lines[5] not in STEP_STAMP_KINDS:
            continue
        stamps.append(StepStamp(key, lines[3], int(lines[4]), lines[5]))
    return stamps


def remove_step_stamps(task_ids: list[str], cwd: str | None = None) -> None:
    """`task_ids` のタスクの段の控えを消す。"""
    d = os.path.join(worktree_state_dir(cwd), STEP_STAMPS_DIR_NAME)
    for stamp in read_step_stamps(cwd):
        if stamp.task_id in task_ids:
            path = os.path.join(d, f"{stamp.task_id}.{stamp.step}")
            if os.path.exists(path):
                os.remove(path)


def _write_key(path: str, key: ContentKey, extra: tuple[str, ...] = ()) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(f"{line}\n" for line in (key.head, key.tree, key.verify_command, *extra, SCHEME_LINE_PREFIX + key.scheme)))
    os.replace(tmp, path)


def _read_key(path: str) -> ContentKey | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if len(lines) < 3:
        return None
    last = next((line for line in reversed(lines) if line), "")
    scheme = last.removeprefix(SCHEME_LINE_PREFIX) if last.startswith(SCHEME_LINE_PREFIX) else ""
    return ContentKey(lines[0], lines[1], lines[2], scheme)
