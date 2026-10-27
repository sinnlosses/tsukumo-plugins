#!/usr/bin/env python3
"""`bin/tw-hook`（hooks/hooks.json が呼ぶ殻）の自己テスト。

使い方: python3 scripts/selftest_tw_hook.py [<試す殻のパス>]（省略時は bin/tw-hook）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_TW = """#!/bin/sh
cat
echo "fake tw $*" >&2
exit "$FAKE_TW_EXIT"
"""

SUBAGENT = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git commit -m x"},
                       "cwd": "/tmp", "agent_id": "a-1", "agent_type": "tsukumo-workflow:no-delegate"})
MAIN = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": "/tmp"})
IN_STRING_ONLY = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                             "tool_input": {"command": 'echo "agent_id": x'}, "cwd": "/tmp"})
STOP = json.dumps({"hook_event_name": "SubagentStop", "cwd": "/tmp", "agent_id": "a-1", "agent_type": "tsukumo-workflow:no-delegate"})

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)
        if detail:
            print(f"       {detail}")


def make_root(tmp: str, shell: str, with_tw: bool) -> str:
    root = tempfile.mkdtemp(dir=tmp)
    os.makedirs(os.path.join(root, "bin"))
    hook = os.path.join(root, "bin", "tw-hook")
    shutil.copyfile(shell, hook)
    os.chmod(hook, 0o755)
    if with_tw:
        tw = os.path.join(root, "bin", "tw")
        with open(tw, "w", encoding="utf-8") as f:
            f.write(FAKE_TW)
        os.chmod(tw, 0o755)
    return hook


def run(hook: str, args: list[str], stdin: str, tw_exit: int) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "FAKE_TW_EXIT": str(tw_exit)}
    return subprocess.run([hook, *args], input=stdin, capture_output=True, text=True, env=env)


def main() -> int:
    shell = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "bin", "tw-hook")
    print("bin/tw-hook: tw の終了コードと入力の agent_id で、止めるか通すかを決める")
    with tempfile.TemporaryDirectory() as tmp:
        hook = make_root(tmp, shell, with_tw=True)
        guard = ["--fail-closed", "commit-guard", "--agent-scoped"]

        r = run(hook, guard, SUBAGENT, 0)
        check("tw が 0 なら 0 で、tw が受け取った stdin と stdout は入力と同じ",
              r.returncode == 0 and r.stdout == SUBAGENT and "fake tw commit-guard --agent-scoped" in r.stderr,
              repr((r.returncode, r.stdout, r.stderr)))
        deny = '{"hookSpecificOutput": {"permissionDecision": "deny"}}'
        r = run(hook, guard, deny, 0)
        check("tw の stdout（拒否の JSON）を1字も変えずに出す", r.returncode == 0 and r.stdout == deny,
              repr((r.returncode, r.stdout)))

        for args in (guard, ["handback-guard", "--agent-scoped"]):
            r = run(hook, args, SUBAGENT, 78)
            check(f"(a) tw が 78 なら {' '.join(args)} でも案内して 0",
                  r.returncode == 0 and "前提が欠けている" in r.stderr, repr((r.returncode, r.stderr)))

        r = run(hook, guard, SUBAGENT, 1)
        check("(b) tw が 1 で --fail-closed・agent_id ありなら 2 で止め、tw の stderr と理由を出す",
              r.returncode == 2 and "fake tw" in r.stderr and "commit-guard が終了コード 1 で落ちたので止めた" in r.stderr,
              repr((r.returncode, r.stderr)))

        r = run(hook, guard, MAIN, 1)
        check("(c) tw が 1 で --fail-closed・agent_id なし（主のセッション）なら 1 で通す",
              r.returncode == 1 and "commit-guard が終了コード 1 で落ちた（関門が掛かっていない）" in r.stderr,
              repr((r.returncode, r.stderr)))

        r = run(hook, guard, IN_STRING_ONLY, 1)
        check("agent_id が文字列の中にしか無ければ主のセッションとみなして 1 で通す",
              r.returncode == 1 and "関門が掛かっていない" in r.stderr, repr((r.returncode, r.stderr)))

        for code in (1, 2):
            r = run(hook, ["handback-guard", "--agent-scoped"], STOP, code)
            check(f"--fail-closed が無ければ tw が {code} でも agent_id ありで 1（2 を写さない）",
                  r.returncode == 1 and "handback-guard が終了コード" in r.stderr, repr((r.returncode, r.stderr)))

        missing = make_root(tmp, shell, with_tw=False)
        r = run(missing, guard, SUBAGENT, 0)
        check("(d) bin/tw が無い（127）なら agent_id ありでも案内して 0",
              r.returncode == 0 and "前提が欠けている" in r.stderr, repr((r.returncode, r.stderr)))

    if failures:
        print(f"NG: {len(failures)} 件")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
