"""着手の印の控え（`beads.write_claim_snapshot`）からの復元（`tracker` が呼ぶ）。

GitHub からの取り込みが消した着手の印（status・assignee・metadata）を、控えと送りの印を見て Beads へ戻す。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import beads
from tracker_github import _loads, _ref_number, _tail

SYNCED_KEY = "task-workflow.github-synced"  # {Issue 番号: その課題を最後に送った・取り込んだ時刻}（CONFLICT の判定）
# 課題ごとのキー `<これ><Issue 番号>`。値は最後に届いた送りの直前に読んだその課題の着手の時刻
# （`started_at`。着手中でなければ空）。この値は JSON でない。
PUSHED_KEY_PREFIX = "task-workflow.github-pushed."


def _claim_restore_args(
    was: beads.Issue, issue: beads.Issue, pushed_claim: str | None, legacy_synced_at: datetime | None
) -> list[str]:
    """取り込みが `was` を `issue` へ上書きして消した着手の印（status・assignee と `beads.MARK_KEYS` の
    metadata）を戻す `bd update` の引数。戻すものが無ければ空。

    `pushed_claim` はこの課題の送りの印（最後に届いた送りが運んだ着手の時刻。空なら着手なし）。印の無い
    課題（印を書く前に送ったもの）は、`legacy_synced_at`（最後に送った・取り込んだ時刻）が着手より前なら
    送りが届いていないとみなす。

    `was` は取り込みの前に読んだ一覧ではなく、取り込みが書く直前の版
    （取り込みの最中に別の作業ツリーが着手すると、前に読んだ一覧にはその着手が無い）。
    GitHub で手放された着手（担当者が付いた、または着手を送ったあとで label `status::in_progress` が
    外れて `open` に戻った）は戻さない。着手を送る前の GitHub の版で `open` に戻ったものは戻す。
    """
    had, has = beads.mark_values(was), beads.mark_values(issue)
    if issue.status == "closed":
        return [
            x
            for k, v in had.items()
            if k in (beads.DIFFICULTY_KEY, beads.LOOPABLE_KEY, beads.DRAFT_KEY, beads.FROM_DRAFT_KEY) and k not in has
            for x in ("--set-metadata", f"{k}={v}")
        ]
    args: list[str] = []
    claimed = was.status == "in_progress" and bool(was.assignee)
    if claimed and not issue.assignee:
        started = beads.parse_time(was.started_at)
        if pushed_claim is not None:
            sent = beads.parse_time(pushed_claim)
            unsent = started is not None and (
                sent is None or sent.replace(microsecond=0) != started.replace(microsecond=0)
            )
        else:
            unsent = started is not None and (
                legacy_synced_at is None or legacy_synced_at < started.replace(microsecond=0)
            )
        if issue.status == "in_progress":
            args += ["--assignee", was.assignee]
        elif issue.status == "open" and unsent:
            args += ["--status", "in_progress", "--assignee", was.assignee]
    args += [x for k, v in had.items() if k not in has for x in ("--set-metadata", f"{k}={v}")]
    return args


def _claim_started(issue: beads.Issue | None) -> str:
    """送りの印に書く値: 着手中（`in_progress` で担当者あり）ならその `started_at`、ほかは空。"""
    if issue is None or issue.status != "in_progress" or not issue.assignee:
        return ""
    return issue.started_at or ""


def _restore_from_snapshots(
    toplevel: str, listed: dict, issues: Callable[[], list[beads.Issue]], bd_ids: list[str] | None, actor: str
) -> tuple[list[str], list[str]]:
    """`restore_claims` の中身。`listed` は `beads.read_kv` の結果（控え・送りの印・同期の時刻を読む）。
    `issues` は今の課題の一覧を返す（控えのある課題を見るときだけ呼ぶ）。

    戻すかの判定は `_claim_restore_args` に控えの版を渡して決める。担当者を戻さない（閉じた・GitHub で
    手放された・別の作業ツリーが取った）課題は、metadata だけを戻さず、控えを消す。戻したら控えを今の版で書き直す
    （戻しで `started_at` が変わると、送りの印との比べが狂う）。
    """
    snapshots = beads.claim_snapshots(listed)
    wanted = [i for i in snapshots if bd_ids is None or i in bd_ids]
    if not wanted:
        return [], []
    pushed, synced = _pushed_of(listed), _synced_of(listed)
    current = {i.bd_id: i for i in issues()}
    lines: list[str] = []
    restored: list[str] = []
    for bd_id in wanted:
        was, issue = snapshots[bd_id], current.get(bd_id)
        if issue is None:
            beads.clear_claim_snapshot(toplevel, bd_id)
            continue
        n = _ref_number(issue.external_ref)
        args = _claim_restore_args(
            was, issue, pushed.get(n) if n is not None else None,
            beads.parse_time(synced.get(str(n))) if n is not None else None,
        )
        holds = issue.status == "in_progress" and issue.assignee == was.assignee
        if not holds and "--assignee" not in args:
            beads.clear_claim_snapshot(toplevel, bd_id)
            continue
        if not args:
            continue
        r = beads.run(toplevel, ["update", bd_id, *args], actor)
        if r.returncode != 0:
            lines.append(f"TRACKER\tFAILED\tgithub\t{beads.to_task_id(bd_id)} の着手の印を控えから戻せない: {_tail(r)}")
            continue
        again = beads.show(toplevel, bd_id)
        if again is not None:
            beads.write_claim_snapshot(toplevel, again)
        lines.append(f"TRACKER\tRESTORED\t{beads.to_task_id(bd_id)}")
        restored.append(bd_id)
    return lines, restored


def _pushed_of(listed: dict) -> dict[int, str]:
    return {
        int(k[len(PUSHED_KEY_PREFIX):]): v for k, v in listed.items()
        if k.startswith(PUSHED_KEY_PREFIX) and k[len(PUSHED_KEY_PREFIX):].isdigit() and isinstance(v, str)
    }


def _synced_of(listed: dict) -> dict:
    raw = listed.get(SYNCED_KEY)
    value = _loads(raw) if isinstance(raw, str) else None
    return value if isinstance(value, dict) else {}
