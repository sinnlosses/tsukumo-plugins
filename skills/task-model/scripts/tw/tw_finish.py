"""`tw finish`: `tw done`・コミット・`tw ship` を1回で打つ。

各部品の出力の行はそのまま並べ、止まる行（`tw done` の `NOT_OWNER`・`ALREADY_DONE` と `COMMITS_SINCE_CLAIM`、
`tw ship` の `SHIPPED`・`NOTHING` 以外）が出たら以降を打たない。
最初に、`--dropped` でなければ、作業先が別のリポジトリでその枝が合流していないとき `WORK_NOT_LANDED`（終了コード4）で何も書かずに止まる
（登録簿の `land` が `branch`・`pr` なら、枝の先端が metadata `work_landed` の SHA と同じでないとき。それ以外は主ブランチに入っていないとき）。
続けて、並びの `accept` より後ろに独自の段があり、最後の `claim` 以降に受け入れの終わりの控え（出来事 `accepted`）があれば、
着手した作業ツリーと（あれば）作業先の枝の作業ツリーの今の `(HEAD, 中身の木)` を控えと比べ、違えば `CHANGED_AFTER_ACCEPT`（終了コード4）で何も書かずに止まる。コミットは対象のファイルを個別に `git add` した1つだけで、
差分が無ければ打たない。対象は `--add` で渡したファイル。渡さなければ作業ツリーの変更のうち通常のファイルで、`.tw/local`・symlink・サブモジュールは除く。
件名の `T-xxx:` は tw が付ける。設定 `commit_task_id = "付けない"` なら付けず、`--message` の先頭の `T-xxx:` も外す。
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from datetime import datetime, timezone

import beads
import gitinfo
import layout
import ledger
import metrics
import tw_accept
import tw_base
import tw_brief
import tw_claim
import tw_config
import tw_handback
import tw_plan
import tw_ship

STRAY_COMMITS_EXIT = 12


def cmd_finish(
    toplevel: str,
    task_id: str,
    dropped: bool,
    result_path: str | None,
    message: str,
    files: list[str],
    reflection: str | None = None,
    resume: bool = False,
) -> None:
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(beads.to_bd_id(task_id))
    marked = _my_ship_mark(toplevel, bd_id) if resume else None
    if not dropped:
        plan_body = tw_handback._claimed_plan_body(toplevel, shown)
        unlanded = tw_brief.unlanded_work_branch(toplevel, shown, plan_body)
        if unlanded is not None:
            print(f"WORK_NOT_LANDED\t{shown}\t{unlanded[0]}\t{unlanded[1]}")
            raise SystemExit(4)
        if marked is None:
            changed = _changed_after_accept(toplevel, shown, plan_body)
            if changed is not None:
                print(f"CHANGED_AFTER_ACCEPT\t{shown}\t{changed[0]}\t{changed[1]}")
                raise SystemExit(4)
    if marked is not None:
        print(f"RESUMED\t{shown}\t{marked}")
    else:
        if result_path is None:
            print("usage: --result-file が要る（--resume は done 済みのタスクだけで省ける）", file=sys.stderr)
            raise SystemExit(2)
        tw_handback.close_open_lap(toplevel, bd_id, "retro")
        tw_handback.close_open_lap(toplevel, bd_id, "accept")
        if _done_found_stray_commits(toplevel, task_id, dropped, result_path, reflection):
            raise SystemExit(STRAY_COMMITS_EXIT)
    with_id = layout.read_config(toplevel).commit_task_id == layout.COMMIT_TASK_ID_ON
    committed = _commit(toplevel, _subject(shown, message, with_id), files)
    if committed is not None:
        _warn_fix_links(toplevel, shown, committed)
    tw_ship.cmd_ship(toplevel)
    events, _ = metrics.read_events(ledger.ledger_root(cwd=toplevel))
    for stage in metrics.open_stages(events, shown):
        print(f"OPEN_SPAN\t{shown}\t{stage}")


def _changed_after_accept(toplevel: str, shown: str, plan_body: str) -> tuple[str, str] | None:
    """受け入れより後ろの独自の段が作業ツリーを変えていれば `(作業ツリーのパス, head|content)`。

    並びの `accept` より後ろに組み込みでない段が無い・最後の `claim` 以降に `accepted` の控えが無いときは `None`。
    作業先が別のリポジトリなら、枝（タスクIDの小文字）の作業ツリーが残っているときだけそちらも比べる。
    """
    steps = tw_config.script_steps(toplevel, layout.read_config(toplevel))
    builtin = {c.value for c in layout.script_step_candidates()}
    if "accept" not in steps or all(s in builtin for s in steps[steps.index("accept") + 1 :]):
        return None
    events, _ = metrics.read_events(ledger.ledger_root(cwd=toplevel))
    own = [e for e in events if e.task == shown]
    claims = [i for i, e in enumerate(own) if e.kind == "claim"]
    accepted = [e for e in (own[claims[-1] :] if claims else own) if e.kind == tw_accept.ACCEPTED_EVENT]
    if not accepted:
        return None
    fields = accepted[-1].fields
    targets = [(toplevel, fields.get("head"), fields.get("tree"))]
    root = tw_plan.other_repo_root(toplevel, plan_body)
    if root is not None:
        trees = [w.path for w in gitinfo.list_worktrees(cwd=root) if w.branch == shown.lower()]
        if trees:
            targets.append((trees[0], fields.get("work_head"), fields.get("work_tree")))
    for path, head, tree in targets:
        now_head, now_tree = tw_accept.snapshot(path)
        if now_head != head:
            return path, "head"
        if now_tree != tree:
            return path, "content"
    return None


def _my_ship_mark(toplevel: str, bd_id: str) -> str | None:
    """自分が着手中で `done`・`dropped` 済みなら、その印（`done`／`dropped`）。"""
    issue = beads.show(toplevel, bd_id)
    if issue is None or issue.status != "in_progress" or issue.assignee != tw_base.actor(toplevel):
        return None
    return beads.ship_mark(issue)


def _subject(shown: str, message: str, with_id: bool) -> str:
    """件名の先頭に `<shown>: ` を一度だけ付ける（旧形式の `<shown>: …` はそのまま受ける）。`with_id` が偽なら外すだけ。"""
    body = re.sub(rf"^\s*{re.escape(shown)}\s*[:：]\s*", "", message, count=1, flags=re.IGNORECASE)
    return f"{shown}: {body}" if with_id else body


def _done_found_stray_commits(
    toplevel: str, task_id: str, dropped: bool, result_path: str, reflection: str | None
) -> bool:
    """`tw done` の出力をそのまま出し、`COMMITS_SINCE_CLAIM` の行があれば `True`。

    `tw done` は済んでいる。続きは `tw finish --resume` で打つ。
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            tw_claim.cmd_done(toplevel, task_id, dropped, result_path, reflection)
    finally:
        sys.stdout.write(buffer.getvalue())
        sys.stdout.flush()
    return any(line.startswith("COMMITS_SINCE_CLAIM\t") for line in buffer.getvalue().splitlines())


