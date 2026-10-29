from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

from selftest_support import RESULT_FULL, RESULT_REQUIRED, check, git, git_env, refuse_direct_run, say, weight, write  # noqa: E402
import beads  # noqa: E402
import commit_guard  # noqa: E402
import escalation  # noqa: E402
import gitinfo  # noqa: E402
import handback_guard  # noqa: E402
import lap_hook  # noqa: E402
import ledger  # noqa: E402
import metrics  # noqa: E402
import ref_guard  # noqa: E402
import stamps  # noqa: E402
import taskfile  # noqa: E402
import tw_escalate  # noqa: E402
import tw_handback  # noqa: E402
import tw_plan  # noqa: E402
from selftest_fixtures import BODY, commit_task, disarm_ref_guard, flow_rows, hook_env, make_repo, metadata, readonly_git, run_guard, run_handback_guard, run_task, snapshot  # noqa: E402


@weight(9)
def test_commit_guard() -> None:
    say("task.py commit-guard: ref-guard が効かない作業ツリーでは、印が立って done 前のコミットを拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "release で外す", "todo", "sonnet", "Y", (), BODY))
        disarm_ref_guard(main_path)

        check("claim の前は通る", run_guard(tmp, wt1, "git commit -m x") is None)
        run_task(wt1, "claim", "T-100")
        check("claim で控えが立つ（作業ツリーごと）",
              ledger.open_claims(cwd=wt1) == ["T-100"] and ledger.open_claims(cwd=wt2) == [])

        reason = run_guard(tmp, wt1, "git commit -m x")
        check("印の作業ツリーの git commit は理由つきで拒む",
              reason is not None and "T-100" in reason and "コミットせず" in reason and "報告で返す" in reason,
              str(reason))
        for command, where in (
            ("git -c user.name=x commit -am y", wt1),
            (f"cd {wt1} && git add -A && git commit -m z", tmp),
            (f"git -C {wt1} commit -m z", tmp),
            ("git cherry-pick HEAD", wt1),
            ("FOO=1 git commit -m z", os.path.join(wt1, ".tw")),
        ):
            check(f"拒む: {command}", run_guard(tmp, where, command) is not None)
        for command, where in (
            ("git status && git log -1", wt1),
            ("tw verify", wt1),
            ("git commit -m x", wt2),
            (f"git -C {wt2} commit -m x", wt1),
            (f"cd {wt2} && git commit -m x", wt1),
            ('git commit -m "閉じない', wt1),
        ):
            check(f"通す: {command}", run_guard(tmp, where, command) is None)
        r = run_task(tmp, "commit-guard", stdin="not json")
        check("読めない入力は何も出さずに通す", r.returncode == 0 and r.stdout == "", r.stdout + r.stderr)

        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "work.txt")
        git(wt1, "commit", "-q", "-m", "メインの手直し")
        check("hook を通らないメインのコミットは印があっても通る",
              git(wt1, "log", "-1", "--format=%s").stdout.strip() == "メインの手直し")

        result_path = write(os.path.join(tmp, "result.md"), RESULT_FULL)
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done で控えが消える", ledger.open_claims(cwd=wt1) == [])
        check("done のあとの git commit は通る", run_guard(tmp, wt1, "git commit -m x") is None)

        run_task(wt2, "claim", "T-101")
        check("release の前は拒む", run_guard(tmp, wt2, "git commit -m x") is not None)
        run_task(wt2, "release", "T-101")
        check("release のあとは通る", run_guard(tmp, wt2, "git commit -m x") is None)

        run_task(wt2, "claim", "T-101")
        run_task(wt1, "release", "T-101", "--force")
        check("人が --force で外すと持ち主の作業ツリーの控えも消え、コミットは通る",
              ledger.open_claims(cwd=wt2) == [] and run_guard(tmp, wt2, "git commit -m x") is None)


def run_ask_guard(tmp: str, where: str, tool: str = "AskUserQuestion", extra: dict | None = None) -> str | None:
    """`tw ask-guard` を git の外（`tmp`）から打つ。拒んだら理由、通したら `None`（落ちたら例外）。"""
    payload = json.dumps({"tool_name": tool, "tool_input": {}, "cwd": where, **(extra or {})})
    r = run_task(tmp, "ask-guard", stdin=payload)
    if r.returncode != 0:
        raise RuntimeError(f"ask-guard が {r.returncode} で終わった: {r.stderr}")
    if r.stdout == "":
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def run_lap_hook(tmp: str, where: str, payload: dict) -> None:
    """`tw lap-hook` を git の外（`tmp`）から打つ。終了コード0で stdout が空でなければ例外。"""
    r = run_task(tmp, "lap-hook", stdin=json.dumps({"cwd": where, **payload}))
    if r.returncode != 0 or r.stdout != "":
        raise RuntimeError(f"lap-hook が {r.returncode} で {r.stdout!r} を出した: {r.stderr}")


def _subagent_start(agent_type: str) -> dict:
    return {"hook_event_name": "SubagentStart", "session_id": "s-1", "agent_id": "a-start", "agent_type": agent_type}


def _subagent_stop(agent_type: object) -> dict:
    stop = {"hook_event_name": "SubagentStop", "session_id": "s-1", "agent_id": "a-stop", "stop_hook_active": False}
    return stop if agent_type is None else {**stop, "agent_type": agent_type}


def _skill_call(skill: str, **extra: str) -> dict:
    return {"hook_event_name": "PreToolUse", "session_id": "s-1", "tool_name": "Skill", "tool_input": {"skill": skill}, **extra}


