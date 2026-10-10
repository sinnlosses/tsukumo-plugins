"""`tw finish`: `tw done`・コミット・`tw ship` を1回で打つ。

各部品の出力の行はそのまま並べ、止まる行（`tw done` の `NOT_OWNER` と、`tw ship` の `SHIPPED`・`NOTHING` 以外）が出たら
以降を打たない。コミットは渡されたファイルを個別に `git add` した1つだけで、渡されなかったか差分が無ければ打たない。
"""

from __future__ import annotations

import sys

import tw_base
import tw_claim
import tw_ship


def cmd_finish(
    toplevel: str, task_id: str, dropped: bool, result_path: str, message: str, files: list[str]
) -> None:
    tw_claim.cmd_done(toplevel, task_id, dropped, result_path)
    sys.stdout.flush()
    _commit(toplevel, message, files)
    tw_ship.cmd_ship(toplevel)


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
