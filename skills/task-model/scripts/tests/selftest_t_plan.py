from __future__ import annotations

import os
import subprocess
import tempfile

from selftest_support import check, git, refuse_direct_run, say, weight, write  # noqa: E402
import beads  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_fixtures import BODY, PLANNED_BODY, _claim_work_and_done, commit_task, issue_count, make_repo, make_submodule_repo, drift_submodule, metadata, run_task, set_plan_directly, shown, shown_body  # noqa: E402


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
            return shown(wt1, tid)

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
def test_edit_sections_in_one_call() -> None:
    say("task.py edit: --section と --body-file の組を繰り返して、複数の節を1回で書く")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        for tid in ("T-120", "T-121"):
            commit_task(main_path, taskfile.Task(tid, "複数節", "todo", "sonnet", "Y", (), task_body()))
        files = {name: os.path.join(tmp, f"{name}.md") for name in ("plan", "note", "ref")}
        write(files["plan"], "### 1. 書く\n\n- x\n")
        write(files["note"], "- 申し送り\n")
        write(files["ref"], "- 参考\n")
        run_task(wt1, "claim", "T-120")
        run_task(wt1, "claim", "T-121")

        def edit(*pairs: tuple[str, str], extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
            flags = [x for name, path in pairs for x in ("--section", name, "--body-file", path)]
            return run_task(wt1, "edit", "T-120", *flags, *extra)

        r = edit(("やること", files["plan"]), ("注意", files["note"]), ("参考情報", files["ref"]))
        after = shown(wt1, "T-120")
        check("3つの節が1回で入り、EDITED は1行だけ", r.returncode == 0 and r.stdout.strip() == "EDITED\tT-120"
              and "### 1. 書く" in after and "- 申し送り" in after and "- 参考" in after, r.stdout + r.stderr + after)
        r = run_task(wt1, "plan-check", "T-120")
        check("複数節の記入も作業より先なら PLAN_FIRST", r.stdout.strip() == "PLAN_FIRST\tT-120", r.stdout + r.stderr)

        for label, pairs in (
            ("不正な節名が混じる", (("注意", files["note"]), ("ほげ", files["ref"]))),
            ("同じ節を2回指す", (("注意", files["note"]), ("## 注意", files["ref"]))),
        ):
            mid = shown(wt1, "T-120")
            r = edit(*pairs)
            check(f"{label}と何も書かずに終了コード2", r.returncode == 2 and "usage:" in r.stderr
                  and shown(wt1, "T-120") == mid, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-120", "--section", "注意", "--section", "参考情報", "--body-file", files["note"])
        check("組の数が違えば何も書かずに終了コード2", r.returncode == 2 and "usage:" in r.stderr, r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-120", "--body-file", files["note"], "--body-file", files["ref"])
        check("--section の無い --body-file の複数は終了コード2", r.returncode == 2 and "usage:" in r.stderr, r.stdout + r.stderr)

        mid = shown(wt1, "T-120")
        write(files["ref"], "別の条件\n")
        r = edit(("注意", files["note"]), ("完了条件", files["ref"]))
        check("複数節でも枠の節を変えれば FRAME_CHANGED で拒み、前の節も書かない", r.returncode == 4
              and r.stdout.startswith("FRAME_CHANGED\tT-120\t") and shown(wt1, "T-120") == mid, r.stdout + r.stderr)
        write(files["note"], "- 二度目の申し送り\n")
        r = edit(("注意", files["note"]), ("完了条件", files["ref"]), extra=("--change-frame",))
        check("--change-frame を付ければ複数節でも書く", r.returncode == 0
              and "- 二度目の申し送り" in shown(wt1, "T-120") and "別の条件" in shown(wt1, "T-120"), r.stdout + r.stderr)

        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "edit", "T-121", "--section", "やること", "--body-file", files["plan"],
                     "--section", "注意", "--body-file", files["note"])
        check("複数節でも作業のあとの初回の記入は WORK_BEFORE_PLAN で拒む", r.returncode == 4
              and r.stdout.startswith("WORK_BEFORE_PLAN\tT-121\t") and "### 1. 書く" not in shown(wt1, "T-121"),
              r.stdout + r.stderr)


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
        check("木に無いパスを名指すと終了コード2で、課題を作らない", r.returncode == 2
              and "nothing.txt" in r.stderr and issue_count(main_path) == 0, r.stdout + r.stderr)
        r = register("空の計画", BODY)
        check("空の ## やること は終了コード2で、課題を作らない", r.returncode == 2 and "--hold" in r.stderr
              and issue_count(main_path) == 0, r.stdout + r.stderr)
        r = register("段に穴", plan_body("shared.txt").replace("### 1. 書く", "### 2. 書く"))
        check("段が `### 1.` から穴なく続かない ## やること は終了コード2と理由で、課題を作らない", r.returncode == 2
              and "### 1." in r.stderr and issue_count(main_path) == 0, r.stdout + r.stderr)
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
        plan_base = lambda tid: metadata(main_path, tid).get(beads.PLAN_BASE_KEY)  # noqa: E731
        check("計画つきの登録は CREATED で、主ブランチの SHA を metadata に控える", same != "" and changed != ""
              and plan_base(same) == head and plan_base(changed) == head,
              r.stdout + r.stderr + r2.stdout + r2.stderr)
        check("--hold の空の計画は CREATED で、控えを作らない", unplanned != ""
              and plan_base(unplanned) is None, r3.stdout + r3.stderr)
        check("作業先のある登録は作業先の主ブランチの SHA を控える", remote_same != "" and remote_changed != ""
              and plan_base(remote_same) == work_head and plan_base(remote_changed) == work_head,
              r4.stdout + r4.stderr + r5.stdout + r5.stderr)
        r = run_task(main_path, "edit", unplanned, "--status", "todo")
        check("--hold の登録を todo に戻す", r.returncode == 0, r.stdout + r.stderr)
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
        r = run_task(wt1, "edit", same, "--body-file", "-",
                     stdin=shown_body(wt1, same).replace("## 注意\n", "## 注意\n作業中の知見\n"))
        check("PLAN_REGISTERED のあとは作業してからの書き足しも WORK_BEFORE_PLAN にならない", r.returncode == 0
              and r.stdout.strip() == f"EDITED\t{same}", r.stdout + r.stderr)

        run_task(wt2, "claim", changed)
        r = run_task(wt2, "plan-check", changed)
        check("名指したファイルが変わっていれば PLAN_STALE と変わったファイル（終了コード0）", r.returncode == 0
              and r.stdout.strip() == f"PLAN_STALE\t{changed}\tsrc/new.txt", r.stdout + r.stderr)
        check("claim が判定した主ブランチの先端を metadata に控える",
              metadata(main_path, changed).get(beads.PLAN_TIP_KEY) == tip)
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
        check("登録時の計画のまま送れる", r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)


@weight(1)
def test_direct_mark() -> None:
    say("task.py new・edit・claim --direct: 近道の印は基準に当たるときだけ付き、着手時に測り直す")

    def register(body: str, difficulty: str = "haiku") -> subprocess.CompletedProcess:
        return run_task(main_path, "new", "--summary", "近道", "--difficulty", difficulty, "--loopable", "Y", "--direct",
                        "--body-file", "-", stdin=body)

    def front(task_id: str) -> str:
        return shown(main_path, task_id).split("\n---\n", 1)[0]

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        write(os.path.join(main_path, "src", "a.txt"), "a\n")
        write(os.path.join(main_path, "src", "b.txt"), "b\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "src を足す")

        for label, body, difficulty in (
            ("difficulty が haiku でない", task_body([("書く", "x")], ["shared.txt"]), "sonnet"),
            ("段が2つ", task_body([("書く", "x"), ("足す", "y")], ["shared.txt"]), "haiku"),
            ("名指すファイルが3つ", task_body([("書く", "x")], ["shared.txt", "src/b.txt", "src/"]), "haiku"),
            ("作業先がある", task_body([("書く", "x")], ["shared.txt"], tmp), "haiku"),
        ):
            r = register(body, difficulty)
            check(f"{label}の --direct は終了コード2で、課題を作らない",
                  r.returncode == 2 and "近道" in r.stderr and issue_count(main_path) == 0, r.stdout + r.stderr)

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
def test_root_ignored() -> None:
    say("task.py: config.toml に旧い root が書いてあっても拒まず無視する")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        config = os.path.join(main_path, ".tw", "config.toml")
        write(config, 'verify = "true"\nstore = "beads"\nroot = "work/tw/"\n')
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "root")
        git(wt1, "merge", "-q", "--ff-only", "main")
        commit_task(main_path, taskfile.Task("T-100", "根の下", "todo", "sonnet", "Y", (), BODY))

        lines = run_task(wt1, "config").stdout.splitlines()
        check("config が root・direction・draft の行を出さない",
              lines[:1] == ["CONFIG\t.tw/config.toml"]
              and not any(l.split("\t")[0] in ("root", "direction", "draft") for l in lines), "\n".join(lines))
        r = run_task(wt1, "status")
        check("status がタスクを読む", any(l.startswith("T-100\ttodo") for l in r.stdout.splitlines()),
              r.stdout + r.stderr)

        r = run_task(wt1, "claim", "T-100")
        check("claim が通る", r.returncode == 0, r.stdout + r.stderr)
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin="- 振り返り: 兆候なし\n")
        check("done が通る", r.returncode == 0 and r.stdout.startswith("DONE\tT-100\t"), r.stdout + r.stderr)

        for odd in ("../x", "/x", ".", ".git", ".tw/local"):
            write(config, f'verify = "なし"\nroot = "{odd}"\n')
            r = run_task(main_path, "config")
            check(f"root = {odd!r} でも拒まず、root の行を出さない",
                  r.returncode == 0 and not any(l.startswith("root\t") for l in r.stdout.splitlines()),
                  r.stdout + r.stderr)


def test_submodule_drift_is_not_work() -> None:
    say("task.py edit・plan-check: submodule のずれだけなら作業に数えず、通常の変更かコミットがあれば数える")
    planned = task_body([("書く", "")])
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2, origin = make_submodule_repo(tmp)
        for tid in ("T-100", "T-101", "T-102"):
            commit_task(main_path, taskfile.Task(tid, "submodule のずれ", "todo", "sonnet", "Y", (), BODY))
        git(wt1, "merge", "-q", "--ff-only", "main")

        run_task(wt1, "claim", "T-100")
        drift_submodule(origin, wt1, "v2")
        check("submodule のずれが git status に出ている", " M vendor/sub" in git(wt1, "status", "--porcelain").stdout)
        r = run_task(wt1, "edit", "T-100", "--body-file", "-", stdin=planned)
        check("submodule のずれだけなら初回の edit は EDITED（終了コード0）", r.returncode == 0
              and r.stdout.strip() == "EDITED\tT-100", r.stdout + r.stderr)
        r = run_task(wt1, "plan-check", "T-100")
        check("submodule のずれだけなら PLAN_FIRST", r.stdout.strip() == "PLAN_FIRST\tT-100", r.stdout + r.stderr)
        git(wt1, "submodule", "update", "-q")

        run_task(wt1, "claim", "T-101")
        drift_submodule(origin, wt1, "v3")
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "edit", "T-101", "--body-file", "-", stdin=planned)
        check("submodule のずれに通常ファイルの変更が加われば WORK_BEFORE_PLAN（終了コード4）", r.returncode == 4
              and r.stdout.startswith("WORK_BEFORE_PLAN\tT-101\t"), r.stdout + r.stderr)
        os.remove(os.path.join(wt1, "work.txt"))
        git(wt1, "submodule", "update", "-q")

        run_task(wt1, "claim", "T-102")
        drift_submodule(origin, wt1, "v4")
        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "work.txt")
        git(wt1, "commit", "-q", "-m", "作業")
        r = run_task(wt1, "edit", "T-102", "--body-file", "-", stdin=planned)
        check("submodule のずれに着手後のコミットが加われば WORK_BEFORE_PLAN（終了コード4）", r.returncode == 4
              and r.stdout.startswith("WORK_BEFORE_PLAN\tT-102\t"), r.stdout + r.stderr)