@weight(6)
def test_ask_guard() -> None:
    say("task.py ask-guard: loopable: Y の着手の控えがある作業ツリーで AskUserQuestion を拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "聞かない", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "聞いてよい", "todo", "sonnet", "N", (), BODY))

        check("claim の前は通る", run_ask_guard(tmp, wt1) is None)
        run_task(wt1, "claim", "T-100")
        reason = run_ask_guard(tmp, wt1)
        check("loopable: Y の控えの作業ツリーは tw hold 1つを案内して拒む",
              reason is not None and "tw hold T-100 --reason-file -" in reason and "CONTINUE" in reason
              and "tw release" not in reason and "--status hold" not in reason and "--section 注意" not in reason,
              str(reason))
        subagent = {"agent_id": "a-implementer", "agent_type": "my-implementer"}
        check("サブエージェント（agent_id）は名前を問わず拒む", run_ask_guard(tmp, wt1, extra=subagent) is not None)
        for tool in ("Bash", "mcp__tsukumo__speak"):
            check(f"ほかのツール（{tool}）は通す", run_ask_guard(tmp, wt1, tool) is None)
        check("控えの無い作業ツリーは通す", run_ask_guard(tmp, wt2) is None)
        r = run_task(tmp, "ask-guard", stdin="not json")
        check("読めない入力は何も出さずに通す", r.returncode == 0 and r.stdout == "", r.stdout + r.stderr)

        for lap, refused in (
            (("delegate", "start"), True),
            (("accept", "start"), True),
            (("review", "start"), True),
            (("review", "end"), True),
            (("accept", "end"), False),
            (("delegate", "start"), True),
            (("direct",), True),
            (("accept", "start"), True),
            (("accept", "end"), False),
            (("retro", "start"), False),
            (("retro", "end"), False),
            (("land", "start"), False),
            (("sync", "start"), False),
        ):
            run_task(wt1, "lap", "T-100", *lap)
            check(f"主のセッションは最後の lap が {' '.join(lap)} なら{'拒む' if refused else '通す'}",
                  (run_ask_guard(tmp, wt1) is not None) == refused)
        check("作業の段の後でもサブエージェントは拒む", run_ask_guard(tmp, wt1, extra=subagent) is not None)
        run_task(wt1, "release", "T-100")
        check("release のあとは通す", run_ask_guard(tmp, wt1) is None)
        run_task(wt1, "claim", "T-100")
        check("claim し直すと前の accept end より後の lap が無いので拒む", run_ask_guard(tmp, wt1) is not None)
        for label, act, refused in (
            ("hook の delegate", lambda: run_lap_hook(tmp, wt1, _subagent_start("no-delegate")), True),
            ("手打ちの delegate start", lambda: run_task(wt1, "lap", "T-100", "delegate", "start"), True),
            ("tw accept", lambda: run_task(wt1, "accept", "T-100"), False),
            ("受け入れの後の hook の delegate（委譲し直し）", lambda: run_lap_hook(tmp, wt1, _subagent_start("no-delegate")), True),
            ("hook の retro", lambda: run_lap_hook(tmp, wt1, _skill_call("retrospect")), False),
        ):
            act()
            check(f"hook と手打ちを混ぜた並びでも、{label} のあとは{'拒む' if refused else '通す'}",
                  (run_ask_guard(tmp, wt1) is not None) == refused, repr([(e.get("stage"), e.get("edge")) for e in flow_rows(wt1)[-6:]]))
        run_task(wt1, "release", "T-100")

        run_task(wt2, "claim", "T-101")
        check("loopable: N の控えは通す", run_ask_guard(tmp, wt2) is None)


@weight(6)
def test_commit_guard_shell_edit() -> None:
    say("task.py commit-guard: 印が立って done 前の作業ツリーで、シェルからのファイルの書き換えを拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        disarm_ref_guard(main_path)
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        write(os.path.join(wt1, ".gitignore"), "*.log\n")
        outside = os.path.join(tmp, "outside")
        os.makedirs(outside)
        py_write = "open(p, 'w').write('x')"
        py_read = "print(open(p).read())"

        reason = run_guard(tmp, wt1, "sed -i s/a/b/ f.txt")
        check("理由にタスクIDと Edit・Write が入る",
              reason is not None and "T-100" in reason and "Edit" in reason and "Write" in reason, str(reason))
        for command, where in (
            ("sed -i s/a/b/ f.txt", wt1),
            ("sed -i '' s/a/b/ f.txt", wt1),
            ("sed -ni 1p f.txt", wt1),
            ("sed -i.bak s/a/b/ f.txt", wt1),
            (f'cd "$X" && git -C {wt1} commit -m x', tmp),
            (f'cd "$X" && echo x > {wt1}/f.txt', tmp),
            ("perl -pi.bak -e s/a/b/ f.txt", wt1),
            ("# it's done\ngit commit -m x", wt1),
            ("sed --in-place -e s/a/b/ f.txt", wt1),
            ("sed -i s/a/b/ f.txt 2>&1", wt1),
            ("perl -pi -e s/a/b/ f.txt", wt1),
            ("echo x > f.txt", wt1),
            ("echo x >f.txt", wt1),
            ("echo x >> sub/f.txt", wt1),
            ("> f.txt", wt1),
            ("echo x &> f.txt", wt1),
            ("cat > f.txt <<'EOF'\nhi > there\nEOF", wt1),
            ("echo x | tee f.txt", wt1),
            ("tee -a f.txt", wt1),
            ("for f in a; do sed -i s/a/b/ f.txt; done", wt1),
            (f"cd {wt1} && echo x > f.txt", tmp),
            ("cd sub && echo x > ../f.txt", wt1),
            (f"echo x > {wt1}/f.txt", tmp),
            (f"python3 - <<'EOF'\n{py_write}\nEOF", wt1),
            ("python3 -c \"Path(p).write_text('x')\"", wt1),
            ("python3 -c 'open(p, \"a\")'", wt1),
        ):
            check(f"拒む: {command.splitlines()[0]}", run_guard(tmp, where, command) is not None)
        for command, where in (
            ("tw edit T-100 --section 'やること' --body-file - <<'EOF'\na > b\nEOF", wt1),
            ("tw done T-100 --result-file - <<'EOF'\nx > y\nEOF", wt1),
            ("ls 2>&1", wt1),
            ("./check.sh >/dev/null 2>&1", wt1),
            ("echo x >&2", wt1),
            ("echo hi # note > out", wt1),
            ("(( n > 3 ))", wt1),
            ("if (( $# > 0 )); then echo y; fi", wt1),
            ('cd "$TMPDIR" && echo x > f', wt1),
            ('cd "$TMPDIR" && git commit -m x', wt1),
            ("echo x > ignored.log", wt1),
            (f"echo x > {outside}/log.txt", wt1),
            ("echo x > /tmp/gh536-log.txt", wt1),
            (f"git diff > {outside}/p.diff", wt1),
            (f"sed -i s/a/b/ {outside}/o.txt 2>&1", wt1),
            (f"echo x | tee {outside}/o.txt", wt1),
            (f"cd {outside} && echo x > f.txt", wt1),
            ("grep \">\" f.txt", wt1),
            ("sed -n 1,5p f.txt", wt1),
            ("git status && git log -1", wt1),
            (f"python3 - <<'EOF'\n{py_read}\nEOF", wt1),
            ("python3 -c \"open(p, 'r')\"", wt1),
            ("python3 - <<'EOF'\nopen('/tmp/x', 'w')\nEOF", wt1),
            ("python3 script.py", wt1),
            ("echo x > $F", wt1),
            ("echo x > \"$(pwd)/f.txt\"", wt1),
            ("eval 'echo x > f.txt'", wt1),
            ("bash -c 'echo x > f.txt'", wt1),
            ("echo \"x", wt1),
            ("sed -i s/a/b/ f.txt", wt2),
            ("echo x > f.txt", wt2),
            (f"python3 - <<'EOF'\n{py_write}\nEOF", wt2),
            (f"cd {wt2} && sed -i s/a/b/ f.txt", wt1),
        ):
            check(f"通す: {command.splitlines()[0]}", run_guard(tmp, where, command) is None)

        for agent_id, refused in ((None, False), ("a-implementer", True)):
            payload = {"tool_name": "Bash", "tool_input": {"command": "echo x > f.txt"}, "cwd": wt1}
            if agent_id is not None:
                payload["agent_id"] = agent_id
            r = run_task(tmp, "commit-guard", "--agent-scoped", stdin=json.dumps(payload))
            check(f"--agent-scoped の書き換えの拒否は agent_id={agent_id} で{'拒む' if refused else '通す'}",
                  r.returncode == 0 and (r.stdout != "") == refused, r.stdout + r.stderr)

        result_path = write(os.path.join(tmp, "result.md"), RESULT_FULL)
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done のあとの書き換えは通る", run_guard(tmp, wt1, "echo x > f.txt") is None)


@weight(6)
def test_commit_guard_verify() -> None:
    say("task.py commit-guard: 印が立って done 前の作業ツリーで、設定の検証コマンドの直打ちを拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`./check.sh`", preship="`./ship-check.sh`")
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        os.makedirs(os.path.join(wt1, "sub"), exist_ok=True)
        run_task(wt1, "claim", "T-100")

        reason = run_guard(tmp, wt1, "./check.sh")
        check("理由にタスクIDと tw verify が入る",
              reason is not None and "T-100" in reason and "tw verify" in reason, str(reason))
        for command, where in (
            ("./check.sh", wt1),
            ("./check.sh --full", wt1),
            ("FOO=1 ./check.sh", wt1),
            ("command ./check.sh", wt1),
            (f"cd {wt1} && ./check.sh", tmp),
            (f"{wt1}/check.sh", os.path.join(wt1, "sub")),
            ("../check.sh", os.path.join(wt1, "sub")),
            ("./check.sh 2>&1 | tail", wt1),
            ("./check.sh >/dev/null 2>&1", wt1),
            ("git status && ./check.sh", wt1),
            ("( ./check.sh )", wt1),
            ("# note\n./check.sh", wt1),
            ("if (( 1 )); then ./check.sh; fi", wt1),
            ("./ship-check.sh", wt1),
            ("./check.sh --plan=x", wt1),
        ):
            check(f"拒む: {command.splitlines()[-1]}", run_guard(tmp, where, command) is not None)
        for command, where in (
            ("tw verify", wt1),
            ("tw verify T-100", wt1),
            ("./check.sh path/to/one.test.ts", wt1),
            ("./check.sh --plan", wt1),
            ("./check.sh --full --plan", wt1),
            ("./check.sh --help", wt1),
            ("./check.sh -h", wt1),
            ("FOO=1 ./check.sh --plan", wt1),
            ("python3 skills/task-model/scripts/tests/selftest_task.py test_x", wt1),
            ("bash ./check.sh", wt1),
            ("echo ./check.sh", wt1),
            ("git log -- ./check.sh", wt1),
            ("ls ./check.sh", wt1),
            ("./check-other.sh", wt1),
            ("./check.sh", os.path.join(wt1, "sub")),
            ("echo hi # ./check.sh", wt1),
            ("(( n > 3 )) && echo ./check.sh", wt1),
            ("if (( $# > 0 )); then echo y; fi", wt1),
            ('cd "$TMPDIR" && ./check.sh', wt1),
            ("./check.sh", wt2),
            (f"cd {wt2} && ./check.sh", wt1),
            ("./check.sh '閉じない", wt1),
        ):
            check(f"通す: {command.splitlines()[-1]}", run_guard(tmp, where, command) is None)

        for text, command, refused in (
            ('verify = "a && b"\n', "a", False),
            ('verify = "なし"\n', "./check.sh", False),
            ('verify = "pnpm run check"\n', "pnpm run check --full", True),
            ('verify = "pnpm run check"\n', "pnpm run test", False),
            ('verify = "pnpm run check"\n', "pnpm run check --plan", False),
            ("verify = 壊れた\n", "./check.sh", False),
        ):
            write(os.path.join(wt1, ".tw", "config.toml"), text)
            check(f"設定 {text.strip()} のとき {command} は{'拒む' if refused else '通す'}",
                  (run_guard(tmp, wt1, command) is not None) == refused)

        write(os.path.join(wt1, ".tw", "config.toml"), 'verify = "./check.sh"\n')
        for agent_id, refused in ((None, False), ("a-implementer", True)):
            payload = {"tool_name": "Bash", "tool_input": {"command": "./check.sh"}, "cwd": wt1}
            if agent_id is not None:
                payload["agent_id"] = agent_id
            r = run_task(tmp, "commit-guard", "--agent-scoped", stdin=json.dumps(payload))
            check(f"--agent-scoped の検証コマンドの拒否は agent_id={agent_id} で{'拒む' if refused else '通す'}",
                  r.returncode == 0 and (r.stdout != "") == refused, r.stdout + r.stderr)

        result_path = write(os.path.join(tmp, "result.md"), RESULT_FULL)
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done のあとの検証コマンドは通る", run_guard(tmp, wt1, "./check.sh") is None)


@weight(9)
def test_commit_guard_other_repo() -> None:
    say("task.py commit-guard: 作業先が別のリポジトリなら、作業先の作業ツリーの書き換えと検証コマンドの直打ちも拒み、コミットは通す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(os.path.join(tmp, "own"), verify="`./check.sh`")
        work_repo, _, _ = make_repo(os.path.join(tmp, "work"), verify="`./check.sh`")
        commit_task(main_path, taskfile.Task("T-100", "作業先で直す", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-",
                 stdin=f"### 1. 書く\n\n### 作業先\n- `{work_repo}`\n")
        work_tree = os.path.join(tmp, "work-t-100")
        git(work_repo, "worktree", "add", "-q", "-b", "t-100", work_tree, "main")
        other_tree = os.path.join(tmp, "work-other")
        git(work_repo, "worktree", "add", "-q", "-b", "other", other_tree, "main")
        write(os.path.join(work_tree, ".gitignore"), "*.log\n")

        reason = run_guard(tmp, wt1, f"echo x > {work_tree}/f.txt")
        check("作業先の作業ツリーへの書き換えは理由にタスクIDを入れて拒む",
              reason is not None and "T-100" in reason and "Edit" in reason, str(reason))
        for command in (
            f"cd {work_tree} && sed -i s/a/b/ f.txt",
            f"cd {work_tree} && echo x | tee f.txt",
            f"cd {work_tree} && ./check.sh",
            f"cd {work_tree} && ./check.sh --full",
        ):
            check(f"拒む: {command}", run_guard(tmp, wt1, command) is not None)
        for command, where in (
            (f"cd {work_tree} && tw verify", wt1),
            (f"cd {work_tree} && python3 skills/task-model/scripts/tw/task.py verify", wt1),
            (f"git -C {work_tree} status", wt1),
            (f"cd {work_tree} && git log -1 && git diff", wt1),
            (f"git -C {work_tree} commit -m x", wt1),
            (f"cd {work_tree} && git add f.txt && git commit -m x", wt1),
            (f"echo x > {work_tree}/ignored.log", wt1),
            (f"echo x > {other_tree}/f.txt", wt1),
            (f"cd {other_tree} && ./check.sh", wt1),
            (f"echo x > {work_tree}/f.txt", wt2),
        ):
            check(f"通す: {command}（cwd {os.path.basename(where)}）", run_guard(tmp, where, command) is None)

        run_task(wt1, "release", "T-100")
        check("release のあとは作業先の作業ツリーの書き換えも直打ちも通す",
              run_guard(tmp, wt1, f"echo x > {work_tree}/f.txt") is None
              and run_guard(tmp, wt1, f"cd {work_tree} && ./check.sh") is None)


HOOKS_JSON = os.path.join(HERE, "..", "..", "..", "..", "hooks", "hooks.json")


PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))


def _plugin_hooks() -> list[tuple[str, str | None, list[str]]]:
    """hooks.json の hook ごとの（イベント, matcher, exec 形の argv）。argv の `${CLAUDE_PLUGIN_ROOT}` は置き換えない。"""
    with open(HOOKS_JSON, encoding="utf-8") as f:
        hooks = json.load(f)["hooks"]
    return [(event, group.get("matcher"), [h["command"], *h.get("args", [])])
            for event, groups in hooks.items() for group in groups for h in group["hooks"]]


def _run_hook(argv: list[str], payload: dict | str, env: dict, root: str = PLUGIN_ROOT,
              cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    resolved = [a.replace("${CLAUDE_PLUGIN_ROOT}", root) for a in argv]
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(resolved, input=stdin, capture_output=True, text=True, env=env, cwd=cwd)


@weight(6)
def test_agent_scoped_guard() -> None:
    say("task.py commit-guard・handback-guard --agent-scoped: 名前を問わずサブエージェント（agent_id）に関門を掛ける")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, verify="`true`")
        disarm_ref_guard(main_path)
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        write(os.path.join(wt1, "work.txt"), "x\n")
        commit = {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": wt1}
        stop = {"hook_event_name": "SubagentStop", "cwd": wt1}
        for agent_id in ("a-writer-1", "a-writer-2", "a-writer-3"):
            _send_edit(tmp, wt1, agent_id)
        for subcommand, fields in (("commit-guard", commit), ("handback-guard", stop)):
            for flags, agent_id, agent_type, refused in (
                ((), None, None, True),
                ((), None, "general-purpose", True),
                (("--agent-scoped",), None, None, False),
                (("--agent-scoped",), None, "no-delegate", False),
                (("--agent-scoped",), "a-writer-1", "general-purpose", True),
                (("--agent-scoped",), "a-writer-2", "my-implementer", True),
                (("--agent-scoped",), "a-writer-3", "tsukumo-workflow:no-delegate", True),
                (("--agent-scoped",), "../escape", "no-delegate", False),
            ):
                ids = {"agent_id": agent_id} if agent_id is not None else {}
                payload = {**fields, **ids, **({} if agent_type is None else {"agent_type": agent_type})}
                r = run_task(tmp, subcommand, *flags, stdin=json.dumps(payload))
                check(f"{subcommand} {' '.join(flags)} agent_id={agent_id} agent_type={agent_type} は"
                      f"{'拒む' if refused else '通す'}",
                      r.returncode == 0 and (r.stdout != "") == refused, r.stdout + r.stderr)
        r = run_task(tmp, "commit-guard", "--agent-scoped",
                     stdin=json.dumps({**commit, "agent_id": "a-reader", "agent_type": "acceptor"}))
        check("commit-guard --agent-scoped は書いた印の無いサブエージェントも拒む",
              r.returncode == 0 and r.stdout != "", r.stdout + r.stderr)

        all_hooks = _plugin_hooks()
        check("hooks/hooks.json の hook はすべて exec 形で ${CLAUDE_PLUGIN_ROOT}/bin/tw-hook を呼ぶ",
              len(all_hooks) == 10 and all(argv[0] == "${CLAUDE_PLUGIN_ROOT}/bin/tw-hook" and len(argv) > 1 for _, _, argv in all_hooks),
              str(all_hooks))
        hooks = [argv for _, _, argv in all_hooks if "--agent-scoped" in argv]
        ask_hooks = [argv for _, _, argv in all_hooks if "ask-guard" in argv]
        lap_hooks = {(event, matcher): argv for event, matcher, argv in all_hooks if "lap-hook" in argv}
        check("hooks/hooks.json は SubagentStart・SubagentStop と Skill の PreToolUse で --agent-scoped なしの lap-hook を1つずつ呼ぶ",
              sorted(e for e, _ in lap_hooks) == ["PreToolUse", "SubagentStart", "SubagentStop"]
              and ("PreToolUse", "Skill") in lap_hooks
              and all(argv[1:] == ["lap-hook"] for argv in lap_hooks.values()),
              str(lap_hooks))
        start_matcher = next((m for e, m in lap_hooks if e == "SubagentStart"), None) or ""
        stop_matcher = next((m for e, m in lap_hooks if e == "SubagentStop"), None) or ""
        check("lap-hook の matcher は lap_hook の実装役・受け入れ役の名前（裸と plugin 付き）に当たり、ほかに当たらない",
              all(re.search(start_matcher, f"{p}{t}") for t in lap_hook.IMPLEMENTER_TYPES for p in ("", "tsukumo-workflow:"))
              and all(re.search(stop_matcher, f"{p}{lap_hook.ACCEPTOR_TYPE}") for p in ("", "tsukumo-workflow:"))
              and not any(re.search(m, n) for m in (start_matcher, stop_matcher)
                          for n in ("Explore", "", "tsukumo-workflow:acceptor-x", "no-delegate-x"))
              and not re.search(stop_matcher, "tsukumo-workflow:no-delegate"),
              f"{start_matcher!r} {stop_matcher!r}")
        check("hooks/hooks.json は --agent-scoped 付きで commit-guard・handback-guard を呼ぶ hook を4つ持つ",
              len(hooks) == 4 and all(a in ("commit-guard", "handback-guard") for a in (argv[-2] for argv in hooks)),
              str(all_hooks))
        check("hooks/hooks.json は Edit・Write・NotebookEdit の PreToolUse で handback-guard を呼ぶ",
              any(e == "PreToolUse" and m == "Edit|Write|NotebookEdit" and "handback-guard" in argv and "--agent-scoped" in argv
                  for e, m, argv in all_hooks),
              str(all_hooks))
        check("hooks/hooks.json は --agent-scoped なしで ask-guard を呼ぶ hook を1つ持つ",
              len(ask_hooks) == 1 and "--agent-scoped" not in ask_hooks[0],
              str(all_hooks))
        env = hook_env(tmp, f"{os.path.dirname(sys.executable)}:/usr/bin:/bin")
        for hook in ask_hooks:
            payload = {"tool_name": "AskUserQuestion", "tool_input": {}, "cwd": wt1}
            r = _run_hook(hook, payload, env)
            check(f"hooks.json の {hook} は agent_type が無くても loopable: Y の控えで拒む",
                  r.returncode == 0 and '"deny"' in r.stdout, r.stdout + r.stderr)
        for hook in hooks:
            fields = commit if "commit-guard" in hook else stop
            for agent_id, refused in (("a-writer-1", True), (None, False)):
                payload = {**fields, **({} if agent_id is None else {"agent_id": agent_id, "agent_type": "my-implementer"})}
                r = _run_hook(hook, payload, env)
                check(f"hooks.json の {hook} は agent_id={agent_id} で{'拒む' if refused else '通す'}",
                      r.returncode == 0 and (r.stdout != "") == refused, r.stdout + r.stderr)
        start_hook = next((argv for (e, _), argv in lap_hooks.items() if e == "SubagentStart"), ["/bin/false"])
        payload = {"hook_event_name": "SubagentStart", "cwd": wt1, "agent_id": "a-new", "agent_type": "tsukumo-workflow:no-delegate"}
        r = _run_hook(start_hook, payload, env)
        delegate_laps = [e for e in flow_rows(wt1) if e["event"] == "lap" and e.get("stage") == "delegate"]
        check("hooks.json の SubagentStart の lap-hook は控え1件の作業ツリーに delegate の start を書き、stdout は空",
              r.returncode == 0 and r.stdout == "" and [(e["task"], e.get("edge")) for e in delegate_laps] == [("T-100", "start")],
              repr(delegate_laps) + r.stdout + r.stderr)
        run_task(wt1, "lap", "T-100", "review", "start")
        stop_hook = next((argv for (e, _), argv in lap_hooks.items() if e == "SubagentStop"), ["/bin/false"])
        payload = {"hook_event_name": "SubagentStop", "cwd": wt1, "agent_id": "a-acc", "agent_type": "tsukumo-workflow:acceptor"}
        r = _run_hook(stop_hook, payload, env)
        review_laps = [e for e in flow_rows(wt1) if e["event"] == "lap" and e.get("stage") == "review"]
        check("hooks.json の SubagentStop の lap-hook は開いている review の end を書き、stdout は空",
              r.returncode == 0 and r.stdout == "" and [(e["task"], e.get("edge")) for e in review_laps] == [("T-100", "start"), ("T-100", "end")],
              repr(review_laps) + r.stdout + r.stderr)
        beat_hooks = [(e, m, argv) for e, m, argv in all_hooks if "heartbeat" in argv]
        check("hooks/hooks.json は PostToolUse・PostToolUseFailure で matcher の無い heartbeat を1つずつ呼ぶ",
              sorted(e for e, _, _ in beat_hooks) == ["PostToolUse", "PostToolUseFailure"]
              and all(m is None and argv[1:] == ["heartbeat"] for _, m, argv in beat_hooks),
              str(beat_hooks))
        beats = os.path.join(wt1, ".tw", "local", ledger.AGENT_BEATS_DIR_NAME)
        for event, _, hook in beat_hooks:
            tool = {"hook_event_name": event, "tool_name": "Read", "cwd": wt1}
            agent = f"a-beat-{event}"
            r = _run_hook(hook, {**tool, "agent_id": agent}, env, cwd=wt1)
            check(f"hooks.json の {event} の heartbeat は agent_id 付きなら控えのある作業ツリーに鼓動を書く",
                  r.returncode == 0 and r.stdout == "" and os.path.isfile(os.path.join(beats, agent)), r.stdout + r.stderr)
            before = sorted(os.listdir(beats))
            r = _run_hook(hook, tool, env, cwd=wt1)
            check(f"hooks.json の {event} の heartbeat は agent_id が無ければ書かない",
                  r.returncode == 0 and r.stdout == "" and sorted(os.listdir(beats)) == before, r.stdout + r.stderr)
        fake_root = tempfile.mkdtemp(dir=tmp)
        os.makedirs(os.path.join(fake_root, "bin"))
        shutil.copy2(os.path.join(PLUGIN_ROOT, "bin", "tw-hook"), os.path.join(fake_root, "bin", "tw-hook"))
        fake_tw = os.path.join(fake_root, "bin", "tw")
        write(fake_tw, "#!/bin/sh\necho boom >&2\nexit 1\n")
        os.chmod(fake_tw, 0o755)
        crashed = {"hook_event_name": "PreToolUse", "cwd": wt1, "agent_id": "a-writer-1"}
        for event, matcher, hook in all_hooks:
            if hook[1:] == ["heartbeat"]:
                r = _run_hook(hook, crashed, env, root=fake_root, cwd=wt1)
                check(f"hooks.json の {event} の heartbeat は tw を呼ばないので、tw が落ちても終了コード0",
                      r.returncode == 0 and r.stdout == "" and "boom" not in r.stderr, r.stdout + r.stderr)
                continue
            blocks = event == "PreToolUse" and any(g in hook for g in ("commit-guard", "handback-guard", "ask-guard"))
            r = _run_hook(hook, crashed, env, root=fake_root)
            check(f"hooks.json の {event}（matcher {matcher}）の {hook[1:]} は、サブエージェントで tw が落ちると"
                  f"終了コード {2 if blocks else 1}（{'止める' if blocks else '通す'}）",
                  r.returncode == (2 if blocks else 1) and r.stdout == "" and "boom" in r.stderr, r.stdout + r.stderr)


def _send_edit(tmp: str, where: str, agent_id: str, file_path: str | None = None, tool_input: dict | None = None) -> None:
    """サブエージェントの `Edit` の `PreToolUse` を `handback-guard --agent-scoped` に渡す（書いた印を残させる）。

    書き先 `file_path` の既定は `where` の中。`tool_input` を渡すとそのまま使う。
    """
    if tool_input is None:
        tool_input = {"file_path": file_path or os.path.join(where, "work.txt")}
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": tool_input, "cwd": where, "agent_id": agent_id}
    r = run_task(tmp, "handback-guard", "--agent-scoped", stdin=json.dumps(payload))
    if r.returncode != 0 or r.stdout != "":
        raise RuntimeError(f"handback-guard の Edit が {r.returncode} で {r.stdout!r} を出した: {r.stderr}")


def _scoped_handback(tmp: str, where: str, agent_id: str, body: str | None = None) -> str:
    """`handback-guard --agent-scoped` にサブエージェントの返却を渡す。`body` があれば `SubagentHandback`、無ければ `SubagentStop`。"""
    if body is None:
        payload: dict = {"hook_event_name": "SubagentStop", "cwd": where}
    else:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "SubagentHandback", "tool_input": {"message": body}, "cwd": where}
    r = run_task(tmp, "handback-guard", "--agent-scoped", stdin=json.dumps({**payload, "agent_id": agent_id, "agent_type": "acceptor"}))
    if r.returncode != 0:
        raise RuntimeError(f"handback-guard が {r.returncode} で終わった: {r.stderr}")
    return r.stdout


def _send_bash(tmp: str, where: str, **ids: str) -> None:
    """サブエージェントの `Bash` の `PreToolUse` を `commit-guard --agent-scoped` に渡す（`agent` の出来事を残させる）。

    `agent_id`・`session_id` の入り方は Agent SDK の `BaseHookInput` の型定義に従う（実機の hook 入力のダンプではない）。
    """
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "tw show T-100"}, "cwd": where, **ids}
    r = run_task(tmp, "commit-guard", "--agent-scoped", stdin=json.dumps(payload))
    if r.returncode != 0 or r.stdout != "":
        raise RuntimeError(f"commit-guard の Bash が {r.returncode} で {r.stdout!r} を出した: {r.stderr}")


