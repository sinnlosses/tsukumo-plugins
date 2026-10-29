from __future__ import annotations

import os
import subprocess
import tempfile

from selftest_support import RESULT_FULL, check, git, refuse_direct_run, say, weight, write  # noqa: E402
import beads  # noqa: E402
import doc_budget  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import INTERVENTION, UNLOCK, task_body  # noqa: E402
from selftest_fixtures import BODY, PLANNED_BODY, _claim_work_and_done, commit_task, issue_count, make_repo, make_submodule_repo, drift_submodule, metadata, run_task, set_acceptance_directly, set_plan_directly, shown, shown_body  # noqa: E402


@weight(21)
def test_edit_and_plan_check() -> None:
    say("task.py edit・plan-check: ## やること を作業より先に書いたかを知らせる")
    planned = task_body([("書く", "")])
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        for tid in ("T-100", "T-101", "T-102", "T-103", "T-105", "T-106"):
            commit_task(main_path, taskfile.Task(tid, "やることの順", "todo", "sonnet", "Y", (), BODY))

        def reset(tid: str) -> None:
            git(wt1, "checkout", "--", ".")
            git(wt1, "clean", "-fdq")

        run_task(wt1, "claim", "T-100")
        inner_body = write(os.path.join(wt1, "plan-body.md"), planned)
        r = run_task(wt1, "edit", "T-100", "--body-file", inner_body)
        text = shown(wt1, "T-100")
        check("edit が本文を書き換える", r.returncode == 0 and r.stdout.strip() == "EDITED\tT-100"
              and "1. 書く" in text and "\nstatus: todo\n" in text, r.stdout + r.stderr + text)
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "edit", "T-100", "--body-file", "-", stdin=planned.replace("1. 書く", "1. 書き直す"))
        check("一度書いたあとの書き直しは作業の後でも拒まない", r.returncode == 0
              and r.stdout.strip() == "EDITED\tT-100", r.stdout + r.stderr)
        r = run_task(wt1, "plan-check", "T-100")
        check("作業より先に書けば PLAN_FIRST（渡した本文のファイル・後の書き直しは数えない）",
              r.returncode == 0 and r.stdout.strip() == "PLAN_FIRST\tT-100", r.stdout + r.stderr)
        r = run_task(wt2, "plan-check", "T-100")
        check("自分の印が無ければ NOT_OWNER（終了コード4）", r.returncode == 4 and r.stdout.strip() == "NOT_OWNER\tT-100", r.stdout)
        reset("T-100")

        run_task(wt1, "claim", "T-101")
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "plan-check", "T-101")
        check("書かずに作業へ進むと PLAN_NOT_FIRST missing", r.returncode == 0
              and r.stdout.strip() == "PLAN_NOT_FIRST\tT-101\tmissing", r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-101", "--body-file", "-", stdin=planned)
        check("作業のあとの初回の記入は WORK_BEFORE_PLAN で拒み、書き込まない（終了コード4）", r.returncode == 4
              and r.stdout.startswith("WORK_BEFORE_PLAN\tT-101\t") and "--after-work" in r.stdout
              and "1. 書く" not in shown(wt1, "T-101"), r.stdout + r.stderr)
        r = run_task(wt1, "plan-check", "T-101")
        check("拒んだあとは印を残さない（missing のまま）", r.stdout.strip() == "PLAN_NOT_FIRST\tT-101\tmissing", r.stdout)
        r = run_task(wt1, "edit", "T-101", "--after-work", "--body-file", "-", stdin=planned)
        check("--after-work なら書き込み、EDITED に続けて PLAN_AFTER_WORK を出す（終了コード0）", r.returncode == 0
              and r.stdout.splitlines() == ["EDITED\tT-101", "PLAN_AFTER_WORK\tT-101\t作業の後に書いた"], r.stdout + r.stderr)
        r = run_task(wt1, "plan-check", "T-101")
        check("作業のあとで書くと PLAN_NOT_FIRST after-work", r.returncode == 0
              and r.stdout.strip() == "PLAN_NOT_FIRST\tT-101\tafter-work", r.stdout + r.stderr)
        reset("T-101")

        run_task(wt1, "claim", "T-102")
        set_plan_directly(wt1, "T-102", "### 1. 直に書く\n")
        r = run_task(wt1, "plan-check", "T-102")
        check("edit を通さずに書くと PLAN_NOT_FIRST unrecorded", r.returncode == 0
              and r.stdout.strip() == "PLAN_NOT_FIRST\tT-102\tunrecorded", r.stdout + r.stderr)
        set_plan_directly(wt1, "T-102", "1. 段の無い計画\n")
        r = run_task(wt1, "plan-check", "T-102")
        check("段の読めない計画は PLAN_NOT_FIRST steps（書き直しの経路）", r.returncode == 0
              and r.stdout.strip() == "PLAN_NOT_FIRST\tT-102\tsteps", r.stdout + r.stderr)
        reset("T-102")

        run_task(wt1, "claim", "T-105")
        r = run_task(wt1, "edit", "T-105", "--after-work", "--body-file", "-", stdin=planned)
        r2 = run_task(wt1, "plan-check", "T-105")
        check("作業の前なら --after-work を付けても first", r.returncode == 0 and r.stdout.strip() == "EDITED\tT-105"
              and r2.stdout.strip() == "PLAN_FIRST\tT-105", r.stdout + r2.stdout + r.stderr)
        reset("T-105")

        run_task(wt1, "claim", "T-106")
        fielded = task_body([
            ("書く", "- 触るファイル: `src/a.py`"),
            ("文書", "- 前の段: なし\n- 触るファイル: `docs/`"),
            ("試す", "- 前の段: なし\n- 触るファイル: `src/a.py`"),
            ("合わせる", "x"),
        ])
        r = run_task(wt1, "edit", "T-106", "--body-file", "-", stdin=fielded.replace("- 前の段: なし\n- 触るファイル: `docs/`", "- 前の段: 2"))
        check("段の欄の誤った計画は edit が終了コード2で拒む", r.returncode == 2 and "段 2" in r.stderr, r.stdout + r.stderr)
        run_task(wt1, "edit", "T-106", "--body-file", "-", stdin=fielded)
        r = run_task(wt1, "plan-check", "T-106")
        check("plan-check は1行目のあとに並列の組と、触るファイルの重なりで外した組と、段ごとの待つ段を出す", r.returncode == 0
              and r.stdout.splitlines() == [
                  "PLAN_FIRST\tT-106", "PARALLEL\tT-106\t1,2", "PARALLEL\tT-106\t2,3", "SERIAL\tT-106\t1,3\tsrc/a.py",
                  "STEP\tT-106\t1\tなし", "STEP\tT-106\t2\tなし", "STEP\tT-106\t3\t1", "STEP\tT-106\t4\t2,3",
              ], r.stdout + r.stderr)
        none_steps = [
            ("書く", "- 触るファイル: `src/a.py`"),
            ("撮る", "- 前の段: なし\n- 触るファイル: なし"),
            ("試す", "- 前の段: なし\n- 触るファイル: `src/a.py`"),
            ("合わせる", "x"),
        ]
        none_plan = task_body(none_steps)
        none_named = task_body(none_steps, ["docs/"])
        r = run_task(wt1, "edit", "T-106", "--body-file", "-", stdin=none_plan)
        check("触るファイルが なし の段を含む計画を edit が受ける", r.returncode == 0
              and r.stdout.strip() == "EDITED\tT-106", r.stdout + r.stderr)
        r = run_task(wt1, "plan-check", "T-106")
        check("なし の段は欄があって何とも重ならず、並列の組に入る", r.returncode == 0
              and r.stdout.splitlines() == [
                  "PLAN_FIRST\tT-106", "PARALLEL\tT-106\t1,2", "PARALLEL\tT-106\t2,3", "SERIAL\tT-106\t1,3\tsrc/a.py",
                  "STEP\tT-106\t1\tなし", "STEP\tT-106\t2\tなし", "STEP\tT-106\t3\t1", "STEP\tT-106\t4\t2,3",
              ], r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-106", "--body-file", "-", stdin=none_plan.replace("触るファイル: なし", "触るファイル: なしです"))
        check("パスのコンマ区切りでも なし でもない値はこれまでの文言で拒む", r.returncode == 2
              and "段 2 の `- 触るファイル:` は `` `パス` `` のコンマ区切り: なしです" in r.stderr, r.stdout + r.stderr)
        r = run_task(main_path, "new", "--summary", "触らない段", "--difficulty", "sonnet", "--loopable", "Y",
                     "--body-file", "-", stdin=none_named)
        check("触るファイルが なし の段を含む計画を new が受ける", r.returncode == 0 and r.stdout.startswith("CREATED\t"),
              r.stdout + r.stderr)
        subprocess.run(["bd", "create", "--id", "t-none", "--title", "取り込み", "--silent"], cwd=main_path,
                       capture_output=True, text=True)
        r = run_task(main_path, "adopt", "T-NONE", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", "-",
                     stdin=none_named)
        check("触るファイルが なし の段を含む計画を adopt が受ける", r.returncode == 0 and r.stdout.startswith("ADOPTED\t"),
              r.stdout + r.stderr)
        reset("T-106")

        run_task(wt1, "claim", "T-103")
        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "work.txt")
        git(wt1, "commit", "-q", "-m", "作業")
        r = run_task(wt1, "edit", "T-103", "--body-file", "-", stdin=planned)
        check("claim 後にコミットしてからの初回の記入も拒む", r.returncode == 4
              and r.stdout.startswith("WORK_BEFORE_PLAN\tT-103\t"), r.stdout + r.stderr)
        run_task(wt1, "edit", "T-103", "--after-work", "--body-file", "-", stdin=planned)
        r = run_task(wt1, "plan-check", "T-103")
        check("claim 後にコミットしてから書いても after-work", r.stdout.strip() == "PLAN_NOT_FIRST\tT-103\tafter-work", r.stdout)

        other = planned.replace("## 目的・背景\nx", "## 目的・背景\n別のタスクの目的")
        r = run_task(wt1, "edit", "T-103", "--body-file", "-", stdin=other)
        check("別のタスクの本文は FRAME_CHANGED（終了コード4）で拒み、書き込まない", r.returncode == 4
              and r.stdout.startswith("FRAME_CHANGED\tT-103\t") and "--change-frame" in r.stdout
              and "別のタスクの目的" not in shown(wt1, "T-103"), r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-103", "--change-frame", "--body-file", "-", stdin=other)
        check("--change-frame を付ければ書き込む", r.returncode == 0 and r.stdout.startswith("EDITED\tT-103")
              and "別のタスクの目的" in shown(wt1, "T-103"), r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-103", "--body-file", "-", stdin=other.replace("1. 書く", "1. 直す"))
        check("## やること だけの書き換えは通る", r.returncode == 0 and r.stdout.startswith("EDITED\tT-103"),
              r.stdout + r.stderr)

        r = run_task(wt1, "edit", "T-103", "--body-file", "-", stdin="---\nid: T-103\n---\n\n" + planned)
        check("edit は front matter 付きを剥がし方つきで拒む（終了コード2）",
              r.returncode == 2 and "front matter" in r.stderr and "## " in r.stderr, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-103", "--body-file", "-", stdin=planned + "\n## 結果\n\nx\n")
        check("edit は ## 結果 を拒む（終了コード2）", r.returncode == 2, r.stdout + r.stderr)


@weight(4)
def test_edit_section() -> None:
    say("task.py edit --section: 指した節の中身だけを置き換える")
    purpose = "文中の `## やること` は境目でない\nx"
    mentions = task_body(purpose=purpose)
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        for tid in ("T-110", "T-111"):
            commit_task(main_path, taskfile.Task(tid, "節だけ", "todo", "sonnet", "Y", (), mentions))

        def read(tid: str) -> str:
            return "".join(l for l in shown(wt1, tid).splitlines(keepends=True) if not l.startswith("updated: "))

        run_task(wt1, "claim", "T-110")
        run_task(wt1, "claim", "T-111")
        before = read("T-110")
        for label, plan in (("段の無い", "- x\n"), ("`### 1.` から始まらない", "### 2. 書く\n")):
            r = run_task(wt1, "edit", "T-110", "--section", "やること", "--body-file", "-", stdin=plan)
            check(f"{label} ## やること は終了コード2と理由で拒み、書き込まない", r.returncode == 2
                  and "### 1." in r.stderr and read("T-110") == before, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-110", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n\n- x\n")
        after = read("T-110")
        want = before.replace("## やること\n\n## 完了条件", "## やること\n\n### 1. 書く\n\n- x\n\n## 完了条件")
        check("節だけが置き換わり、ほかの節は1バイトも変わらない（文中の `## やること` は境目に数えない）",
              r.returncode == 0 and r.stdout.strip() == "EDITED\tT-110" and after == want and after != before,
              r.stdout + r.stderr + after)
        r = run_task(wt1, "plan-check", "T-110")
        check("--section の記入も作業より先なら PLAN_FIRST", r.stdout.strip() == "PLAN_FIRST\tT-110", r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-110", "--section", "## やること", "--body-file", "-", stdin="### 1. y\n")
        check("節名は `## ` 付きでも受け、2回目の書き直しもできる", r.returncode == 0 and "### 1. y\n\n## 完了条件" in read("T-110"),
              r.stdout + r.stderr)
        mid = read("T-110")
        for name in ("ほげ", "結果"):
            r = run_task(wt1, "edit", "T-110", "--section", name, "--body-file", "-", stdin="x\n")
            check(f"枠に無い見出し {name} は書き込まずに拒む（終了コード2）",
                  r.returncode == 2 and "usage:" in r.stderr and read("T-110") == mid, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-110", "--section", "決まっていること", "--body-file", "-", stdin="- 括弧なし\n")
        check("全角括弧を省いた見出し「決まっていること」は「決まっていること（蒸し返さない）」に当たり、書き込む",
              r.returncode == 0 and "- 括弧なし" in read("T-110"), r.stdout + r.stderr)
        mid = read("T-110")
        r = run_task(wt1, "edit", "T-110", "--section", "やること", "--body-file", "-", stdin="a\n## 完了条件\nb\n")
        check("中身に `## ` で始まる行があれば書き込まずに拒む（終了コード2）",
              r.returncode == 2 and "usage:" in r.stderr and read("T-110") == mid, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-110", "--section", "完了条件", "--body-file", "-", stdin="別の条件\n")
        check("枠の節を変えれば FRAME_CHANGED（終了コード4）で拒む", r.returncode == 4
              and r.stdout.startswith("FRAME_CHANGED\tT-110\t") and read("T-110") == mid, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-110", "--section", "完了条件", "--change-frame", "--body-file", "-", stdin="別の条件\n")
        check("--change-frame を付ければ書き込む", r.returncode == 0 and "別の条件" in read("T-110"), r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-110", "--section", "やること")
        check("--section だけで --body-file が無ければ拒む（終了コード2）", r.returncode == 2, r.stdout + r.stderr)
        mid = read("T-110")
        for flags in (("--section", "やること"), ()):
            r = run_task(wt1, "edit", "T-110", *flags, "--body-file", "-", stdin="  \n\n")
            check(f"空白だけの --body-file（{' '.join(flags) or '本文全体'}）は書き込まずに拒む（終了コード2）",
                  r.returncode == 2 and "usage:" in r.stderr and read("T-110") == mid, r.stdout + r.stderr)

        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "edit", "T-111", "--section", "やること", "--body-file", "-", stdin="### 1. z\n")
        check("作業のあとの初回の記入は WORK_BEFORE_PLAN（終了コード4）で拒み、書き込まない", r.returncode == 4
              and r.stdout.startswith("WORK_BEFORE_PLAN\tT-111\t") and "### 1. z" not in read("T-111"), r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-111", "--section", "やること", "--after-work", "--body-file", "-", stdin="### 1. z\n")
        r2 = run_task(wt1, "plan-check", "T-111")
        check("--after-work なら書き込み、印は after-work", r.returncode == 0
              and r.stdout.splitlines()[:2] == ["EDITED\tT-111", "PLAN_AFTER_WORK\tT-111\t作業の後に書いた"]
              and r2.stdout.strip() == "PLAN_NOT_FIRST\tT-111\tafter-work", r.stdout + r2.stdout + r.stderr)

        r = run_task(wt1, "edit", "T-110", "--section", "注意", "--body-file", "-", stdin="- 申し送り\n")
        r2 = run_task(wt1, "plan-check", "T-110")
        check("作業のあとでも ## やること を変えない --section 注意 は拒まず、記録も変えない", r.returncode == 0
              and r.stdout.strip() == "EDITED\tT-110" and "- 申し送り" in read("T-110")
              and r2.stdout.strip() == "PLAN_FIRST\tT-110", r.stdout + r2.stdout + r.stderr)
        stdin = shown_body(wt1, "T-110").replace("- 申し送り", "- 別の申し送り")
        r = run_task(wt1, "edit", "T-110", "--body-file", "-", stdin=stdin)
        r2 = run_task(wt1, "plan-check", "T-110")
        check("本文ごと渡しても ## やること が同じなら同じ", r.returncode == 0 and r.stdout.strip() == "EDITED\tT-110"
              and "- 別の申し送り" in read("T-110") and r2.stdout.strip() == "PLAN_FIRST\tT-110", r.stdout + r2.stdout + r.stderr)


@weight(4)
def test_edit_keeps_plan_sections() -> None:
    say("task.py edit: ## やること の書き直しで作業先・名指すファイルの小見出しが落ちるのは終了コード2で拒む")
    other = "/tmp/other-repo"
    step = "### 1. 書く\n\nx\n"
    repo = f"{taskfile.PLAN_WORK_REPO_HEADING}\n- `{other}`\n"
    files = f"{taskfile.PLAN_FILES_HEADING}\n- `a.txt`\n"
    repo_name, files_name = taskfile.PLAN_WORK_REPO_HEADING, taskfile.PLAN_FILES_HEADING
    refused = (
        ("作業先だけ落ちる", step + files, [repo_name]),
        ("名指すファイルだけ落ちる", step + repo, [files_name]),
        ("両方落ちる", step, [repo_name, files_name]),
        ("作業先の見出しに空白が無い", step + f"###作業先\n- `{other}`\n" + files, [repo_name]),
        ("作業先の見出しが全角スペース", step + f"###　作業先\n- `{other}`\n" + files, [repo_name]),
        ("作業先の見出しに : が付く", step + f"### 作業先:\n- `{other}`\n" + files, [repo_name]),
        ("名指すファイルの見出しが割れる", step + repo + "### 名指す ファイル\n- `a.txt`\n", [files_name]),
    )
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-120", "別リポジトリ", "todo", "sonnet", "Y", (), task_body([("書く", "x")], ["a.txt"], other)))
        commit_task(main_path, taskfile.Task("T-121", "同じリポジトリ", "todo", "sonnet", "Y", (), task_body([("書く", "x")], ["a.txt"])))
        run_task(wt1, "claim", "T-120")
        run_task(wt1, "claim", "T-121")
        before = shown(wt1, "T-120")
        before_body = shown_body(wt1, "T-120")
        for label, plan, names in refused:
            want = "・".join(names) + " が落ちている"
            r = run_task(wt1, "edit", "T-120", "--section", "やること", "--body-file", "-", stdin=plan)
            check(f"{label} --section やること は終了コード2で、落ちた見出しを理由に出し、書き込まない",
                  r.returncode == 2 and r.stderr.startswith("usage:") and want in r.stderr and shown(wt1, "T-120") == before,
                  r.stdout + r.stderr)
            whole = taskfile.replace_section(before_body, taskfile.PLAN_HEADING, plan)
            r = run_task(wt1, "edit", "T-120", "--body-file", "-", stdin=whole)
            check(f"{label} 本文ごとの --body-file も終了コード2で、落ちた見出しを理由に出し、書き込まない",
                  r.returncode == 2 and r.stderr.startswith("usage:") and want in r.stderr and shown(wt1, "T-120") == before,
                  r.stdout + r.stderr)

        def reason(task_id: str, plan: str) -> str:
            r = run_task(wt1, "edit", task_id, "--section", "やること", "--body-file", "-", stdin=plan)
            return r.stderr if r.returncode == 2 else f"拒まれていない: {r.stdout}{r.stderr}"

        no_range, unreadable = "NO_RANGE", "unreadable"
        text = reason("T-120", step + files)
        check("作業先だけ落ちた理由は NO_RANGE を言い、名指すファイルの影響は言わない", no_range in text and unreadable not in text, text)
        text = reason("T-120", step + repo)
        check("作業先のあるタスクで名指すファイルだけ落ちた理由は unreadable を言い、NO_RANGE・別のリポジトリは言わない",
              unreadable in text and no_range not in text and "別のリポジトリ" not in text, text)
        text = reason("T-120", step)
        check("両方落ちた理由は NO_RANGE と unreadable の両方を言う", no_range in text and unreadable in text, text)
        text = reason("T-121", step)
        check("作業先の無いタスクで名指すファイルが落ちた理由は unreadable を言い、NO_RANGE・別のリポジトリは言わない",
              unreadable in text and no_range not in text and "別のリポジトリ" not in text, text)

        r = run_task(wt1, "edit", "T-120", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n\ny\n\n" + repo + files)
        check("節を残して段の中身だけ変える書き直しは通る", r.returncode == 0 and r.stdout.startswith("EDITED\tT-120"), r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-120", "--section", "やること", "--body-file", "-", stdin=files + repo + step)
        check("節を段の前へ動かす書き直しは、見出しが残っていれば通る", r.returncode == 0 and r.stdout.startswith("EDITED\tT-120"), r.stdout + r.stderr)
        moved = f"{taskfile.PLAN_WORK_REPO_HEADING}\n- `/tmp/moved-repo`\n"
        r = run_task(wt1, "edit", "T-120", "--section", "やること", "--body-file", "-", stdin=step + moved + files)
        check("作業先のパスを書き換える書き直しは落ちではないので通る", r.returncode == 0 and "/tmp/moved-repo" in shown(wt1, "T-120"),
              r.stdout + r.stderr)

        r = run_task(wt1, "edit", "T-121", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n\nz\n\n" + files)
        check("書き直し前に ### 作業先 が無い本文は、無いまま書き直しても通る", r.returncode == 0 and r.stdout.startswith("EDITED\tT-121"),
              r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-121", "--section", "注意", "--body-file", "-", stdin="- 申し送り\n")
        r2 = run_task(wt1, "edit", "T-121", "--summary", "別の要約")
        check("## やること を変えない tw edit は通る", r.returncode == 0 and r2.returncode == 0, r.stdout + r.stderr + r2.stdout + r2.stderr)


@weight(13)
def test_edit_deps() -> None:
    say("task.py edit --add-deps・--remove-deps: 台帳の依存を後から変える")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)

        def new() -> str:
            r = run_task(wt1, "new", "--summary", "x", "--difficulty", "sonnet", "--loopable", "Y",
                         "--body-file", "-", stdin=PLANNED_BODY)
            return r.stdout.split("\t")[1]

        def read(tid: str) -> str:
            return shown(wt1, tid)

        def body_of(text: str) -> str:
            return text.split("\n---\n", 1)[1]

        def ready(tid: str) -> str:
            for line in run_task(wt1, "status").stdout.splitlines():
                cols = line.split("\t")
                if cols[0] == tid:
                    return cols[5]
            return ""

        a, b, c, d = new(), new(), new(), new()
        r = run_task(wt1, "edit", b, "--add-deps", a)
        check("--add-deps で依存が入り、status が BLOCKED になる", r.returncode == 0 and r.stdout.strip() == f"EDITED\t{b}"
              and f"dependencies: [{a}]" in read(b) and ready(b) == f"BLOCKED:{a}", r.stdout + r.stderr + read(b))
        before = read(b)
        r = run_task(wt1, "edit", b, "--add-deps", a)
        check("すでにある依存の追加は通り、重ならない", r.returncode == 0 and read(b) == before, r.stdout + r.stderr)
        r = run_task(wt1, "edit", c, "--add-deps", f"{a},{b}")
        check("2件を一度に足せる", r.returncode == 0 and ready(c) in (f"BLOCKED:{a},{b}", f"BLOCKED:{b},{a}"),
              r.stdout + r.stderr + read(c))
        r = run_task(wt1, "edit", b, "--remove-deps", a)
        check("--remove-deps で外れ、READY に戻る", r.returncode == 0 and ready(b) == "READY", r.stdout + r.stderr)
        check("本文は変わらない", body_of(read(b)) == body_of(before))
        run_task(wt1, "edit", d, "--add-deps", c)

        snapshot = {t: read(t) for t in (a, b, c, d)}
        for label, args in (
            ("自分自身", (a, "--add-deps", a)),
            ("存在しない ID", (a, "--add-deps", "T-999")),
            ("形の違う ID", (a, "--add-deps", "xyz")),
            ("依存にない ID の削除", (a, "--remove-deps", b)),
            ("追加と削除に同じ ID", (a, "--add-deps", b, "--remove-deps", b)),
            ("直接の循環（C は B に依存済み）", (b, "--add-deps", c)),
            ("間接の循環（D→C→A に A→D）", (a, "--add-deps", d)),
        ):
            r = run_task(wt1, "edit", *args)
            check(f"{label}は終了コード2で拒み、何も書かない", r.returncode == 2 and "usage:" in r.stderr
                  and {t: read(t) for t in (a, b, c, d)} == snapshot, r.stdout + r.stderr)
        r = run_task(wt1, "edit", a, "--add-deps", d)
        check("循環の文言に道が出る", f"{a}→{d}→{c}→{a}" in r.stderr, r.stderr)
        r = run_task(wt1, "edit", a, "--add-deps", f"{b},{d}")
        check("追加の一部が循環なら全体を書かない",r.returncode == 2 and read(a) == snapshot[a], r.stdout + r.stderr)

        r = run_task(wt1, "edit", a)
        check("直すものが無ければ終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = run_task(wt1, "edit", a, "--section", "やること", "--add-deps", b)
        check("--body-file 無しの --section は終了コード2", r.returncode == 2 and read(a) == snapshot[a], r.stdout + r.stderr)
        r = run_task(wt1, "edit", b, "--add-deps", a, "--body-file", "-", stdin=body_of(read(b)).lstrip("\n"))
        check("--body-file と一緒に渡せば両方が反映される", r.returncode == 0 and f"dependencies: [{a}]" in read(b),
              r.stdout + r.stderr)

        commit_task(main_path, taskfile.Task("T-900", "終わり", "done", "sonnet", "Y", (), BODY))
        r = run_task(wt1, "edit", b, "--remove-deps", a, "--add-deps", "T-900")
        check("done のタスクへの依存は足せる（解決済みなので READY のまま）", r.returncode == 0 and ready(b) == "READY",
              r.stdout + r.stderr)


TESTS = (
    test_edit_and_plan_check,
    test_edit_deps,
    test_edit_section,
    test_edit_keeps_plan_sections,
)


if __name__ == "__main__":
    refuse_direct_run()

