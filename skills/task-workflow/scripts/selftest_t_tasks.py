from __future__ import annotations

import os
import tempfile

from selftest_support import check, git, refuse_direct_run, say, write  # noqa: E402
import beads  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_fixtures import BODY, PLANNED_BODY, body_file, commit_task, issue_json, make_repo, run_task, shown, start_task, unset_metadata  # noqa: E402


def test_taskfile_body() -> None:
    say("taskfile.validate_new_body ほか（本文の検査）")
    check("全角括弧を省いた「決まっていること」が枠の見出しに当たる",
          taskfile.section_heading("決まっていること") in taskfile.SECTION_HEADINGS)

    check("正しい登録時の本文はOK（空・「なし」の欄を含む）", taskfile.validate_new_body(PLANNED_BODY, hold=False) is None)
    check("枠の見出しが欠けた本文は拒む", taskfile.validate_new_body("## 目的・背景\nx\n", hold=True) is not None)
    check(
        "枠の見出しの順が違う本文は拒む",
        taskfile.validate_new_body(BODY.replace("## 注意\n\n## 参考情報\n", "## 参考情報\n\n## 注意\n"), hold=True)
        is not None,
    )
    check("枠の外の見出しは拒む", taskfile.validate_new_body(BODY + "\n## 背景\nx\n", hold=True) is not None)
    check("見出しより前の文は拒む", taskfile.validate_new_body("前置き\n\n" + BODY, hold=True) is not None)
    check(
        "目的・背景が「なし」なら拒む",
        taskfile.validate_new_body(BODY.replace("## 目的・背景\nx\n", "## 目的・背景\nなし\n"), hold=True) is not None,
    )
    check("完了条件が空なら拒む",
          taskfile.validate_new_body(BODY.replace("## 完了条件\nx\n", "## 完了条件\n"), hold=True) is not None)
    check("空の ## やること は拒む", taskfile.validate_new_body(BODY, hold=False) is not None)
    check("--hold なら空の ## やること を通す", taskfile.validate_new_body(BODY, hold=True) is None)
    filled_plan = task_body([("書く", "")])
    check("名指すファイルの小見出しが無い計画は拒む（--hold でも）",
          taskfile.validate_new_body(filled_plan, hold=False) is not None
          and taskfile.validate_new_body(filled_plan, hold=True) is not None)
    check("着手後の本文（やることあり）は validate_body を通る", taskfile.validate_body(filled_plan) is None)
    named = task_body([("書く", "x")], ["src/a.py", "docs/"]).replace("`src/a.py`", "`src/a.py`（直す）")
    check("名指すファイルつきの本文を通す（--hold でも）", taskfile.validate_new_body(named, hold=False) is None
          and taskfile.validate_new_body(named, hold=True) is None)
    two_steps = named.replace("### 1. 書く\nx\n", "### 1. 書く\nx\n### 2. 試す\ny\n")
    check("段を番号の順に読み、名指すファイル・作業先は段に数えない",
          taskfile.plan_steps(two_steps) == (("書く", "試す"), None), repr(taskfile.plan_steps(two_steps)))
    specs, err = taskfile.plan_step_specs(two_steps)
    check("欄の無い段は直前の段のあとに走り、並列の組が無い",
          err is None and [s.after for s in specs] == [(), (1,)] and taskfile.parallel_steps(specs) == ([], []),
          repr((specs, err)))

    def fielded(*steps: tuple[str, str]) -> str:
        return task_body(list(steps), ["src/a.py"])

    specs, err = taskfile.plan_step_specs(fielded(
        ("書く", "- 触るファイル: `src/a.py`"),
        ("文書", "- 前の段: なし\n- 触るファイル: `docs/`, `README.md`"),
        ("試す", "- 前の段: 1\n- 触るファイル: `src/a_test.py`"),
        ("合わせる", "- 前の段: 2, 3"),
    ))
    check("前の段・触るファイルの欄を読み、並列の組を番号の順に出す",
          err is None and [s.after for s in specs] == [(), (), (1,), (2, 3)]
          and specs[1].files == ("docs/", "README.md")
          and taskfile.parallel_steps(specs) == ([(1, 2), (2, 3)], []), repr((specs, err)))
    check("段ごとの直の待つ段と、並列の組になる段の触るファイルを出す",
          taskfile.step_waits(specs) == [(), (), (1,), (2, 3)]
          and taskfile.parallel_files(specs, 2) == ("src/a.py", "src/a_test.py")
          and taskfile.parallel_files(specs, 4) == (),
          repr((taskfile.step_waits(specs), taskfile.parallel_files(specs, 2))))
    specs, err = taskfile.plan_step_specs(fielded(
        ("書く", "- 触るファイル: `src/a.py`"),
        ("文書", "- 前の段: なし\n- 触るファイル: `docs/`"),
        ("合わせる", "- 前の段: 2"),
    ))
    check("最後の段は欄によらずほかの段すべてのあとに走り、触るファイルの欄が無くても並列の組に入らない",
          err is None and taskfile.parallel_steps(specs) == ([(1, 2)], [])
          and taskfile.step_waits(specs) == [(), (), (1, 2)],
          repr((specs, err, taskfile.parallel_steps(specs) if specs else None)))
    specs, err = taskfile.plan_step_specs(fielded(
        ("書く", "- 触るファイル: `src/`"),
        ("文書", "- 前の段: なし\n- 触るファイル: `docs/a.md`"),
        ("試す", "- 前の段: なし\n- 触るファイル: `src/a.py`"),
        ("直す", "- 前の段: 2\n- 触るファイル: `docs/a.md`"),
    ))
    check("触るファイルがディレクトリの中で重なる組を外す（前後の決まった組の重なりは見ない）",
          err is None and taskfile.parallel_steps(specs) == ([(1, 2), (2, 3)], [(1, 3, ("src/",))])
          and taskfile.step_waits(specs) == [(), (), (1,), (2, 3)],
          repr((specs, err, taskfile.parallel_steps(specs) if specs else None)))
    specs, err = taskfile.plan_step_specs(fielded(
        ("書く", "- 触るファイル: `src/a.py`"),
        ("足す", "- 前の段: なし\n- 触るファイル: `src/a.py`"),
        ("試す", "- 前の段: 2\n- 触るファイル: `src/b.py`"),
        ("合わせる", ""),
    ))
    check("重なりで外した辺で前後の決まった組も並列から外す",
          err is None and taskfile.parallel_steps(specs) == ([], [(1, 2, ("src/a.py",))]),
          repr((specs, err, taskfile.parallel_steps(specs) if specs else None)))
    for label, steps in (
        ("前の段がその段以上の番号", [("書く", ""), ("試す", "- 前の段: 2\n- 触るファイル: `a`")]),
        ("前の段が数字でない", [("書く", ""), ("試す", "- 前の段: 一\n- 触るファイル: `a`")]),
        ("前の段の番号が重なる", [("書く", ""), ("試す", ""), ("合わせる", "- 前の段: 1, 1")]),
        ("同じ欄が2行", [("書く", ""), ("試す", "- 前の段: 1\n- 前の段: 1")]),
        ("触るファイルが `パス` の並びでない", [("書く", "- 触るファイル: src/a.py")]),
        ("触るファイルが絶対パス", [("書く", "- 触るファイル: `/etc/x`")]),
        ("触るファイルに ..", [("書く", "- 触るファイル: `../x`")]),
        ("並列の組に入る段に触るファイルが無い",
         [("書く", ""), ("試す", "- 前の段: なし\n- 触るファイル: `a`"), ("合わせる", "")]),
    ):
        check(f"{label} 計画は登録の検査で拒む", taskfile.validate_new_body(fielded(*steps), hold=False) is not None,
              repr(taskfile.plan_step_specs(fielded(*steps))))
    for label, bad in (
        ("段が1つも無い", named.replace("### 1. 書く\n", "")),
        ("`### 2.` から始まる", named.replace("### 1. 書く", "### 2. 書く")),
        ("番号に穴がある", two_steps.replace("### 2. 試す", "### 3. 試す")),
        ("番号の無い `### ` 見出しがある", named.replace("### 1. 書く", "### 書く")),
    ):
        check(f"{label} ## やること は登録の検査で拒む（--hold でも）",
              taskfile.validate_new_body(bad, hold=False) is not None and taskfile.validate_new_body(bad, hold=True) is not None)
    unnumbered = BODY.replace("## やること\n", "## やること\n1. 書く\n")
    check("edit の検査は ## やること を変えたときだけ段を求める",
          taskfile.validate_edited_body(BODY, unnumbered) is not None
          and taskfile.validate_edited_body(BODY, filled_plan) is None
          and taskfile.validate_edited_body(unnumbered, unnumbered.replace("## 注意\n", "## 注意\n- y\n")) is None)
    check("名指すファイルを読む（説明と次の行を除く）", taskfile.plan_files(named) == (("src/a.py", "docs/"), None),
          repr(taskfile.plan_files(named)))
    check("名指すファイルの形の違う行は拒む",
          taskfile.validate_new_body(named.replace("- `docs/`", "docs/"), hold=False) is not None)
    check("名指すファイルの .. を含むパスは拒む",
          taskfile.validate_new_body(named.replace("`docs/`", "`../x`"), hold=False) is not None)
    check("名指すファイルの絶対パスは拒む",
          taskfile.validate_new_body(named.replace("`docs/`", "`/etc/x`"), hold=False) is not None)

    def with_repo(lines: str) -> str:
        return task_body([("書く", "x")], ["src/a.py", "docs/"], "/w/placeholder").replace("- `/w/placeholder`\n", lines)

    check("作業先が無ければ (None, None)", taskfile.plan_work_repo(named) == (None, None))
    check("作業先の絶対パス1行を読み、名指すファイルの範囲に入れない",
          taskfile.validate_new_body(with_repo("- `/w/repo`（作業先）\n"), hold=False) is None
          and taskfile.plan_work_repo(with_repo("- `/w/repo`\n")) == ("/w/repo", None)
          and taskfile.plan_files(with_repo("- `/w/repo`\n")) == (("src/a.py", "docs/"), None),
          repr(taskfile.plan_work_repo(with_repo("- `/w/repo`\n"))))
    for label, lines in (
        ("相対パス", "- `w/repo`\n"),
        ("~ で始まるパス", "- `~/repo`\n"),
        ("2行", "- `/w/a`\n- `/w/b`\n"),
        ("形の違う行", "/w/repo\n"),
        ("空", ""),
    ):
        check(f"作業先の{label}は拒む", taskfile.validate_new_body(with_repo(lines), hold=False) is not None,
              repr(taskfile.plan_work_repo(with_repo(lines))))
    check("作業先の小見出しが2つなら拒む",
          taskfile.validate_new_body(with_repo("- `/w/a`\n\n### 作業先\n- `/w/b`\n"), hold=False) is not None)
    check(
        "本文で結果の名前に言及しただけなら通る（見出しではない）",
        taskfile.validate_new_body(
            PLANNED_BODY.replace("## 参考情報\n", "## 参考情報\n`## 結果`セクションは作業後に追加します\n"), hold=False
        )
        is None,
    )
    check(
        "本文に結果の見出しがあると拒まれる",
        taskfile.validate_new_body(PLANNED_BODY + "\n## 結果\n結果の内容\n", hold=False) is not None,
    )


