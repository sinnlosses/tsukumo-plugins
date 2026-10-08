"""`tw ship`（作業ブランチの変更を主ブランチへ送り、送り終えたタスクの印を消す）と
`tw land`（作業先が別のリポジトリのタスクの枝を主ブランチへ合流し、その作業ツリーと枝を消す）。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable, NoReturn

import beads
import layout
import ledger
import ship
import tracker
import tw_base


def _release_own_claims_when_shipped(root: str, toplevel: str) -> list[str]:
    """4.4・5.8手順7: 自分の作業ツリーの印のうち、主ブランチ（HEAD）で done/dropped に
    なったものを消す。主ブランチを正として読む（`tw_base.load_tasks` は git show 経由なので、
    直前に送った変更もここで反映済みのものとして見える）。"""
    tasks, _invalid, _local_only = tw_base.load_tasks(toplevel)
    released: list[str] = []
    for tid in ledger.list_claims(root):
        owner = ledger.read_owner(ledger.claim_dir(root, tid))
        if owner is None or owner.get("worktree") != toplevel:
            continue
        t = tasks.get(tid)
        if t is not None and t.status in ("done", "dropped"):
            ledger.release_claim(root, tid, toplevel)
            ledger.clear_plan_base(root, tid)
            ledger.clear_open_claim(tid, cwd=toplevel)
            released.append(tid)
    return released


@dataclass(frozen=True)
class ShipHooks:
    """`ship` のうち、タスクの置き場（ファイル方式・Beads 方式）で変わる3点。

    `release_shipped`: 送り終えたあと自分の印を消し、消した ID を返す。
    `claimed_branch`: `claim` した時点の枝（作業ブランチから降りる先）。
    `after_send`: `SHIPPED`・`NOTHING` の行のあとに足す行（トラッカー・バックアップ）。
    """

    release_shipped: Callable[[], list[str]]
    claimed_branch: Callable[[str], "str | None"]
    after_send: Callable[[], list[str]]


def _file_ship_hooks(toplevel: str) -> ShipHooks:
    root = ledger.ledger_root_for_write(cwd=toplevel)
    return ShipHooks(
        release_shipped=lambda: _release_own_claims_when_shipped(root, toplevel),
        claimed_branch=lambda tid: (ledger.read_owner(ledger.claim_dir(root, tid)) or {}).get("branch"),
        after_send=lambda: [],
    )


def cmd_ship(toplevel: str, hooks: "ShipHooks | None" = None) -> None:
    if not ledger.is_clean(cwd=toplevel):
        print("DIRTY")
        raise SystemExit(4)

    def resolved_hooks() -> ShipHooks:
        """ファイル方式の hooks は台帳の置き場を作るので、送る道では `require_git_writable` のあとに作る。"""
        return hooks if hooks is not None else _file_ship_hooks(toplevel)

    branch_setting = layout.read_config(toplevel).branch
    base = ledger.base_branch(toplevel)
    branch = ledger.current_branch(cwd=toplevel)

    if branch == base:
        # 4.4: 主ブランチを出している作業ツリーで起こしたときは送る段が無い。
        local_hooks = resolved_hooks()
        ledger.clear_verify_owed(cwd=toplevel)
        released = local_hooks.release_shipped()
        _record_shipped(toplevel, released)
        print(f"SHIPPED\t{base}\t(送る段なし)\treleased={','.join(released) or '-'}")
        tw_base.print_lines(local_hooks.after_send())
        return

    ahead = tw_base.run_git(toplevel, ["rev-list", "--count", f"{base}..HEAD"])
    ahead_count = int(ahead.stdout.strip()) if ahead.returncode == 0 and ahead.stdout.strip().isdigit() else 0
    if ahead_count == 0:
        local_hooks = resolved_hooks()
        ledger.clear_verify_owed(cwd=toplevel)
        local_hooks.release_shipped()
        print(f"NOTHING\t({base} に無いコミットが無い)")
        tw_base.print_lines(local_hooks.after_send())
        return

    worktrees = ledger.list_worktrees(cwd=toplevel)
    base_worktree = ship.find_base_worktree(worktrees, toplevel, base)
    if base_worktree is not None and not ledger.is_clean(cwd=base_worktree.path):
        print(f"MAIN_DIRTY\t{base_worktree.path}")
        raise SystemExit(4)
    ledger.require_git_writable(toplevel)
    hooks = resolved_hooks()

    old_base = tw_base.run_git(toplevel, ["rev-parse", base]).stdout.strip()
    config = layout.read_config(toplevel)
    verify_command = config.verify
    verify_owed = ledger.is_verify_owed(cwd=toplevel)
    preship_command = config.verify_before_ship
    stamp = ledger.read_verify_stamp(cwd=toplevel)
    preship_stamped = (
        preship_command is not None
        and stamp is not None
        and stamp.verify_command == preship_command
        and stamp.tree == ledger.content_tree(toplevel)
    )
    outcome = ship.attempt(
        toplevel,
        base_worktree,
        verify_command,
        base,
        verify_owed=verify_owed,
        preship_command=preship_command,
        preship_stamped=preship_stamped,
    )

    if outcome.kind in ("CONFLICT", "VERIFY_FAILED", "RACE"):
        tw_base.record_claimed(toplevel, "ship", result=outcome.kind)
    if outcome.kind == "CONFLICT":
        print("CONFLICT\t" + (",".join(outcome.conflict_files) or "?"))
        raise SystemExit(7)
    if outcome.kind == "VERIFY_FAILED":
        # 付け替え済みのまま送っていない。次の `ship` は打ち直しでも検証を飛ばさない（T-777）。
        ledger.mark_verify_owed(outcome.verify_command or "", cwd=toplevel)
        print(f"VERIFY_FAILED\t{outcome.verify_command}\t{outcome.verify_log}")
        if outcome.verify_tail:
            print(outcome.verify_tail)
        raise SystemExit(8)
    if outcome.kind == "RACE":
        print("RACE\t3")
        raise SystemExit(9)

    # ここに来るのは `SENT` だけ（`attempt` は借りがあれば検証を通してからでないと
    # `SENT` を返さない）。送れたので借りは無い。
    ledger.clear_verify_owed(cwd=toplevel)

    branch_note = f"branch={branch}"
    if branch_setting in ("既定", "作業ブランチを切る") and FEATURE_BRANCH.fullmatch(branch):
        # 印を消す前に読む（戻り先は印にある）。
        branch_note = _leave_feature_branch(toplevel, branch, hooks.claimed_branch)

    released = hooks.release_shipped()
    _record_shipped(toplevel, released)
    new_base = tw_base.run_git(toplevel, ["rev-parse", base]).stdout.strip()
    preship_note = ""
    if preship_command is not None:
        preship_note = "\tpreship=ran" if outcome.preship_ran else "\tpreship=skipped"
    print(
        f"SHIPPED\t{old_base}..{new_base}\trebased={'yes' if outcome.rebased else 'no'}"
        f"\tverify={outcome.verify_state}{preship_note}\ttries={outcome.tries}\treleased={','.join(released) or '-'}"
        f"\t{branch_note}"
    )
    tw_base.print_lines(hooks.after_send())


def _record_shipped(toplevel: str, released: list[str]) -> None:
    for task_id in released:
        tw_base.record(toplevel, "ship", task_id, result="SHIPPED")


def cmd_land(toplevel: str, branch: str) -> None:
    """`branch` を主ブランチへ ff-only で合流し、主ブランチに入ったことを確かめてから、その枝の作業ツリーと枝を消す。

    合流は主ブランチを出している作業ツリーで打つ。合流できない・入っていない・どちらかの作業ツリーが汚れているか無いときは
    何も消さずに `NOT_LANDED`。合流のあとに消せなかったものは `NOT_REMOVED` の行で残りを知らせる。
    """
    base = ledger.base_branch(toplevel)
    known = tw_base.run_git(toplevel, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0
    if branch == base or not known:
        print(f"usage: land <枝>（主ブランチ {base} 以外の、在る枝を渡す）: {branch}", file=sys.stderr)
        raise SystemExit(2)
    worktrees = ledger.list_worktrees(toplevel)
    base_tree = next((w.path for w in worktrees if w.branch == base), None)
    if base_tree is None:
        _refuse_land(branch, f"{base} を出している作業ツリーが無い")
    branch_trees = [w.path for w in worktrees if w.branch == branch]
    missing = [path for path in [base_tree, *branch_trees] if not os.path.isdir(path)]
    if missing:
        _refuse_land(branch, f"作業ツリーのディレクトリが無い: {','.join(missing)}")
    if not ledger.is_clean(base_tree):
        _refuse_land(branch, f"{base} を出している作業ツリーに未コミットの変更がある: {base_tree}")
    dirty = [path for path in branch_trees if not ledger.is_clean(path)]
    if dirty:
        _refuse_land(branch, f"作業ツリーに未コミットの変更がある: {','.join(dirty)}")

    ledger.require_git_writable(base_tree)
    merged = tw_base.run_git(base_tree, ["merge", "--ff-only", "--quiet", branch])
    if merged.returncode != 0:
        _refuse_land(branch, f"{base} へ ff-only で合流できない: {_first_line(merged.stderr)}")
    if tw_base.run_git(base_tree, ["merge-base", "--is-ancestor", branch, base]).returncode != 0:
        _refuse_land(branch, f"{base} に入っていない")
    print(f"LANDED\t{branch}\t{base}\t{tw_base.run_git(base_tree, ['rev-parse', base]).stdout.strip()}")

    leftovers: list[tuple[str, str]] = []
    for path in branch_trees:
        removed = tw_base.run_git(base_tree, ["worktree", "remove", path])
        if removed.returncode == 0:
            print(f"REMOVED\t{path}")
        else:
            leftovers.append((path, _first_line(removed.stderr)))
    deleted = tw_base.run_git(base_tree, ["branch", "-d", branch])
    if deleted.returncode == 0:
        print(f"DELETED\t{branch}")
    else:
        leftovers.append((branch, _first_line(deleted.stderr)))
    for name, reason in leftovers:
        print(f"NOT_REMOVED\t{name}\t{reason}")
    if leftovers:
        raise SystemExit(4)


def _refuse_land(branch: str, reason: str) -> NoReturn:
    print(f"NOT_LANDED\t{branch}\t{reason}")
    raise SystemExit(4)


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


FEATURE_BRANCH = layout.FEATURE_BRANCH_PATTERN


def _leave_feature_branch(toplevel: str, branch: str, claimed_branch: Callable[[str], "str | None"]) -> str:
    """送り終えた `feature/T-xxx` から降りて枝を消す（6.2手順7）。出力の `branch=…` 欄を返す。

    戻り先は `claim` した時点の枝（`claimed_branch`。ファイル方式は印の owner の `branch=`）→ 主ブランチの順に試す。主ブランチを
    別の作業ツリー（本体）が出していると `checkout` は通らないので、作業ツリー固有の枝が
    あればそこへ戻して主ブランチまで追い付かせる。どちらにも移れなければ主ブランチの位置で
    detached HEAD にする（枝を黙って残さない。detached のままでも次の `claim` は主ブランチから切る）。
    消せなかったときは `kept=<枝>` を添えて知らせる。
    """
    base = ledger.base_branch(toplevel)
    m = FEATURE_BRANCH.fullmatch(branch)
    back = claimed_branch(m.group(1)) if m else None
    targets = [b for b in dict.fromkeys([back, base]) if b and b not in (branch, "HEAD")]

    landed = None
    for target in targets:
        if tw_base.run_git(toplevel, ["checkout", "-q", target]).returncode == 0:
            if target != base:
                tw_base.run_git(toplevel, ["merge", "--ff-only", "-q", base])
            landed = target
            break
    if landed is None:
        if tw_base.run_git(toplevel, ["checkout", "-q", "--detach", base]).returncode != 0:
            return f"branch={branch}\tkept={branch}"
        landed = "detached"

    if tw_base.run_git(toplevel, ["branch", "-d", branch]).returncode != 0:
        return f"branch={landed}\tkept={branch}"
    return f"branch={landed}"


def beads_ship_hooks(toplevel: str) -> ShipHooks:
    actor = tw_base.actor(toplevel)
    not_closed: list[str] = []

    def release_shipped() -> list[str]:
        closed: list[str] = []
        for issue in beads.list_issues(toplevel):
            mark = beads.ship_mark(issue)
            if issue.status != "in_progress" or issue.assignee != actor or mark is None:
                continue
            reason = "cancelled" if mark == "dropped" else "done"
            r = beads.run(toplevel, ["close", issue.bd_id, "--reason", reason], actor)
            if r.returncode != 0:
                not_closed.append(f"NOT_CLOSED\t{beads.to_task_id(issue.bd_id)}\t{(r.stderr or r.stdout).strip()}")
                continue
            update = ["update", issue.bd_id, "--remove-label", beads.SHIP_LABELS[mark]]
            if mark == "dropped":
                update += ["--add-label", beads.CANCELLED_LABEL]
            if issue.external_ref and tracker.read_tracker(toplevel).kind == "jira":
                update += ["--add-label", beads.JIRA_CLOSE_LABEL]
            beads.run_ok(toplevel, update, actor)
            ledger.clear_open_claim(beads.to_task_id(issue.bd_id), cwd=toplevel)
            closed.append(beads.to_task_id(issue.bd_id))
        return closed

    def claimed_branch(task_id: str) -> str | None:
        issue = beads.show(toplevel, beads.to_bd_id(task_id))
        metadata = issue.raw.get("metadata") if issue is not None else None
        return metadata.get(beads.CLAIM_BRANCH_KEY) if isinstance(metadata, dict) else None

    def after_send() -> list[str]:
        return not_closed + tracker.session(toplevel).sync_all(pull=False) + beads.backup(toplevel)

    return ShipHooks(release_shipped, claimed_branch, after_send)
