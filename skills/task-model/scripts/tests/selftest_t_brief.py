from __future__ import annotations

import os
import re
import subprocess
import tempfile

from selftest_support import check, git, refuse_direct_run, say, write  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_fixtures import BODY, commit_task, make_repo, run_task  # noqa: E402

PATH_IN_TEXT = re.compile(r"(/[^\s`（）]+\.md)")


def _claim_with_plan(wt: str, main_path: str, plan: str | None, acceptance: str = "x") -> None:
    commit_task(main_path, taskfile.Task("T-100", "依頼文", "todo", "sonnet", "Y", (), task_body(acceptance=acceptance)))
    run_task(wt, "claim", "T-100")
    if plan is not None:
        run_task(wt, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin=plan)


def _brief(wt: str, *extra: str) -> subprocess.CompletedProcess:
    return run_task(wt, "brief", "implementer", "T-100", *extra)


def _head(r: subprocess.CompletedProcess) -> str:
    return r.stdout.splitlines()[0] if r.stdout else ""


def _paths_exist(text: str) -> bool:
    paths = PATH_IN_TEXT.findall(text)
    return bool(paths) and all(os.path.isfile(p) for p in paths)


def _work_repo(path: str) -> str:
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "test")
    write(os.path.join(path, "lib.txt"), "x\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


def test_brief_implementer_planned() -> None:
    say("task.py brief implementer: 作業より先に書いた計画なら 計画どおり で段1を渡し、型と WORKFLOW のパスは実在する絶対パス")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, "### 1. 書く\nx\n\n### 2. 試す\nx\n")
        r = _brief(wt1)
        check("BRIEF の行が 常に・計画どおり だけ", r.returncode == 0 and _head(r) == "BRIEF\tT-100\timplementer\t常に,計画どおり",
              r.stdout + r.stderr)
        body = r.stdout.split("\n", 1)[1]
        check("段1を渡し、ID が埋まり、{{ が残らない",
              "段1（`### 1.`）" in body and "tw show T-100" in body and "{{" not in body, body)
        check("本文の .md のパスはどれも実在する", _paths_exist(body), body)
        r = _brief(wt1, "--step", "2")
        check("--step 2 なら段2", r.returncode == 0 and "段2（`### 2.`）" in r.stdout, r.stdout + r.stderr)
        r = _brief(wt1, "--step", "3")
        check("段の外の番号は終了コード2", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)


def test_brief_implementer_rewrite() -> None:
    say("task.py brief implementer: 計画が無い・古い・--rewrite なら 書き直し、古い登録計画は変わったファイルを添える")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, None)
        r = _brief(wt1)
        check("計画が無ければ 常に・書き直し", r.returncode == 0 and _head(r) == "BRIEF\tT-100\timplementer\t常に,書き直し",
              r.stdout + r.stderr)
        check("WORKFLOW.md の絶対パスが実在する", _paths_exist(r.stdout.split("\n", 1)[1]), r.stdout)
        r = _brief(wt1, "--step", "2")
        check("書き直しに --step は終了コード2", r.returncode == 2, r.stdout + r.stderr)

        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\nx\n")
        r = _brief(wt1, "--rewrite")
        check("計画どおりでも --rewrite なら 書き直し", _head(r) == "BRIEF\tT-100\timplementer\t常に,書き直し", r.stdout + r.stderr)
        r = _brief(wt1, "--rewrite", "--step", "1")
        check("--rewrite と --step は終了コード2", r.returncode == 2, r.stdout + r.stderr)

        r = run_task(main_path, "new", "--summary", "古くなる", "--difficulty", "sonnet", "--loopable", "Y",
                     "--body-file", "-", stdin=task_body([("書く", "x")], ["shared.txt"]))
        stale = r.stdout.split("\t")[1]
        write(os.path.join(main_path, "shared.txt"), "line2\n")
        git(main_path, "commit", "-qam", "主ブランチが進む")
        run_task(wt2, "claim", stale)
        r = run_task(wt2, "brief", "implementer", stale)
        check("古い登録計画は 古い計画 を足し、変わったファイルを本文に出す",
              r.returncode == 0 and _head(r) == f"BRIEF\t{stale}\timplementer\t常に,書き直し,古い計画"
              and "変わったファイル: shared.txt" in r.stdout, r.stdout + r.stderr)


