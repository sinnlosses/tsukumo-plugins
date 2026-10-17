from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

from selftest_support import RESULT_FULL, RESULT_REQUIRED, check, git, git_env, refuse_direct_run, say, weight, write  # noqa: E402
import beads  # noqa: E402
import gitinfo  # noqa: E402
import ledger  # noqa: E402
import metrics  # noqa: E402
import ref_guard  # noqa: E402
import stamps  # noqa: E402
import taskfile  # noqa: E402
from selftest_fixtures import BODY, TASK_PY, commit_task, disarm_ref_guard, flow_rows, hook_env, make_repo, metadata, readonly_git, run_guard, run_handback_guard, run_task, snapshot  # noqa: E402


NO_DELEGATE = os.path.join(HERE, "..", "..", "..", "..", "agents", "no-delegate.md")


def _hook_command(subcommand: str) -> str:
    with open(NO_DELEGATE, encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith(f"command: tw {subcommand}"):
                return line.strip()[len("command: "):]
    raise RuntimeError(f"no-delegate.md に tw {subcommand} の hook の行が無い")


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
        _check_hook_line(tmp, wt1)

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
        check("loopable: Y の控えの作業ツリーは理由つきで拒む",
              reason is not None and "T-100" in reason and "tw edit T-100 --status hold" in reason
              and "tw release T-100" in reason
              and reason.index("tw release T-100") < reason.index("tw edit T-100 --status hold"),
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
            (("accept", "end"), False),
            (("retro", "start"), False),
            (("retro", "end"), False),
            (("land", "start"), False),
            (("sync", "end"), False),
        ):
            run_task(wt1, "lap", "T-100", *lap)
            check(f"主のセッションは最後の lap が {' '.join(lap)} なら{'拒む' if refused else '通す'}",
                  (run_ask_guard(tmp, wt1) is not None) == refused)
        check("作業の段の後でもサブエージェントは拒む", run_ask_guard(tmp, wt1, extra=subagent) is not None)
        run_task(wt1, "release", "T-100")
        check("release のあとは通す", run_ask_guard(tmp, wt1) is None)
        run_task(wt1, "claim", "T-100")
        check("claim し直すと前の accept end より後の lap が無いので拒む", run_ask_guard(tmp, wt1) is not None)
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
        ):
            check(f"拒む: {command.splitlines()[-1]}", run_guard(tmp, where, command) is not None)
        for command, where in (
            ("tw verify", wt1),
            ("tw verify T-100", wt1),
            ("./check.sh path/to/one.test.ts", wt1),
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


def _plugin_hook_commands() -> list[str]:
    with open(HOOKS_JSON, encoding="utf-8") as f:
        hooks = json.load(f)["hooks"]
    return [h["command"] for groups in hooks.values() for group in groups for h in group["hooks"]]


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

        all_hooks = _plugin_hook_commands()
        hooks = [h for h in all_hooks if " ask-guard" not in h]
        ask_hooks = [h for h in all_hooks if " ask-guard" in h]
        check("hooks/hooks.json は --agent-scoped 付きで ${CLAUDE_PLUGIN_ROOT}/bin/tw を呼ぶ hook を4つ持つ",
              len(hooks) == 4 and all(
                  h.startswith('"${CLAUDE_PLUGIN_ROOT}/bin/tw" ') and " --agent-scoped " in h for h in hooks),
              str(all_hooks))
        with open(HOOKS_JSON, encoding="utf-8") as f:
            pre = json.load(f)["hooks"]["PreToolUse"]
        check("hooks/hooks.json は Edit・Write・NotebookEdit の PreToolUse で handback-guard を呼ぶ",
              any(g.get("matcher") == "Edit|Write|NotebookEdit"
                  and all(" handback-guard --agent-scoped " in h["command"] for h in g["hooks"]) for g in pre),
              str(pre))
        check("hooks/hooks.json は --agent-scoped なしで ${CLAUDE_PLUGIN_ROOT}/bin/tw ask-guard を呼ぶ hook を1つ持つ",
              len(ask_hooks) == 1 and ask_hooks[0].startswith('"${CLAUDE_PLUGIN_ROOT}/bin/tw" ask-guard ')
              and "--agent-scoped" not in ask_hooks[0],
              str(all_hooks))
        env = {
            **hook_env(tmp, f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"),
            "CLAUDE_PLUGIN_ROOT": os.path.abspath(os.path.join(HERE, "..", "..", "..", "..")),
        }
        for hook in ask_hooks:
            payload = {"tool_name": "AskUserQuestion", "tool_input": {}, "cwd": wt1}
            r = subprocess.run(["sh", "-c", hook], input=json.dumps(payload), capture_output=True, text=True, env=env)
            check(f"hooks.json の {hook} は agent_type が無くても loopable: Y の控えで拒む",
                  r.returncode == 0 and '"deny"' in r.stdout, r.stdout + r.stderr)
        for hook in hooks:
            fields = commit if "commit-guard" in hook else stop
            for agent_id, refused in (("a-writer-1", True), (None, False)):
                payload = {**fields, **({} if agent_id is None else {"agent_id": agent_id, "agent_type": "my-implementer"})}
                r = subprocess.run(["sh", "-c", hook], input=json.dumps(payload), capture_output=True, text=True, env=env)
                check(f"hooks.json の {hook} は agent_id={agent_id} で{'拒む' if refused else '通す'}",
                      r.returncode == 0 and (r.stdout != "") == refused, r.stdout + r.stderr)
        for code, expected_exit in ((1, 1), (78, 0), (127, 0)):
            fake_root = tempfile.mkdtemp(dir=tmp)
            os.makedirs(os.path.join(fake_root, "bin"))
            fake_tw = os.path.join(fake_root, "bin", "tw")
            write(fake_tw, f"#!/bin/sh\necho boom >&2\nexit {code}\n")
            os.chmod(fake_tw, 0o755)
            for hook in all_hooks:
                guard = "ask-guard" if " ask-guard" in hook else "commit-guard" if "commit-guard" in hook else "handback-guard"
                r = subprocess.run(["sh", "-c", hook], input="{}", capture_output=True, text=True,
                                   env={**env, "CLAUDE_PLUGIN_ROOT": fake_root})
                if code == 1:
                    ok = (r.returncode == 1 and r.stdout == "" and "boom" in r.stderr
                          and f"{guard} が終了コード 1 で落ちた" in r.stderr)
                else:
                    ok = r.returncode == 0 and r.stdout == "" and "前提が欠けている" in r.stderr
                check(f"hooks.json の {guard} は tw が終了コード {code} で落ちると終了コード {expected_exit} で理由を出す",
                      ok, r.stdout + r.stderr)


def _send_edit(tmp: str, where: str, agent_id: str) -> None:
    """サブエージェントの `Edit` の `PreToolUse` を `handback-guard --agent-scoped` に渡す（書いた印を残させる）。"""
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {}, "cwd": where, "agent_id": agent_id}
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


@weight(6)
def test_agent_scoped_reader_handback() -> None:
    say("task.py handback-guard --agent-scoped: 書き換える道具を使っていないサブエージェント（受け入れ役）の返却は判定しない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "受け入れ", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n")
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "verify")
        verdict = "受け入れ 受け入れ | 指摘なし\n"

        check("検証が通った最後の段の状態で、書いていない受け入れ役の返却は ## 構造の変更 が無くても通す",
              r.returncode == 0 and _scoped_handback(tmp, wt1, "a-reader", verdict) == "", r.stdout + r.stderr)
        write(os.path.join(wt1, "work.txt"), "formatted\n")
        check("検証が欠けた作業（SKIP の整形のあと）でも、書いていない受け入れ役の SubagentStop は通す",
              _scoped_handback(tmp, wt1, "a-reader") == "")
        _send_edit(tmp, wt1, "a-writer")
        check("Edit を使ったサブエージェントの SubagentStop は検証の欠けで拒む",
              '"block"' in _scoped_handback(tmp, wt1, "a-writer"))
        run_task(wt1, "verify")
        check("Edit を使ったサブエージェントの最後の段の返却は ## 構造の変更 の欠けで拒む",
              "構造の変更" in _scoped_handback(tmp, wt1, "a-writer", verdict))
        check("同じ agent_id の再開（SendMessage）でも印は残る",
              ledger.is_agent_writer("a-writer", cwd=wt1) and not ledger.is_agent_writer("a-reader", cwd=wt1))

        _send_edit(tmp, wt2, "a-elsewhere")
        check("着手の控えの無い作業ツリーでは印を書かない",
              not os.path.exists(os.path.join(wt2, ".tw", "local", ledger.AGENT_WRITERS_DIR_NAME)))
        run_task(wt1, "release", "T-100")
        check("控えが無くなると印も消える",
              not os.path.exists(os.path.join(wt1, ".tw", "local", ledger.AGENT_WRITERS_DIR_NAME)))