def test_new_and_status_single_worktree() -> None:
    say("task.py new/status（単独の作業ツリー）")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp)

        r = run_task(
            wt1, "new", "--summary", "1件目", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", body_file(wt1)
        )
        check("CREATEDで返る", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
        task_id = r.stdout.split("\t")[1]
        written = shown(wt1, task_id)
        check(
            "show の閉じる --- の次は空行1行で、末尾は改行1つ",
            "\n---\n\n## " in written and "\n---\n\n\n" not in written and written.endswith("\n") and not written.endswith("\n\n"),
            written,
        )

        r = run_task(wt1, "status")
        check(
            "status に READY の行が出る",
            any(l.startswith(f"{task_id}\ttodo\t") and "\tREADY\t" in l for l in r.stdout.splitlines()),
            r.stdout,
        )
        check("long_summary 行は0件", "\nlong_summary\t0\t-" in r.stdout, r.stdout)

        run_task(
            wt1, "new", "--summary", "あ" * 41, "--difficulty", "haiku", "--loopable", "Y", "--body-file", body_file(wt1)
        )
        r = run_task(wt1, "status")
        check("80桁を超える summary を long_summary に出す", "\nlong_summary\t1\t" in r.stdout, r.stdout)

        bad_body = write(os.path.join(wt1, "bad.md"), "## 目的\nx\n")
        r = run_task(
            wt1, "new", "--summary", "本文が足りない", "--difficulty", "haiku", "--loopable", "N", "--body-file", bad_body
        )
        check("必須節が無い本文は終了コード2", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)


def test_claim_and_release_single_worktree() -> None:
    say("task.py claim/release（単独の作業ツリー）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "既存タスク", "todo", "sonnet", "Y", (), BODY))

        r = run_task(wt1, "claim", "T-100")
        check(
            "CLAIMEDで返り、feature枝を切る",
            r.returncode == 0 and r.stdout.startswith("CLAIMED\tT-100"),
            r.stdout + r.stderr,
        )
        check("feature/T-100に移っている", ledger.current_branch(cwd=wt1) == "feature/T-100")

        r = run_task(wt1, "claim", "T-100")
        check("同じ作業ツリーからの2回目はTAKEN", r.returncode == 4 and r.stdout.startswith("TAKEN\t"), r.stdout)

        r = run_task(wt1, "release", "T-100")
        check("releaseでRELEASED", r.returncode == 0 and r.stdout.strip() == "RELEASED\tT-100", r.stdout)

        r = run_task(wt1, "release", "T-100")
        check(
            "2回目のreleaseもNOT_CLAIMEDで0終了",
            r.returncode == 0 and r.stdout.strip() == "NOT_CLAIMED\tT-100",
            r.stdout,
        )

        r = run_task(wt1, "claim", "T-999")
        check("存在しないIDはNOT_READY", r.returncode == 4 and r.stdout.startswith("NOT_READY\t"), r.stdout)


#
# **架空のタスクだけを使う**（実際のプロジェクトの tasks.json をフィクスチャにしない）。


def test_new_parallel_no_collision() -> None:
    say("task.py new: 2本の作業ツリーから同時に打っても番号が重ならない")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, wt2 = make_repo(tmp)
        b1 = body_file(wt1)
        b2 = body_file(wt2)
        p1 = start_task(wt1, "new", "--summary", "並行1", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", b1)
        p2 = start_task(wt2, "new", "--summary", "並行2", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", b2)
        out1, err1 = p1.communicate(timeout=30)
        out2, err2 = p2.communicate(timeout=30)
        check("wt1側はCREATED", p1.returncode == 0 and out1.startswith("CREATED\t"), out1 + err1)
        check("wt2側はCREATED", p2.returncode == 0 and out2.startswith("CREATED\t"), out2 + err2)
        id1 = out1.split("\t")[1] if out1.startswith("CREATED\t") else "?1"
        id2 = out2.split("\t")[1] if out2.startswith("CREATED\t") else "?2"
        check("番号が重ならない", id1 != id2, f"{id1} {id2}")


def test_claim_race() -> None:
    say("task.py claim: 2本から取り合うと片方だけ通る")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "取り合うタスク", "todo", "sonnet", "Y", (), BODY))

        p1 = start_task(wt1, "claim", "T-100")
        p2 = start_task(wt2, "claim", "T-100")
        out1, err1 = p1.communicate(timeout=30)
        out2, err2 = p2.communicate(timeout=30)
        results = sorted([out1.split("\t")[0], out2.split("\t")[0]])
        check(
            "片方だけCLAIMEDでもう片方はTAKEN",
            results == ["CLAIMED", "TAKEN"],
            f"out1={out1!r} err1={err1!r} out2={out2!r} err2={err2!r}",
        )


def test_new_avoids_history_ids() -> None:
    say("task.py new: docs/history/tasks.md の番号を採番が避ける")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        write(
            os.path.join(main_path, "docs", "history", "tasks.md"),
            "# 完了タスクのアーカイブ\n\n## T-050\n\nむかしのタスク\n",
        )
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "T-050を履歴に積む")

        r = run_task(
            wt1, "new", "--summary", "履歴の後", "--difficulty", "haiku", "--loopable", "Y", "--body-file", body_file(wt1)
        )
        check("CREATEDで返る", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
        task_id = r.stdout.split("\t")[1]
        check("履歴のT-050より大きい番号になる（重複を避ける）", taskfile.id_number(task_id) > 50, task_id)


def test_taskfile_set_result_section() -> None:
    say("taskfile.set_result_section")
    body = BODY
    out = taskfile.set_result_section(body, "結果その1")
    check("結果が無ければ末尾に足す", out.endswith("## 結果\n\n結果その1\n"), out)
    check("元の節は残る", "## 目的・背景" in out and "## 参考情報" in out, out)

    out2 = taskfile.set_result_section(out, "結果その2（差し替え）")
    check(
        "既存の結果は置き換わる",
        "結果その1" not in out2 and "結果その2（差し替え）" in out2,
        out2,
    )
    check("置き換え後も節は1つだけ", out2.count("## 結果") == 1, out2)


def test_done_single_worktree() -> None:
    say("task.py done（単独の作業ツリー）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "完了させる", "todo", "sonnet", "Y", (), BODY))

        # premature.md は wt1 の外に置く（中に置くと未claimの判定の前にDIRTYでclaimが拒まれる）。
        premature = write(os.path.join(tmp, "premature.md"), "早すぎる結果\n")
        r = run_task(wt1, "done", "T-100", "--result-file", premature)
        check("未claimはNOT_OWNER", r.returncode == 4 and r.stdout.strip() == "NOT_OWNER\tT-100", r.stdout)

        r = run_task(wt1, "claim", "T-100")
        check("claimできる", r.returncode == 0, r.stdout + r.stderr)

        empty_result = write(os.path.join(wt1, "empty.md"), "   \n")
        r = run_task(wt1, "done", "T-100", "--result-file", empty_result)
        check("結果が空なら終了コード2", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)

        result_path = write(os.path.join(wt1, "result.md"), "bun run check: 5 pass\n")
        r = run_task(wt1, "done", "T-100", "--result-file", result_path)
        check(
            "DONEで返り、ship で閉じると言う",
            r.returncode == 0 and r.stdout.strip() == "DONE\tT-100\tbeads:t-100\tship で閉じる",
            r.stdout,
        )
        check("何も stage しない", git(wt1, "diff", "--cached", "--name-only").stdout == "")
        labels = issue_json(wt1, "T-100").get("labels") or []
        check("送ったら done で閉じる印が付く", beads.SHIP_LABELS["done"] in labels, repr(labels))
        text = shown(wt1, "T-100")
        check("結果節が入る", "## 結果" in text and "bun run check: 5 pass" in text, text)

        r = run_task(wt1, "done", "T-100", "--dropped", "--result-file", result_path)
        check("--droppedもDONEで返る", r.returncode == 0, r.stdout + r.stderr)
        labels = issue_json(wt1, "T-100").get("labels") or []
        check("--dropped で送ったら dropped で閉じる印に替わる",
              beads.SHIP_LABELS["dropped"] in labels and beads.SHIP_LABELS["done"] not in labels, repr(labels))


def test_done_commits_since_claim() -> None:
    say("task.py done: claim 後のコミットを COMMITS_SINCE_CLAIM で知らせる（控えの無い印は出さない）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "コミットを知らせる", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "控えの無い印", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-100")
        write(os.path.join(wt1, "illicit.txt"), "x\n")
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "illicit")
        sha = git(wt1, "rev-parse", "--short", "HEAD").stdout.strip()
        result_path = write(os.path.join(wt1, "result.md"), "bun run check: 1 pass\n")
        r = run_task(wt1, "done", "T-100", "--result-file", result_path)
        lines = r.stdout.splitlines()
        check(
            "claim 後のコミットは COMMITS_SINCE_CLAIM で続けて知らせる",
            r.returncode == 0
            and lines[0] == "DONE\tT-100\tbeads:t-100\tship で閉じる"
            and lines[1:] == [f"COMMITS_SINCE_CLAIM\tT-100\t{sha}"],
            r.stdout,
        )

        run_task(wt2, "claim", "T-101")
        unset_metadata(wt2, "T-101", beads.CLAIM_HEAD_KEY)
        write(os.path.join(wt2, "illicit2.txt"), "x\n")
        git(wt2, "add", "-A")
        git(wt2, "commit", "-q", "-m", "illicit2")
        result_path2 = write(os.path.join(wt2, "result2.md"), "bun run check: 1 pass\n")
        r2 = run_task(wt2, "done", "T-101", "--result-file", result_path2)
        check(
            "控え（task_claim_head）の無い印はコミットがあっても落ちず、知らせない",
            r2.returncode == 0 and r2.stdout.strip() == "DONE\tT-101\tbeads:t-101\tship で閉じる",
            r2.stdout,
        )


TESTS = (
    test_done_commits_since_claim,
    test_done_single_worktree,
    test_claim_and_release_single_worktree,
    test_new_and_status_single_worktree,
    test_claim_race,
    test_new_parallel_no_collision,
    test_new_avoids_history_ids,
    test_taskfile_body,
    test_taskfile_set_result_section,
)


if __name__ == "__main__":
    refuse_direct_run()