def _block_reason(out: dict | None) -> str:
    return str(out.get("reason", "")) if out is not None and out.get("decision") == "block" else ""


@weight(20)
def test_handback_guard() -> None:
    say("task.py handback-guard・pause: 作業があるのに計画か検証が欠けた返却を拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "先に計画", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "計画なしで作業", "todo", "sonnet", "Y", (), BODY))

        check("着手の印が無い委譲は通す", run_handback_guard(tmp, wt1) is None)
        run_task(wt1, "claim", "T-100")
        check("印があっても作業が無ければ通す（前提が誤り・dropped）", run_handback_guard(tmp, wt1) is None)
        r = run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n")
        check("計画だけの回は通す（タスクのファイルの変更は作業に数えない）",
              r.returncode == 0 and run_handback_guard(tmp, wt1) is None, r.stdout + r.stderr)

        write(os.path.join(wt1, "work.txt"), "x\n")
        reason = _block_reason(run_handback_guard(tmp, wt1))
        check("計画があっても検証が無ければ SubagentStop を block し、理由に verify-check の行と次の一手",
              "NOT_VERIFIED\tnone" in reason and "PLAN_NOT_FIRST" not in reason and "tw verify" in reason
              and "tw pause" in reason and "T-100" in reason, reason)
        out = run_handback_guard(tmp, wt1, "PreToolUse", "SubagentHandback")
        check("SubagentHandback の PreToolUse は deny で理由を返す",
              out is not None and out["hookSpecificOutput"]["permissionDecision"] == "deny"
              and "NOT_VERIFIED" in out["hookSpecificOutput"]["permissionDecisionReason"], str(out))
        check("ほかのツールの PreToolUse には何も出さない", run_handback_guard(tmp, wt1, "PreToolUse", "Bash") is None)
        check("ほかのイベントには何も出さない", run_handback_guard(tmp, wt1, "PostToolUse", "SubagentHandback") is None)

        r = run_task(wt1, "verify")
        check("plan-check が PLAN_FIRST で tw verify が通れば通す",
              r.returncode == 0 and run_handback_guard(tmp, wt1) is None, r.stdout + r.stderr)
        stamp_path = os.path.join(stamps.worktree_state_dir(wt1), stamps.VERIFY_STAMP_FILE_NAME)
        with open(stamp_path, encoding="utf-8") as f:
            stamp_lines = f.read().splitlines()
        write(stamp_path, "\n".join([*stamp_lines[:-1], "scheme:other"]) + "\n")
        reason = _block_reason(run_handback_guard(tmp, wt1))
        check("別の版の tw が控えていれば block し、理由に scheme", "NOT_VERIFIED\tscheme" in reason, reason)
        write(stamp_path, "\n".join(stamp_lines) + "\n")
        write(os.path.join(wt1, "work.txt"), "y\n")
        check("検証のあとに中身を変えると block（NOT_VERIFIED content）",
              "NOT_VERIFIED\tcontent" in _block_reason(run_handback_guard(tmp, wt1)))
        r = run_task(wt1, "pause")
        check("tw pause で PAUSED を出し、いまの中身なら通す（目視待ち・止めて返す）",
              r.returncode == 0 and r.stdout.startswith("PAUSED\t") and run_handback_guard(tmp, wt1) is None,
              r.stdout + r.stderr)
        write(os.path.join(wt1, "work.txt"), "z\n")
        check("pause のあとに中身を変えると block", _block_reason(run_handback_guard(tmp, wt1)) != "")

        run_task(wt2, "claim", "T-101")
        git(wt2, "commit", "-q", "--allow-empty", "-m", "claim のあとのコミット")
        reason = _block_reason(run_handback_guard(tmp, wt2))
        check("claim のあとのコミットも作業に数え、計画も検証も無ければ両方の行を理由に書く",
              "PLAN_NOT_FIRST\tT-101\tmissing" in reason and "NOT_VERIFIED\tnone" in reason, reason)
        r = run_task(wt2, "edit", "T-101", "--section", "やること", "--after-work", "--body-file", "-", stdin="### 1. 書く\n")
        run_task(wt2, "verify")
        check("作業の後に書いた計画は検証が通っても block（PLAN_NOT_FIRST after-work）",
              "PLAN_NOT_FIRST\tT-101\tafter-work" in _block_reason(run_handback_guard(tmp, wt2)), r.stdout)
        run_task(wt2, "pause")
        check("pause を打てば通す", run_handback_guard(tmp, wt2) is None)
        run_task(wt2, "release", "T-101")
        check("release のあとは印が無いので通す", run_handback_guard(tmp, wt2) is None)

        r = run_task(tmp, "handback-guard", stdin="not json")
        check("読めない入力は何も出さずに通す", r.returncode == 0 and r.stdout == "", r.stdout + r.stderr)


@weight(11)
def test_handback_guard_step() -> None:
    say("task.py step・handback-guard: 途中の段の返却は tw step で通し、最後の段は検証を求める")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "段ごと", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "後から書く", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n\n### 2. 試す\n")

        write(os.path.join(wt1, "work.txt"), "x\n")
        check("段の印も検証も無ければ block", "NOT_VERIFIED" in _block_reason(run_handback_guard(tmp, wt1)))
        r = run_task(wt1, "step", "T-100", "1")
        check("途中の段は STEPPED（n/N）を出し、いまの中身なら返却を通す", r.returncode == 0
              and r.stdout.startswith("STEPPED\tT-100\t1/2\t") and run_handback_guard(tmp, wt1) is None,
              r.stdout + r.stderr)
        write(os.path.join(wt1, "work.txt"), "y\n")
        check("段の印のあとに中身を変えると block", "NOT_VERIFIED" in _block_reason(run_handback_guard(tmp, wt1)))
        r = run_task(wt1, "step", "T-100", "2")
        check("最後の段は LAST_STEP（終了コード4）で印を残さず、返却は検証が無ければ block", r.returncode == 4
              and r.stdout.startswith("LAST_STEP\tT-100\t2/2\t")
              and "NOT_VERIFIED" in _block_reason(run_handback_guard(tmp, wt1)), r.stdout + r.stderr)
        r = run_task(wt1, "verify")
        check("最後の段は tw verify が通れば通す", r.returncode == 0 and run_handback_guard(tmp, wt1) is None,
              r.stdout + r.stderr)
        for label, args in (("段の外の番号", ("T-100", "3")), ("数でない番号", ("T-100", "x"))):
            r = run_task(wt1, "step", *args)
            check(f"{label}は終了コード2", r.returncode == 2 and "usage:" in r.stderr, r.stdout + r.stderr)
        r = run_task(wt1, "step", "T-101", "1")
        check("自分が着手していないタスクは NOT_OWNER（終了コード4）", r.returncode == 4
              and r.stdout.strip() == "NOT_OWNER\tT-101", r.stdout + r.stderr)

        run_task(wt2, "claim", "T-101")
        write(os.path.join(wt2, "work.txt"), "x\n")
        run_task(wt2, "edit", "T-101", "--section", "やること", "--after-work", "--body-file", "-",
                 stdin="### 1. 書く\n\n### 2. 試す\n")
        r = run_task(wt2, "step", "T-101", "1")
        check("段の印があっても計画を作業の後に書いたなら block（PLAN_NOT_FIRST after-work）", r.returncode == 0
              and "PLAN_NOT_FIRST\tT-101\tafter-work" in _block_reason(run_handback_guard(tmp, wt2)), r.stdout + r.stderr)


def _commit_files(repo: str, **files: str) -> None:
    for name, text in files.items():
        write(os.path.join(repo, name), text)
        git(repo, "add", name)
    git(repo, "commit", "-q", "-m", "主ブランチが進む")


@weight(15)
def test_handback_guard_skipped_steps() -> None:
    say("task.py handback-guard: 段が2つ以上の計画で tw step の控えが無いまま verify を通した返却は拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "三段", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "一段", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-",
                 stdin="### 1. 書く\n\n### 2. 試す\n\n### 3. 直す\n")
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "verify")
        reason = _block_reason(run_handback_guard(tmp, wt1))
        check("step の控えが無いまま verify を通すと、tw step を打って返す旨で block",
              r.returncode == 0 and "段の控えが無い" in reason and "tw step T-100" in reason, r.stdout + r.stderr + reason)
        r = run_task(wt1, "step", "T-100", "1")
        run_task(wt1, "verify")
        check("step の控えが1つあれば通る", r.returncode == 0 and run_handback_guard(tmp, wt1) is None,
              r.stdout + r.stderr)

        run_task(wt2, "claim", "T-101")
        run_task(wt2, "edit", "T-101", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n")
        write(os.path.join(wt2, "work.txt"), "x\n")
        run_task(wt2, "verify")
        check("段が1つの計画は控え無しで通る", run_handback_guard(tmp, wt2) is None)


@weight(15)
def test_handback_guard_parallel_steps() -> None:
    say("task.py step・pause・handback-guard: 同じ作業ツリーで並列の段の担当どうしが互いの控えを崩さない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "並列の段", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        plan = (
            "### 1. 書く\n- 触るファイル: `a.txt`\n"
            "### 2. 文書\n- 前の段: なし\n- 触るファイル: `b.txt`\n"
            "### 3. 合わせる\n"
        )
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin=plan)
        write(os.path.join(wt1, "a.txt"), "1\n")
        write(os.path.join(wt1, "b.txt"), "1\n")
        r = run_task(wt1, "step", "T-100", "1")
        check("段1の担当の tw step は通る", r.returncode == 0 and r.stdout.startswith("STEPPED\tT-100\t1/3\t"),
              r.stdout + r.stderr)
        write(os.path.join(wt1, "b.txt"), "2\n")
        check("段2の担当が段2の触るファイルを書き換えても、段1の返却は通る", run_handback_guard(tmp, wt1) is None)
        write(os.path.join(wt1, "a.txt"), "2\n")
        check("段1の触るファイルを書き換えれば段1の控えは崩れる",
              "NOT_VERIFIED" in _block_reason(run_handback_guard(tmp, wt1)))
        run_task(wt1, "step", "T-100", "1")
        r = run_task(wt1, "step", "T-100", "2")
        steps = sorted(s.step for s in stamps.read_step_stamps(cwd=wt1) if s.task_id == "T-100")
        check("段2の担当の tw step は段1の控えを上書きせず、段ごとに残る", r.returncode == 0 and steps == [1, 2]
              and run_handback_guard(tmp, wt1) is None, r.stdout + r.stderr + repr(steps))
        write(os.path.join(wt1, "c.txt"), "1\n")
        check("どの並列の段の触るファイルでもない書き換えは、どちらの控えも崩す",
              "NOT_VERIFIED" in _block_reason(run_handback_guard(tmp, wt1)))
        r = run_task(wt1, "pause", "T-100", "2")
        write(os.path.join(wt1, "a.txt"), "3\n")
        check("tw pause <ID> 2 は段1の触るファイルの書き換えで崩れない", r.returncode == 0
              and r.stdout.startswith("PAUSED\t") and run_handback_guard(tmp, wt1) is None, r.stdout + r.stderr)
        write(os.path.join(wt1, "b.txt"), "3\n")
        check("tw pause <ID> 2 は段2の触るファイルの書き換えで崩れる",
              "NOT_VERIFIED" in _block_reason(run_handback_guard(tmp, wt1)))
        r = run_task(wt1, "pause", "T-100", "3")
        check("最後の段も tw pause <ID> <n> で控えられる", r.returncode == 0 and run_handback_guard(tmp, wt1) is None,
              r.stdout + r.stderr)
        r = run_task(wt1, "pause", "T-100", "4")
        check("tw pause の段の外の番号は終了コード2", r.returncode == 2 and "usage:" in r.stderr, r.stdout + r.stderr)
        r = run_task(wt1, "pause", "T-100")
        check("tw pause <ID>（着手中）は引数なしと同じに PAUSED を出して控える", r.returncode == 0
              and r.stdout.startswith("PAUSED\t") and run_handback_guard(tmp, wt1) is None, r.stdout + r.stderr)
        r = run_task(wt1, "pause", "T-999")
        check("tw pause <ID>（着手していない）は NOT_OWNER（終了コード4）", r.returncode == 4
              and r.stdout.strip() == "NOT_OWNER\tT-999", r.stdout + r.stderr)
        stamps.write_step_stamp(stamps.StepStamp(stamps.content_key("", cwd=wt1), "T-999", 1), cwd=wt1)
        run_task(wt1, "step", "T-100", "1")
        check("着手中でないタスクの段の控えは tw step が消す",
              all(s.task_id == "T-100" for s in stamps.read_step_stamps(cwd=wt1)))