def test_draft_to_todo() -> None:
    say("task.py edit: ドラフトは完了条件とやることが書けたときだけ todo に進め、登録時の計画として控える")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        r = run_task(main_path, "new", "--summary", "ドラフト", "--difficulty", "sonnet", "--loopable", "Y", "--draft",
                     "--body-file", "-", stdin="## 目的・背景\n\n- 出どころ: 振り返り: T-001\n- 観点: 黄 道案内\n")
        draft = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""
        check("ドラフトを積む", draft != "", r.stdout + r.stderr)

        def edit(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
            return run_task(main_path, "edit", draft, *args, stdin=stdin)

        def still_draft() -> bool:
            return "\nstatus: draft\n" in shown(main_path, draft) and metadata(main_path, draft).get(beads.DRAFT_KEY) == beads.DRAFT_ON

        r = edit("--status", "todo")
        check("完了条件もやることも無いドラフトの --status todo は NOT_READY DRAFT（終了コード4）で変わらない",
              r.returncode == 4 and r.stdout.startswith(f"NOT_READY\t{draft}\tDRAFT\t") and still_draft(), r.stdout + r.stderr)
        planned = task_body([("書く", "x")], ["shared.txt"])
        plan = planned.split(f"{taskfile.PLAN_HEADING}\n", 1)[1].split(f"\n{taskfile.ACCEPTANCE_HEADING}\n", 1)[0]
        r = edit("--section", "やること", "--body-file", "-", stdin=plan.replace("### 1.", "### 2."))
        check("段の形の違うやることは終了コード2", r.returncode == 2 and still_draft(), r.stdout + r.stderr)
        r = edit("--section", "やること", "--body-file", "-", stdin=plan)
        check("完了条件が空のままでもやることを書ける", r.returncode == 0 and r.stdout.startswith(f"EDITED\t{draft}")
              and still_draft(), r.stdout + r.stderr)
        r = edit("--status", "todo")
        check("完了条件が空なら --status todo は NOT_READY DRAFT", r.returncode == 4
              and r.stdout.startswith(f"NOT_READY\t{draft}\tDRAFT\t") and still_draft(), r.stdout + r.stderr)
        r = edit("--section", "完了条件", "--body-file", "-", stdin="- 通る\n")
        check("空の完了条件を初めて書くのは FRAME_CHANGED にならない", r.returncode == 0
              and r.stdout.startswith(f"EDITED\t{draft}"), r.stdout + r.stderr)

        head = git(main_path, "rev-parse", "main").stdout.strip()
        r = edit("--status", "todo")
        row = next((l.split("\t") for l in run_task(main_path, "status").stdout.splitlines() if l.startswith(f"{draft}\t")), [])
        meta = metadata(main_path, draft)
        check("書けたドラフトは todo・READY になり、印が消えて計画の SHA を控える", r.returncode == 0
              and row[1:2] == ["todo"] and row[5:6] == ["READY"] and beads.DRAFT_KEY not in meta
              and meta.get(beads.PLAN_BASE_KEY) == head, r.stdout + r.stderr + str(row) + str(meta))
        check("todo へ進めると from_draft に色を除いた観点が残る", meta.get(beads.FROM_DRAFT_KEY) == "道案内", str(meta))
        r = run_task(wt1, "claim", draft)
        r2 = run_task(wt1, "plan-check", draft)
        check("todo にしたドラフトを着手でき、計画は PLAN_REGISTERED", r.stdout.startswith(f"CLAIMED\t{draft}")
              and r2.stdout.strip() == f"PLAN_REGISTERED\t{draft}\t{head}", r.stdout + r.stderr + r2.stdout + r2.stderr)
        check("着手のあとも from_draft が残る", metadata(main_path, draft).get(beads.FROM_DRAFT_KEY) == "道案内",
              str(metadata(main_path, draft)))

        r = run_task(main_path, "new", "--summary", "観点の無いドラフト", "--difficulty", "sonnet", "--loopable", "Y", "--draft",
                     "--body-file", "-", stdin="## 目的・背景\n\n作業中に見つけた改善\n")
        plain = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""
        run_task(main_path, "edit", plain, "--section", "完了条件", "--body-file", "-", stdin="- 通る\n")
        r = run_task(main_path, "edit", plain, "--status", "hold")
        meta = metadata(main_path, plain)
        check("観点の行が無いドラフトを hold へ進めると from_draft は 不明", r.returncode == 0 and beads.DRAFT_KEY not in meta
              and meta.get(beads.FROM_DRAFT_KEY) == beads.FROM_DRAFT_UNKNOWN, r.stdout + r.stderr + str(meta))


@weight(3)
def test_opus_question_and_viewpoint() -> None:
    say("task.py new・edit: opus の論点とドラフトの観点・色の検査は終了コード2で拒み、既存のタスクの編集は壊さない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp, branch="切らない")
        asked = task_body([("書く", "x")], ["shared.txt"], question="どちらに寄せるか")
        unasked = task_body([("書く", "x")], ["shared.txt"])

        def new(difficulty: str, body: str, *extra: str) -> subprocess.CompletedProcess:
            return run_task(main_path, "new", "--summary", "検査", "--difficulty", difficulty, "--loopable", "Y", *extra,
                            "--body-file", "-", stdin=body)

        def created(r: subprocess.CompletedProcess) -> str:
            return r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""

        before = issue_count(main_path)
        r = new("opus", unasked)
        check("論点が無い opus の new は終了コード2で作らない", r.returncode == 2 and issue_count(main_path) == before,
              r.stdout + r.stderr)
        r = new("opus", unasked, "--hold")
        check("--hold でも論点が無い opus は拒む", r.returncode == 2 and issue_count(main_path) == before, r.stdout + r.stderr)
        r = new("opus", asked)
        check("論点がある opus の new は通る", created(r) != "", r.stdout + r.stderr)

        sonnet = created(new("sonnet", unasked))
        r = run_task(main_path, "edit", sonnet, "--difficulty", "opus")
        check("論点が無いタスクを --difficulty opus だけで上げる edit は終了コード2",
              r.returncode == 2 and metadata(main_path, sonnet).get(beads.DIFFICULTY_KEY) == "sonnet", r.stdout + r.stderr)
        r = run_task(main_path, "edit", sonnet, "--difficulty", "opus", "--section", "解くべき論点", "--body-file", "-",
                     stdin="どちらに寄せるか\n")
        check("論点を書くのと一緒なら opus に上げられる", r.returncode == 0 and r.stdout.startswith(f"EDITED\t{sonnet}")
              and metadata(main_path, sonnet).get(beads.DIFFICULTY_KEY) == "opus", r.stdout + r.stderr)
        r = run_task(main_path, "edit", sonnet, "--section", "解くべき論点", "--body-file", "-", stdin="なし\n")
        check("opus の論点を「なし」に戻す edit は終了コード2", r.returncode == 2, r.stdout + r.stderr)

        old = created(new("sonnet", unasked))
        subprocess.run(["bd", "update", beads.to_bd_id(old), "--set-metadata", "difficulty=opus"], cwd=main_path,
                       capture_output=True, text=True)
        r = run_task(main_path, "edit", old, "--section", "注意", "--body-file", "-", stdin="- 追記\n")
        check("論点が空のまま opus になっている既存のタスクの別の節の edit は通る",
              r.returncode == 0 and r.stdout.startswith(f"EDITED\t{old}"), r.stdout + r.stderr)

        before = issue_count(main_path)
        r = new("sonnet", "## 目的・背景\n\n- 観点: 緑 道案内\n", "--draft")
        check("語彙外の観点のドラフトは終了コード2で積まない", r.returncode == 2 and issue_count(main_path) == before,
              r.stdout + r.stderr)
        r = new("sonnet", "## 目的・背景\n\n- 観点: 規則の棚卸し\n", "--draft")
        check("色の無い横断の観点のドラフトは積める", created(r) != "", r.stdout + r.stderr)

        pointed = task_body([("書く", "x")], ["shared.txt"], purpose="x\n- 観点: 黄 道案内")
        draft = created(new("sonnet", pointed, "--draft"))
        r = run_task(main_path, "edit", draft, "--difficulty", "opus", "--status", "todo")
        check("論点が無いドラフトを opus で todo に進めると NOT_READY DRAFT で変わらない",
              r.returncode == 4 and r.stdout.startswith(f"NOT_READY\t{draft}\tDRAFT\t")
              and "\nstatus: draft\n" in shown(main_path, draft), r.stdout + r.stderr)
        r = run_task(main_path, "edit", draft, "--difficulty", "opus", "--status", "todo", "--section", "解くべき論点",
                     "--body-file", "-", stdin="どちらに寄せるか\n")
        check("論点を書けば opus のドラフトを todo に進められる", r.returncode == 0
              and "\nstatus: todo\n" in shown(main_path, draft), r.stdout + r.stderr)


@weight(4)
def test_plan_coverage() -> None:
    say("task.py new・adopt・edit・plan-check: 段の `- 満たす完了条件:` が `## 完了条件` を覆わない計画を終了コード2で拒み、plan-check が PLAN_UNCOVERED で知らせる")
    criteria = "1. 通る\n2. 動く\n3. 文書がある"
    field = taskfile.PLAN_COVERS_FIELD

    def plan(*covers: str | None) -> str:
        steps = [(f"段{n}", "" if c is None else f"- {field}: {c}") for n, c in enumerate(covers, 1)]
        return task_body(steps, ["shared.txt"], acceptance=criteria)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")

        def new(body: str, *extra: str) -> subprocess.CompletedProcess:
            return run_task(main_path, "new", "--summary", "覆い", "--difficulty", "sonnet", "--loopable", "Y", *extra,
                            "--body-file", "-", stdin=body)

        def created(r: subprocess.CompletedProcess) -> str:
            return r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""

        before = issue_count(main_path)
        r = new(plan("1, 3", "3"))
        check("引かれていない完了条件の行があれば new は終了コード2で作らず、行番号を言う",
              r.returncode == 2 and "2 行目" in r.stderr and issue_count(main_path) == before, r.stdout + r.stderr)
        r = new(plan("1, 2", "3, 5"))
        check("完了条件に無い行を引けば new は終了コード2で作らず、その番号を言う",
              r.returncode == 2 and "5 は完了条件に無い行" in r.stderr and issue_count(main_path) == before, r.stdout + r.stderr)
        r = new(plan("1, x", "2, 3"))
        check("番号でない欄は new が終了コード2で拒む", r.returncode == 2 and field in r.stderr, r.stdout + r.stderr)
        r = new(plan("1, 2", "なし", "3"))
        covered = created(r)
        check("全行が引かれていれば通る（欄が `なし` の段があってもよい）", covered != "", r.stdout + r.stderr)
        old_style = created(new(plan(None, None)))
        check("欄が1つも無い旧い計画は従来どおり通る", old_style != "", "")
        r = new(plan("99", None).replace(criteria, ""), "--draft")
        check("完了条件が空のドラフトは欄があっても検査しない", r.returncode == 0 and created(r) != ""
              and "\nstatus: draft\n" in shown(main_path, created(r)), r.stdout + r.stderr)

        uncovered_plan = plan("1", "3").split(f"{taskfile.PLAN_HEADING}\n", 1)[1].split(f"\n{taskfile.ACCEPTANCE_HEADING}\n", 1)[0]
        r = run_task(main_path, "edit", covered, "--section", "やること", "--body-file", "-", stdin=uncovered_plan)
        check("edit は覆わない計画を終了コード2で拒み、書き換えない", r.returncode == 2 and "2 行目" in r.stderr
              and "段1" in shown(main_path, covered) and f"- {field}: 1\n" not in shown(main_path, covered), r.stdout + r.stderr)
        r = run_task(main_path, "edit", covered, "--body-file", "-", stdin=plan("1", "3"))
        check("本文ごとの edit も同じ", r.returncode == 2 and "2 行目" in r.stderr, r.stdout + r.stderr)
        r = run_task(main_path, "edit", old_style, "--body-file", "-", stdin=plan("1", "3"))
        check("欄の無い計画へ欄を足して抜ければ edit が拒む", r.returncode == 2, r.stdout + r.stderr)

        r = subprocess.run(["bd", "create", "--id", "t-raw", "--title", "取り込み", "--silent"], cwd=main_path,
                           capture_output=True, text=True)
        r = run_task(main_path, "adopt", "T-RAW", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", "-",
                     stdin=plan("1", "3"))
        check("adopt も覆わない計画を終了コード2で拒む", r.returncode == 2 and "2 行目" in r.stderr, r.stdout + r.stderr)
        r = run_task(main_path, "adopt", "T-RAW", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", "-",
                     stdin=plan("1, 2", "3"))
        check("adopt は覆う計画なら通る", r.returncode == 0 and r.stdout.startswith("ADOPTED\t"), r.stdout + r.stderr)

        bypass = taskfile.Task("T-200", "覆わない", "todo", "sonnet", "Y", (), plan("1", "3, 7"))
        commit_task(main_path, bypass)
        commit_task(main_path, taskfile.Task("T-201", "覆う", "todo", "sonnet", "Y", (), plan("1, 2", "3")))
        commit_task(main_path, taskfile.Task("T-202", "欄なし", "todo", "sonnet", "Y", (), plan(None, None)))
        run_task(wt1, "claim", "T-200")
        r = run_task(wt1, "plan-check", "T-200")
        lines = r.stdout.splitlines()
        check("plan-check は1行目のあとに PLAN_UNCOVERED（引かれていない行はそのまま、存在しない行は ?）を出す（終了コード0）",
              r.returncode == 0 and len(lines) == 2 and lines[0].startswith("PLAN_NOT_FIRST\tT-200\t")
              and lines[1] == "PLAN_UNCOVERED\tT-200\t2,7?", r.stdout + r.stderr)
        run_task(wt1, "release", "T-200", "--force")
        run_task(wt1, "claim", "T-201")
        r = run_task(wt1, "plan-check", "T-201")
        check("覆う計画に PLAN_UNCOVERED は出ない", "PLAN_UNCOVERED" not in r.stdout and r.returncode == 0, r.stdout + r.stderr)
        run_task(wt1, "release", "T-201", "--force")
        run_task(wt1, "claim", "T-202")
        r = run_task(wt1, "plan-check", "T-202")
        check("欄の無い旧い計画に PLAN_UNCOVERED は出ない", "PLAN_UNCOVERED" not in r.stdout and r.returncode == 0, r.stdout + r.stderr)


TESTS = (
    test_plan_coverage,
    test_opus_question_and_viewpoint,
    test_draft_to_todo,
    test_registered_plan,
    test_edit_and_plan_check,
    test_submodule_drift_is_not_work,
    test_edit_deps,
    test_edit_section,
    test_edit_sections_in_one_call,
    test_root_ignored,
    test_direct_mark,
)


if __name__ == "__main__":
    refuse_direct_run()
