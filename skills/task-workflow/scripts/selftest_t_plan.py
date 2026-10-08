from __future__ import annotations

import os
import subprocess
import tempfile

from selftest_support import check, git, say, weight, write  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_fixtures import BODY, PLANNED_BODY, TASK_REL, _claim_work_and_done, body_file, commit_task, make_repo, run_task, snapshot, task_rel  # noqa: E402


@weight(21)
def test_edit_and_plan_check() -> None:
    say("task.py edit・plan-check: ## やること を作業より先に書いたかを知らせる")
    planned = task_body([("書く", "")])
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        for tid in ("T-100", "T-101", "T-102", "T-103", "T-105", "T-106"):
            commit_task(main_path, taskfile.Task(tid, "やることの順", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-104", "閉じたもの", "done", "sonnet", "Y", (), BODY + "\n## 結果\n\nx\n"))
        task_file = lambda tid: os.path.join(wt1, task_rel(wt1), f"{tid}.md")  # noqa: E731

        def reset(tid: str) -> None:
            git(wt1, "checkout", "--", f"{TASK_REL}/{tid}.md")
            git(wt1, "clean", "-fdq")

        run_task(wt1, "claim", "T-100")
        inner_body = write(os.path.join(wt1, "plan-body.md"), planned)
        r = run_task(wt1, "edit", "T-100", "--body-file", inner_body)
        task, _ = taskfile.read_task_file(task_file("T-100"))
        check("ファイル方式の edit が本文を書き換える", r.returncode == 0 and r.stdout.strip() == "EDITED\tT-100"
              and task is not None and "1. 書く" in task.body and task.status == "todo", r.stdout + r.stderr)
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
        task, _ = taskfile.read_task_file(task_file("T-101"))
        check("作業のあとの初回の記入は WORK_BEFORE_PLAN で拒み、書き込まない（終了コード4）", r.returncode == 4
              and r.stdout.startswith("WORK_BEFORE_PLAN\tT-101\t") and "--after-work" in r.stdout
              and task is not None and not taskfile.has_plan(task.body), r.stdout + r.stderr)
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
        with open(task_file("T-102"), encoding="utf-8") as f:
            text = f.read()
        write(task_file("T-102"), text.replace("## やること\n", "## やること\n### 1. 直に書く\n"))
        r = run_task(wt1, "plan-check", "T-102")
        check("edit を通さずに書くと PLAN_NOT_FIRST unrecorded", r.returncode == 0
              and r.stdout.strip() == "PLAN_NOT_FIRST\tT-102\tunrecorded", r.stdout + r.stderr)
        write(task_file("T-102"), text.replace("## やること\n", "## やること\n1. 段の無い計画\n"))
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
        task, _ = taskfile.read_task_file(task_file("T-103"))
        check("別のタスクの本文は FRAME_CHANGED（終了コード4）で拒み、書き込まない", r.returncode == 4
              and r.stdout.startswith("FRAME_CHANGED\tT-103\t") and "--change-frame" in r.stdout
              and task is not None and "別のタスクの目的" not in task.body, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-103", "--change-frame", "--body-file", "-", stdin=other)
        task, _ = taskfile.read_task_file(task_file("T-103"))
        check("--change-frame を付ければ書き込む", r.returncode == 0 and r.stdout.startswith("EDITED\tT-103")
              and task is not None and "別のタスクの目的" in task.body, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-103", "--body-file", "-", stdin=other.replace("1. 書く", "1. 直す"))
        check("## やること だけの書き換えは通る", r.returncode == 0 and r.stdout.startswith("EDITED\tT-103"),
              r.stdout + r.stderr)

        r = run_task(wt1, "edit", "T-103", "--summary", "x")
        check("ファイル方式の edit は --body-file のほかは終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-103", "--body-file", "-", stdin="---\nid: T-103\n---\n\n" + planned)
        check("edit は front matter 付きを剥がし方つきで拒む（終了コード2）",
              r.returncode == 2 and "front matter" in r.stderr and "## " in r.stderr, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-103", "--body-file", "-", stdin=planned + "\n## 結果\n\nx\n")
        check("edit は ## 結果 を拒む（終了コード2）", r.returncode == 2, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-104", "--body-file", "-", stdin=planned)
        check("done のタスクは NOT_READY（終了コード4）", r.returncode == 4
              and r.stdout.strip() == "NOT_READY\tT-104\tdone", r.stdout + r.stderr)


@weight(4)
def test_edit_section() -> None:
    say("task.py edit --section: 指した節の中身だけを置き換える")
    purpose = "文中の `## やること` は境目でない\nx"
    mentions = task_body(purpose=purpose)
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        for tid in ("T-110", "T-111"):
            commit_task(main_path, taskfile.Task(tid, "節だけ", "todo", "sonnet", "Y", (), mentions))
        path = lambda tid: os.path.join(wt1, task_rel(wt1), f"{tid}.md")  # noqa: E731

        def read(tid: str) -> str:
            with open(path(tid), encoding="utf-8") as f:
                return f.read()

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
        whole, _ = taskfile.read_task_file(path("T-110"))
        stdin = whole.body.replace("- 申し送り", "- 別の申し送り") if whole is not None else ""
        r = run_task(wt1, "edit", "T-110", "--body-file", "-", stdin=stdin)
        r2 = run_task(wt1, "plan-check", "T-110")
        check("本文ごと渡しても ## やること が同じなら同じ", r.returncode == 0 and r.stdout.strip() == "EDITED\tT-110"
              and "- 別の申し送り" in read("T-110") and r2.stdout.strip() == "PLAN_FIRST\tT-110", r.stdout + r2.stdout + r.stderr)


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
            with open(os.path.join(wt1, task_rel(wt1), f"{tid}.md"), encoding="utf-8") as f:
                return f.read()

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
        check("2件を一度に足せる", r.returncode == 0 and ready(c) == f"BLOCKED:{a},{b}", r.stdout + r.stderr + read(c))
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
        git(wt1, "merge", "-q", "main")
        r = run_task(wt1, "edit", "T-900", "--add-deps", a)
        check("done のタスクは NOT_READY（終了コード4）", r.returncode == 4 and r.stdout.startswith("NOT_READY\tT-900"),
              r.stdout + r.stderr)
        r = run_task(wt1, "edit", b, "--remove-deps", a, "--add-deps", "T-900")
        check("done のタスクへの依存は足せる（解決済みなので READY のまま）", r.returncode == 0 and ready(b) == "READY",
              r.stdout + r.stderr)


@weight(23)
def test_registered_plan() -> None:
    say("task.py new・claim・plan-check: 登録時の計画が名指すファイルが着手時までに変わったかを知らせる")

    def plan_body(*paths: str, work_repo: str | None = None) -> str:
        return task_body([("書く", "x")], paths, work_repo)

    def new_id(r: subprocess.CompletedProcess) -> str:
        return r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""

    def register(summary: str, body: str, *extra: str) -> subprocess.CompletedProcess:
        return run_task(main_path, "new", "--summary", summary, "--difficulty", "sonnet", "--loopable", "Y", *extra,
                        "--body-file", "-", stdin=body)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        write(os.path.join(main_path, "src", "a.txt"), "a\n")
        write(os.path.join(main_path, "other.txt"), "o\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "src を足す")
        root = ledger.ledger_root(cwd=main_path)
        work = os.path.join(tmp, "work")
        os.makedirs(work)
        git(work, "init", "-q", "-b", "main")
        git(work, "config", "user.email", "test@example.com")
        git(work, "config", "user.name", "test")
        write(os.path.join(work, "lib", "x.txt"), "x\n")
        write(os.path.join(work, "lib", "y.txt"), "y\n")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "init")

        r = register("無いファイル", plan_body("nothing.txt"))
        check("木に無いパスを名指すと終了コード2で、ファイルも控えも作らない", r.returncode == 2
              and "nothing.txt" in r.stderr and not os.path.isdir(os.path.join(main_path, task_rel(main_path)))
              and not os.path.isdir(os.path.join(root, ledger.PLAN_BASE_DIR_NAME)), r.stdout + r.stderr)
        r = register("空の計画", BODY)
        check("空の ## やること は終了コード2で、ファイルを作らない", r.returncode == 2 and "--hold" in r.stderr
              and not os.path.isdir(os.path.join(main_path, task_rel(main_path))), r.stdout + r.stderr)
        r = register("段に穴", plan_body("shared.txt").replace("### 1. 書く", "### 2. 書く"))
        check("段が `### 1.` から穴なく続かない ## やること は終了コード2と理由で、ファイルを作らない", r.returncode == 2
              and "### 1." in r.stderr and not os.path.isdir(os.path.join(main_path, task_rel(main_path))), r.stdout + r.stderr)
        r = register("名指すファイルが作業先に無い", plan_body("shared.txt", work_repo=work))
        check("作業先の木に無いパスを名指すと終了コード2", r.returncode == 2 and "shared.txt" in r.stderr
              and work in r.stderr, r.stdout + r.stderr)
        r = register("作業先が git でない", plan_body("lib/x.txt", work_repo=os.path.join(tmp, "nowhere")))
        check("作業先が git のリポジトリの根でなければ終了コード2", r.returncode == 2 and "作業先" in r.stderr,
              r.stdout + r.stderr)
        r = register("作業先の下の階層", plan_body("x.txt", work_repo=os.path.join(work, "lib")))
        check("作業先がリポジトリの根でない（下の階層）なら終了コード2", r.returncode == 2, r.stdout + r.stderr)

        r = register("変わらない", plan_body("shared.txt"))
        same = new_id(r)
        r2 = register("変わる", plan_body("src/"))
        changed = new_id(r2)
        r3 = register("書かない", BODY, "--hold")
        unplanned = new_id(r3)
        r4 = register("作業先で変わらない", plan_body("lib/x.txt", work_repo=work))
        remote_same = new_id(r4)
        r5 = register("作業先で変わる", plan_body("lib/y.txt", work_repo=work))
        remote_changed = new_id(r5)
        head = git(main_path, "rev-parse", "main").stdout.strip()
        work_head = git(work, "rev-parse", "main").stdout.strip()
        check("計画つきの登録は CREATED で、主ブランチの SHA を台帳に控える", same != "" and changed != ""
              and ledger.read_plan_base(root, same) == head and ledger.read_plan_base(root, changed) == head,
              r.stdout + r.stderr + r2.stdout + r2.stderr)
        check("--hold の空の計画は CREATED で、控えを作らない", unplanned != ""
              and ledger.read_plan_base(root, unplanned) is None, r3.stdout + r3.stderr)
        check("作業先のある登録は作業先の主ブランチの SHA を控える", remote_same != "" and remote_changed != ""
              and ledger.read_plan_base(root, remote_same) == work_head
              and ledger.read_plan_base(root, remote_changed) == work_head,
              r4.stdout + r4.stderr + r5.stdout + r5.stderr)
        unplanned_path = os.path.join(main_path, task_rel(main_path), f"{unplanned}.md")
        with open(unplanned_path, encoding="utf-8") as f:
            held = f.read()
        write(unplanned_path, held.replace("status: hold\n", "status: todo\n"))
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "登録")
        write(os.path.join(main_path, "other.txt"), "o2\n")
        write(os.path.join(main_path, "src", "new.txt"), "n\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "主ブランチが進む")
        tip = git(main_path, "rev-parse", "main").stdout.strip()
        write(os.path.join(work, "lib", "y.txt"), "y2\n")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-m", "作業先が進む")

        run_task(wt1, "claim", same)
        r = run_task(wt1, "plan-check", same)
        check("名指したファイルが変わっていなければ PLAN_REGISTERED（終了コード0）", r.returncode == 0
              and r.stdout.strip() == f"PLAN_REGISTERED\t{same}\t{head}", r.stdout + r.stderr)
        write(os.path.join(wt1, "work.txt"), "x\n")
        with open(os.path.join(wt1, task_rel(wt1), f"{same}.md"), encoding="utf-8") as f:
            current = taskfile.parse(f.read())[0]
        r = run_task(wt1, "edit", same, "--body-file", "-",
                     stdin=current.body.replace("## 注意\n", "## 注意\n作業中の知見\n") if current else "")
        check("PLAN_REGISTERED のあとは作業してからの書き足しも WORK_BEFORE_PLAN にならない", r.returncode == 0
              and r.stdout.strip() == f"EDITED\t{same}", r.stdout + r.stderr)

        run_task(wt2, "claim", changed)
        r = run_task(wt2, "plan-check", changed)
        check("名指したファイルが変わっていれば PLAN_STALE と変わったファイル（終了コード0）", r.returncode == 0
              and r.stdout.strip() == f"PLAN_STALE\t{changed}\tsrc/new.txt", r.stdout + r.stderr)
        check("claim が判定した主ブランチの先端を印に控える", ledger.read_plan_tip(root, changed) == tip)
        write(os.path.join(main_path, "src", "later.txt"), "l\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "着手のあとに主ブランチが進む")
        r = run_task(wt2, "plan-check", changed)
        check("着手のあとに主ブランチが進んでも判定は変わらない", r.stdout.strip() == f"PLAN_STALE\t{changed}\tsrc/new.txt", r.stdout)
        r = run_task(wt2, "edit", changed, "--body-file", "-", stdin=plan_body("src/", "shared.txt"))
        r2 = run_task(wt2, "plan-check", changed)
        check("PLAN_STALE のあとに書き直すと PLAN_FIRST", r.returncode == 0
              and r2.stdout.strip() == f"PLAN_FIRST\t{changed}", r.stdout + r.stderr + r2.stdout)

        git(wt1, "checkout", "--", ".")
        git(wt1, "clean", "-fdq")
        run_task(wt1, "release", same)
        git(wt1, "merge", "-q", "--ff-only", "main")
        run_task(wt1, "claim", unplanned)
        r = run_task(wt1, "plan-check", unplanned)
        check("--hold で登録して todo に戻したものは PLAN_NOT_FIRST missing", r.returncode == 0
              and r.stdout.strip() == f"PLAN_NOT_FIRST\t{unplanned}\tmissing", r.stdout + r.stderr)
        run_task(wt1, "release", unplanned)

        run_task(wt1, "claim", remote_same)
        r = run_task(wt1, "plan-check", remote_same)
        check("作業先で名指したファイルが変わっていなければ PLAN_REGISTERED", r.returncode == 0
              and r.stdout.strip() == f"PLAN_REGISTERED\t{remote_same}\t{work_head}", r.stdout + r.stderr)
        run_task(wt1, "release", remote_same)
        run_task(wt1, "claim", remote_changed)
        r = run_task(wt1, "plan-check", remote_changed)
        check("作業先で名指したファイルが変わっていれば PLAN_STALE と作業先の中のパス", r.returncode == 0
              and r.stdout.strip() == f"PLAN_STALE\t{remote_changed}\tlib/y.txt", r.stdout + r.stderr)
        run_task(wt1, "release", remote_changed)

        git(wt1, "merge", "-q", "--ff-only", "main")
        run_task(wt1, "claim", same)
        _claim_work_and_done(wt1, same)
        r = run_task(wt1, "ship")
        check("ship で送ったタスクの控えは消える", r.stdout.startswith("SHIPPED\t")
              and ledger.read_plan_base(root, same) is None and ledger.read_plan_base(root, changed) == head,
              r.stdout + r.stderr)


@weight(1)
def test_direct_mark() -> None:
    say("task.py new・edit・claim --direct: 近道の印は基準に当たるときだけ付き、着手時に測り直す")

    def register(body: str, difficulty: str = "haiku") -> subprocess.CompletedProcess:
        return run_task(main_path, "new", "--summary", "近道", "--difficulty", difficulty, "--loopable", "Y", "--direct",
                        "--body-file", "-", stdin=body)

    def front(task_id: str) -> str:
        with open(os.path.join(main_path, task_rel(main_path), f"{task_id}.md"), encoding="utf-8") as f:
            return f.read().split("\n---\n", 1)[0]

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        write(os.path.join(main_path, "src", "a.txt"), "a\n")
        write(os.path.join(main_path, "src", "b.txt"), "b\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "src を足す")
        task_dir = os.path.join(main_path, task_rel(main_path))

        for label, body, difficulty in (
            ("difficulty が haiku でない", task_body([("書く", "x")], ["shared.txt"]), "sonnet"),
            ("段が2つ", task_body([("書く", "x"), ("足す", "y")], ["shared.txt"]), "haiku"),
            ("名指すファイルが3つ", task_body([("書く", "x")], ["shared.txt", "src/b.txt", "src/"]), "haiku"),
            ("作業先がある", task_body([("書く", "x")], ["shared.txt"], tmp), "haiku"),
        ):
            r = register(body, difficulty)
            check(f"{label}の --direct は終了コード2で、ファイルを作らない",
                  r.returncode == 2 and "近道" in r.stderr and not os.path.isdir(task_dir), r.stdout + r.stderr)

        r = register(task_body([("書く", "x")], ["shared.txt", "src/a.txt"]))
        ok_id = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""
        check("haiku・1段・名指す2つの --direct は CREATED で、front matter に direct: Y の行が出る",
              ok_id != "" and "\nloopable: Y\ndirect: Y\n" in front(ok_id), r.stdout + r.stderr)
        r = register(task_body([("書く", "x")], ["src/b.txt"]))
        stale_id = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""

        r = run_task(main_path, "new", "--summary", "通常", "--difficulty", "haiku", "--loopable", "Y",
                     "--body-file", "-", stdin=task_body([("書く", "x")], ["shared.txt"]))
        edit_id = r.stdout.split("\t")[1]
        check("--direct なしの登録は direct 行を書かない", "direct:" not in front(edit_id))
        r = run_task(main_path, "edit", edit_id, "--direct", "Y")
        check("edit --direct Y は基準に当たれば印を付ける", r.returncode == 0 and "direct: Y" in front(edit_id),
              r.stdout + r.stderr)
        r = run_task(main_path, "edit", edit_id, "--direct", "N")
        check("edit --direct N は印を外す", r.returncode == 0 and "direct:" not in front(edit_id), r.stdout + r.stderr)
        run_task(main_path, "edit", edit_id, "--direct", "Y")
        r = run_task(main_path, "edit", edit_id, "--body-file", "-",
                     stdin=task_body([("書く", "x"), ("足す", "y")], ["shared.txt"]))
        check("本文の書き換えで基準を外れたら書き込んで印を外し、EDITED の次に DIRECT_OFF を出す",
              r.returncode == 0 and r.stdout.splitlines()[0] == f"EDITED\t{edit_id}"
              and r.stdout.splitlines()[1].startswith(f"DIRECT_OFF\t{edit_id}\t近道は")
              and "direct:" not in front(edit_id), r.stdout + r.stderr)
        r = run_task(main_path, "edit", edit_id, "--direct", "Y")
        check("基準を外れたタスクへの edit --direct Y は終了コード2で、書き込まない",
              r.returncode == 2 and "近道" in r.stderr and "direct:" not in front(edit_id), r.stdout + r.stderr)

        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "登録")
        write(os.path.join(main_path, "src", "b.txt"), "b2\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "b を変える")

        r = run_task(wt1, "claim", ok_id)
        check("印があり基準に当たり計画が古くなければ CLAIMED の行末に direct=Y",
              r.returncode == 0 and r.stdout.splitlines()[0].endswith("\tdirect=Y"), r.stdout + r.stderr)
        r = run_task(wt2, "claim", stale_id)
        check("名指すファイルが登録のあとに変わったら direct=N:登録時の計画が古い",
              r.returncode == 0 and r.stdout.splitlines()[0].endswith("\tdirect=N:登録時の計画が古い"), r.stdout + r.stderr)
        run_task(wt2, "release", stale_id)
        r = run_task(wt2, "claim", edit_id)
        check("印の無いタスクの CLAIMED に direct の列は出ない",
              r.returncode == 0 and "direct=" not in r.stdout, r.stdout + r.stderr)


@weight(2)
def test_root_setting() -> None:
    say("task.py: config.toml の root に置き場が従い、置けない root は INVALID")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        config = os.path.join(main_path, ".tw", "config.toml")
        write(config, 'verify = "true"\nroot = "work/tw/"\n')
        write(os.path.join(main_path, "work", "tw", "task", "T-100.md"),
              taskfile.render(taskfile.Task("T-100", "根の下", "todo", "sonnet", "Y", (), BODY)))
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "root")
        git(wt1, "merge", "-q", "--ff-only", "main")

        lines = run_task(wt1, "config").stdout.splitlines()
        check("config が根の下の置き場を出す",
              {"root\twork/tw\tconfig", "direction\twork/tw/direction.md", "draft\twork/tw/draft", "task\twork/tw/task"}
              <= set(lines), "\n".join(lines))
        r = run_task(wt1, "status")
        check("status が根の下のタスクを読む", any(l.startswith("T-100\ttodo") for l in r.stdout.splitlines()),
              r.stdout + r.stderr)
        r = run_task(wt1, "new", "--summary", "根の下に足す", "--difficulty", "sonnet", "--loopable", "Y",
                     "--body-file", body_file(wt1))
        check("new が根の下に作る", r.returncode == 0 and r.stdout.strip() == "CREATED\tT-101\twork/tw/task/T-101.md"
              and os.path.exists(os.path.join(wt1, "work", "tw", "task", "T-101.md")), r.stdout + r.stderr)
        os.remove(os.path.join(wt1, "work", "tw", "task", "T-101.md"))
        os.remove(os.path.join(wt1, "body.md"))

        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        before = run_task(wt1, "verify-check").stdout.strip()
        write(os.path.join(wt1, "work", "tw", "draft", "x.md"), "- **x**\n")
        check("根の下の draft/ を足しても鍵は変わらない",
              before.startswith("VERIFIED_SAME\t") and run_task(wt1, "verify-check").stdout.strip() == before, before)
        os.remove(os.path.join(wt1, "work", "tw", "draft", "x.md"))
        os.remove(os.path.join(wt1, "work.txt"))

        r = run_task(wt1, "claim", "T-100")
        check("claim が通る", r.returncode == 0, r.stdout + r.stderr)
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin="- 振り返り: 兆候なし\n")
        check("done が根の下のタスクファイルを書く",
              r.returncode == 0 and r.stdout.startswith("DONE\tT-100\twork/tw/task/T-100.md\tstaged"), r.stdout + r.stderr)
        check(".tw/task/ は作られない", not os.path.exists(os.path.join(wt1, TASK_REL)))

        for bad in ("../x", "a/../../x", "/x", ".", "./", ".git", ".git/hooks", ".tw/local", ".tw/local/x"):
            write(config, f'verify = "なし"\nroot = "{bad}"\n')
            r = run_task(main_path, "config")
            check(f"root = {bad!r} は INVALID（終了コード3）",
                  r.returncode == 3 and r.stdout.startswith("INVALID\t.tw/config.toml:2:"), r.stdout + r.stderr)


TESTS = (
    test_registered_plan,
    test_edit_and_plan_check,
    test_edit_deps,
    test_edit_section,
    test_root_setting,
    test_direct_mark,
)
