"""`tw finish`: `tw done`・コミット・`tw ship` を1回で打つ。

各部品の出力の行はそのまま並べ、止まる行（`tw done` の `NOT_OWNER` と `COMMITS_SINCE_CLAIM`、
`tw ship` の `SHIPPED`・`NOTHING` 以外）が出たら以降を打たない。コミットは渡されたファイルを個別に `git add` した1つだけで、渡されなかったか差分が無ければ打たない。
"""

from __future__ import annotations

import contextlib
import io
import sys

import tw_base
import tw_claim
import tw_ship

STRAY_COMMITS_EXIT = 12


def cmd_finish(
    toplevel: str, task_id: str, dropped: bool, result_path: str, message: str, files: list[str]
) -> None:
    if _done_found_stray_commits(toplevel, task_id, dropped, result_path):
        raise SystemExit(STRAY_COMMITS_EXIT)
    _commit(toplevel, message, files)
    tw_ship.cmd_ship(toplevel)


def _done_found_stray_commits(toplevel: str, task_id: str, dropped: bool, result_path: str) -> bool:
    """`tw done` の出力をそのまま出し、`COMMITS_SINCE_CLAIM` の行があれば `True`。

    `tw done` は済んでいる。続きは個別の `git add`・`git commit`・`tw ship` で打つ（`tw done` を打ち直すと
    `## 結果` の comment が二重になる）。
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            tw_claim.cmd_done(toplevel, task_id, dropped, result_path)
    finally:
        sys.stdout.write(buffer.getvalue())
        sys.stdout.flush()
    return any(line.startswith("COMMITS_SINCE_CLAIM\t") for line in buffer.getvalue().splitlines())


def _commit(toplevel: str, message: str, files: list[str]) -> None:
    if not files:
        print("NO_COMMIT\t(ファイルが渡されていない)")
        return
    for path in files:
        _git_ok(toplevel, ["add", "--", path])
    if tw_base.run_git(toplevel, ["diff", "--cached", "--quiet"]).returncode == 0:
        print("NO_COMMIT\t(差分が無い)")
        return
    _git_ok(toplevel, ["commit", "-q", "-m", message])
    print(f"COMMITTED\t{tw_base.run_git(toplevel, ['rev-parse', 'HEAD']).stdout.strip()}")


def _git_ok(toplevel: str, args: list[str]) -> None:
    r = tw_base.run_git(toplevel, args)
    if r.returncode != 0:
        print(f"git {args[0]} が失敗した: {r.stderr.strip() or r.stdout.strip()}", file=sys.stderr)
        raise SystemExit(1)