def test_brief_implementer_other_repo() -> None:
    say("task.py brief implementer: 作業先が別のリポジトリなら、枝と作業ツリーの道を埋める")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        work = _work_repo(os.path.join(tmp, "brief-work-repo"))
        _claim_with_plan(wt1, main_path, f"### 1. 書く\nx\n\n### 作業先\n- `{work}`\n")
        r = _brief(wt1)
        linked = os.path.join(os.path.dirname(os.path.realpath(tmp)), "brief-work-repo", "t-100")
        check("着手した木が本体でなければ <親の親>/<作業先の名前>/<枝> で worktree add を出す",
              r.returncode == 0 and _head(r) == "BRIEF\tT-100\timplementer\t常に,計画どおり,別のリポジトリ"
              and f"git -C {work} worktree add -b t-100 {linked} main" in r.stdout and os.path.realpath(wt1) in r.stdout,
              r.stdout + r.stderr)
        existing = os.path.join(tmp, "elsewhere")
        git(work, "worktree", "add", "-q", "-b", "t-100", existing, "main")
        r = _brief(wt1)
        check("枝 t-100 の作業ツリーがあればそのパス",
              f"worktree add -b t-100 {os.path.realpath(existing)} main" in r.stdout, r.stdout + r.stderr)
        git(work, "worktree", "remove", existing)
        git(work, "branch", "-D", "t-100")
        run_task(wt1, "release", "T-100")

        run_task(main_path, "claim", "T-100")
        r = run_task(main_path, "brief", "implementer", "T-100")
        beside = os.path.join(tmp, "brief-work-repo-t-100")
        check("着手した木が本体なら <作業先の親>/<作業先の名前>-<枝>",
              r.returncode == 0 and f"worktree add -b t-100 {beside} main" in r.stdout, r.stdout + r.stderr)
        os.makedirs(beside)
        r = run_task(main_path, "brief", "implementer", "T-100")
        check("決めた道に別のものがあれば INVALID（終了コード3）",
              r.returncode == 3 and r.stdout.startswith("INVALID\t") and beside in r.stdout, r.stdout + r.stderr)


def test_brief_implementer_parallel() -> None:
    say("task.py brief implementer: 渡す段が並列の組に入れば 並列の段 と段の番号つきの pause・触るファイルを足す")
    plan = task_body([
        ("書く", "- 触るファイル: `src/a.py`"),
        ("文書", "- 前の段: なし\n- 触るファイル: `docs/`"),
        ("合わせる", "x"),
    ]).split("## やること\n", 1)[1].split("\n## ", 1)[0]
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, plan)
        r = _brief(wt1, "--step", "2")
        check("並列の組の段2は 並列の段 と `tw pause T-100 2` と `docs/`",
              r.returncode == 0 and _head(r).endswith("\t常に,計画どおり,並列の段")
              and "tw pause T-100 2" in r.stdout and "`docs/`" in r.stdout, r.stdout + r.stderr)
        r = _brief(wt1, "--step", "3")
        check("最後の段は並列の組に入らない", r.returncode == 0 and _head(r).endswith("\t常に,計画どおり"), r.stdout + r.stderr)


def test_brief_implementer_visual() -> None:
    say("task.py brief implementer: 完了条件に目視があるか --visual なら 目視 を足す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, "### 1. 書く\nx\n", acceptance="画面を目視で確かめる")
        r = _brief(wt1)
        check("完了条件に目視があれば 目視", r.returncode == 0 and _head(r).endswith(",目視"), r.stdout + r.stderr)
        commit_task(main_path, taskfile.Task("T-101", "見た目なし", "todo", "sonnet", "Y", (), BODY))
        run_task(wt2, "claim", "T-101")
        run_task(wt2, "edit", "T-101", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\nx\n")
        r = run_task(wt2, "brief", "implementer", "T-101")
        check("目視が無ければ付かない", r.returncode == 0 and not _head(r).endswith(",目視"), r.stdout + r.stderr)
        r = run_task(wt2, "brief", "implementer", "T-101", "--visual")
        check("--visual なら付く", r.returncode == 0 and _head(r).endswith(",目視"), r.stdout + r.stderr)


def test_brief_refusals() -> None:
    say("task.py brief: 着手していなければ NOT_OWNER、並びに型を持つスキルが無ければ MISSING")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない", extra='steps = ["no-such-step"]\n')
        commit_task(main_path, taskfile.Task("T-100", "依頼文", "todo", "sonnet", "Y", (), BODY))
        r = _brief(wt1)
        check("着手していなければ NOT_OWNER（終了コード4）", r.returncode == 4 and r.stdout.startswith("NOT_OWNER\t"),
              r.stdout + r.stderr)
        run_task(wt1, "claim", "T-100")
        r = _brief(wt1)
        check("型を持つスキルが並びに無ければ MISSING（終了コード6）",
              r.returncode == 6 and r.stdout.startswith("MISSING\tbrief-implementer"), r.stdout + r.stderr)


TESTS = (
    test_brief_implementer_planned,
    test_brief_implementer_rewrite,
    test_brief_implementer_other_repo,
    test_brief_implementer_parallel,
    test_brief_implementer_visual,
    test_brief_refusals,
)


if __name__ == "__main__":
    refuse_direct_run()