def _send_bash(tmp: str, where: str, **ids: str) -> None:
    """サブエージェントの `Bash` の `PreToolUse` を `commit-guard --agent-scoped` に渡す（`agent` の出来事を残させる）。

    `agent_id`・`session_id` の入り方は Agent SDK の `BaseHookInput` の型定義に従う（実機の hook 入力のダンプではない）。
    """
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "tw show T-100"}, "cwd": where, **ids}
    r = run_task(tmp, "commit-guard", "--agent-scoped", stdin=json.dumps(payload))
    if r.returncode != 0 or r.stdout != "":
        raise RuntimeError(f"commit-guard の Bash が {r.returncode} で {r.stdout!r} を出した: {r.stderr}")


@weight(6)
def test_agent_task_record() -> None:
    say("task.py commit-guard: 委譲先の最初の Bash で agent の出来事（agent_id・session_id・タスク）を台帳に1回だけ残す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "対応", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "二つ目", "todo", "sonnet", "Y", (), BODY))

        def agent_rows() -> list[dict]:
            return [r for r in flow_rows(wt1) if r["event"] == "agent"]

        _send_bash(tmp, wt1, agent_id="a-one", session_id="s-1")
        check("着手の控えが無い作業ツリーでは残さない", agent_rows() == [])
        run_task(wt1, "claim", "T-100")
        _send_bash(tmp, wt1, agent_id="a-one", session_id="s-1")
        _send_bash(tmp, wt1, agent_id="a-one", session_id="s-1")
        rows = agent_rows()
        check("控えが1件なら agent の出来事を1回だけ残す（2回送っても1行）",
              len(rows) == 1 and rows[0]["task"] == "T-100" and rows[0]["agent"] == "a-one" and rows[0]["session"] == "s-1",
              str(rows))
        _send_bash(tmp, wt1, session_id="s-1")
        _send_bash(tmp, wt1, agent_id="a-two")
        _send_bash(tmp, wt1, agent_id="../x", session_id="s-1")
        _send_bash(tmp, wt1, agent_id="a-two", session_id="../s")
        check("agent_id の無い主・session_id の無い入力・ファイル名に使えない文字は残さない", len(agent_rows()) == 1, str(agent_rows()))
        check("agent の出来事が入っても台帳の集計（metrics）は読める",
              any(e.kind == "agent" for e in metrics.read_events(ledger.ledger_root(cwd=wt1))[0]))

        run_task(wt1, "claim", "T-101")
        _send_bash(tmp, wt1, agent_id="a-three", session_id="s-1")
        check("控えが2件で決められないときは残さない", len(agent_rows()) == 1, str(agent_rows()))

        run_task(wt1, "release", "T-100")
        run_task(wt1, "release", "T-101")
        check("控えが無くなると agent の印も消える",
              not os.path.exists(os.path.join(wt1, ".tw", "local", ledger.AGENT_TASKS_DIR_NAME)))


def _check_hook_line(
    tmp: str,
    claimed: str,
    subcommand: str = "commit-guard",
    payload_fields: dict | None = None,
    refused: str = '"deny"',
) -> None:
    """`no-delegate.md` の hook の行を `sh -c` で打つ。`claimed` は拒まれる状態の作業ツリー。"""
    hook = _hook_command(subcommand)
    fields = payload_fields or {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
    payload = json.dumps({**fields, "cwd": claimed})
    bare_path = "/usr/bin:/bin"

    def run_hook(tw_script: str | None) -> subprocess.CompletedProcess:
        path = bare_path
        if tw_script is not None:
            bin_dir = tempfile.mkdtemp(dir=tmp)
            write(os.path.join(bin_dir, "tw"), tw_script)
            os.chmod(os.path.join(bin_dir, "tw"), 0o755)
            path = f"{bin_dir}:{bare_path}"
        return subprocess.run(["sh", "-c", hook], input=payload, capture_output=True, text=True, env=hook_env(tmp, path))

    r = run_hook(f'#!/bin/sh\nexec {sys.executable} {TASK_PY} "$@"\n')
    check(f"hook の行は tw {subcommand} を呼んで拒む", r.returncode == 0 and refused in r.stdout, r.stdout + r.stderr)
    r = run_hook(None)
    check(f"tw が PATH に無くても {subcommand} の hook は何も出さず0で通す",
          r.returncode == 0 and r.stdout == "", r.stdout + r.stderr)
    r = run_hook(f"#!/bin/sh\nexec {sys.executable} -c 'raise RuntimeError(\"x\")'\n")
    check(f"tw {subcommand} が例外で落ちても hook は何も出さず0で通す",
          r.returncode == 0 and r.stdout == "", r.stdout + r.stderr)


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
        _check_hook_line(tmp, wt1, "handback-guard", {"hook_event_name": "SubagentStop"}, '"block"')

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


def test_lap() -> None:
    say("task.py lap: 5つの段が flow に1行ずつ書かれ、知らない段は拒み、印が無ければ記録しない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "段", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "通常の振り返り", "todo", "sonnet", "Y", (), BODY))
        r = run_task(wt1, "lap", "T-100", "delegate")
        check("印が無ければ NOT_CLAIMED で記録しない", r.stdout.startswith("NOT_CLAIMED\tT-100") and not flow_rows(wt1),
              r.stdout + r.stderr)
        run_task(wt1, "claim", "T-100")
        for stage in ("direct", "delegate", "accept", "review", "retro"):
            r = run_task(wt1, "lap", "T-100", stage)
            check(f"{stage} は LAPPED", r.returncode == 0 and r.stdout.strip() == f"LAPPED\tT-100\t{stage}",
                  r.stdout + r.stderr)
        laps = [(e["task"], e["stage"]) for e in flow_rows(wt1) if e["event"] == "lap"]
        check("flow に lap が5行、段の順で並ぶ",
              laps == [("T-100", s) for s in ("direct", "delegate", "accept", "review", "retro")], repr(laps))
        r = run_task(wt1, "lap", "T-100", "bogus")
        check("知らない段は終了コード2で記録しない", r.returncode == 2
              and len([e for e in flow_rows(wt1) if e["event"] == "lap"]) == 5, r.stdout + r.stderr)
        run_task(wt1, "done", "T-100", "--result-file", "-", stdin=RESULT_REQUIRED + "- 振り返り: 近道（省いた）\n")
        done = [e for e in flow_rows(wt1) if e["event"] == "done"]
        check("振り返りの行が近道なら done の reflection は skipped",
              len(done) == 1 and done[0]["reflection"] == "skipped", repr(done))
        run_task(wt2, "claim", "T-101")
        run_task(wt2, "done", "T-101", "--result-file", "-",
                 stdin=RESULT_REQUIRED + "- 振り返り: 近道の基準が広い（ドラフト1件）\n")
        done = [e for e in flow_rows(wt1) if e["event"] == "done" and e["task"] == "T-101"]
        check("通常の道の振り返りの行が「近道」を含んでも reflection は some",
              len(done) == 1 and done[0]["reflection"] == "some", repr(done))
    import tw_claim

    check("振り返りの行が設定で外したなら reflection は skipped",
          tw_claim._reflection_of("- 検証: x\n- 振り返り: 設定で外した\n") == "skipped")


def test_done_reflection_flag() -> None:
    say("task.py done --reflection: 渡した値が台帳に残り、`## 結果` の行は見ず、不正な値は終了コード2で何も書かない")
    cases = (
        ("none", RESULT_REQUIRED + "- 振り返り: 近道（省いた）\n"),
        ("some", RESULT_REQUIRED + "- 振り返り: 兆候なし\n"),
        ("skipped", RESULT_REQUIRED + "- 振り返り: 兆候なし\n"),
    )
    for value, result in cases:
        with tempfile.TemporaryDirectory() as tmp:
            main_path, wt1, _wt2 = make_repo(tmp, verify="`true`")
            commit_task(main_path, taskfile.Task("T-100", "旗", "todo", "sonnet", "Y", (), BODY))
            run_task(wt1, "claim", "T-100")
            r = run_task(wt1, "done", "T-100", "--reflection", value, "--result-file", "-", stdin=result)
            done = [e for e in flow_rows(wt1) if e["event"] == "done"]
            check(f"--reflection {value} は結果の行によらず reflection が {value}",
                  r.returncode == 0 and len(done) == 1 and done[0]["reflection"] == value, r.stdout + r.stderr + repr(done))
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "旗", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        r = run_task(wt1, "done", "T-100", "--reflection", "bogus", "--result-file", "-", stdin="- 検証: x\n")
        check("不正な値は終了コード2で done を記録しない",
              r.returncode == 2 and not [e for e in flow_rows(wt1) if e["event"] == "done"], r.stdout + r.stderr)
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=RESULT_FULL)
        done = [e for e in flow_rows(wt1) if e["event"] == "done"]
        check("不正な値で止めたあとも着手の印は残り、フラグなしは従来どおり行から none",
              r.returncode == 0 and len(done) == 1 and done[0]["reflection"] == "none", r.stdout + r.stderr)


@weight(11)
def test_handback_guard_structure() -> None:
    say("task.py handback-guard: 最後の段の返却の本文に ## 構造の変更 が無ければ拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "段ごと", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "作業なし", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n\n### 2. 試す\n")
        write(os.path.join(wt1, "work.txt"), "x\n")
        handback = ("PreToolUse", "SubagentHandback")
        with_section = "段 2/2 | 済\n\n## 構造の変更\n+ A -> B: 語\n"

        run_task(wt1, "step", "T-100", "1")
        check("最後でない段（tw step の印あり）は節が無くても通す",
              run_handback_guard(tmp, wt1, *handback, body="段 1/2 | 済\n") is None)
        write(os.path.join(wt1, "work.txt"), "y\n")
        run_task(wt1, "step", "T-100", "2")
        r = run_task(wt1, "verify")
        check("最後の段で節が無ければ deny し、理由は本文を写さない", r.returncode == 0 and "構造の変更" in (
            reason := (run_handback_guard(tmp, wt1, *handback, body="段 2/2 | 秘密の本文\n") or {})
            .get("hookSpecificOutput", {}).get("permissionDecisionReason", "")) and "秘密" not in reason, reason)
        check("最後の段で節があれば通す", run_handback_guard(tmp, wt1, *handback, body=with_section) is None)
        check("なし: <理由> の節でも通す",
              run_handback_guard(tmp, wt1, *handback, body="段 2/2 | 済\n\n## 構造の変更\nなし: 構造は変わらない\n") is None)
        check("本文が読めない経路（SubagentStop）では見ない", run_handback_guard(tmp, wt1) is None)

        def shaped(section: str) -> bool:
            return run_handback_guard(tmp, wt1, *handback, body=f"段 2/2 | 済\n\n## 構造の変更\n{section}") is None

        check("辺の行が複数あり、- と ~ の印と空行があっても通す", shaped("+ A -> B\n\n- C -> D: 語\n~ E -> F\n"))
        check("節のあとに別の見出しが続いても、節の中だけを見る",
              run_handback_guard(tmp, wt1, *handback, body=with_section + "\n## 次\n形の違う行\n") is None)
        check("中身が空の節は拒む", not shaped(""))
        check("空行だけの節は拒む", not shaped("\n\n"))
        check("形の違う行は拒む", not shaped("構造は変わらない\n"))
        check("印の無い辺の行は拒む", not shaped("A -> B\n"))
        check("辺の行のあとに形の違う行があれば拒む", not shaped("+ A -> B\n説明\n"))
        check("なし: の理由が空なら拒む", not shaped("なし: \n"))
        check("なし: と辺の行の混在は拒む", not shaped("なし: 変わらない\n+ A -> B\n"))
        check("なし: が2行あれば拒む", not shaped("なし: 一\nなし: 二\n"))

        run_task(wt2, "claim", "T-101")
        check("作業の無い返却は節が無くても通す",
              run_handback_guard(tmp, wt2, *handback, body="計画 0/1 | 前提が誤り\n") is None)


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


@weight(15)
def test_step_stamps_other_repo() -> None:
    say("task.py step: 作業先が別のリポジトリなら、作業先の作業ツリーの段の控えは作業先の中身の鍵で段ごとに書かれる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(os.path.join(tmp, "own"), verify="`true`")
        work_repo, _, _ = make_repo(os.path.join(tmp, "work"), verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "作業先で直す", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        plan = f"### 1. 書く\n\n### 2. 試す\n\n### 3. 直す\n\n### 作業先\n- `{work_repo}`\n"
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin=plan)
        work_tree = os.path.join(tmp, "work-t-100")
        git(work_repo, "worktree", "add", "-q", "-b", "t-100", work_tree, "main")

        outs = []
        for n, text in ((1, "x\n"), (2, "y\n")):
            write(os.path.join(work_tree, "work.txt"), text)
            git(work_tree, "add", "work.txt")
            git(work_tree, "commit", "-q", "-m", f"段{n}")
            r = run_task(wt1, "step", "T-100", str(n))
            outs.append(r.stdout.strip().split("\t"))
            check(f"段{n}の tw step が STEPPED", r.returncode == 0 and outs[-1][0] == "STEPPED", r.stdout + r.stderr)
        by_step = {s.step: s for s in stamps.read_step_stamps(cwd=work_tree)}
        own_by_step = {s.step: s for s in stamps.read_step_stamps(cwd=wt1)}
        table = "\n".join(
            f"段{n}\t着手側 {outs[n - 1][3]}\t作業先 {by_step[n].key.tree}" for n in (1, 2) if n in by_step
        )
        check("STEPPED の鍵は着手側のもので、着手側を触らないと段1・2で同じ", outs[0][3] == outs[1][3], table)
        check("作業先の控えは段ごとに別のファイルで、鍵が段ごとに違う",
              sorted(by_step) == [1, 2] and by_step[1].key != by_step[2].key, table)
        check("作業先の控えの鍵は着手側のものと違う",
              by_step[1].key.tree != own_by_step[1].key.tree and by_step[2].key.tree != own_by_step[2].key.tree, table)
        measured = stamps.content_key("true", cwd=work_tree)
        check("段2の控えの鍵は段2を打った時点の作業先の中身", by_step[2].key.tree == measured.tree, table)
        r = run_task(wt1, "step", "T-100", "3")
        check("最後の段の tw step は LAST_STEP", r.returncode == 4 and r.stdout.startswith("LAST_STEP"), r.stdout + r.stderr)
        check("作業先の中身が段2のままなら返却を通す", run_handback_guard(tmp, wt1) is None, _block_reason(run_handback_guard(tmp, wt1)))

        run_task(work_tree, "verify")
        run_task(wt1, "verify")
        stamps.remove_step_stamps(["T-100"], cwd=work_tree)
        stamps.remove_step_stamps(["T-100"], cwd=wt1)
        reason = _block_reason(run_handback_guard(tmp, wt1))
        check("段の控えが無ければ、検証が通っていても拒む", "段の控えが無い" in reason, reason)


@weight(17)
def test_worktree_state_dir() -> None:
    say("stamps.py .tw/local/: 作業ツリーごとの控えとログを .tw/local/ に置き、旧い置き場の控えも読んで移す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        disarm_ref_guard(main_path)
        commit_task(main_path, taskfile.Task("T-100", "新しい置き場", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "古い置き場", "todo", "sonnet", "Y", (), BODY))

        local1 = os.path.join(wt1, ".tw", "local")
        run_task(wt1, "claim", "T-100")
        with open(os.path.join(local1, ".gitignore"), encoding="utf-8") as f:
            ignore = f.read()
        check("claim が .tw/local/task-open-claims/ に控えを置き、.tw/local/.gitignore は * で .tw/.gitignore は作らない",
              os.path.isfile(os.path.join(local1, "task-open-claims", "T-100")) and ignore == "*\n"
              and not os.path.exists(os.path.join(wt1, ".tw", ".gitignore"))
              and not os.path.exists(os.path.join(gitinfo.git_dir(wt1), "task-open-claims")), ignore)
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n")
        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "作業")
        r = run_task(wt1, "verify")
        check("verify の控えとログが .tw/local/ にあり、git status に出ない",
              r.returncode == 0 and os.path.isfile(os.path.join(local1, "task-verify-stamp"))
              and os.path.isfile(os.path.join(local1, "task-verify.log"))
              and git(wt1, "status", "--porcelain").stdout == "", r.stdout + git(wt1, "status", "--porcelain").stdout)

        run_task(wt2, "claim", "T-101")
        run_task(wt2, "edit", "T-101", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n")
        write(os.path.join(wt2, "work.txt"), "x\n")
        run_task(wt2, "verify")
        tw2 = os.path.join(wt2, ".tw")
        local2 = os.path.join(tw2, "local")
        old = gitinfo.git_dir(wt2)
        shutil.copy2(os.path.join(local2, "task-verify-stamp"), os.path.join(old, "task-verify-stamp"))
        shutil.copytree(os.path.join(local2, "task-open-claims"), os.path.join(old, "task-open-claims"))
        shutil.rmtree(local2)
        r = run_task(wt2, "verify-check")
        check(".tw/ が無ければ git_dir の控えを verify-check が読む", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout)
        check("git_dir の控えのまま、検証した中身なら handback-guard は通す", run_handback_guard(tmp, wt2) is None)
        write(os.path.join(wt2, "work.txt"), "y\n")
        check("git_dir の着手の控えを handback-guard が読み、検証と違う中身なら block",
              "NOT_VERIFIED\tcontent" in _block_reason(run_handback_guard(tmp, wt2)))
        write(os.path.join(wt2, "work.txt"), "x\n")
        r = run_task(wt2, "pause")
        check("初めて書くときに git_dir の控えを .tw/local/ へ写して古いほうを消す",
              r.returncode == 0 and os.path.isfile(os.path.join(local2, "task-verify-stamp"))
              and os.path.isfile(os.path.join(local2, "task-open-claims", "T-101"))
              and not os.path.exists(os.path.join(old, "task-verify-stamp"))
              and not os.path.exists(os.path.join(old, "task-open-claims"))
              and run_task(wt2, "verify-check").stdout.startswith("VERIFIED_SAME\t"), r.stdout + r.stderr)

        for name in ("task-verify-stamp", "task-pause-stamp", "task-open-claims"):
            shutil.move(os.path.join(local2, name), os.path.join(tw2, name))
        shutil.rmtree(local2)
        write(os.path.join(tw2, "task-verify.log"), "旧いログ\n")
        write(os.path.join(tw2, ".gitignore"), "*\n")
        r = run_task(wt2, "verify-check")
        check(".tw/local/ が無ければ .tw/ 直下の旧い控えを verify-check が読む", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout)
        check(".tw/ 直下の旧い着手の控えを commit-guard が読んで拒む", run_guard(tmp, wt2, "git commit -m x") is not None)
        r = run_task(wt2, "pause")
        check("初めて書くときに .tw/ 直下の控えとログを .tw/local/ へ移し、直下から消す",
              r.returncode == 0 and os.path.isfile(os.path.join(local2, "task-verify-stamp"))
              and os.path.isfile(os.path.join(local2, "task-open-claims", "T-101"))
              and os.path.isfile(os.path.join(local2, "task-verify.log"))
              and stamps.legacy_state_entries(tw2) == []
              and run_task(wt2, "verify-check").stdout.startswith("VERIFIED_SAME\t"), r.stdout + r.stderr)
        with open(os.path.join(tw2, ".gitignore"), encoding="utf-8") as f:
            check("旧い .tw/.gitignore は書き換えない", f.read() == "*\n")

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, verify="`true`")
        write(os.path.join(main_path, ".tw", ".gitignore"), "local/\n")
        git(main_path, "add", ".tw/.gitignore")
        git(main_path, "commit", "-q", "-m", ".tw/.gitignore を足す")
        commit_task(main_path, taskfile.Task("T-100", "local/ だけを外す .tw/", "todo", "sonnet", "Y", (), BODY))
        git(wt1, "merge", "-q", "--ff-only", "main")
        run_task(wt1, "claim", "T-100")
        r = run_task(wt1, "verify")
        with open(os.path.join(wt1, ".tw", ".gitignore"), encoding="utf-8") as f:
            ignore = f.read()
        check(".tw/.gitignore が local/ の作業ツリーで、控えとログが git status に出ず .tw/.gitignore は変わらない",
              ignore == "local/\n" and git(wt1, "status", "--porcelain").stdout == "",
              r.stdout + git(wt1, "status", "--porcelain").stdout)


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


def test_readonly_git_stops_writers() -> None:
    say("task.py: .git が読み取り専用なら ship は GIT_READ_ONLY で止まり、何も変えない（done は .git に書かない）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない", verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "書けない", "todo", "sonnet", "Y", (), BODY))
        git(wt1, "merge", "-q", "--ff-only", "main")
        state = os.path.join(tmp, "state")
        env = {ledger.STATE_DIR_ENV: state}
        run_task(wt1, "claim", "T-100", env=env)
        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "work.txt")
        git(wt1, "commit", "-q", "-m", "作業")
        result_path = write(os.path.join(tmp, "result.md"), RESULT_FULL)

        def unchanged() -> tuple[str, str, dict[str, bytes]]:
            refs = git(wt1, "rev-parse", "HEAD", "main").stdout
            return refs, git(wt1, "status", "--porcelain").stdout, snapshot(wt1, state)

        refs = git(wt1, "rev-parse", "HEAD", "main").stdout
        with readonly_git(main_path):
            r = run_task(wt1, "done", "T-100", "--result-file", result_path, env=env)
        check("done は .git に書かないので読み取り専用でも DONE で、HEAD・主ブランチ・index を変えない",
              r.returncode == 0 and r.stdout.startswith("DONE\tT-100\t")
              and git(wt1, "rev-parse", "HEAD", "main").stdout == refs
              and git(wt1, "status", "--porcelain").stdout == "", r.stdout + r.stderr)

        before = unchanged()
        with readonly_git(main_path):
            r = run_task(wt1, "ship", env=env)
        check("ship は GIT_READ_ONLY（終了コード11）で、主ブランチ・HEAD・作業ツリー・台帳を変えない",
              r.returncode == 11 and r.stdout.startswith("GIT_READ_ONLY\t") and unchanged() == before,
              r.stdout + r.stderr)
        fresh = os.path.join(tmp, "fresh-state")
        with readonly_git(main_path):
            r = run_task(wt1, "ship", env={ledger.STATE_DIR_ENV: fresh})
        check("TW_STATE_DIR の台帳がまだ無い ship は GIT_READ_ONLY で止まり、新しい台帳を作らない",
              r.returncode == 11 and not os.path.exists(fresh), r.stdout + r.stderr)


def test_state_dir() -> None:
    say("ledger.py TW_STATE_DIR: 流れの記録をクローンごとの置き場に書き、共有の git dir には書かない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "置き場", "todo", "sonnet", "Y", (), BODY))
        shared_root = ledger.ledger_root(cwd=main_path)
        state = os.path.join(tmp, "state")
        env = {ledger.STATE_DIR_ENV: state}

        r = run_task(wt1, "claim", "T-100", env=env)
        roots = os.listdir(state) if os.path.isdir(state) else []
        state_flow = ledger.flow_dir(os.path.join(state, roots[0])) if len(roots) == 1 else ""
        check("TW_STATE_DIR の下の「本体の作業ツリーの名前-…」に流れの記録を書き、共有の git dir には書かない",
              r.returncode == 0 and roots[:1] != [] and roots[0].startswith("base-")
              and os.path.isdir(state_flow) and os.listdir(state_flow) != []
              and not os.path.exists(shared_root), r.stdout + r.stderr + str(roots))

        r = run_task(wt1, "release", "T-100")
        check("TW_STATE_DIR が無ければ共有の git dir の台帳に書く",
              r.returncode == 0 and os.path.isdir(ledger.flow_dir(shared_root)), r.stdout + r.stderr)


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


@weight(4)
def test_ref_guard_install_states() -> None:
    say("ref_guard.install: 同じ中身は書かず、別の task.py の殻は書き直し、利用者の hook と core.hooksPath には書かない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        hook = _ref_hook(main_path)
        check("初めては置く", ref_guard.install(wt1) is None and os.access(hook, os.X_OK))
        earlier = os.stat(hook).st_mtime_ns - 10**9
        os.utime(hook, ns=(earlier, earlier))
        check("同じ中身なら書かない", ref_guard.install(wt1) is None and os.stat(hook).st_mtime_ns == earlier)
        write(hook, ref_guard.hook_body("/nonexistent/task.py"))
        check("印付きで別の task.py を指す殻は書き直す",
              ref_guard.install(wt1) is None and _read_bytes(hook).decode() == ref_guard.hook_body(
                  os.path.realpath(os.path.join(HERE, "..", "tw", "task.py"))))

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "利用者の hook", "todo", "sonnet", "Y", (), BODY))
        hook = disarm_ref_guard(main_path)
        before = _read_bytes(hook)
        r = run_task(wt1, "claim", "T-100")
        check("利用者の hook があれば置かず、hook はバイト単位で同じ・標準エラーに ref-guard: の1行・stdout は CLAIMED",
              r.returncode == 0 and _read_bytes(hook) == before and "ref-guard:" in r.stderr
              and r.stdout.startswith("CLAIMED\t") and "ref-guard" not in r.stdout, r.stdout + r.stderr)
        check("利用者の hook があるときは commit-guard が git commit を拒む", run_guard(tmp, wt1, "git commit -m x") is not None)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "core.hooksPath", "todo", "sonnet", "Y", (), BODY))
        hooks_path = os.path.join(tmp, "user-hooks")
        os.makedirs(hooks_path)
        git(main_path, "config", "core.hooksPath", hooks_path)
        r = run_task(wt1, "claim", "T-100")
        check("core.hooksPath があれば置かず、指す先も共有の hooks も空のまま・標準エラーに ref-guard: の1行",
              r.returncode == 0 and os.listdir(hooks_path) == [] and not os.path.exists(_ref_hook(main_path))
              and "ref-guard:" in r.stderr and r.stdout.startswith("CLAIMED\t"), r.stdout + r.stderr)


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


TESTS = (
    test_ref_guard_blocks_commits,
    test_ref_guard_install_states,
    test_commit_guard_with_ref_guard,
    test_commit_guard_covers_inactive_ref_guard,
    test_handback_guard,
    test_handback_guard_other_repo,
    test_step_stamps_other_repo,
    test_worktree_state_dir,
    test_state_dir,
    test_handback_guard_parallel_steps,
    test_handback_guard_step,
    test_handback_guard_structure,
    test_handback_guard_skipped_steps,
    test_commit_guard,
    test_ask_guard,
    test_commit_guard_shell_edit,
    test_commit_guard_verify,
    test_commit_guard_other_repo,
    test_agent_scoped_guard,
    test_agent_scoped_reader_handback,
    test_agent_task_record,
    test_readonly_git,
    test_lap,
    test_done_reflection_flag,
    test_readonly_git_stops_writers,
)


if __name__ == "__main__":
    refuse_direct_run()
