"""git の薄い包みと主ブランチの決め方、共有の `.git` の中（か `TW_STATE_DIR`）に置く台帳の流れの記録（`flow/`）、
作業ツリーの根の `.tw/local/` に置く作業ツリーごとの控え（検証・中断・段の鍵、着手の控え、ログ）。

台帳はクローンに1つ（`git rev-parse --path-format=absolute --git-common-dir` の下）で、どちらもコミットしないので主ブランチを動かさない。
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

import layout

LEDGER_DIR_NAME = "task-workflow"
STATE_DIR_ENV = "TW_STATE_DIR"
READ_ONLY_ERRNOS = (errno.EACCES, errno.EPERM, errno.EROFS)


class GitCommandError(RuntimeError):
    """git を呼んで非0で終わった。環境の故障として扱う（データの不備ではない）。"""


class NoBaseBranch(RuntimeError):
    """主ブランチが決まらなかった。データの不備（呼ぶ側が `INVALID`・終了コード3 にする）。"""


class GitReadOnly(RuntimeError):
    """`.git` に書けない（呼ぶ側が `GIT_READ_ONLY`・終了コード11 にする）。"""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.path = path


def _git(args: list[str], cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise GitCommandError(f"git {' '.join(args)} が失敗（{r.returncode}）: {r.stderr.strip()}")
    return r.stdout.strip()


def git_common_dir(cwd: str | None = None) -> str:
    return _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)


def git_dir(cwd: str | None = None) -> str:
    """この作業ツリー**だけ**の git dir（共有の `git_common_dir` とは違う。連結した
    作業ツリーでは `<共通の git dir>/worktrees/<名前>`）。作業ツリーの控えの最も古い置き場。"""
    return _git(["rev-parse", "--path-format=absolute", "--git-dir"], cwd)


def require_git_writable(cwd: str | None = None) -> None:
    """git に書く操作の前に、作業ツリー固有の git dir と共有の git dir に書いて消せるかを確かめる。
    書けなければ `GitReadOnly`（打つ前に止めるので、作業ツリーも台帳も変わらない）。"""
    for d in dict.fromkeys([git_dir(cwd), git_common_dir(cwd)]):
        try:
            fd, probe = tempfile.mkstemp(prefix=".tw-probe-", dir=d)
            os.close(fd)
            os.remove(probe)
        except OSError as e:
            if e.errno in READ_ONLY_ERRNOS:
                raise GitReadOnly(d) from e
            raise


def git_toplevel(cwd: str | None = None) -> str:
    return _git(["rev-parse", "--show-toplevel"], cwd)


def current_branch(cwd: str | None = None) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def is_clean(cwd: str | None = None) -> bool:
    return _git(["status", "--porcelain"], cwd) == ""


def head_sha_or_none(cwd: str | None = None) -> str | None:
    """`git rev-parse HEAD`。引けなければ `None`（`claim` が `head` を控えずに済ませる）。"""
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


# --- 主ブランチ（`main`・`master`・`trunk` …） -----------------------------

BASE_BRANCH_CANDIDATES = ("main", "master", "trunk")
_base_branch_cache: dict[str, tuple[str, int]] = {}


def _resolve_base_branch(cwd: str | None) -> tuple[str, int]:
    """`(主ブランチ名, 決まった順)` を1回だけ git に問い合わせて決める（`base_branch`・
    `base_branch_order` の共有部分。呼ぶ側を増やしても判定は1箇所のまま）。

    決め方の順:

    1. 設定の `base_branch`（任意）
    2. `git symbolic-ref --short refs/remotes/origin/HEAD` の枝名。**`origin` だけを見る**
       （別名のリモートしか無いリポジトリは順3へ落ちる。唯一のリモートを `origin` 扱いすると、
       fork 元を指す `upstream` を主ブランチの出どころにしてしまう。逃げ道は順1の行）
    3. `main`・`master`・`trunk` のうち `rev-parse --verify` で実在するもの（この順）
    4. どれも無ければ `NoBaseBranch`。**黙って `main` を作らない**

    1プロセスで1回だけ git に問い合わせて覚える（`task status` は1回の実行で何度も要る）。
    """
    # 呼ぶ側はふつう toplevel を渡すので、覚えていればそのまま返す（git を呼ばない）。
    if cwd is not None and cwd in _base_branch_cache:
        return _base_branch_cache[cwd]
    toplevel = git_toplevel(cwd)
    cached = _base_branch_cache.get(toplevel)
    if cached is not None:
        return cached

    found = layout.read_config(toplevel).base_branch
    order = 1
    if found is None:
        order = 2
        r = subprocess.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=toplevel,
            capture_output=True,
            text=True,
        )
        head = r.stdout.strip()
        if r.returncode == 0 and head.startswith("origin/"):
            found = head[len("origin/") :]
    if found is None:
        order = 3
        for name in BASE_BRANCH_CANDIDATES:
            r = subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
                cwd=toplevel,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                found = name
                break
    if found is None:
        raise NoBaseBranch(
            "主ブランチが決まらない（設定の base_branch も、origin/HEAD も、"
            f"{'・'.join(BASE_BRANCH_CANDIDATES)} の枝も無い）"
        )

    _base_branch_cache[toplevel] = (found, order)
    return found, order


def base_branch(cwd: str | None = None) -> str:
    """このリポジトリの主ブランチ名（`main` に固定しない）。決め方の順は `_resolve_base_branch`。"""
    return _resolve_base_branch(cwd)[0]


def base_branch_order(cwd: str | None = None) -> int:
    """主ブランチが決まった順（1〜3。`_resolve_base_branch` の docstring）。`task config-doctor` 向け。"""
    return _resolve_base_branch(cwd)[1]


def clear_base_branch_cache() -> None:
    """覚えた主ブランチ名を捨てる（同じプロセスで別のリポジトリを作り替えるテスト用）。"""
    _base_branch_cache.clear()


@dataclass(frozen=True)
class Worktree:
    path: str
    branch: str | None  # detached HEAD なら None


def list_worktrees(cwd: str | None = None) -> list[Worktree]:
    """`git worktree list --porcelain` を読む（4.3 の取り残し判定に使う）。"""
    out = _git(["worktree", "list", "--porcelain"], cwd)
    worktrees: list[Worktree] = []
    path: str | None = None
    branch: str | None = None
    for line in out.splitlines() + [""]:
        if line == "":
            if path is not None:
                worktrees.append(Worktree(path, branch))
            path, branch = None, None
            continue
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
            branch = None
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            branch = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
    return worktrees


def ledger_root(cwd: str | None = None) -> str:
    """流れの記録（`flow/`）を置く台帳の置き場。`TW_STATE_DIR` があればその下の台帳（`_state_ledger_root`）、
    無ければ共有の git dir の `task-workflow/`。"""
    state = _state_ledger_root(cwd)
    return state if state is not None else os.path.join(git_common_dir(cwd), LEDGER_DIR_NAME)


def _state_ledger_root(cwd: str | None) -> str | None:
    """`$TW_STATE_DIR/<本体の作業ツリーの名前>-<共有の git dir の realpath の sha1 の先頭12桁>`
    （環境変数は全リポジトリに掛かるのでクローンごとに分ける）。`TW_STATE_DIR` が無ければ `None`。"""
    state = os.environ.get(STATE_DIR_ENV)
    if not state:
        return None
    real = os.path.realpath(git_common_dir(cwd))
    digest = hashlib.sha1(real.encode("utf-8")).hexdigest()[:12]
    name = os.path.basename(os.path.dirname(real))
    return os.path.join(os.path.abspath(os.path.expanduser(state)), f"{name}-{digest}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    toplevel = git_toplevel(cwd)
    tw_dir = os.path.join(toplevel, WORKTREE_STATE_DIR_NAME)
    target = os.path.join(toplevel, layout.LOCAL_DIR)
    if os.path.isdir(target):
        ignore = os.path.join(target, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as f:
                f.write(WORKTREE_STATE_IGNORE)
        return target
    old = git_dir(toplevel)
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


def _worktree_state_read_dir(cwd: str | None = None) -> str:
    """読む側の置き場。`.tw/local/` → `.tw/` 直下（旧い控えがあれば）→ `git_dir` の順。"""
    toplevel = git_toplevel(cwd)
    target = os.path.join(toplevel, layout.LOCAL_DIR)
    if os.path.isdir(target):
        return target
    tw_dir = os.path.join(toplevel, WORKTREE_STATE_DIR_NAME)
    if any(n in _CARRIED_OVER for n in legacy_state_entries(tw_dir)):
        return tw_dir
    return git_dir(cwd)


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
    return os.path.exists(os.path.join(_worktree_state_read_dir(cwd), VERIFY_OWED_FILE_NAME))


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


def content_key(verify_command: str, cwd: str | None = None, excluded: tuple[str, ...] = ()) -> ContentKey:
    """いまの作業ツリーの中身の鍵。`excluded` のパスも木から外す。"""
    toplevel = git_toplevel(cwd)
    head = head_sha_or_none(toplevel) or "-"
    return ContentKey(head, content_tree(toplevel, excluded), verify_command)


def content_tree(toplevel: str, excluded: tuple[str, ...] = ()) -> str:
    """鍵に取る木の SHA。`worktree_tree` の木から `<根>/draft/`・`.tw/local/` と `excluded` を外す。`.git` に書かない。"""
    places = (layout.draft_dir(toplevel), layout.LOCAL_DIR)
    return worktree_tree(toplevel, objects_in_repo=False, excluded=(*places, *excluded))


def scratch_object_env(toplevel: str, scratch_dir: str) -> dict[str, str]:
    """新しい object を `scratch_dir` に書き、元の object は読むだけにする git の環境変数。`.git` には何も書かない。"""
    real_objects = _git(["rev-parse", "--path-format=absolute", "--git-path", "objects"], toplevel)
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
    real_index = _git(["rev-parse", "--path-format=absolute", "--git-path", "index"], toplevel)
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
        for args in steps:
            r = subprocess.run(["git", *args], cwd=toplevel, env=env, capture_output=True, text=True)
            if r.returncode != 0:
                raise GitCommandError(f"git {' '.join(args)} が失敗（{r.returncode}）: {r.stderr.strip()}")
        return r.stdout.strip()


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
    return _read_key(os.path.join(_worktree_state_read_dir(cwd), VERIFY_STAMP_FILE_NAME))


def clear_verify_stamp(cwd: str | None = None) -> None:
    path = os.path.join(worktree_state_dir(cwd), VERIFY_STAMP_FILE_NAME)
    if os.path.exists(path):
        os.remove(path)


def write_pause_stamp(key: ContentKey, cwd: str | None = None) -> None:
    """`tw pause` を打った時点の中身の鍵。"""
    _write_key(os.path.join(worktree_state_dir(cwd), PAUSE_STAMP_FILE_NAME), key)


def read_pause_stamp(cwd: str | None = None) -> ContentKey | None:
    return _read_key(os.path.join(_worktree_state_read_dir(cwd), PAUSE_STAMP_FILE_NAME))


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
    d = os.path.join(_worktree_state_read_dir(cwd), STEP_STAMPS_DIR_NAME)
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
        f.write("".join(f"{line}\n" for line in (key.head, key.tree, key.verify_command, *extra)))
    os.replace(tmp, path)


def _read_key(path: str) -> ContentKey | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if len(lines) < 3:
        return None
    return ContentKey(lines[0], lines[1], lines[2])


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
    except (OSError, GitCommandError) as e:
        print(f"flow: 記録を書けなかった（{type(e).__name__}）", file=sys.stderr)


# --- open-claim（`claim` から `done` までの作業ツリー固有の控え）------------


def mark_open_claim(task_id: str, cwd: str | None = None) -> None:
    """**作業ツリーごと**に置く（共有の台帳に置くと、別の作業ツリーのコミットまで拒む）。"""
    d = os.path.join(worktree_state_dir(cwd), OPEN_CLAIMS_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, task_id), "w", encoding="utf-8"):
        pass


def clear_open_claim(task_id: str, cwd: str | None = None) -> None:
    path = os.path.join(worktree_state_dir(cwd), OPEN_CLAIMS_DIR_NAME, task_id)
    if os.path.exists(path):
        os.remove(path)


def open_claims(cwd: str | None = None) -> list[str]:
    d = os.path.join(_worktree_state_read_dir(cwd), OPEN_CLAIMS_DIR_NAME)
    if not os.path.isdir(d):
        return []
    return sorted(os.listdir(d))
