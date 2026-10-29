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
import time

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
NESTED_ONLY = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "X", "cwd": "/tmp", "tool_response": {"agent_id": "nested"}})
TOP_AFTER_NESTED = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"agent_id": "nested"},
                               "cwd": "/tmp", "agent_id": "a-1"})
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


def run(hook: str, args: list[str], stdin: str, tw_exit: int, cwd: str | None = None,
        path_prefix: str | None = None) -> subprocess.CompletedProcess[str]:
    path = "/usr/bin:/bin" if path_prefix is None else f"{path_prefix}:/usr/bin:/bin"
    env = {"PATH": path, "FAKE_TW_EXIT": str(tw_exit)}
    return subprocess.run([hook, *args], input=stdin, capture_output=True, text=True, env=env, cwd=cwd)


def check_heartbeat_spares_python(tmp: str, hook: str) -> None:
    repo = os.path.join(tmp, "repo-spy")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    spy = os.path.join(tmp, "spy-bin")
    os.makedirs(spy)
    mark = os.path.join(tmp, "python-called")
    with open(os.path.join(spy, "python3"), "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\n: > "{mark}"\nexec /usr/bin/python3 "$@"\n')
    os.chmod(os.path.join(spy, "python3"), 0o755)
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Read", "cwd": repo, "agent_id": "a-spy"})

    r = run(hook, ["heartbeat"], payload, 1, cwd=repo, path_prefix=spy)
    check("heartbeat は着手の控えが無い作業ツリーでは python3 を起こさない",
          r.returncode == 0 and not os.path.exists(mark), repr((r.returncode, r.stderr)))

    claims = os.path.join(repo, ".tw", "local", "task-open-claims")
    os.makedirs(claims)
    with open(os.path.join(claims, "T-1"), "w", encoding="utf-8"):
        pass
    r = run(hook, ["heartbeat"], payload, 1, cwd=repo, path_prefix=spy)
    check("heartbeat は控えがあるときだけ python3 で最上位の agent_id を読んで書く",
          r.returncode == 0 and os.path.exists(mark)
          and os.path.isfile(os.path.join(repo, ".tw", "local", "task-agent-beats", "a-spy")),
          repr((r.returncode, r.stderr)))


def check_heartbeat(tmp: str, hook: str) -> None:
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    local = os.path.join(repo, ".tw", "local")
    beats = os.path.join(local, "task-agent-beats")

    def beat(payload: dict | str, where: str = repo) -> subprocess.CompletedProcess[str]:
        return run(hook, ["heartbeat"], payload if isinstance(payload, str) else json.dumps(payload), 1, cwd=where)

    tool = {"hook_event_name": "PostToolUse", "tool_name": "Read", "cwd": repo}
    r = beat({**tool, "agent_id": "a-1"})
    check("heartbeat は着手の控えが無ければ書かず、.tw/local も作らない",
          r.returncode == 0 and r.stdout == "" and not os.path.exists(local), repr((r.returncode, r.stdout, r.stderr)))

    os.makedirs(os.path.join(local, "task-open-claims"))
    with open(os.path.join(local, "task-open-claims", "T-1"), "w", encoding="utf-8"):
        pass
    before = int(time.time())
    r = beat({**tool, "agent_id": "a-1"})
    after = int(time.time())
    try:
        with open(os.path.join(beats, "a-1"), encoding="utf-8") as f:
            written = f.read().strip()
    except OSError:
        written = ""
    check("heartbeat は控えがあり agent_id 付きなら task-agent-beats/<agent_id> に今の epoch 秒を書き、tw を呼ばない",
          r.returncode == 0 and r.stdout == "" and "fake tw" not in r.stderr
          and written.isdigit() and before - 5 <= int(written) <= after + 5,
          repr((r.returncode, r.stdout, r.stderr, written)))

    for label, payload in (
        ("agent_id の無い入力（主のセッション）", tool),
        ("文字列の中にしか agent_id が無い入力", {**tool, "tool_input": {"command": 'echo "agent_id": "a-evil"'}}),
        ("ファイル名に使えない agent_id", {**tool, "agent_id": "../x"}),
        ("入れ子のキーにしか agent_id が無い入力（主のセッション）", {**tool, "tool_response": {"agent_id": "a-nested"}}),
        ("入れ子のキーが前後に複数あり最上位に無い入力",
         {"tool_input": {"items": [{"agent_id": "a-n1"}]}, **tool, "tool_response": {"agent_id": "a-n2", "x": {"agent_id": "a-n3"}}}),
        ("最上位の agent_id が文字列でない入力", {**tool, "agent_id": 5, "tool_response": {"agent_id": "a-n4"}}),
    ):
        r = beat(payload)
        names = sorted(os.listdir(beats))
        check(f"heartbeat は {label}では書かない", r.returncode == 0 and r.stdout == "" and names == ["a-1"],
              repr((r.returncode, r.stdout, names)))

    nested = {"tool_input": {"agent_id": "a-n1"}, "tool_response": {"agent_id": "a-n2", "list": [{"agent_id": "a-n3"}]}}
    for label, name, payload in (
        ("入れ子のキーより後ろにある最上位", "a-top-after", json.dumps({**tool, **nested, "agent_id": "a-top-after"})),
        ("入れ子のキーより前にある最上位", "a-top-before", json.dumps({"agent_id": "a-top-before", **tool, **nested})),
        ("入れ子が前後に複数ある間の最上位", "a-top-mid",
         json.dumps({"tool_input": {"agent_id": "a-n1"}, "agent_id": "a-top-mid", **tool, "tool_response": {"agent_id": "a-n2"}})),
        ("整形済み（改行と字下げ）の入力の最上位", "a-top-pretty", json.dumps({**tool, **nested, "agent_id": "a-top-pretty"}, indent=2)),
    ):
        r = beat(payload)
        names = sorted(os.listdir(beats))
        check(f"heartbeat は {label}の agent_id だけで書く",
              r.returncode == 0 and r.stdout == "" and name in names and not any(n.startswith("a-n") for n in names),
              repr((r.returncode, r.stdout, r.stderr, names)))

    outside = os.path.join(tmp, "not-git")
    os.makedirs(outside)
    r = beat({**tool, "agent_id": "a-2", "cwd": outside}, where=outside)
    check("heartbeat は git の外では何も書かず 0", r.returncode == 0 and r.stdout == "" and os.listdir(outside) == [],
          repr((r.returncode, r.stdout, r.stderr)))


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

        r = run(hook, guard, NESTED_ONLY, 1)
        check("agent_id が入れ子のキーにしか無ければ主のセッションとみなして 1 で通す",
              r.returncode == 1 and "関門が掛かっていない" in r.stderr, repr((r.returncode, r.stderr)))

        r = run(hook, guard, TOP_AFTER_NESTED, 1)
        check("入れ子のキーの後ろに最上位の agent_id があれば 2 で止める",
              r.returncode == 2 and "落ちたので止めた" in r.stderr, repr((r.returncode, r.stderr)))

        for code in (1, 2):
            r = run(hook, ["handback-guard", "--agent-scoped"], STOP, code)
            check(f"--fail-closed が無ければ tw が {code} でも agent_id ありで 1（2 を写さない）",
                  r.returncode == 1 and "handback-guard が終了コード" in r.stderr, repr((r.returncode, r.stderr)))

        missing = make_root(tmp, shell, with_tw=False)
        r = run(missing, guard, SUBAGENT, 0)
        check("(d) bin/tw が無い（127）なら agent_id ありでも案内して 0",
              r.returncode == 0 and "前提が欠けている" in r.stderr, repr((r.returncode, r.stderr)))

        check_heartbeat(tmp, hook)
        check_heartbeat_spares_python(tmp, hook)

    if failures:
        print(f"NG: {len(failures)} 件")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