@weight(18)
def test_handback_guard_other_repo() -> None:
    say("task.py step・pause・handback-guard: 作業先が別のリポジトリなら、枝の名前がタスクIDの作業ツリーも見る")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(os.path.join(tmp, "own"), verify="`true`")
        work_repo, _, _ = make_repo(os.path.join(tmp, "work"), verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "作業先で直す", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        plan = f"### 1. 書く\n\n### 2. 試す\n\n### 作業先\n- `{work_repo}`\n"
        r = run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin=plan)
        check("作業先の作業ツリーがまだ無ければ通す（計画だけの回）",
              r.returncode == 0 and run_handback_guard(tmp, wt1) is None, r.stdout + r.stderr)

        work_tree = os.path.join(tmp, "work-t-100")
        git(work_repo, "worktree", "add", "-q", "-b", "t-100", work_tree, "main")
        write(os.path.join(work_tree, "work.txt"), "x\n")
        git(work_tree, "add", "work.txt")
        git(work_tree, "commit", "-q", "-m", "作業先で直す")
        reason = _block_reason(run_handback_guard(tmp, wt1))
        check("作業先の作業ツリーにコミットがあり検証が無ければ、最後の段の返却を block し、理由に作業ツリーのパス",
              "NOT_VERIFIED\tnone" in reason and work_tree in reason, reason)

        r = run_task(wt1, "step", "T-100", "1")
        check("着手した作業ツリーで tw step を打てば、途中の段の返却を通す",
              r.returncode == 0 and r.stdout.startswith("STEPPED\tT-100\t1/2\t") and run_handback_guard(tmp, wt1) is None,
              r.stdout + r.stderr)
        write(os.path.join(work_tree, "work.txt"), "y\n")
        check("段の印のあとに作業先の中身を変えると block",
              "NOT_VERIFIED" in _block_reason(run_handback_guard(tmp, wt1)))

        r = run_task(wt1, "pause")
        check("着手した作業ツリーで tw pause を打てば、作業先の中身が同じあいだ通す",
              r.returncode == 0 and run_handback_guard(tmp, wt1) is None, r.stdout + r.stderr)
        write(os.path.join(work_tree, "work.txt"), "z\n")
        git(work_tree, "commit", "-q", "-am", "直し直す")
        check("pause のあとに作業先の中身を変えると block", _block_reason(run_handback_guard(tmp, wt1)) != "")

        r = run_task(work_tree, "verify")
        check("作業先の作業ツリーで tw verify が通れば（VERIFIED_SAME）最後の段の返却を通す",
              r.returncode == 0 and run_handback_guard(tmp, wt1) is None, r.stdout + r.stderr)

        bare_repo = os.path.join(tmp, "bare")
        os.makedirs(bare_repo)
        git(bare_repo, "init", "-q", "-b", "main")
        git(bare_repo, "config", "user.email", "test@example.com")
        git(bare_repo, "config", "user.name", "test")
        write(os.path.join(bare_repo, "CLAUDE.md"), "# x\n\n## タスク運用\n\n- 検証コマンド: `true`\n")
        git(bare_repo, "add", "-A")
        git(bare_repo, "commit", "-q", "-m", "init")
        bare_tree = os.path.join(tmp, "bare-t-101")
        git(bare_repo, "worktree", "add", "-q", "-b", "t-101", bare_tree, "main")
        write(os.path.join(bare_tree, "work.txt"), "x\n")
        git(bare_tree, "add", "work.txt")
        git(bare_tree, "commit", "-q", "-m", "作業先で直す")
        commit_task(main_path, taskfile.Task("T-101", "台帳の無い作業先", "todo", "sonnet", "Y", (), BODY))
        run_task(wt2, "claim", "T-101")
        run_task(wt2, "edit", "T-101", "--section", "やること", "--body-file", "-",
                 stdin=f"### 1. 書く\n\n### 作業先\n- `{bare_repo}`\n")
        r = run_task(bare_tree, "verify")
        check(".tw/config.toml が無い作業先では、tw verify が MISSING で、最後の段の返却を通す",
              r.returncode == 6 and r.stdout.strip() == "MISSING" and run_handback_guard(tmp, wt2) is None,
              r.stdout + r.stderr)

        ledgerless = os.path.join(tmp, "ledgerless")
        os.makedirs(ledgerless)
        git(ledgerless, "init", "-q", "-b", "main")
        git(ledgerless, "config", "user.email", "test@example.com")
        git(ledgerless, "config", "user.name", "test")
        write(os.path.join(ledgerless, ".tw", "config.toml"), 'verify = "true"\n')
        write(os.path.join(ledgerless, ".tw", ".gitignore"), "local/\n")
        git(ledgerless, "add", "-A")
        git(ledgerless, "commit", "-q", "-m", "init")
        ledgerless_tree = os.path.join(tmp, "ledgerless-t-102")
        git(ledgerless, "worktree", "add", "-q", "-b", "t-102", ledgerless_tree, "main")
        write(os.path.join(ledgerless_tree, "work.txt"), "x\n")
        git(ledgerless_tree, "add", "work.txt")
        git(ledgerless_tree, "commit", "-q", "-m", "作業先で直す")
        main_wt3 = os.path.join(tmp, "wt3")
        git(main_path, "worktree", "add", "-q", "-b", "wt3", main_wt3, "main")
        commit_task(main_path, taskfile.Task("T-102", "台帳は無いが設定がある作業先", "todo", "sonnet", "Y", (), BODY))
        run_task(main_wt3, "claim", "T-102")
        run_task(main_wt3, "edit", "T-102", "--section", "やること", "--body-file", "-",
                 stdin=f"### 1. 書く\n\n### 作業先\n- `{ledgerless}`\n")
        reason = _block_reason(run_handback_guard(tmp, main_wt3))
        check(".tw/config.toml が在る作業先では、控えが無ければ最後の段の返却を block し、理由に作業ツリーのパス",
              "NOT_VERIFIED\tnone" in reason and ledgerless_tree in reason, reason)
        r = run_task(ledgerless_tree, "verify")
        check(".beads の無い作業先でも tw verify が通れば最後の段の返却を通す",
              r.returncode == 0 and r.stdout.startswith("VERIFIED\t") and run_handback_guard(tmp, main_wt3) is None,
              r.stdout + r.stderr)
        write(os.path.join(ledgerless_tree, "work.txt"), "y\n")
        check("控えのあとに中身を変えると block", "NOT_VERIFIED" in _block_reason(run_handback_guard(tmp, main_wt3)))


