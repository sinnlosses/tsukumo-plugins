"""`tw work-repo-guard`: Claude Code の `PreToolUse` hook の入力（stdin の JSON）を読み、`Edit`・`Write`・`NotebookEdit` の書き先が、
着手中のタスクの `### 作業先`（別のリポジトリ）の本体の作業ツリーの中なら拒む。タスクや作業ツリーが引けないときは通す。
拒むときだけ stdout に JSON を1行出し、終了コードはいつも0。
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import IO

import agent_scope
import beads
import gitinfo
import ledger
import taskfile
import tw_handback

WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
RULE = "WORK_REPO_MAIN_TREE"


def run(stdin: IO[str], stdout: IO[str]) -> None:
    try:
        payload = json.loads(stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    reason = decide(payload)
    if reason is None:
        return
    agent_scope.record_denial(payload, "tw-work-repo-guard", RULE)
    stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def decide(payload: dict) -> str | None:
    if payload.get("hook_event_name") != "PreToolUse" or payload.get("tool_name") not in WRITE_TOOLS:
        return None
    cwd = payload.get("cwd")
    tool_input = payload.get("tool_input")
    if not isinstance(cwd, str) or not os.path.isdir(cwd) or not isinstance(tool_input, dict):
        return None
    path = tool_input.get("notebook_path" if payload.get("tool_name") == "NotebookEdit" else "file_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        target = (pathlib.Path(cwd) / path).resolve()
        toplevel = gitinfo.git_toplevel(cwd)
        for task_id in ledger.open_claims(cwd=cwd):
            reason = _refusal(toplevel, task_id, target)
            if reason is not None:
                return reason
    except (beads.BeadsError, gitinfo.GitCommandError, OSError, RuntimeError, ValueError):
        return None
    return None


def _refusal(toplevel: str, task_id: str, target: pathlib.Path) -> str | None:
    issue = beads.show(toplevel, beads.to_bd_id(task_id))
    if issue is None:
        return None
    work_repo, _ = taskfile.plan_work_repo(f"{taskfile.PLAN_HEADING}\n{issue.raw.get('notes') or ''}\n")
    if work_repo is None or not os.path.isdir(work_repo):
        return None
    if gitinfo.git_common_dir(work_repo) == gitinfo.git_common_dir(toplevel):
        return None
    main_tree = pathlib.Path(work_repo).resolve()
    if not target.is_relative_to(main_tree):
        return None
    branch_trees = tw_handback.task_trees(toplevel, task_id)[1:]
    if any(target.is_relative_to(pathlib.Path(tree).resolve()) for tree in branch_trees):
        return None
    branch = task_id.lower()
    return (
        f"{RULE}: 書き先 {target} は {task_id} の作業先の本体の作業ツリー {main_tree} の中です。"
        f"枝 {branch} の作業ツリーで書いてください。無ければ作ります: "
        f"git -C {main_tree} worktree add <{main_tree.name} の外のパス> -b {branch}"
    )
