"""`tw relay`（段の申し送りを Beads の comment に書く・読む）。"""

from __future__ import annotations

import sys

import beads
import tw_base


def cmd_relay(toplevel: str, task_id: str, key: str, body_path: str | None) -> None:
    """`body_path` があれば鍵 `key` の申し送りを書き、無ければ最後のものを読む。"""
    if key.strip() == "" or any(c in key for c in "\t\r\n"):
        print(f"usage: 鍵 {key!r} が空かタブ・改行を含む", file=sys.stderr)
        raise SystemExit(2)
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    if beads.show(toplevel, bd_id) is None:
        print(f"NOT_READY\t{shown}\t存在しない")
        raise SystemExit(4)
    if body_path is None:
        body = beads.last_relay(beads.comments(toplevel, bd_id), key)
        if body is None:
            print(f"NONE\t{shown}\t{key}")
            return
        print(f"RELAY\t{shown}\t{key}")
        print(body)
        return
    if not tw_base.owns(toplevel, shown):
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)
    body = tw_base.read_body(body_path).strip("\n")
    text = f"{beads.RELAY_HEADING_PREFIX}{key}\n\n{body}\n" if body else f"{beads.RELAY_HEADING_PREFIX}{key}\n"
    beads.run_ok(toplevel, ["comment", bd_id, "--stdin"], tw_base.actor(toplevel), text)
    print(f"RELAYED\t{shown}\t{key}")