def test_readonly_git() -> None:
    say("task.py: .git が読み取り専用でも claim・verify・verify-check・step が通り、git に書く claim・verify は GIT_READ_ONLY")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない", verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "読み取り専用", "todo", "sonnet", "Y", (), BODY))
        git(wt1, "merge", "-q", "--ff-only", "main")
        state = os.path.join(tmp, "state")
        env = {ledger.STATE_DIR_ENV: state}
        with readonly_git(main_path):
            r = run_task(wt1, "claim", "T-100", env=env)
            check("claim が CLAIMED", r.returncode == 0 and r.stdout.startswith("CLAIMED\tT-100\t"), r.stdout + r.stderr)
            r = run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-",
                         stdin="### 1. 書く\n\n### 2. 試す\n", env=env)
            check("edit が EDITED", r.returncode == 0, r.stdout + r.stderr)
            write(os.path.join(wt1, "work.txt"), "x\n")
            r = run_task(wt1, "step", "T-100", "1", env=env)
            check("step が STEPPED", r.returncode == 0 and r.stdout.startswith("STEPPED\tT-100\t1/2\t"),
                  r.stdout + r.stderr)
            r = run_task(wt1, "verify", env=env)
            verified = r.stdout.splitlines()[0] if r.stdout else ""
            check("verify が VERIFIED", r.returncode == 0 and verified.startswith("VERIFIED\t"), r.stdout + r.stderr)
            r = run_task(wt1, "verify-check", env=env)
            check("verify-check が VERIFIED_SAME", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout + r.stderr)
        tree = verified.split("\t")[1] if verified.count("\t") >= 1 else ""
        check("読み取り専用で取った鍵の木の SHA が、書ける .git で取った木と同じ",
              tree != "" and tree == stamps.content_tree(wt1), tree)

        write(os.path.join(main_path, "shared.txt"), "line2\n")
        git(main_path, "commit", "-q", "-am", "主ブランチを進める")
        head = git(wt1, "rev-parse", "HEAD").stdout
        before = snapshot(wt1, state)
        with readonly_git(main_path):
            r = run_task(wt1, "verify", env=env)
            check("取り込みが要る verify は GIT_READ_ONLY（終了コード11）で、検証コマンドを打たない",
                  r.returncode == 11 and r.stdout.startswith("GIT_READ_ONLY\t") and "sandbox" in r.stdout,
                  r.stdout + r.stderr)
            check("止まった verify は HEAD と作業ツリーを変えない",
                  git(wt1, "rev-parse", "HEAD").stdout == head
                  and {p: c for p, c in snapshot(wt1).items() if "/.tw/" not in p}
                  == {p: c for p, c in before.items() if p.startswith(wt1 + os.sep) and "/.tw/" not in p})

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="既定", verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "枝を切る", "todo", "sonnet", "Y", (), BODY))
        state = os.path.join(tmp, "state")
        branch = git(wt1, "rev-parse", "--abbrev-ref", "HEAD").stdout
        with readonly_git(main_path):
            r = run_task(wt1, "claim", "T-100", env={ledger.STATE_DIR_ENV: state})
        check("枝を切る claim は印を立てる前に GIT_READ_ONLY（終了コード11）で止まる",
              r.returncode == 11 and r.stdout.startswith("GIT_READ_ONLY\t")
              and beads.CLAIM_HEAD_KEY not in metadata(main_path, "T-100")
              and not os.path.exists(os.path.join(wt1, ".tw", "local", "task-open-claims"))
              and git(wt1, "rev-parse", "--abbrev-ref", "HEAD").stdout == branch, r.stdout + r.stderr)


