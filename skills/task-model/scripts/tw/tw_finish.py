"""`tw finish`: `tw done`・コミット・`tw ship` を1回で打つ。

各部品の出力の行はそのまま並べ、止まる行（`tw done` の `NOT_OWNER`・`ALREADY_DONE` と `COMMITS_SINCE_CLAIM`、
`tw ship` の `SHIPPED`・`NOTHING` 以外）が出たら以降を打たない。コミットは対象のファイルを個別に `git add` した1つだけで、
差分が無ければ打たない。対象は `--add` で渡したファイル。渡さなければ作業ツリーの変更のうち通常のファイルで、`.tw/local`・symlink・サブモジュールは除く。
件名の `T-xxx:` は tw が付ける。
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys

import beads
import gitinfo
import ledger
import metrics
import tw_base
import tw_claim
import tw_handback
import tw_ship

STRAY_COMMITS_EXIT = 12
LOCAL_DIR = ".tw/local"


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
    if marked is not None:
        print(f"RESUMED\t{shown}\t{marked}")
    else:
        if result_path is None:
            print("usage: --result-file が要る（--resume は done 済みのタスクだけで省ける）", file=sys.stderr)
            raise SystemExit(2)
        tw_handback.close_open_lap(toplevel, bd_id, "retro")
        if _done_found_stray_commits(toplevel, task_id, dropped, result_path, reflection):
            raise SystemExit(STRAY_COMMITS_EXIT)
    _commit(toplevel, _subject(shown, message), files)
    tw_ship.cmd_ship(toplevel)
    events, _ = metrics.read_events(ledger.ledger_root(cwd=toplevel))
    for stage in metrics.open_stages(events, shown):
        print(f"OPEN_SPAN\t{shown}\t{stage}")


def _my_ship_mark(toplevel: str, bd_id: str) -> str | None:
    """自分が着手中で `done`・`dropped` 済みなら、その印（`done`／`dropped`）。"""
    issue = beads.show(toplevel, bd_id)
    if issue is None or issue.status != "in_progress" or issue.assignee != tw_base.actor(toplevel):
        return None
    return beads.ship_mark(issue)


def _subject(shown: str, message: str) -> str:
    """件名の先頭に `<shown>: ` を一度だけ付ける（旧形式の `<shown>: …` はそのまま受ける）。"""
    body = re.sub(rf"^\s*{re.escape(shown)}\s*[:：]\s*", "", message, count=1, flags=re.IGNORECASE)
    return f"{shown}: {body}"


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


def _is_local(path: str) -> bool:
    return path == LOCAL_DIR or path.startswith(LOCAL_DIR + "/")


def _submodule_paths(toplevel: str) -> set[str]:
    """索引か `HEAD` がサブモジュール（モード 160000）として持つパス。"""
    paths: set[str] = set()
    for args in (["ls-files", "-s", "-z"], ["ls-tree", "-r", "-z", "HEAD"]):
        out = tw_base.run_git(toplevel, args)
        for entry in out.stdout.split("\0"):
            meta, _, path = entry.partition("\t")
            if meta.startswith("160000 ") and path:
                paths.add(path)
    return paths


def _skipped_kind(toplevel: str, path: str, submodules: set[str]) -> str | None:
    """`--add` 省略時に対象から外す種類（`symlink`／`submodule`）。通常のファイルなら `None`。

    サブモジュールは、指す先のずれ・中の変更・削除（索引か `HEAD` に載るパス）と、
    追加前の入れ子のリポジトリ（`git status` が末尾 `/` のディレクトリで出す）。
    """
    if path.rstrip("/") in submodules or path.endswith("/"):
        return "submodule"
    if os.path.islink(os.path.join(toplevel, path)):
        return "symlink"
    return None


def _commit(toplevel: str, message: str, files: list[str]) -> None:
    targets = files
    if not files:
        targets = []
        submodules = _submodule_paths(toplevel)
        for path in gitinfo.dirty_paths(cwd=toplevel):
            if _is_local(path):
                continue
            kind = _skipped_kind(toplevel, path, submodules)
            if kind is not None:
                print(f"SKIPPED\t{path}\t{kind}")
                tw_base.run_git(toplevel, ["reset", "-q", "--", path.rstrip("/")])
                continue
            targets.append(path)
    for path in targets:
        _git_ok(toplevel, ["add", "--", path])
    if tw_base.run_git(toplevel, ["diff", "--cached", "--quiet"]).returncode == 0:
        print("NO_COMMIT\t(差分が無い)")
    else:
        _git_ok(toplevel, ["commit", "-q", "-m", message])
        print(f"COMMITTED\t{tw_base.run_git(toplevel, ['rev-parse', 'HEAD']).stdout.strip()}")
    for path in gitinfo.dirty_paths(cwd=toplevel):
        if not _is_local(path):
            print(f"UNADDED\t{path}")


def _git_ok(toplevel: str, args: list[str]) -> None:
    r = tw_base.run_git(toplevel, args)
    if r.returncode != 0:
        print(f"git {args[0]} が失敗した: {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)
        raise SystemExit(1)
