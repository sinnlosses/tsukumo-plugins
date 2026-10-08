"""`task ship` の git 操作（rebase → 検証 → 送る）。

正典は `docs/task-workflow-redesign.md` の5.8・6章。**merge commit は作らない**——
取り込みは `git rebase`、送るのは `git merge --ff-only`（本体がある場合）か比較付きの
`git update-ref`（無い場合）だけ（6.2手順5）。取り合い（`--ff-only`／`update-ref` の失敗）は
「相手に先を越された」合図として、最大 `MAX_TRIES` 回まで rebase からやり直す。

クレームの後始末・出力の組み立て・終了コードは呼び出し側（`task.py` の `cmd_ship`）が持つ。
ここは1回の `ship` にかかる git の手順だけを持つ。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import layout
import ledger

MAX_TRIES = 3


@dataclass(frozen=True)
class ShipOutcome:
    kind: str  # "SENT" | "CONFLICT" | "VERIFY_FAILED" | "RACE"
    rebased: bool = False
    verify_state: str = "none"  # "ran" | "skipped" | "none"（6.3）
    tries: int = 0
    conflict_files: tuple[str, ...] = field(default_factory=tuple)
    verify_command: str | None = None
    verify_tail: str = ""
    verify_log: str = ""
    preship_ran: bool = False


def read_stamp_command(toplevel: str) -> str | None:
    """`tw verify` が打ち、控えの鍵に入れるコマンド。送る前の検証コマンドがあればそれ、無ければ検証コマンド。"""
    config = layout.read_config(toplevel)
    return config.verify_before_ship or config.verify


def find_base_worktree(
    worktrees: list[ledger.Worktree], own_path: str, base: str
) -> ledger.Worktree | None:
    """主ブランチを出している別の作業ツリー（本体）を探す（6.2手順4）。自分自身は数えない。"""
    return next((w for w in worktrees if w.branch == base and w.path != own_path), None)


def _run_logged(toplevel: str, command: str) -> tuple[int, str, str]:
    """検証を打ち、stdout と stderr を出た順に1本のログへ書く。(終了コード, ログのパス, 末尾40行)。"""
    log_path = ledger.ship_verify_log_path(toplevel)
    with open(log_path, "w") as log:
        rc = subprocess.run(["sh", "-c", command], cwd=toplevel, stdout=log, stderr=subprocess.STDOUT).returncode
    with open(log_path, errors="replace") as log:
        tail = "\n".join(log.read().splitlines()[-40:])
    if rc != 0:
        log_path = ledger.keep_failed_log(log_path)
    return rc, log_path, tail


def _run(cwd: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def attempt(
    toplevel: str,
    base_worktree: ledger.Worktree | None,
    verify_command: str | None,
    base: str,
    verify_owed: bool = False,
    preship_command: str | None = None,
    preship_stamped: bool = False,
) -> ShipOutcome:
    """最大 `MAX_TRIES` 回、rebase → 検証（付け替えた回だけ）→ 送る、を繰り返す（6.2手順5・6）。

    `base` は主ブランチの名前（`ledger.base_branch`）。送る先は `base_worktree` があれば
    `git -C <本体> merge --ff-only <HEAD のコミット>`、無ければ比較付きの `git update-ref`。
    失敗（相手に先を越された）は次の回の rebase からやり直す。

    `verify_owed` は前回の `ship` が `VERIFY_FAILED` で終わった印（`ledger.is_verify_owed`）。
    立っていれば、1回目の試行で付け替えが起きなくても検証を打つ——付け替え済みのまま次の
    `ship` を打つと、そのままでは検証を素通りして送ってしまうため（T-777）。

    `preship_command` があれば、上の検証の代わりにこれを送る直前に打つ（検証コマンドは打たない）。
    打つのは付け替えた回・借りの1回目・`preship_stamped`（控えの木とコマンドが今の中身と同じ）が
    偽のとき。落ちたら `VERIFY_FAILED`（`verify_command` はこのコマンド）。
    """
    rebased_any = False
    preship_ran = False
    verify_state = "none" if verify_command is None else "skipped"

    for tries in range(1, MAX_TRIES + 1):
        rebased_this_round = False
        is_ancestor = _run(toplevel, ["merge-base", "--is-ancestor", base, "HEAD"]).returncode == 0
        if not is_ancestor:
            r = _run(toplevel, ["rebase", base])
            if r.returncode != 0:
                conflicts = _run(toplevel, ["diff", "--name-only", "--diff-filter=U"]).stdout.splitlines()
                _run(toplevel, ["rebase", "--abort"])
                return ShipOutcome(kind="CONFLICT", conflict_files=tuple(f for f in conflicts if f))
            rebased_this_round = True
            rebased_any = True

        owed_this_round = tries == 1 and verify_owed
        if (rebased_this_round or owed_this_round) and verify_command is not None and preship_command is None:
            vrc, vlog, tail = _run_logged(toplevel, verify_command)
            verify_state = "ran"
            if vrc != 0:
                return ShipOutcome(
                    kind="VERIFY_FAILED",
                    rebased=rebased_any,
                    verify_state=verify_state,
                    tries=tries,
                    verify_command=verify_command,
                    verify_tail=tail,
                    verify_log=vlog,
                )

        if preship_command is not None and (rebased_this_round or owed_this_round or not preship_stamped):
            preship_ran = True
            prc, plog, tail = _run_logged(toplevel, preship_command)
            if prc != 0:
                return ShipOutcome(
                    kind="VERIFY_FAILED",
                    rebased=rebased_any,
                    verify_state=verify_state,
                    tries=tries,
                    verify_command=preship_command,
                    verify_tail=tail,
                    verify_log=plog,
                )

        # 枝の名前ではなくコミットで送る（detached HEAD の `HEAD` は本体の側では本体自身を指す）。
        head = _run(toplevel, ["rev-parse", "HEAD"]).stdout.strip()
        if base_worktree is not None:
            sent = _run(base_worktree.path, ["merge", "--ff-only", head]).returncode == 0
        else:
            # 見たときの位置と突き合わせる更新（同じ1つの枝名で `rev-parse` と `update-ref` を
            # 打つので、他の作業ツリーに先を越されたときの検知は枝名が変わっても同じ）。
            seen = _run(toplevel, ["rev-parse", base]).stdout.strip()
            sent = _run(toplevel, ["update-ref", f"refs/heads/{base}", head, seen]).returncode == 0

        if sent:
            return ShipOutcome(
                kind="SENT",
                rebased=rebased_any,
                verify_state=verify_state,
                tries=tries,
                preship_ran=preship_ran,
            )

    return ShipOutcome(kind="RACE", rebased=rebased_any, verify_state=verify_state, tries=MAX_TRIES)