def _raw_git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    """`ref-guard` を外す変数を付けない git（委譲先やメインが手で打つ git）。失敗しても例外にしない。"""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=git_env(allow_ref_update=False))


def _raw_sh(cwd: str, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", command], cwd=cwd, capture_output=True, text=True, env=git_env(allow_ref_update=False))


def _ref_hook(main_path: str) -> str:
    return os.path.join(gitinfo.git_common_dir(main_path), "hooks", ref_guard.HOOK_NAME)


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


@weight(6)
def test_ref_guard_blocks_commits() -> None:
    say("task.py ref-guard: 印が立って done 前の作業ツリーで、書き方と呼び手を問わずコミットを拒み、tw の git は通す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "done のあと", "todo", "sonnet", "Y", (), BODY))
        r = run_task(wt1, "claim", "T-100")
        hook = _ref_hook(main_path)
        check("claim で印付きの reference-transaction が置かれ、実行できる",
              r.returncode == 0 and os.access(hook, os.X_OK) and ref_guard.MARKER in _read_bytes(hook).decode(),
              r.stdout + r.stderr)

        write(os.path.join(wt1, "work.txt"), "x\n")
        _raw_git(wt1, "add", "work.txt")
        head = _raw_git(wt1, "rev-parse", "HEAD").stdout.strip()
        for label, run in (
            ("git commit -m x", lambda: _raw_git(wt1, "commit", "-q", "-m", "x")),
            ("sh -c 'git commit --no-verify -m x'", lambda: _raw_sh(wt1, "git commit -q --no-verify -m x")),
            ("git commit --amend", lambda: _raw_git(wt1, "commit", "-q", "--amend", "-m", "x")),
        ):
            done = run()
            check(f"呼び手を問わず（メインが手で打っても）拒む: {label}",
                  done.returncode != 0 and "着手の印" in done.stderr
                  and _raw_git(wt1, "rev-parse", "HEAD").stdout.strip() == head, done.stdout + done.stderr)

        write(os.path.join(main_path, "advance.txt"), "m\n")
        _raw_git(main_path, "add", "advance.txt")
        advanced = _raw_git(main_path, "commit", "-q", "-m", "main を進める")
        check("控えの無い作業ツリーのコミットは通る（本体）", advanced.returncode == 0, advanced.stderr)
        ff = _raw_git(wt1, "merge", "-q", "--ff-only", "main")
        check("主ブランチへの早送りは通る", ff.returncode == 0, ff.stderr)
        stash_name = f"gh908-selftest-{os.getpid()}"
        stashed = _raw_git(wt1, "stash", "push", "-q", "-u", "-m", stash_name)
        sha = next((l.split()[0] for l in _raw_git(wt1, "stash", "list", "--format=%H %gs").stdout.splitlines()
                    if l.endswith(stash_name)), None)
        check("git stash は通る", stashed.returncode == 0 and sha is not None, stashed.stderr)
        if sha is not None:
            _raw_git(wt1, "stash", "apply", "-q", sha)
            _raw_git(wt1, "stash", "drop", "-q")

        write(os.path.join(wt2, "other.txt"), "o\n")
        _raw_git(wt2, "add", "other.txt")
        other = _raw_git(wt2, "commit", "-q", "-m", "別の作業ツリー")
        check("控えの無い別の作業ツリーのコミットは通る", other.returncode == 0, other.stderr)
        _raw_git(wt2, "reset", "-q", "--hard", "main")

        r = run_task(wt1, "finish", "T-100", "--result-file", "-", "--message", "T-100: 完了", "--add", "work.txt",
                     stdin=RESULT_FULL)
        kinds = [l.split("\t", 1)[0] for l in r.stdout.splitlines()]
        check("tw finish は DONE・COMMITTED・SHIPPED まで進む",
              r.returncode == 0 and [k for k in kinds if k in ("DONE", "COMMITTED", "SHIPPED")] == ["DONE", "COMMITTED", "SHIPPED"],
              r.stdout + r.stderr)

        run_task(wt2, "claim", "T-101")
        write(os.path.join(wt2, "after.txt"), "a\n")
        _raw_git(wt2, "add", "after.txt")
        before = _raw_git(wt2, "commit", "-q", "-m", "done の前")
        check("done の前は素の git commit を拒む", before.returncode != 0 and "T-101" in before.stderr, before.stderr)
        run_task(wt2, "done", "T-101", "--result-file", "-", stdin=RESULT_FULL)
        after = _raw_git(wt2, "commit", "-q", "-m", "done のあと")
        check("tw done のあとの素の git commit は通る", after.returncode == 0, after.stderr)


def test_commit_guard_with_ref_guard() -> None:
    say("task.py commit-guard: ref-guard が効く作業ツリーではコミットを git の hook に任せ、外す変数は拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "任せる", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        check("git commit の入力は通す（hook に任せる）", run_guard(tmp, wt1, "git commit -m x") is None)
        reason = run_guard(tmp, wt1, "TW_REF_GUARD_ALLOW=1 git commit -m x")
        check("TW_REF_GUARD_ALLOW を付けたコマンドは拒む",
              reason is not None and "TW_REF_GUARD_ALLOW" in reason and "T-100" in reason, str(reason))
        check("export TW_REF_GUARD_ALLOW も拒む", run_guard(tmp, wt1, "export TW_REF_GUARD_ALLOW; git commit -m x") is not None)
        check("名前を読むだけのコマンドは通す", run_guard(tmp, wt1, "grep -rn TW_REF_GUARD_ALLOW .") is None)
        for command in (
            "git -c core.hooksPath=/dev/null commit -m x",
            "sh -c 'git -c core.hooksPath=/dev/null commit -m x'",
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null sh ./c.sh",
            "GIT_CONFIG_PARAMETERS=\"'core.hooksPath'='/dev/null'\" git commit -m x",
            "git --config-env=core.hooksPath=HOOKS commit -m x",
            "git config core.hooksPath /dev/null",
            "git config --global core.hooksPath /tmp/hooks",
            "git config set core.hooksPath /tmp/hooks",
            "sh -c 'git config core.hooksPath /tmp/hooks'",
        ):
            reason = run_guard(tmp, wt1, command)
            check(f"core.hooksPath を指し直すコマンドは拒む: {command}",
                  reason is not None and "core.hooksPath" in reason, str(reason))
        for command in (
            "grep -rn hooksPath .",
            "rg -n core.hooksPath skills",
            "git config --get core.hooksPath",
            "git config get core.hooksPath",
            "git config core.hooksPath",
            "git config core.hooksPath 2>/dev/null",
            "git config --list | grep -i hookspath",
            "git log --grep hooksPath",
            "git config --unset core.hooksPath",
        ):
            check(f"hooksPath を読むだけ（か外して既定に戻す）のコマンドは通す: {command}", run_guard(tmp, wt1, command) is None)


def _guard_in_env(tmp: str, where: str, command: str, env: dict[str, str]) -> str | None:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": where})
    r = run_task(tmp, "commit-guard", stdin=payload, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"commit-guard が {r.returncode} で終わった: {r.stderr}")
    return None if r.stdout == "" else json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def _bin_with(tmp: str, name: str, links: dict[str, str]) -> str:
    """`links` の `{名前: 実体のパス}` だけを置いたディレクトリ。"""
    d = os.path.join(tmp, name)
    os.makedirs(d)
    for link, target in links.items():
        os.symlink(target, os.path.join(d, link))
    return d


@weight(5)
def test_commit_guard_covers_inactive_ref_guard() -> None:
    say("task.py commit-guard: 控えがあり ref-guard が効かないどの場面でも、commit-guard が git commit を拒む")
    real_git = shutil.which("git") or "git"
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "効かない場面", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        hook = _ref_hook(main_path)
        body = _read_bytes(hook).decode()
        check("効いているあいだは commit-guard は通す", run_guard(tmp, wt1, "git commit -m x") is None)

        old_git = write(os.path.join(tmp, "old-git", "git"),
                        f'#!/bin/sh\n[ "$1" = --version ] && {{ echo "git version 2.27.0"; exit 0; }}\nexec {real_git} "$@"\n')
        os.chmod(old_git, 0o755)
        no_python = _bin_with(tmp, "no-python", {"git": real_git})
        for label, env in (
            ("git が 2.28 より古い", {"PATH": os.path.dirname(old_git) + os.pathsep + os.environ.get("PATH", "")}),
            ("python3 が PATH に無い", {"PATH": no_python}),
            ("環境に TW_REF_GUARD_ALLOW がある", {"TW_REF_GUARD_ALLOW": "1"}),
        ):
            check(f"{label}: commit-guard が git commit を拒む", _guard_in_env(tmp, wt1, "git commit -m x", env) is not None)

        for label, prepare in (
            ("殻の指す task.py が無い", lambda: write(hook, ref_guard.hook_body("/nonexistent/task.py"))),
            ("殻が実行できない", lambda: os.chmod(hook, 0o644)),
            ("殻が消えた", lambda: os.remove(hook)),
            ("利用者の hook に置き換わった", lambda: disarm_ref_guard(main_path)),
            ("core.hooksPath が設定された", lambda: git(main_path, "config", "core.hooksPath", os.path.join(tmp, "elsewhere"))),
        ):
            prepare()
            check(f"{label}: ref_guard.active は偽", not ref_guard.active(wt1), str(ref_guard.inactive_reason(wt1)))
            check(f"{label}: commit-guard が git commit を拒む", run_guard(tmp, wt1, "git commit -m x") is not None)
            _raw_git(main_path, "config", "--unset-all", "core.hooksPath")
            write(hook, body)
            os.chmod(hook, 0o755)
        check("殻を戻せば commit-guard はまた hook に任せる", run_guard(tmp, wt1, "git commit -m x") is None)


def _denial_rows(main_path: str) -> list[dict]:
    d = os.path.join(main_path, ".git", ledger.DENIALS_DIR_NAME)
    rows: list[dict] = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else ():
        with open(os.path.join(d, name), encoding="utf-8") as f:
            rows += [json.loads(line) for line in f if line.strip()]
    return rows


@weight(9)
def test_commit_guard_git_operations() -> None:
    say("task.py commit-guard: 印が立った作業ツリーで git stash・add -A・push・worktree remove・branch -d を拒み、読む形は通す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        disarm_ref_guard(main_path)
        run_task(wt1, "claim", "T-100")

        refused = (
            ("git stash", "GIT_STASH"),
            ("git stash push -u -m x", "GIT_STASH"),
            ("git stash -u", "GIT_STASH"),
            ("git stash pop", "GIT_STASH"),
            ("git stash apply stash@{0}", "GIT_STASH"),
            ("git stash drop", "GIT_STASH"),
            ("git -c core.pager=cat stash save x", "GIT_STASH"),
            ("git add -A", "GIT_ADD_ALL"),
            ("git add --all", "GIT_ADD_ALL"),
            ("git add -Av", "GIT_ADD_ALL"),
            ("git add .", "GIT_ADD_ALL"),
            ("git add ./", "GIT_ADD_ALL"),
            ("git add -- .", "GIT_ADD_ALL"),
            ("git add a.txt -A", "GIT_ADD_ALL"),
            ("git push", "GIT_PUSH"),
            ("git push origin main", "GIT_PUSH"),
            ("git push --force-with-lease", "GIT_PUSH"),
            ("/usr/bin/git push", "GIT_PUSH"),
            ("env FOO=1 git push", "GIT_PUSH"),
            ("git status && git push 2>&1", "GIT_PUSH"),
            (f"git -C {wt1} push", "GIT_PUSH"),
            ("git worktree remove ../x", "GIT_WORKTREE_REMOVE"),
            ("git worktree remove --force ../x", "GIT_WORKTREE_REMOVE"),
            ("git worktree prune", "GIT_WORKTREE_REMOVE"),
            ("git branch -d x", "GIT_BRANCH_DELETE"),
            ("git branch -D x", "GIT_BRANCH_DELETE"),
            ("git branch --delete x", "GIT_BRANCH_DELETE"),
            ("git branch -vd x", "GIT_BRANCH_DELETE"),
        )
        for command, rule in refused:
            check(f"拒む: {command}", run_guard(tmp, wt1, command) is not None)
            invocations = commit_guard._git_invocations(command, wt1)
            keys = [hit[0] for _w, sub, rest in invocations if (hit := commit_guard._dangerous_git(sub, rest))]
            check(f"規則 {rule}: {command}", keys == [rule], str(keys))
        reason = run_guard(tmp, wt1, "git push")
        check("理由にタスクIDと次の一手が入る", reason is not None and "T-100" in reason and "git push" in reason, str(reason))

        for command in (
            "git stash list",
            "git stash show",
            "git stash show -p stash@{0}",
            "git stash --help",
            "git add a.txt",
            "git add -- -A",
            "git add -- -A b.txt",
            "git add .gitignore",
            "git add src/.x",
            "git add -n -A",
            "git add --dry-run .",
            "git push -n",
            "git push --dry-run origin main",
            "git worktree list",
            "git worktree add ../y -b y",
            "git branch",
            "git branch -a",
            "git branch -v",
            "git branch --list",
            "git branch --show-current",
            "git branch -m a b",
            "git status",
            "git log --grep=stash",
            "git log --oneline -- push",
            "git config alias.push x",
            "git config --get remote.origin.url",
            "git diff -- .",
            "git show HEAD:push",
            'echo "git push"',
            "echo git add -A",
            'grep -n "git stash" README.md',
            "gh pr create",
            "gh pr push",
            "tw verify",
            "tw ship",
            "tw land gh-950",
            "tw finish T-100 --add a.txt",
            "git stash 'unclosed",
            f"git -C {wt2} push",
            f"cd {wt2} && git push",
            f"cd {wt2} && git add -A",
            "sh -c 'git push'",
        ):
            check(f"通す: {command}", run_guard(tmp, wt1, command) is None)
        check("控えの無い作業ツリーでは通す", run_guard(tmp, wt2, "git push") is None)

        payload = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": wt1}
        for agent_id, expect in ((None, False), ("a-implementer", True)):
            fields = dict(payload, **({"agent_id": agent_id} if agent_id else {}))
            r = run_task(tmp, "commit-guard", "--agent-scoped", stdin=json.dumps(fields))
            check(f"--agent-scoped の git の拒否は agent_id={agent_id} で{'拒む' if expect else '通す'}",
                  r.returncode == 0 and (r.stdout != "") == expect, r.stdout + r.stderr)

        result_path = write(os.path.join(tmp, "result.md"), RESULT_FULL)
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        for command in ("git push", "git stash", "git add -A", "git branch -d x", "git worktree remove ../x"):
            check(f"done のあとは通る: {command}", run_guard(tmp, wt1, command) is None)


@weight(9)
def test_commit_guard_acceptor() -> None:
    say("task.py commit-guard: 受け入れ役（--acceptor か agent_type）は tw と検証コマンドの直打ちを拒み、読む形は通す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`./check.sh`")
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        disarm_ref_guard(main_path)
        run_task(wt1, "claim", "T-100")

        def denied(command: str, where: str = wt1, *flags: str, **fields: str) -> str | None:
            payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": where, "agent_id": "a-1", **fields}
            r = run_task(tmp, "commit-guard", *flags, stdin=json.dumps(payload))
            check(f"終了コード0: {command}", r.returncode == 0, r.stdout + r.stderr)
            return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if r.stdout else None

        refused = (
            "tw show GH-1",
            "tw verify",
            "tw accept T-100",
            "/usr/local/bin/tw accept T-100",
            "env X=1 tw status",
            "X=1 tw status",
            "command tw status",
            "git status && tw verify",
            f"cd {wt1} && tw verify",
            "./check.sh",
        )
        passed = (
            "git diff main..HEAD",
            "git log --oneline -3",
            "cat README.md",
            "python3 skills/task-model/scripts/tests/selftest_task.py test_x",
            "echo tw",
            'grep -n "tw verify" README.md',
            "twine upload",
            "ls tw",
            "tw 'unclosed",
            f"cd {wt2} && tw show GH-1",
        )
        for label, flags, fields in (
            ("--acceptor", ("--acceptor",), {}),
            ("--agent-scoped・agent_type=acceptor", ("--agent-scoped",), {"agent_type": "acceptor"}),
            ("--agent-scoped・agent_type=tsukumo-workflow:acceptor", ("--agent-scoped",), {"agent_type": "tsukumo-workflow:acceptor"}),
        ):
            for command in refused:
                check(f"拒む（{label}）: {command}", denied(command, wt1, *flags, **fields) is not None)
            for command in passed:
                check(f"通す（{label}）: {command}", denied(command, wt1, *flags, **fields) is None)
            check(f"控えの無い木では通す（{label}）", denied("tw verify", wt2, *flags, **fields) is None)
            reason = denied("tw verify", wt1, *flags, **fields)
            check(f"tw の理由は受け入れ役向け（{label}）", reason is not None and "受け入れ役" in reason, str(reason))
            reason = denied("./check.sh", wt1, *flags, **fields)
            check(f"検証コマンドの理由は tw verify を案内しない（{label}）",
                  reason is not None and "tw verify" not in reason and "メイン" in reason, str(reason))

        for agent_type in ("no-delegate", "tsukumo-workflow:no-delegate", "general-purpose"):
            check(f"agent_type={agent_type} の tw verify は通す", denied("tw verify", wt1, "--agent-scoped", agent_type=agent_type) is None)
        check("旗も agent_type も無い tw verify は通す", denied("tw verify", wt1) is None)
        reason = denied("./check.sh", wt1, "--agent-scoped", agent_type="no-delegate")
        check("委譲先の検証コマンドの理由は tw verify を案内する", reason is not None and "tw verify" in reason, str(reason))
        check("受け入れ役でも git commit は拒む", denied("git commit -m x", wt1, "--acceptor") is not None)



@weight(9)
def test_commit_guard_bd_writes() -> None:
    say("task.py commit-guard: 印が立った作業ツリーで書き込みの bd を拒み（メインにも）、読む形と tw 経由は通す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        disarm_ref_guard(main_path)
        run_task(wt1, "claim", "T-100")

        def verdict(command: str, where: str, *flags: str, agent_id: str | None = None) -> bool:
            payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": where}
            if agent_id:
                payload["agent_id"] = agent_id
            r = run_task(tmp, "commit-guard", *flags, stdin=json.dumps(payload))
            check(f"終了コード0: {command}", r.returncode == 0, r.stdout + r.stderr)
            return r.stdout != ""

        refused = (
            "bd update GH-1 --claim",
            "bd close x",
            "bd reclaim",
            "bd create x",
            "bd unclaim x",
            "bd reopen x",
            "bd delete x",
            "bd label add x y",
            "bd --actor a update x",
            "bd --json -q close x",
            "bd -C /x close y",
            "bd dep add a b",
            "bd dep remove a b",
            "bd kv set k v",
            "bd kv clear k",
            "bd config set k v",
            "bd github sync",
            "bd jira push",
            "/usr/local/bin/bd create x",
            "env BEADS_ACTOR=a bd update x",
            "BEADS_ACTOR=a bd close x",
            "bd show GH-1 && bd close GH-1",
            f"cd {wt1} && bd close x",
        )
        passed = (
            "bd show GH-1",
            "bd show GH-1 --json",
            "bd list --json",
            "bd ready",
            "bd kv get k",
            "bd kv list",
            "bd dep list x",
            "bd dep tree x",
            "bd dep cycles",
            "bd config get k",
            "bd --readonly update x",
            "bd search update",
            "bd where",
            "bd doctor",
            "echo bd update",
            'grep "bd close" README.md',
            "git log --grep='bd update'",
            "bdx update",
            "bd",
            "tw edit GH-950 --section x --body-file -",
            "tw claim GH-1",
            "tw done GH-1",
            "tw ship",
            "bd 'close",
            f"cd {wt2} && bd close x",
        )
        for flags, agent_id in (((), None), (("--agent-scoped",), None), (("--agent-scoped",), "a-implementer")):
            label = f"{' '.join(flags) or '指定なし'}・agent_id={agent_id}"
            for command in refused:
                check(f"拒む（{label}）: {command}", verdict(command, wt1, *flags, agent_id=agent_id))
            for command in passed:
                check(f"通す（{label}）: {command}", not verdict(command, wt1, *flags, agent_id=agent_id))
            check(f"控えの無い木では通す（{label}）", not verdict("bd close x", wt2, *flags, agent_id=agent_id))

        reason = run_guard(tmp, wt1, "bd close x")
        check("理由にタスクIDと tw を案内する", reason is not None and "T-100" in reason and "tw" in reason, str(reason))

        calls = []
        original = commit_guard._bd_verdict
        commit_guard._bd_verdict = lambda command, start: calls.append(command) or None
        try:
            payload = {"tool_name": "Bash", "tool_input": {"command": "ls -la && git status"}, "cwd": wt1}
            check("bd の語が無い主の Bash は bd の判定（解析）に入らない",
                  commit_guard.decide_main(payload) is None and calls == [], str(calls))
            commit_guard.decide_main(dict(payload, tool_input={"command": "bd show x"}))
            check("bd の語がある主の Bash は bd の判定に入る", calls == ["bd show x"], str(calls))
            check("Bash 以外は判定しない", commit_guard.decide_main(dict(payload, tool_name="Edit")) is None)
        finally:
            commit_guard._bd_verdict = original

        result_path = write(os.path.join(tmp, "result.md"), RESULT_FULL)
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done のあとの bd close は通る", run_guard(tmp, wt1, "bd close x") is None)


def _exit_with_output(fn, *args) -> tuple[int | None, str]:
    import contextlib
    import io

    out = io.StringIO()
    code = None
    with contextlib.redirect_stdout(out):
        try:
            fn(*args)
        except SystemExit as e:
            code = e.code
    return code, out.getvalue()


TESTS = (
    test_commit_guard_acceptor,
    test_commit_guard_bd_writes,
    test_commit_guard_git_operations,
    test_ref_guard_blocks_commits,
    test_commit_guard_with_ref_guard,
    test_commit_guard_covers_inactive_ref_guard,
    test_handback_guard,
    test_handback_guard_other_repo,
    test_handback_guard_parallel_steps,
    test_handback_guard_step,
    test_handback_guard_skipped_steps,
    test_commit_guard,
    test_ask_guard,
    test_commit_guard_shell_edit,
    test_commit_guard_verify,
    test_commit_guard_other_repo,
    test_agent_scoped_guard,
    test_readonly_git,
)


if __name__ == "__main__":
    refuse_direct_run()

