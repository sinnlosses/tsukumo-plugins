from __future__ import annotations

import json
import os
import subprocess
import tempfile

from selftest_support import check, refuse_direct_run, say, write  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_fixtures import BODY, commit_task, make_repo, run_task  # noqa: E402

TAIL = ["受け入れ", "振り返り", "送り出し"]
ONE_STEP = task_body([("書く", "x")], ["shared.txt"])
PARALLEL_STEPS = task_body(
    [
        ("左", "- 触るファイル: `left.txt`"),
        ("右", "- 前の段: なし\n- 触るファイル: `right.txt`"),
        ("まとめ", "x"),
    ],
    ["shared.txt"],
)


def _register(main_path: str, body: str, *extra: str, difficulty: str = "sonnet", loopable: str = "Y") -> str:
    r = run_task(main_path, "new", "--summary", "最小", "--difficulty", difficulty, "--loopable", loopable, *extra,
                 "--body-file", "-", stdin=body)
    return r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""


def _phases(cwd: str, task_id: str, *args: str) -> tuple[dict | None, subprocess.CompletedProcess]:
    r = run_task(cwd, "plan-phases", task_id, *args)
    head = f"PHASES\t{task_id}\t"
    return (json.loads(r.stdout.strip()[len(head):]) if r.stdout.startswith(head) else None), r


