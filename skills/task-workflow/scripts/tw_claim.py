"""`tw claim`・`release`・`done`（着手の印を立てる・外す・閉じる）。"""

from __future__ import annotations

import os
import sys

import beads
import layout
import ledger
import taskfile
import tracker
import tw_base
import tw_plan


REFLECTION_SKIPPED_LINE = "- 振り返り: 近道（省いた）"


def cmd_claim(toplevel: str, task_id: str) -> None:
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    branch_setting, base = _claim_preflight(toplevel)

    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None:
        print(f"NOT_READY\t{shown}\t存在しない")
        raise SystemExit(4)
    task, err = beads.to_task(issue)
    if err is not None:
        print(f"INVALID\t{err}")
        raise SystemExit(3)
    if task is None:
        print(f"NOT_READY\t{shown}\tTRIAGE")
        raise SystemExit(4)
    if issue.status == "in_progress":
        _print_taken(shown, issue)
    if task.status != "todo":
        print(f"NOT_READY\t{shown}\t{task.status}")
        raise SystemExit(4)
    blocked = [beads.to_task_id(d) for d in issue.dependencies if not _resolved(toplevel, d)]
    if blocked:
        print(f"NOT_READY\t{shown}\tBLOCKED:{','.join(blocked)}")
        raise SystemExit(4)

    # `bd update --claim` は依存を見ないので、上で依存を確かめてから取る。取り合いの勝ち負けは Beads が決める。
    actor = tw_base.actor(toplevel)
    branch_after_sync = ledger.current_branch(cwd=toplevel)
    claim_args = ["update", bd_id, "--claim", "--set-metadata", f"{beads.CLAIM_BRANCH_KEY}={branch_after_sync}"]
    head = ledger.head_sha_or_none(cwd=toplevel)
    if head is not None:
        claim_args += ["--set-metadata", f"{beads.CLAIM_HEAD_KEY}={head}"]
    metadata = issue.raw.get("metadata")
    plan_base = metadata.get(beads.PLAN_BASE_KEY) if isinstance(metadata, dict) else None
    plan = str(issue.raw.get("notes") or "")
    plan_body = f"{taskfile.PLAN_HEADING}\n{plan}\n"
    registered = False
    if plan_base and not taskfile.is_blank(plan):
        tip = tw_plan.plan_tip(toplevel, plan_body)
        if tip is not None:
            claim_args += ["--set-metadata", f"{beads.PLAN_TIP_KEY}={tip}"]
        if tw_plan.registered_plan_changes(toplevel, plan_base, tip, plan_body) == []:
            claim_args += ["--set-metadata", f"{beads.PLAN_KEY}={tw_plan.PLAN_REGISTERED}"]
            registered = True
        else:
            claim_args += ["--unset-metadata", beads.PLAN_KEY]
    r = beads.run(toplevel, claim_args, actor)
    if r.returncode != 0:
        again = beads.show(toplevel, bd_id)
        if again is not None and again.status == "in_progress":
            _print_taken(shown, again)
        raise beads.BeadsError(f"bd update --claim が失敗: {(r.stderr or r.stdout).strip()}")
    ledger.mark_open_claim(shown, cwd=toplevel)
    tw_base.record(toplevel, "claim", shown, issue)
    _claim_branch_out(
        toplevel, shown, branch_setting, base, branch_after_sync, f"beads:{bd_id}",
        tw_plan.direct_column(task.direct, task.difficulty, plan_body, registered),
    )
    tw_base.print_lines(pulled + trk.after([bd_id]))


def cmd_release(toplevel: str, task_id: str, force: bool) -> None:
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None or issue.status != "in_progress":
        print(f"NOT_CLAIMED\t{shown}")
        tw_base.print_lines(pulled)
        return
    actor = tw_base.actor(toplevel)
    if not force and issue.assignee != actor:
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)
    beads.run_ok(toplevel, ["unclaim", bd_id] + (["--force"] if force else []), actor)
    marks = [v for v in beads.SHIP_LABELS.values() if v in issue.labels]
    if marks:
        beads.run_ok(toplevel, ["update", bd_id] + [x for m in marks for x in ("--remove-label", m)], actor)
    owner_paths = [w.path for w in ledger.list_worktrees(cwd=toplevel) if os.path.basename(w.path) == issue.assignee]
    for path in owner_paths:
        _clear_open_claim_in(path, shown)
    print(f"RELEASED\t{shown}")
    tw_base.record(toplevel, "release", shown, issue)
    tw_base.print_lines(pulled + trk.after([bd_id]))


def cmd_done(toplevel: str, task_id: str, dropped: bool, result_path: str) -> None:
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    actor = tw_base.actor(toplevel)
    result = tw_base.read_body(result_path).strip()
    if result == "":
        print("usage: --result-file の中身が空", file=sys.stderr)
        raise SystemExit(2)
    trk = tracker.session(toplevel)
    pulled = trk.before([bd_id])
    issue = beads.show(toplevel, bd_id)
    if issue is None or issue.status != "in_progress" or issue.assignee != actor:
        print(f"NOT_OWNER\t{shown}")
        tw_base.print_lines(pulled)
        raise SystemExit(4)
    kind = "dropped" if dropped else "done"
    beads.run_ok(toplevel, ["comment", bd_id, "--stdin"], actor, f"{beads.RESULT_HEADING}\n\n{result}\n")
    other = beads.SHIP_LABELS["done" if dropped else "dropped"]
    beads.run_ok(
        toplevel, ["update", bd_id, "--add-label", beads.SHIP_LABELS[kind], "--remove-label", other], actor
    )
    print(f"DONE\t{shown}\tbeads:{bd_id}\tship で閉じる")
    tw_base.record(toplevel, "done", shown, issue, dropped=dropped, reflection=_reflection_of(result))
    ledger.clear_open_claim(shown, cwd=toplevel)
    metadata = issue.raw.get("metadata")
    head = metadata.get(beads.CLAIM_HEAD_KEY) if isinstance(metadata, dict) else None
    _print_commits_since_claim(toplevel, shown, head)
    tw_base.print_lines(pulled + trk.after([bd_id]))