_ID_IN_TEXT = re.compile(r"\b(?:T-\d{3,}|GH-\d+|[A-Z][A-Z0-9_]+-\d+)\b", re.IGNORECASE)
_REVERT_WORDS = ("戻す", "撤去", "revert")
_REVERTS_SHA = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


def _recent_shipped(toplevel: str, own: str) -> set[str]:
    """直近7日に `SHIPPED` したタスクの見せる ID（自分を除く）。"""
    events, _ = metrics.read_events(ledger.ledger_root(cwd=toplevel))
    now = datetime.now(timezone.utc)
    return {
        e.task.upper()
        for e in events
        if e.kind == "ship" and e.fields.get("result") == "SHIPPED" and now - metrics.FIX_WINDOW <= e.at <= now
    } - {own.upper()}


def _warn_fix_links(toplevel: str, shown: str, sha: str) -> None:
    """finish のコミットが直近7日に送ったタスクを指していれば `WARN_FIX_LINK\\t<ID>` を出す（拒まない・結ばない）。"""
    recent = _recent_shipped(toplevel, shown)
    if not recent:
        return
    text = tw_base.run_git(toplevel, ["log", "-1", "--format=%B", sha]).stdout
    found = {m.upper() for m in _ID_IN_TEXT.findall(text)}
    subject = text.splitlines()[0] if text else ""
    if any(w in subject.lower() for w in _REVERT_WORDS):
        for target in _REVERTS_SHA.findall(text):
            head = tw_base.run_git(toplevel, ["show", "-s", "--format=%s", target]).stdout
            found.update(m.upper() for m in _ID_IN_TEXT.findall(head.split(":", 1)[0]))
    for task in sorted(found & recent):
        print(f"WARN_FIX_LINK\t{task}")


def _commit(toplevel: str, message: str, files: list[str]) -> str | None:
    """コミットを作ったらその sha、差分が無ければ `None`。"""
    targets = files
    if not files:
        submodules = tw_base.submodule_paths(toplevel)
        for path in sorted(submodules):
            tw_base.run_git(toplevel, ["reset", "-q", "--", path])
        targets, skipped = tw_base.split_dirty(toplevel, submodules)
        for path, kind in skipped:
            print(f"SKIPPED\t{path}\t{kind}")
            tw_base.run_git(toplevel, ["reset", "-q", "--", path])
    for path in targets:
        _git_ok(toplevel, ["add", "--", path])
    sha = None
    if tw_base.run_git(toplevel, ["diff", "--cached", "--quiet"]).returncode == 0:
        print("NO_COMMIT\t(差分が無い)")
    else:
        _git_ok(toplevel, ["commit", "-q", "-m", message])
        sha = tw_base.run_git(toplevel, ["rev-parse", "HEAD"]).stdout.strip()
        print(f"COMMITTED\t{sha}")
    for path in gitinfo.dirty_paths(cwd=toplevel):
        if not tw_base.is_local(path):
            print(f"UNADDED\t{path}")
    return sha


def _git_ok(toplevel: str, args: list[str]) -> None:
    r = tw_base.run_git(toplevel, args)
    if r.returncode != 0:
        print(f"git {args[0]} が失敗した: {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)
        raise SystemExit(1)
