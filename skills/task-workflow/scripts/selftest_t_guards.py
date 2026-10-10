from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

from selftest_support import check, git, say, weight, write  # noqa: E402
import beads  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
from selftest_fixtures import BODY, TASK_PY, commit_task, flow_rows, hook_env, make_repo, metadata, readonly_git, run_handback_guard, run_task, snapshot  # noqa: E402


NO_DELEGATE = os.path.join(HERE, "..", "..", "..", "agents", "no-delegate.md")


def run_guard(tmp: str, where: str, command: str) -> str | None:
    """`tw commit-guard` を git の外（`tmp`）から打つ。拒んだら理由、通したら `None`（落ちたら例外）。"""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": where})
    r = run_task(tmp, "commit-guard", stdin=payload)
    if r.returncode != 0:
        raise RuntimeError(f"commit-guard が {r.returncode} で終わった: {r.stderr}")
    if r.stdout == "":
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def _hook_command(subcommand: str) -> str:
    with open(NO_DELEGATE, encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith(f"command: tw {subcommand}"):
                return line.strip()[len("command: "):]
    raise RuntimeError(f"no-delegate.md に tw {subcommand} の hook の行が無い")


@weight(9)
def test_commit_guard() -> None:
    say("task.py commit-guard: 印が立って done 前の作業ツリーのコミットを拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "release で外す", "todo", "sonnet", "Y", (), BODY))

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

        result_path = write(os.path.join(tmp, "result.md"), "結果\n")
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done で控えが消える", ledger.open_claims(cwd=wt1) == [])
        check("done のあとの git commit は通る", run_guard(tmp, wt1, "git commit -m x") is None)

        run_task(wt2, "claim", "T-101")
        check("release の前は拒む", run_guard(tmp, wt2, "git commit -m x") is not None)
        run_task(wt2, "release", "T-101")
        check("release のあとは通る", run_guard(tmp, wt2, "git commit -m x") is None)


HOOKS_JSON = os.path.join(HERE, "..", "..", "..", "hooks", "hooks.json")


def _plugin_hook_commands() -> list[str]:
    with open(HOOKS_JSON, encoding="utf-8") as f:
        hooks = json.load(f)["hooks"]
    return [h["command"] for groups in hooks.values() for group in groups for h in group["hooks"]]


@weight(6)
def test_agent_scoped_guard() -> None:
    say("task.py commit-guard・handback-guard --agent-scoped: no-delegate のときだけ関門を掛ける")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "拒む", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        write(os.path.join(wt1, "work.txt"), "x\n")
        commit = {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": wt1}
        stop = {"hook_event_name": "SubagentStop", "cwd": wt1}
        for subcommand, fields in (("commit-guard", commit), ("handback-guard", stop)):
            for flags, agent_type, refused in (
                ((), None, True),
                ((), "general-purpose", True),
                (("--agent-scoped",), None, False),
                (("--agent-scoped",), "general-purpose", False),
                (("--agent-scoped",), "no-delegate", True),
                (("--agent-scoped",), "tsukumo-workflow:no-delegate", True),
            ):
                payload = {**fields, **({} if agent_type is None else {"agent_type": agent_type})}
                r = run_task(tmp, subcommand, *flags, stdin=json.dumps(payload))
                check(f"{subcommand} {' '.join(flags)} agent_type={agent_type} は{'拒む' if refused else '通す'}",
                      r.returncode == 0 and (r.stdout != "") == refused, r.stdout + r.stderr)

        hooks = _plugin_hook_commands()
        check("hooks/hooks.json は --agent-scoped 付きで ${CLAUDE_PLUGIN_ROOT}/bin/tw を呼ぶ hook を3つ持つ",
              len(hooks) == 3 and all(
                  h.startswith('"${CLAUDE_PLUGIN_ROOT}/bin/tw" ') and " --agent-scoped " in h for h in hooks),
              str(hooks))
        env = {
            **hook_env(tmp, f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"),
            "CLAUDE_PLUGIN_ROOT": os.path.abspath(os.path.join(HERE, "..", "..", "..")),
        }
        for hook in hooks:
            fields = commit if "commit-guard" in hook else stop
            for agent_type, refused in (("tsukumo-workflow:no-delegate", True), (None, False)):
                payload = {**fields, **({} if agent_type is None else {"agent_type": agent_type})}
                r = subprocess.run(["sh", "-c", hook], input=json.dumps(payload), capture_output=True, text=True, env=env)
                check(f"hooks.json の {hook} は agent_type={agent_type} で{'拒む' if refused else '通す'}",
                      r.returncode == 0 and (r.stdout != "") == refused, r.stdout + r.stderr)


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
        run_task(wt1, "done", "T-100", "--result-file", "-", stdin="- 検証: x\n- 振り返り: 近道（省いた）\n")
        done = [e for e in flow_rows(wt1) if e["event"] == "done"]
        check("振り返りの行が近道なら done の reflection は skipped",
              len(done) == 1 and done[0]["reflection"] == "skipped", repr(done))
        run_task(wt2, "claim", "T-101")
        run_task(wt2, "done", "T-101", "--result-file", "-",
                 stdin="- 検証: x\n- 振り返り: 近道の基準が広い（ドラフト1件）\n")
        done = [e for e in flow_rows(wt1) if e["event"] == "done" and e["task"] == "T-101"]
        check("通常の道の振り返りの行が「近道」を含んでも reflection は some",
              len(done) == 1 and done[0]["reflection"] == "some", repr(done))


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
        steps = sorted(s.step for s in ledger.read_step_stamps(cwd=wt1) if s.task_id == "T-100")
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
        for label, args in (("段の番号が無い", ("T-100",)), ("段の外の番号", ("T-100", "4"))):
            r = run_task(wt1, "pause", *args)
            check(f"tw pause の引数が{label}なら終了コード2", r.returncode == 2 and "usage:" in r.stderr, r.stdout + r.stderr)
        ledger.write_step_stamp(ledger.StepStamp(ledger.content_key("", cwd=wt1), "T-999", 1), cwd=wt1)
        run_task(wt1, "step", "T-100", "1")
        check("着手中でないタスクの段の控えは tw step が消す",
              all(s.task_id == "T-100" for s in ledger.read_step_stamps(cwd=wt1)))


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


@weight(17)
def test_worktree_state_dir() -> None:
    say("ledger.py .tw/local/: 作業ツリーごとの控えとログを .tw/local/ に置き、旧い置き場の控えも読んで移す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`")
        commit_task(main_path, taskfile.Task("T-100", "新しい置き場", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "古い置き場", "todo", "sonnet", "Y", (), BODY))

        local1 = os.path.join(wt1, ".tw", "local")
        run_task(wt1, "claim", "T-100")
        with open(os.path.join(local1, ".gitignore"), encoding="utf-8") as f:
            ignore = f.read()
        check("claim が .tw/local/task-open-claims/ に控えを置き、.tw/local/.gitignore は * で .tw/.gitignore は作らない",
              os.path.isfile(os.path.join(local1, "task-open-claims", "T-100")) and ignore == "*\n"
              and not os.path.exists(os.path.join(wt1, ".tw", ".gitignore"))
              and not os.path.exists(os.path.join(ledger.git_dir(wt1), "task-open-claims")), ignore)
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
        old = ledger.git_dir(wt2)
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
              and ledger.legacy_state_entries(tw2) == []
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
              tree != "" and tree == ledger.content_tree(wt1), tree)

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
        result_path = write(os.path.join(tmp, "result.md"), "- 検証コマンド: 1 pass\n")

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


TESTS = (
    test_handback_guard,
    test_handback_guard_other_repo,
    test_worktree_state_dir,
    test_state_dir,
    test_handback_guard_parallel_steps,
    test_handback_guard_step,
    test_commit_guard,
    test_agent_scoped_guard,
    test_readonly_git,
    test_lap,
    test_readonly_git_stops_writers,
)