def _claim_preflight(toplevel: str) -> tuple[str, str]:
    """`claim` の git の前提（`- ブランチ:` が読める・clean・未送りなし・主ブランチへ追い付く）。

    `(ブランチの設定の先頭語, 主ブランチ)` を返す。前提を欠けば出力して `SystemExit`。
    追い付くか枝を切るのに `.git` へ書けなければ、印を立てる前に `ledger.GitReadOnly`。
    """
    branch_setting = layout.read_config(toplevel).branch
    if branch_setting not in layout.BRANCH_VALUES:
        print(f"INVALID\t- ブランチ: の値 {branch_setting!r} を機械が読めない")
        raise SystemExit(3)

    if not ledger.is_clean(cwd=toplevel):
        print("DIRTY")
        raise SystemExit(4)

    base = ledger.base_branch(toplevel)
    branch = ledger.current_branch(cwd=toplevel)
    if branch != base:
        ahead = tw_base.run_git(toplevel, ["rev-list", "--count", f"{base}..HEAD"])
        if ahead.returncode == 0 and ahead.stdout.strip() not in ("0", ""):
            print(f"UNSHIPPED\t{ahead.stdout.strip()}")
            raise SystemExit(4)
        # 追い付き済みでも `merge` は ORIG_HEAD を書くので、要るときだけ打つ。
        if tw_base.run_git(toplevel, ["merge-base", "--is-ancestor", base, "HEAD"]).returncode != 0:
            ledger.require_git_writable(toplevel)
            r = tw_base.run_git(toplevel, ["merge", "--ff-only", base])
            if r.returncode != 0:
                print(f"INVALID\t{base} へ追い付けない（{r.stderr.strip()}）")
                raise SystemExit(3)
    if branch_setting in ("既定", "作業ブランチを切る"):
        ledger.require_git_writable(toplevel)
    return branch_setting, base


def _claim_branch_out(
    toplevel: str, task_id: str, branch_setting: str, base: str, branch_after_sync: str, where: str, direct: str = ""
) -> None:
    """印を立てたあと、設定なら作業ブランチを切って `CLAIMED` を出す（`where` は3列目、`direct` は行末の列）。"""
    if branch_setting in ("既定", "作業ブランチを切る"):
        feature_branch = f"{layout.FEATURE_BRANCH_PREFIX}{task_id}"
        r = tw_base.run_git(toplevel, ["checkout", "-b", feature_branch, base])
        if r.returncode != 0:
            print(f"CLAIMED\t{task_id}\t{where}\tbranch=(切れない: {r.stderr.strip()}){direct}")
            return
        print(f"CLAIMED\t{task_id}\t{where}\tbranch={feature_branch}{direct}")
    else:
        print(f"CLAIMED\t{task_id}\t{where}\tbranch={branch_after_sync}{direct}")


def _print_taken(shown: str, issue: beads.Issue) -> None:
    age = tw_base.age_seconds(issue.started_at)
    print(f"TAKEN\t{shown}\t{issue.assignee or '?'}\t{tw_base.format_elapsed(age) if age is not None else '?'}")
    raise SystemExit(4)


def _resolved(toplevel: str, bd_id: str) -> bool:
    dep = beads.show(toplevel, bd_id)
    return dep is None or dep.status == "closed"


def _clear_open_claim_in(worktree: str, task_id: str) -> None:
    """`worktree` の作業ツリー固有の控えを消す。作業ツリーが消えていれば何もしない。"""
    if not os.path.isdir(worktree):
        return
    try:
        ledger.clear_open_claim(task_id, cwd=worktree)
    except ledger.GitCommandError:
        return


def _reflection_of(result: str) -> str:
    """`## 結果` の `- 振り返り:` の行が `none`（兆候なし）・`skipped`（近道で省いた）・`some`・`unknown`（行が無い）。"""
    for line in result.splitlines():
        if line.startswith("- 振り返り:"):
            if line.strip() == REFLECTION_SKIPPED_LINE:
                return "skipped"
            return "none" if "兆候なし" in line else "some"
    return "unknown"


def _print_commits_since_claim(toplevel: str, shown_id: str, head: str | None) -> None:
    """claim から今までに委譲先が作ったコミットがあれば `COMMITS_SINCE_CLAIM` の行で知らせる。

    `next-task` の手順6（受け入れる）向けの合図で、`DONE` の判定・終了コードは変えない。
    """
    commits = tw_plan.commits_since_claim(toplevel, head)
    if commits:
        print(f"COMMITS_SINCE_CLAIM\t{shown_id}\t{','.join(commits)}")