def test_phases_delegate_flow() -> None:
    say("task.py plan-phases: 委譲する流れの並びと位置（計画の前・登録どおり・書き直し後・並列）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        registered = _register(main_path, ONE_STEP)
        run_task(wt1, "claim", registered)
        got, r = _phases(wt1, registered)
        check("登録どおりは計画の段なしで、委譲の範囲は先頭から段の数",
              got == {"phases": ["書く", *TAIL], "delegatedRange": {"first": 0, "count": 1}, "current": 0,
                      "finishedInGroup": []}, r.stdout + r.stderr)
        got, r = _phases(wt1, registered, "--through", "実装")
        check("--through 実装 で受け入れが今の段になる", got is not None and got["current"] == 1, r.stdout + r.stderr)
        got, r = _phases(wt1, registered, "--through", "受け入れ")
        check("--through 受け入れ で振り返りが今の段になる", got is not None and got["current"] == 2, r.stdout + r.stderr)
        got, r = _phases(wt1, registered, "--through", "振り返り")
        check("--through 振り返り で送り出しが今の段になる", got is not None and got["current"] == 3, r.stdout + r.stderr)
        r = run_task(wt1, "plan-phases", registered, "--through", "送り出し")
        check("送り出しまで済ませる指定は NO_PHASE（終了コード2）",
              r.returncode == 2 and r.stdout.startswith(f"NO_PHASE\t{registered}\t"), r.stdout + r.stderr)
        r = run_task(wt1, "plan-phases", registered, "--through", "存在しない")
        check("並びに無い段は NO_PHASE（終了コード2）", r.returncode == 2 and r.stdout.startswith("NO_PHASE\t"), r.stdout)
        r = run_task(wt1, "plan-phases", registered, "--also", "x")
        check("--also が番号でなければ NO_PHASE（終了コード2）", r.returncode == 2 and r.stdout.startswith("NO_PHASE\t"), r.stdout)

        written = "T-101"
        commit_task(main_path, taskfile.Task(written, "書き直し", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "release", registered)
        run_task(wt1, "claim", written)
        run_task(wt1, "edit", written, "--body-file", "-", stdin=ONE_STEP.replace("1. 書く", "1. 書き直す"))
        got, r = _phases(wt1, written)
        check("書き直したあと（PLAN_FIRST）は先頭に計画を足し、範囲は 1 から",
              got is not None and got["phases"] == ["計画", "書き直す", *TAIL]
              and got["delegatedRange"] == {"first": 1, "count": 1} and got["current"] == 0, r.stdout + r.stderr)
        got, r = _phases(wt1, written, "--through", "計画")
        check("--through 計画 で計画が済み、段1が今の段になる", got is not None and got["current"] == 1, r.stdout + r.stderr)

        missing = "T-100"
        commit_task(main_path, taskfile.Task(missing, "計画なし", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "release", written, "--force")
        run_task(wt1, "claim", missing)
        got, r = _phases(wt1, missing)
        check("計画の前は 計画・実装 の仮の並びで、範囲は {first} だけ",
              got == {"phases": ["計画", "実装", *TAIL], "delegatedRange": {"first": 1}, "current": 0,
                      "finishedInGroup": []}, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        for name in ("left.txt", "right.txt"):
            write(os.path.join(main_path, name), "x\n")
        parallel = _register(main_path, PARALLEL_STEPS)
        run_task(wt1, "claim", parallel)
        plan = run_task(wt1, "plan-check", parallel).stdout
        got, r = _phases(wt1, parallel)
        check("並列の組は配列1つにまとまり、範囲の段の数は平らにした数",
              "PARALLEL\t" in plan and got is not None and got["phases"] == [["左", "右"], "まとめ", *TAIL]
              and got["delegatedRange"] == {"first": 0, "count": 3}, plan + r.stdout + r.stderr)
        got, r = _phases(wt1, parallel, "--through", "1")
        check("組の途中は current を進めず finishedInGroup に名前を入れる",
              got is not None and got["current"] == 0 and got["finishedInGroup"] == ["左"], r.stdout + r.stderr)
        got, r = _phases(wt1, parallel, "--through", "1", "--also", "2")
        check("組が全部済むと current は組の次の要素", got is not None and got["current"] == 1
              and got["finishedInGroup"] == [], r.stdout + r.stderr)
        got, r = _phases(wt1, parallel, "--through", "実装")
        check("--through 実装 は配列を1要素に数えて受け入れの位置", got is not None and got["current"] == 2, r.stdout + r.stderr)
        got, r = _phases(wt1, parallel, "--also", "2")
        check("順番どおりでない完了（--also だけ）は finishedInGroup に右が入る",
              got is not None and got["current"] == 0 and got["finishedInGroup"] == ["右"], r.stdout + r.stderr)
        r = run_task(wt1, "plan-phases", parallel, "--also", "3")
        check("今のまとまりの外を先に済ませる --also は拒む", r.returncode == 2 and r.stdout.startswith("NO_PHASE\t"),
              r.stdout + r.stderr)

        r = run_task(_wt2_of(wt1), "plan-phases", parallel)
        check("着手の印が無い作業ツリーは NOT_OWNER（終了コード4）", r.returncode == 4
              and r.stdout.strip() == f"NOT_OWNER\t{parallel}", r.stdout + r.stderr)


def _wt2_of(wt1: str) -> str:
    return os.path.join(os.path.dirname(wt1), "wt2")


def test_phases_without_retrospect() -> None:
    say("task.py plan-phases: parts.retrospect が なし のときは振り返りを省く")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", extra='\n[parts]\nretrospect = "なし"\n')
        task_id = _register(main_path, ONE_STEP)
        run_task(wt1, "claim", task_id)
        got, r = _phases(wt1, task_id)
        check("受け入れ以降は2段", got is not None and got["phases"] == ["書く", "受け入れ", "送り出し"], r.stdout + r.stderr)
        r = run_task(wt1, "plan-phases", task_id, "--through", "振り返り")
        check("省いた振り返りは指せない", r.returncode == 2 and r.stdout.startswith("NO_PHASE\t"), r.stdout)


def test_phases_main_and_direct_flows() -> None:
    say("task.py plan-phases: main の流れ・loopable N は範囲なし、近道は 直す・検証・送り出し")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", extra='flow = "main"\n')
        task_id = _register(main_path, ONE_STEP)
        run_task(wt1, "claim", task_id)
        got, r = _phases(wt1, task_id)
        check("flow = main は同じ並びで delegatedRange なし",
              got == {"phases": ["書く", *TAIL], "current": 0, "finishedInGroup": []}, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        task_id = _register(main_path, ONE_STEP, loopable="N")
        run_task(wt1, "claim", task_id)
        got, r = _phases(wt1, task_id)
        check("loopable: N は delegatedRange なし", got is not None and "delegatedRange" not in got
              and got["phases"] == ["書く", *TAIL], r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        task_id = _register(main_path, ONE_STEP, "--direct", difficulty="haiku")
        run_task(wt1, "claim", task_id)
        got, r = _phases(wt1, task_id)
        check("近道は 直す・検証・送り出し の3段で範囲なし",
              got == {"phases": ["直す", "検証", "送り出し"], "current": 0, "finishedInGroup": []}, r.stdout + r.stderr)
        got, r = _phases(wt1, task_id, "--through", "直す")
        check("--through 直す で検証が今の段", got is not None and got["current"] == 1, r.stdout + r.stderr)


def _steps_line(*names: str) -> str:
    return f"steps = {json.dumps(list(names))}\n"


def test_phases_follow_steps() -> None:
    say("task.py plan-phases: 実装のあとの段は steps の並びから組む（外した段は出さず、ほかのスキルは名前の段）")
    impl, accept, retro, ship = (f"tsukumo-workflow:{n}" for n in taskfile.DEFAULT_STEP_NAMES)
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", extra=_steps_line(impl, accept, ship))
        task_id = _register(main_path, ONE_STEP)
        run_task(wt1, "claim", task_id)
        got, r = _phases(wt1, task_id)
        check("並びから振り返りを外すと帯にも出ない",
              got is not None and got["phases"] == ["書く", "受け入れ", "送り出し"], r.stdout + r.stderr)

    custom = _steps_line(impl, accept, "x:lint-step", retro, ship)
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", extra=custom)
        task_id = _register(main_path, ONE_STEP)
        run_task(wt1, "claim", task_id)
        got, r = _phases(wt1, task_id)
        check("既定の4つ以外のスキルは : より後ろの名前の段として並びの位置に入る",
              got == {"phases": ["書く", "受け入れ", "lint-step", "振り返り", "送り出し"],
                      "delegatedRange": {"first": 0, "count": 1}, "current": 0, "finishedInGroup": []},
              r.stdout + r.stderr)
        got, r = _phases(wt1, task_id, "--through", "lint-step")
        check("--through にそのスキルの段を指せる", got is not None and got["current"] == 3, r.stdout + r.stderr)
        got, r = _phases(wt1, task_id, "--through", "lint-step", "--skipped", "lint-step")
        check("済んだ段を --skipped に渡すと skippedPhases に入る",
              got is not None and got.get("skippedPhases") == ["lint-step"], r.stdout + r.stderr)
        r = run_task(wt1, "plan-phases", task_id, "--through", "lint-step", "--skipped", "振り返り")
        check("まだ済んでいない段の --skipped は NO_PHASE", r.returncode == 2 and r.stdout.startswith("NO_PHASE\t"),
              r.stdout + r.stderr)
        r = run_task(wt1, "plan-phases", task_id, "--through", "lint-step", "--skipped", "無い段")
        check("並びに無い段の --skipped は NO_PHASE", r.returncode == 2 and r.stdout.startswith("NO_PHASE\t"),
              r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", extra=custom)
        task_id = _register(main_path, ONE_STEP, "--direct", difficulty="haiku")
        run_task(wt1, "claim", task_id)
        got, r = _phases(wt1, task_id)
        check("近道でも既定の4つ以外のスキルの段は残り、受け入れ・振り返りは省く",
              got is not None and got["phases"] == ["直す", "検証", "lint-step", "送り出し"], r.stdout + r.stderr)

    got, error = taskfile.work_plan_phases((), "delegate", taskfile.PLAN_MISSING_STATE, True, None, (), ())
    check("並びが空（plugin の外の tw）なら既定の4つの順で組む",
          got is not None and got["phases"] == ["計画", "実装", *TAIL], str(error))


TESTS = (
    test_phases_delegate_flow,
    test_phases_without_retrospect,
    test_phases_main_and_direct_flows,
    test_phases_follow_steps,
)


if __name__ == "__main__":
    refuse_direct_run()
