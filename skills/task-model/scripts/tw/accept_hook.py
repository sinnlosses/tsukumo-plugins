"""`tw accept-hook`: Claude Code の hook の入力（stdin の JSON）から、受け入れ役（`acceptor`）の準備と締めを打つ。

- `SubagentStart` で、`agent_type` の最後の `:` より後ろが `acceptor` のもの → 準備。開いた周回が無ければ1周目として
  `tw accept <ID>` と `tw brief acceptor <ID> --accept-file -`、あれば `tw lap <ID> review start` と
  `tw brief acceptor <ID> --round <n+1> --since <前の木>` を打ち、出力を `additionalContext` で受け入れ役に渡す。
  `REVIEW` の周（2周目以降を含む）だけ、台帳に `accept_round`（`round`・`tree`）を書く。
- `SubagentStop` で、同じく `acceptor` のもの → 締め。`agent_transcript_path` の末尾の `StructuredOutput` の
  `verdict` が `accept` で、開いた周回があれば `tw accept <ID> --after-review` を打つ。最後の行が `VERIFIED` で始まれば
  周回を閉じ（`round` が 0 の `accept_round`）、それ以外なら受け入れ役を `decision: block` で差し戻させる。

周回は最後の `claim` 以降の最後の `accept_round` で読む（`claim` し直すと周回は無い）。
打つのは入力の `cwd` の作業ツリーの着手の控えがちょうど1件のときだけ（設定 `flow` は見ない）。終了コードはいつも0。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import IO

import gitinfo
import lap_hook
import layout
import ledger
import metrics
import tw_base
import tw_brief

ROUND_EVENT = "accept_round"
STOP_WORD = "STOP"
TASK_PY = os.path.join(os.path.dirname(os.path.realpath(__file__)), "task.py")
TRANSCRIPT_TAIL_LINES = 12
READ_RETRIES = 4
READ_INTERVAL_SECONDS = 0.5


def run(stdin: IO[str], stdout: IO[str]) -> None:
    try:
        payload = json.loads(stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    agent_type = payload.get("agent_type")
    cwd = payload.get("cwd")
    if not isinstance(agent_type, str) or agent_type.rsplit(":", 1)[-1] != lap_hook.ACCEPTOR_TYPE:
        return
    if not isinstance(cwd, str) or not os.path.isdir(cwd):
        return
    event = payload.get("hook_event_name")
    try:
        claims = ledger.open_claims(cwd=cwd)
        if len(claims) != 1:
            return
        toplevel = gitinfo.git_toplevel(cwd)
        if event == "SubagentStart":
            out: dict | None = {
                "hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": _prepare(toplevel, claims[0])}
            }
        elif event == "SubagentStop":
            out = _close(toplevel, claims[0], payload)
        else:
            return
    except (gitinfo.GitCommandError, layout.ConfigError, OSError):
        return
    if out is not None:
        stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


def open_round(toplevel: str, task_id: str) -> tuple[int, str] | None:
    """最後の `claim` 以降の最後の `accept_round` が開いて（`round` が1以上）いれば `(round, tree)`。"""
    events = metrics.read_events(ledger.ledger_root(toplevel))[0]
    own = [e for e in events if e.task == task_id]
    claims = [i for i, e in enumerate(own) if e.kind == "claim"]
    rounds = [e for e in (own[claims[-1] :] if claims else []) if e.kind == ROUND_EVENT]
    if not rounds:
        return None
    number, tree = rounds[-1].fields.get("round"), rounds[-1].fields.get("tree")
    if not isinstance(number, int) or number < 1 or not isinstance(tree, str):
        return None
    return number, tree


def close_round(toplevel: str, task_id: str) -> None:
    """開いた周回があれば `round` が 0 の `accept_round` を書いて閉じる（何も出さない）。"""
    if open_round(toplevel, task_id) is not None:
        tw_base.record(toplevel, ROUND_EVENT, task_id, round=0, tree="")


def _tw(toplevel: str, *args: str, stdin: str | None = None) -> tuple[int, str]:
    r = subprocess.run([sys.executable, TASK_PY, *args], cwd=toplevel, input=stdin, capture_output=True, text=True)
    return r.returncode, r.stdout


def _stop(line: str) -> str:
    return f"{STOP_WORD}\t{line}"


def _last_line(output: str) -> str:
    lines = output.strip("\n").splitlines()
    return lines[-1] if lines else ""


def _prepare(toplevel: str, task_id: str) -> str:
    """受け入れ役の `additionalContext`。1行目は判定の行（止まったら `STOP\\t<行>`）、続けて木と依頼文。"""
    opened = open_round(toplevel, task_id)
    if opened is not None:
        number, since = opened[0] + 1, opened[1]
        code, lapped = _tw(toplevel, "lap", task_id, "review", "start")
        if code != 0:
            return _stop(_last_line(lapped) or f"tw lap が終了コード {code} で止まった")
        code, brief = _tw(toplevel, "brief", "acceptor", task_id, "--round", str(number), "--since", since)
        head = brief.splitlines()[0] if brief else ""
        if code != 0 or not head.startswith("BRIEF\t"):
            return _stop(_last_line(brief) or f"tw brief が終了コード {code} で止まった")
        tree = head.rsplit("\t", 1)[-1]
        tw_base.record(toplevel, ROUND_EVENT, task_id, round=number, tree=tree)
        return f"REVIEW\tround\t{number}\nSNAPSHOT\t{tree}\n{brief}"
    code, accepted = _tw(toplevel, "accept", task_id)
    lines = accepted.splitlines()
    verdict = next((line for line in lines if line.split("\t", 1)[0] in tw_brief.VERDICT_WORDS), None)
    if code != 0 or verdict is None:
        return _stop(_last_line(accepted) or f"tw accept が終了コード {code} で止まった") + f"\n{accepted}"
    code, brief = _tw(toplevel, "brief", "acceptor", task_id, "--accept-file", "-", stdin=accepted)
    if code != 0 or not brief.startswith("BRIEF\t"):
        return _stop(_last_line(brief) or f"tw brief が終了コード {code} で止まった") + f"\n{accepted}"
    snapshot = next((line.split("\t", 1)[1] for line in lines if line.startswith("SNAPSHOT\t")), "")
    if verdict.startswith("REVIEW") and snapshot != "":
        tw_base.record(toplevel, ROUND_EVENT, task_id, round=1, tree=snapshot)
    tree_line = f"SNAPSHOT\t{snapshot}\n" if snapshot != "" else ""
    return f"{verdict}\n{tree_line}{accepted}\n{brief}"


def _close(toplevel: str, task_id: str, payload: dict) -> dict | None:
    path = payload.get("agent_transcript_path")
    if not isinstance(path, str) or read_verdict(path) != "accept":
        return None
    if open_round(toplevel, task_id) is None:
        return None
    _code, closed = _tw(toplevel, "accept", task_id, "--after-review")
    line = _last_line(closed)
    if line.startswith("VERIFIED"):
        close_round(toplevel, task_id)
        return None
    if payload.get("stop_hook_active") is True:
        return None
    return {
        "decision": "block",
        "reason": f"`tw accept {task_id} --after-review` が {line or '（出力なし）'} で止まった。"
        "verdict を reject にし、その行を findings に入れて返し直す。",
    }


def read_verdict(path: str) -> str | None:
    """transcript の末尾の `StructuredOutput` の `input.verdict`。読めなければ間を置いて読み直し、それでも無ければ `None`。"""
    for attempt in range(READ_RETRIES + 1):
        found = _tail_verdict(path)
        if found is not None:
            return found
        if attempt < READ_RETRIES:
            time.sleep(READ_INTERVAL_SECONDS)
    return None


def _tail_verdict(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-TRANSCRIPT_TAIL_LINES:]
    except OSError:
        return None
    for line in reversed(tail):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = row.get("message") if isinstance(row, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name") == "StructuredOutput":
                verdict = part.get("input", {}).get("verdict") if isinstance(part.get("input"), dict) else None
                if isinstance(verdict, str):
                    return verdict
    return None
