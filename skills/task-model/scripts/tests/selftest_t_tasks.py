from __future__ import annotations

import functools
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from selftest_support import RESULT_FULL, RESULT_REQUIRED, check, git, refuse_direct_run, say, write  # noqa: E402
import beads  # noqa: E402
import gitinfo  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import INTERVENTION, UNLOCK, task_body  # noqa: E402
from selftest_fixtures import BODY, PLANNED_BODY, body_file, commit_task, flow_rows, issue_count, issue_json, make_repo, metadata, run_task, shown, shown_body, start_task, unset_metadata  # noqa: E402


def held(body: str) -> str:
    """`## 解除の条件` を足した本文（`hold` の登録の検査を通す）。"""
    return taskfile.put_section(body, taskfile.UNLOCK_HEADING, UNLOCK) or body


def test_taskfile_body() -> None:
    say("taskfile.validate_new_body ほか（本文の検査）")
    check("全角括弧を省いた「決まっていること」が枠の見出しに当たる",
          taskfile.section_heading("決まっていること") in taskfile.SECTION_HEADINGS)

    check("正しい登録時の本文はOK（空・「なし」の欄を含む）", taskfile.validate_new_body(PLANNED_BODY, hold=False, difficulty="sonnet") is None)
    check("枠の見出しが欠けた本文は拒む", taskfile.validate_new_body("## 目的・背景\nx\n", hold=True, difficulty="sonnet") is not None)
    check(
        "枠の見出しの順が違う本文は拒む",
        taskfile.validate_new_body(held(BODY.replace("## 注意\n\n## 参考情報\n", "## 参考情報\n\n## 注意\n")), hold=True, difficulty="sonnet")
        is not None,
    )
    check("枠の外の見出しは拒む", taskfile.validate_new_body(held(BODY) + "\n## 背景\nx\n", hold=True, difficulty="sonnet") is not None)
    check("見出しより前の文は拒む", taskfile.validate_new_body("前置き\n\n" + held(BODY), hold=True, difficulty="sonnet") is not None)
    check(
        "目的・背景が「なし」なら拒む",
        taskfile.validate_new_body(held(BODY.replace("## 目的・背景\nx\n", "## 目的・背景\nなし\n")), hold=True, difficulty="sonnet") is not None,
    )
    check("完了条件が空なら拒む",
          taskfile.validate_new_body(held(BODY.replace("## 完了条件\nx\n", "## 完了条件\n")), hold=True, difficulty="sonnet") is not None)
    check("空の ## やること は拒む", taskfile.validate_new_body(BODY, hold=False, difficulty="sonnet") is not None)
    check("--hold なら空の ## やること を通す", taskfile.validate_new_body(held(BODY), hold=True, difficulty="sonnet") is None)
    filled_plan = task_body([("書く", "")])
    check("名指すファイルの小見出しが無い計画は拒む（--hold でも）",
          taskfile.validate_new_body(filled_plan, hold=False, difficulty="sonnet") is not None
          and taskfile.validate_new_body(held(filled_plan), hold=True, difficulty="sonnet") is not None)
    check("着手後の本文（やることあり）は validate_body を通る", taskfile.validate_body(filled_plan) is None)
    named = task_body([("書く", "x")], ["src/a.py", "docs/"]).replace("`src/a.py`", "`src/a.py`（直す）")
    check("名指すファイルつきの本文を通す（--hold でも）", taskfile.validate_new_body(named, hold=False, difficulty="sonnet") is None
          and taskfile.validate_new_body(held(named), hold=True, difficulty="sonnet") is None)
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
        check(f"{label} 計画は登録の検査で拒む", taskfile.validate_new_body(fielded(*steps), hold=False, difficulty="sonnet") is not None,
              repr(taskfile.plan_step_specs(fielded(*steps))))
    for label, bad in (
        ("段が1つも無い", named.replace("### 1. 書く\n", "")),
        ("`### 2.` から始まる", named.replace("### 1. 書く", "### 2. 書く")),
        ("番号に穴がある", two_steps.replace("### 2. 試す", "### 3. 試す")),
        ("番号の無い `### ` 見出しがある", named.replace("### 1. 書く", "### 書く")),
    ):
        check(f"{label} ## やること は登録の検査で拒む（--hold でも）",
              taskfile.validate_new_body(bad, hold=False, difficulty="sonnet") is not None and taskfile.validate_new_body(held(bad), hold=True, difficulty="sonnet") is not None)
    unnumbered = BODY.replace("## やること\n", "## やること\n1. 書く\n")
    edited = lambda old, new: taskfile.validate_edited_body(old, new, draft=False, old_difficulty="sonnet", difficulty="sonnet")
    check("edit の検査は ## やること を変えたときだけ段を求める",
          edited(BODY, unnumbered) is not None
          and edited(BODY, filled_plan) is None
          and edited(unnumbered, unnumbered.replace("## 注意\n", "## 注意\n- y\n")) is None)
    check("名指すファイルを読む（説明と次の行を除く）", taskfile.plan_files(named) == (("src/a.py", "docs/"), None),
          repr(taskfile.plan_files(named)))
    check("名指すファイルの形の違う行は拒む",
          taskfile.validate_new_body(named.replace("- `docs/`", "docs/"), hold=False, difficulty="sonnet") is not None)
    check("名指すファイルの .. を含むパスは拒む",
          taskfile.validate_new_body(named.replace("`docs/`", "`../x`"), hold=False, difficulty="sonnet") is not None)
    check("名指すファイルの絶対パスは拒む",
          taskfile.validate_new_body(named.replace("`docs/`", "`/etc/x`"), hold=False, difficulty="sonnet") is not None)

    def with_repo(lines: str) -> str:
        return task_body([("書く", "x")], ["src/a.py", "docs/"], "/w/placeholder").replace("- `/w/placeholder`\n", lines)

    check("作業先が無ければ (None, None)", taskfile.plan_work_repo(named) == (None, None))
    check("作業先の絶対パス1行を読み、名指すファイルの範囲に入れない",
          taskfile.validate_new_body(with_repo("- `/w/repo`（作業先）\n"), hold=False, difficulty="sonnet") is None
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
        check(f"作業先の{label}は拒む", taskfile.validate_new_body(with_repo(lines), hold=False, difficulty="sonnet") is not None,
              repr(taskfile.plan_work_repo(with_repo(lines))))
    check("作業先の小見出しが2つなら拒む",
          taskfile.validate_new_body(with_repo("- `/w/a`\n\n### 作業先\n- `/w/b`\n"), hold=False, difficulty="sonnet") is not None)
    check(
        "本文で結果の名前に言及しただけなら通る（見出しではない）",
        taskfile.validate_new_body(
            PLANNED_BODY.replace("## 参考情報\n", "## 参考情報\n`## 結果`セクションは作業後に追加します\n"), hold=False, difficulty="sonnet"
        )
        is None,
    )
    check(
        "本文に結果の見出しがあると拒まれる",
        taskfile.validate_new_body(PLANNED_BODY + "\n## 結果\n結果の内容\n", hold=False, difficulty="sonnet") is not None,
    )
    for label, path, hit in (
        ("report- で始まる md", "docs/research/report-schema.md", True),
        ("report.ts", "report.ts", True),
        ("ディレクトリ名だけが report", "docs/report/notes.md", False),
        ("末尾が report の名", "my-report.md", False),
        ("reports で終わる名", "comprehension-for-reports.md", False),
        ("./ 付き", "./report.ts", True),
        ("行番号付き", "docs/report-x.md:12", True),
        ("裸の語（MCP ツール名）", "report", False),
        ("識別子", "reportBody", False),
        ("テスト ID", "report-chart", False),
        ("ディレクトリ（末尾が /）", "docs/report/", False),
    ):
        got = taskfile.report_named_paths(task_body([("書く", f"`{path}` を書く")], acceptance="x"))
        check(f"report で始まる名の判定: {label}", (got == (path,)) == hit and (hit or got == ()), repr(got))
    both = task_body([("書く", "`a/report.ts` と `a/report.ts`")], ["src/a.py"], acceptance="`docs/report-x.md` を置く")
    check("やること・完了条件の両方から順に重複なく拾う", taskfile.report_named_paths(both) == ("a/report.ts", "docs/report-x.md"),
          repr(taskfile.report_named_paths(both)))
    check("バッククォートの外の文・目的・背景のパスは拾わない",
          taskfile.report_named_paths(task_body([("書く", "report.ts を書く")], purpose="`report.md`")) == ())


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
        date_line = r"\d{4}-\d\d-\d\d \d\d:\d\d"
        check(
            "show の front matter の dependencies の次に created と updated が日時で出る",
            re.search(rf"\ndependencies: \[[^\]]*\]\ncreated: {date_line}\nupdated: {date_line}\n---\n", written) is not None,
            written,
        )
        check(
            "Beads に日時が無ければ created と updated は -",
            "\ncreated: -\nupdated: -\n---\n" in taskfile.render(taskfile.Task("T-1", "題", "todo", "sonnet", "Y", (), ""))
            and beads.local_minute(None) == "-" and beads.local_minute("壊れた値") == "-",
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


def test_new_fixes() -> None:
    say("task.py new --fixes（直しが結ぶ元のタスクを metadata fixes に置き、台帳に new の出来事を残す）")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp)
        new = lambda *extra: run_task(
            wt1, "new", "--summary", "直し", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", body_file(wt1), *extra
        )
        original = new().stdout.split("\t")[1]
        check("--fixes なしの new は台帳に new を残さない", not [r for r in flow_rows(wt1) if r["event"] == "new"])

        r = new("--fixes", f"{original},{original.lower()}")
        fix = r.stdout.split("\t")[1]
        check("元のタスクを指す --fixes は CREATED", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
        check("metadata fixes に重複を畳んだ元のIDが入る", metadata(wt1, fix).get("fixes") == original, str(metadata(wt1, fix)))
        rows = [r for r in flow_rows(wt1) if r["event"] == "new"]
        check("台帳に new の出来事が fixes つきで1行残る", len(rows) == 1 and rows[0]["task"] == fix and rows[0]["fixes"] == original, str(rows))

        before = issue_count(wt1)
        for bad in ("T-9999", "foo"):
            r = new("--fixes", bad)
            check(f"--fixes {bad} は終了コード2で何も作らない", r.returncode == 2 and r.stdout == "" and issue_count(wt1) == before, r.stdout + r.stderr)


def _batch_file(dirpath: str, entries: list[dict]) -> str:
    return write(os.path.join(dirpath, "batch.json"), json.dumps(entries, ensure_ascii=False))


def _batch_entry(dirpath: str, summary: str, **extra) -> dict:
    name = f"body-{summary}.md"
    body_file(dirpath, name)
    return {"summary": summary, "difficulty": "sonnet", "loopable": "Y", "body_file": name, **extra}


def test_new_batch() -> None:
    say("task.py new --batch（複数件を1回で登録。後ろの要素が前の要素を deps に使える・1件の誤りで何も作らない・単件は従来どおり）")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp)
        first = run_task(wt1, "new", "--summary", "既存", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", body_file(wt1))
        existing = first.stdout.split("\t")[1]

        entries = [
            _batch_entry(wt1, "一つ目", deps=[existing]),
            _batch_entry(wt1, "二つ目", deps=["@1"]),
            _batch_entry(wt1, "三つ目", deps=["@1", "@2"], kind="bug"),
        ]
        r = run_task(wt1, "new", "--batch", _batch_file(wt1, entries))
        created = [l.split("\t")[1] for l in r.stdout.splitlines() if l.startswith("CREATED\t")]
        check("3件が CREATED の行で順に返る", r.returncode == 0 and len(created) == 3, r.stdout + r.stderr)
        check("TRACKER の行は高々1回（トラッカー無しなら0回）", sum(1 for l in r.stdout.splitlines() if l.startswith("TRACKER")) <= 1, r.stdout)
        deps = lambda t: sorted(d.get("depends_on_id") or d.get("id") for d in issue_json(wt1, t).get("dependencies") or [])
        check("1件目は既存のタスク、2件目は1件目、3件目は1・2件目に依存する",
              deps(created[0]) == [beads.to_bd_id(existing)] and deps(created[1]) == [beads.to_bd_id(created[0])]
              and deps(created[2]) == sorted(beads.to_bd_id(c) for c in created[:2]),
              str([deps(c) for c in created]))
        check("kind が付く", f"{beads.KIND_LABEL}bug" in (issue_json(wt1, created[2]).get("labels") or []))

        before = issue_count(wt1)
        write(os.path.join(wt1, "bad.md"), "## 目的\nx\n")
        broken = [_batch_entry(wt1, "良い"), {**_batch_entry(wt1, "悪い"), "body_file": "bad.md"}]
        r = run_task(wt1, "new", "--batch", _batch_file(wt1, broken))
        check("本文が検査に落ちる要素が1つでもあれば終了コード2で何も作らない",
              r.returncode == 2 and r.stdout == "" and issue_count(wt1) == before and "2件目" in r.stderr, r.stdout + r.stderr)
        for label, bad in (
            ("前の要素でない参照", [_batch_entry(wt1, "自分", deps=["@1"])]),
            ("Beads に無い deps", [_batch_entry(wt1, "無い", deps=["T-9999"])]),
            ("未知の欄", [_batch_entry(wt1, "未知", draft=True)]),
        ):
            r = run_task(wt1, "new", "--batch", _batch_file(wt1, bad))
            check(f"{label}は終了コード2で何も作らない", r.returncode == 2 and r.stdout == "" and issue_count(wt1) == before, r.stdout + r.stderr)

        r = run_task(wt1, "new", "--batch", _batch_file(wt1, [_batch_entry(wt1, "混ぜる")]), "--summary", "x")
        check("単件の引数と一緒だと終了コード2", r.returncode == 2 and issue_count(wt1) == before, r.stdout + r.stderr)
        r = run_task(wt1, "new", "--summary", "単件", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", body_file(wt1))
        check("単件の new は従来どおり CREATED", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
        r = run_task(wt1, "new", "--summary", "足りない")
        check("--batch も単件の必須の引数も無ければ終了コード2", r.returncode == 2, r.stdout + r.stderr)


def test_kind_label() -> None:
    say("task.py new・edit・adopt --kind（種類は label kind:<種類> の1つ）")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp)

        def kinds(task_id: str) -> list[str]:
            return sorted(l for l in issue_json(wt1, task_id).get("labels") or [] if l.startswith(beads.KIND_LABEL))

        def new(*extra: str) -> str:
            r = run_task(wt1, "new", "--summary", "種類", "--difficulty", "sonnet", "--loopable", "Y", *extra,
                         "--body-file", body_file(wt1))
            check(f"new {' '.join(extra) or '（--kind なし）'} は CREATED", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
            return r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""

        typed = new("--kind", "bug")
        check("new --kind bug は label kind:bug を付ける", kinds(typed) == ["kind:bug"], repr(kinds(typed)))
        plain = new()
        check("--kind なしは kind: の label を付けない", kinds(plain) == [], repr(kinds(plain)))
        r = run_task(wt1, "status")
        check("種類の無いタスクも有るタスクも status で READY",
              all(any(l.startswith(f"{t}\ttodo\t") and "\tREADY\t" in l for l in r.stdout.splitlines()) for t in (typed, plain)),
              r.stdout)

        before = issue_count(wt1)
        r = run_task(wt1, "new", "--summary", "語彙の外", "--difficulty", "sonnet", "--loopable", "Y", "--kind", "fix",
                     "--body-file", body_file(wt1))
        check("語彙の外の --kind は終了コード2で作らない", r.returncode == 2 and issue_count(wt1) == before, r.stdout + r.stderr)

        r = run_task(wt1, "edit", typed, "--kind", "feature")
        check("edit --kind feature で付け替わり kind: は1つ", r.returncode == 0 and kinds(typed) == ["kind:feature"],
              r.stdout + r.stderr + repr(kinds(typed)))
        r = run_task(wt1, "edit", typed, "--kind", "feature")
        check("同じ種類の edit --kind は通り、kind: は1つのまま", r.returncode == 0 and kinds(typed) == ["kind:feature"],
              r.stdout + r.stderr + repr(kinds(typed)))
        r = run_task(wt1, "edit", typed, "--kind", "-")
        check("edit --kind - で外れる", r.returncode == 0 and kinds(typed) == [], r.stdout + r.stderr + repr(kinds(typed)))

        subprocess.run(["bd", "label", "add", beads.to_bd_id(plain), "kind:bug"], cwd=wt1, capture_output=True, text=True)
        subprocess.run(["bd", "label", "add", beads.to_bd_id(plain), "kind:docs"], cwd=wt1, capture_output=True, text=True)
        r = run_task(wt1, "status")
        check("kind: が2つあれば invalid に出る", f"\ninvalid\t1\t{plain}" in r.stdout, r.stdout)
        r = run_task(wt1, "edit", plain, "--kind", "docs")
        check("edit --kind docs で kind: が1つに戻る", r.returncode == 0 and kinds(plain) == ["kind:docs"],
              r.stdout + r.stderr + repr(kinds(plain)))

        r = subprocess.run(["bd", "create", "--id", "t-raw", "--title", "取り込み", "-l", "kind:bug", "--silent"],
                           cwd=wt1, capture_output=True, text=True)
        check("振り分け前の課題（kind:bug つき）を作れる", r.returncode == 0, r.stdout + r.stderr)
        r = run_task(wt1, "adopt", "T-RAW", "--difficulty", "sonnet", "--loopable", "Y", "--kind", "refactor",
                     "--body-file", "-", stdin=PLANNED_BODY)
        adopted = r.stdout.strip().split("\t")[-1] if r.stdout.startswith("ADOPTED\t") else ""
        check("adopt --kind refactor は kind: を refactor の1つにする", adopted != "" and kinds(adopted) == ["kind:refactor"],
              r.stdout + r.stderr + (repr(kinds(adopted)) if adopted else ""))


def test_find() -> None:
    say("task.py find（READY を語と種類で絞り、FOUND・MANY・NONE で返す）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)

        def add(number: int, summary: str, kind: str | None = None, files: tuple[str, ...] = ("shared.txt",),
                status: str = "todo", deps: tuple[str, ...] = ()) -> str:
            task_id = f"T-{number}"
            commit_task(main_path, taskfile.Task(task_id, summary, status, "sonnet", "Y", deps, task_body([("書く", "x")], files)))
            if kind is not None:
                subprocess.run(["bd", "label", "add", beads.to_bd_id(task_id), f"{beads.KIND_LABEL}{kind}"],
                               cwd=main_path, capture_output=True, text=True)
            return task_id

        def find(*args: str) -> subprocess.CompletedProcess:
            return run_task(wt1, "find", *args)

        add(101, "hoge の表示を直す")
        add(102, "ログを整える", files=("src/Render.ts",))
        many = [add(110 + i, f"一括の{i}") for i in range(6)]
        three = [add(120 + i, f"三件の{i}") for i in range(3)]
        kind_only = add(130, "種類だけ", kind="bug")
        add(131, "種類だけ")
        mixed_plain = add(140, "混合の種類なし")
        mixed_bug = add(141, "混合の不具合", kind="bug")
        add(142, "混合の機能", kind="feature")
        other_bug = add(143, "別の不具合", kind="bug")
        add(150, "除外の保留", status="hold")
        blocker = add(151, "依存の先")
        add(152, "除外の依存待ち", deps=(blocker,))
        claimed = add(153, "除外の着手中")
        r = run_task(wt2, "claim", claimed)
        check("着手中にする", r.returncode == 0, r.stdout + r.stderr)
        r = run_task(wt1, "new", "--summary", "除外のドラフト", "--difficulty", "sonnet", "--loopable", "Y", "--draft",
                     "--body-file", "-", stdin=DRAFT_BODY)
        check("ドラフトを積む", r.returncode == 0, r.stdout + r.stderr)

        status = run_task(wt1, "status").stdout
        ready = next((l.split("\t")[1] for l in status.splitlines() if l.startswith("ready\t")), "?")

        r = find("どこにも無い語")
        check("当たらなければ NONE と絞る前の READY の件数", r.returncode == 0 and r.stdout == f"NONE\t{ready}\n", r.stdout + r.stderr)
        r = find("ＨＯＧＥ")
        check("全角・大文字違いの語でも summary に当たれば FOUND",
              r.returncode == 0 and r.stdout == "FOUND\tT-101\tY\thoge の表示を直す\n", r.stdout + r.stderr)
        r = find("render.TS")
        check("名指すファイルのパスだけに当たっても FOUND", r.stdout.startswith("FOUND\tT-102\t"), r.stdout + r.stderr)
        r = find("一括")
        lines = r.stdout.splitlines()
        check("6件なら MANY 6・先頭4件の CANDIDATE・MORE 2",
              lines == ["MANY\t6", *(f"CANDIDATE\t{t}\tY\t一括の{i}" for i, t in enumerate(many[:4])), "MORE\t2"], r.stdout)
        r = find("三件")
        lines = r.stdout.splitlines()
        check("3件なら MANY 3・CANDIDATE 全件・MORE なし",
              lines == ["MANY\t3", *(f"CANDIDATE\t{t}\tY\t三件の{i}" for i, t in enumerate(three))], r.stdout)
        r = find("該当しない", "種類だけ")
        check("語どうしはどれか1つに当たればよい", r.stdout.startswith("MANY\t2\n"), r.stdout)
        r = find("--kind", "bug")
        ids = [l.split("\t")[1] for l in r.stdout.splitlines() if l.startswith("CANDIDATE")]
        check("--kind だけなら種類の無いタスクは当たらない",
              r.stdout.startswith("MANY\t3\n") and ids == [kind_only, mixed_bug, other_bug], r.stdout)
        r = find("混合", "--kind", "bug")
        ids = [l.split("\t")[1] for l in r.stdout.splitlines() if l.startswith("CANDIDATE")]
        check("--kind と語なら、語の当たる種類なしと同じ種類だけ", r.stdout.startswith("MANY\t2\n")
              and ids == [mixed_plain, mixed_bug], r.stdout)
        r = find("除外")
        check("hold・ドラフト・着手中・依存待ちは当たらない", r.stdout == f"NONE\t{ready}\n", r.stdout)
        r = find()
        check("語も --kind も無ければ終了コード2", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)
        r = find("一括", "--kind", "fix")
        check("語彙の外の --kind は終了コード2", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)


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
        check("feature/T-100に移っている", gitinfo.current_branch(cwd=wt1) == "feature/T-100")

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


def _ready_loopable_rows(wt: str) -> list[str]:
    """`tw status` の行のうち、着手可否が `READY` で `loopable` が `Y` の ID。"""
    rows = [l.split("\t") for l in run_task(wt, "status").stdout.split("\n---\n")[0].splitlines()]
    return [row[0] for row in rows if len(row) > 5 and row[3] == "Y" and row[5] == "READY"]


def test_hold() -> None:
    say("task.py hold（着手中・todo・すでに hold で HELD と CONTINUE を出し、## 注意 に論点を足す）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        body = task_body(caution="既存の注意", unlock=UNLOCK)
        for tid, status, loopable in (
            ("T-100", "todo", "Y"), ("T-101", "todo", "Y"), ("T-102", "todo", "N"), ("T-103", "todo", "Y"), ("T-104", "done", "Y"),
        ):
            commit_task(main_path, taskfile.Task(tid, tid, status, "sonnet", loopable, (), body))

        def hold(where: str, tid: str, reason: str) -> subprocess.CompletedProcess:
            return run_task(where, "hold", tid, "--reason-file", "-", stdin=reason)

        def caution(tid: str) -> str:
            return taskfile.section_content(shown_body(wt1, tid), taskfile.CAUTION_HEADING)

        run_task(wt1, "claim", "T-100")
        r = hold(wt2, "T-100", "- 論点X\n")
        check("他の作業ツリーの着手中は NOT_OWNER で書かない",
              r.returncode == 4 and r.stdout.startswith("NOT_OWNER\tT-100") and caution("T-100") == "既存の注意",
              r.stdout + r.stderr)

        before = _ready_loopable_rows(wt1)
        r = hold(wt1, "T-100", "- 論点A\n")
        after = _ready_loopable_rows(wt1)
        check("着手中は印を外してから HELD と CONTINUE を出す",
              r.returncode == 0 and r.stdout.splitlines()[:3] == ["RELEASED\tT-100", "HELD\tT-100", f"CONTINUE\tY\t{len(after)}"]
              and after == before == ["T-101", "T-103"], r.stdout + r.stderr + repr((before, after)))
        check("着手中から hold にし、## 注意 の既存の中身の後ろに論点を足す",
              "\nstatus: hold\n" in shown(wt1, "T-100") and caution("T-100") == "既存の注意\n- 論点A", caution("T-100"))
        check("台帳に hold の出来事を1行足す",
              [row["event"] for row in flow_rows(wt1) if row.get("task") == "T-100"] == ["claim", "release", "hold"],
              repr(flow_rows(wt1)))

        r = hold(wt1, "T-100", "- 論点B\n")
        check("すでに hold なら印を外さず、論点を足して HELD と CONTINUE を出す",
              r.returncode == 0 and r.stdout.splitlines() == ["HELD\tT-100", "CONTINUE\tY\t2"]
              and caution("T-100") == "既存の注意\n- 論点A\n- 論点B", r.stdout + caution("T-100"))

        before = _ready_loopable_rows(wt1)
        r = hold(wt1, "T-101", "- 論点C\n")
        after = _ready_loopable_rows(wt1)
        check("todo は印を外さず、CONTINUE は tw status の READY かつ Y から自分を除いた数",
              r.returncode == 0 and r.stdout.splitlines() == ["HELD\tT-101", f"CONTINUE\tY\t{len(after)}"]
              and [t for t in before if t != "T-101"] == after == ["T-103"], r.stdout + repr((before, after)))

        r = hold(wt1, "T-103", "- 論点D\n")
        check("READY かつ Y が残らなければ CONTINUE N 0",
              r.returncode == 0 and r.stdout.splitlines() == ["HELD\tT-103", "CONTINUE\tN\t0"] and _ready_loopable_rows(wt1) == [],
              r.stdout)

        r = run_task(wt1, "new", "--summary", "ドラフト", "--difficulty", "sonnet", "--loopable", "Y", "--draft",
                     "--body-file", "-", stdin=DRAFT_BODY)
        draft = r.stdout.split("\t")[1]
        for tid, label in (("T-104", "閉じたタスク"), (draft, "ドラフト"), ("T-999", "無い ID")):
            r = hold(wt1, tid, "- 論点\n")
            check(f"{label}は NOT_READY で書かない", r.returncode == 4 and r.stdout.startswith(f"NOT_READY\t{tid}"), r.stdout + r.stderr)
        r = hold(wt1, "T-102", " \n")
        check("論点が空なら終了コード2で書かない",
              r.returncode == 2 and r.stdout == "" and "\nstatus: todo\n" in shown(wt1, "T-102"), r.stdout + r.stderr)


def test_edit_refuses_loopable_n_while_claimed() -> None:
    say("task.py edit --loopable N（着手中の Y は拒んで tw hold を案内し、todo の Y は通す）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        for tid in ("T-200", "T-201"):
            commit_task(main_path, taskfile.Task(tid, "元の題", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-200")

        r = run_task(wt1, "edit", "T-200", "--loopable", "N", "--summary", "変えた題")
        check("着手中の Y→N はほかの旗ごと書かずに終了コード2で、tw hold を案内する",
              r.returncode == 2 and "tw hold T-200 --reason-file -" in r.stderr
              and metadata(wt1, "T-200").get("loopable") == "Y" and "\nsummary: 元の題\n" in shown(wt1, "T-200"),
              r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-200", "--loopable", "Y")
        check("着手中でも Y のままの --loopable Y は通る", r.returncode == 0 and r.stdout.startswith("EDITED\tT-200"), r.stdout + r.stderr)
        r = run_task(wt2, "edit", "T-200", "--loopable", "N")
        check("他の作業ツリーが持つ着手中への Y→N は通る（人が直す）",
              r.returncode == 0 and metadata(wt1, "T-200").get("loopable") == "N", r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-201", "--loopable", "N")
        check("todo のままの Y→N は通る",
              r.returncode == 0 and r.stdout.startswith("EDITED\tT-201") and metadata(wt1, "T-201").get("loopable") == "N",
              r.stdout + r.stderr)


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


def test_taskfile_draft_viewpoint() -> None:
    say("taskfile.draft_viewpoint（ドラフトの観点の行）")
    for body, expected in (
        ("## 目的・背景\n\n- 出どころ: 振り返り: T-001\n- 観点: 赤 機械の検査\n- 根拠: x\n", "機械の検査"),
        ("## 目的・背景\n\n- 観点: 黄 道案内\n", "道案内"),
        ("## 目的・背景\n\n- 観点: 段の所要時間\n", "段の所要時間"),
        ("## 目的・背景\n\n- 観点: 赤\n", None),
        ("## 目的・背景\n\n直したい改善\n", None),
        ("## 目的・背景\n\n直したい改善\n\n## 注意\n\n- 観点: 黄 道案内\n", None),
    ):
        check(f"{body.splitlines()[-1]!r} の観点は {expected!r}", taskfile.draft_viewpoint(body) == expected,
              repr(taskfile.draft_viewpoint(body)))


def test_taskfile_opus_and_viewpoint() -> None:
    say("taskfile の opus の論点・ドラフトの観点と色の検査（変えた節だけを見る）")
    planned = lambda **kw: task_body([("書く", "x")], ["shared.txt"], **kw)
    for label, difficulty, question, hold, ok in (
        ("opus で論点が「なし」", "opus", "なし", False, False),
        ("opus で論点が空", "opus", "", False, False),
        ("opus で論点が「なし」は --hold でも", "opus", "なし", True, False),
        ("opus で論点がある", "opus", "どちらに寄せるか", False, True),
        ("sonnet で論点が「なし」", "sonnet", "なし", False, True),
        ("haiku で論点が空", "haiku", "", False, True),
    ):
        got = taskfile.validate_new_body(planned(question=question), hold=hold, difficulty=difficulty)
        check(f"validate_new_body: {label}は{'通す' if ok else '拒む'}", (got is None) == ok, repr(got))

    empty, asked = planned(), planned(question="どちらに寄せるか")
    edit = functools.partial(taskfile.validate_edited_body, draft=False)
    check("opus で論点が空の既存本文の ## 注意 だけを変える編集は通る",
          edit(empty, empty.replace("## 注意\n", "## 注意\n- y\n"), old_difficulty="opus", difficulty="opus") is None)
    check("opus で論点を空に変える編集は拒む",
          edit(asked, empty, old_difficulty="opus", difficulty="opus") is not None)
    check("sonnet から opus に上げる編集は論点が空なら拒む",
          edit(empty, empty, old_difficulty="sonnet", difficulty="opus") is not None)
    check("sonnet から opus に上げる編集は論点があれば通る",
          edit(asked, asked, old_difficulty="sonnet", difficulty="opus") is None)
    check("opus のまま difficulty を指定し直す編集は論点が空でも通る",
          edit(empty, empty, old_difficulty="opus", difficulty="opus") is None)
    check("ドラフトの編集は opus の論点を見ない",
          edit(empty, empty, draft=True, old_difficulty="sonnet", difficulty="opus") is None)

    def purposed(line: str) -> str:
        return planned(purpose=f"- 出どころ: 振り返り: T-001\n{line}\n- 根拠: x")

    for line, ok in (
        ("- 観点: 赤 機械の検査", True),
        ("- 観点: 黄 道案内", True),
        ("- 観点: 流れの数", True),
        ("- 観点: 段の所要時間", True),
        ("- 観点: 規則の棚卸し", True),
        ("- 観点: 緑 道案内", False),
        ("- 観点: 赤道案内", False),
        ("- 観点: 赤", False),
        ("- 観点: なにか", False),
        ("- 根拠: 観点の行なし", True),
    ):
        got = taskfile.viewpoint_refusal(purposed(line))
        check(f"viewpoint_refusal: {line!r} は{'通す' if ok else '拒む'}", (got is None) == ok, repr(got))
    check("validate_draft_body は語彙外の観点を拒む",
          taskfile.validate_draft_body("## 目的・背景\n\n- 観点: 緑 道案内\n") is not None
          and taskfile.validate_draft_body(f"## 目的・背景\n\n- 観点: 黄 道案内\n\n## 介入\n\n{INTERVENTION}\n") is None)
    odd = purposed("- 観点: なにか")
    check("語彙外の観点を持つ既存本文の ## 注意 だけを変える編集は通り、## 目的・背景 を変えると拒む",
          edit(odd, odd.replace("## 注意\n", "## 注意\n- y\n"), old_difficulty="sonnet", difficulty="sonnet") is None
          and edit(odd, odd.replace("- 根拠: x", "- 根拠: y"), old_difficulty="sonnet", difficulty="sonnet") is not None)


def test_taskfile_intervention() -> None:
    say("taskfile の ## 介入（観点つきの本文と harness の面を名指す計画で必須、形は語彙で見る）")
    new = functools.partial(taskfile.validate_new_body, hold=False, difficulty="sonnet")
    named = lambda path, text="": task_body([("書く", "x")], [path], intervention=text)  # noqa: E731
    pointed = "## 目的・背景\n\n- 出どころ: 振り返り: T-001\n- 観点: 黄 道案内\n"

    check("観点つきのドラフトに ## 介入 が無ければ拒む", taskfile.validate_draft_body(pointed) is not None)
    check("観点つきのドラフトに5行の ## 介入 があれば通す",
          taskfile.validate_draft_body(f"{pointed}\n## 介入\n\n{INTERVENTION}\n") is None)
    check("観点つきの本文は --hold で計画が無くても ## 介入 を求める",
          taskfile.validate_new_body(task_body(purpose="- 観点: 黄 道案内", unlock=UNLOCK), hold=True, difficulty="sonnet") is not None
          and taskfile.validate_new_body(task_body(purpose="- 観点: 黄 道案内", unlock=UNLOCK, intervention=INTERVENTION), hold=True,
                                         difficulty="sonnet") is None)
    for path in ("skills/x.md", "CLAUDE.md", "AGENTS.md", ".tw/config.toml", "scripts/hook/a.sh", ".claude/rules/a.md", "docs/workflow.md",
                 "skills/", "scripts/hook"):
        check(f"{path} を名指す計画は ## 介入 が無ければ拒み、あれば通す",
              new(named(path)) is not None and new(named(path, INTERVENTION)) is None, repr(new(named(path))))
    for path in ("docs/", "shared.txt", "skillsx/a.md", "src/CLAUDE.md", "src/AGENTS.md", "scripts/"):
        check(f"{path} は面に当たらず ## 介入 が無くても通す", new(named(path)) is None, repr(new(named(path))))

    def shaped(text: str) -> str | None:
        return new(named("skills/x.md", text))

    target = "- 出し先の種類: hook"
    for label, text, ok in (
        ("語彙の外の出し先の種類", INTERVENTION.replace(target, "- 出し先の種類: 文書"), False),
        ("出し先の種類が2語", INTERVENTION.replace(target, "- 出し先の種類: hook 検査"), False),
        ("常設の文章で削るもの無し", INTERVENTION.replace(target, "- 出し先の種類: 常設の文章"), False),
        ("常設の文章で削るもの有り", INTERVENTION.replace(target, "- 出し先の種類: 常設の文章") + "\n- 削るもの: 旧い節", True),
        ("常設の文章で削るものが空", INTERVENTION.replace(target, "- 出し先の種類: 常設の文章") + "\n- 削るもの:", False),
        ("基準線の行が欠ける", INTERVENTION.replace("- 基準線: 直近28日で12回\n", ""), False),
        ("やめる条件が空", INTERVENTION.replace("- やめる条件: 打ち直しが減らなければ外す", "- やめる条件:"), False),
        ("狙う兆候が2行", INTERVENTION + "\n- 狙う兆候: 人の差し戻し", False),
        ("梯子の6語のどれでも", INTERVENTION.replace(target, "- 出し先の種類: サブエージェント"), True),
        ("節が「なし」", "なし", False),
    ):
        got = shaped(text)
        check(f"## 介入: {label}は{'通す' if ok else '拒む'}", (got is None) == ok, repr(got))
    for word in taskfile.INTERVENTION_TARGETS:
        check(f"出し先の種類 {word} は語彙の中", shaped(INTERVENTION.replace(target, f"- 出し先の種類: {word}")
                                                  + "\n- 削るもの: x") is None)
    check("面も観点も無い本文でも ## 介入 があれば形を見る",
          new(named("shared.txt", INTERVENTION.replace(target, "- 出し先の種類: 文書"))) is not None
          and new(named("shared.txt", INTERVENTION)) is None)
    body, bare = named("skills/x.md", INTERVENTION), named("skills/x.md")
    check("## 介入 が ## 参考情報 より前なら拒む",
          taskfile.validate_body(bare.replace("## 参考情報\n", f"## 介入\n{INTERVENTION}\n## 参考情報\n")) is not None)
    check("## 介入 が2つなら拒む", taskfile.validate_body(f"{body}\n## 介入\n{INTERVENTION}\n") is not None)

    edit = functools.partial(taskfile.validate_edited_body, draft=False, old_difficulty="sonnet", difficulty="sonnet")
    check("面を名指し節の無い既存本文の ## 注意 だけを変える編集は通る",
          edit(bare, bare.replace("## 注意\n", "## 注意\n- y\n")) is None)
    check("節のある本文の ## 注意 だけを変える編集は通る", edit(body, body.replace("## 注意\n", "## 注意\n- y\n")) is None)
    check("## 介入 を語彙の外に変える編集は拒む", edit(body, body.replace(target, "- 出し先の種類: 文書")) is not None)
    check("ドラフトの ## 介入 を語彙の外に変える編集も拒む",
          taskfile.validate_edited_body(f"{pointed}\n## 介入\n\n{INTERVENTION}\n",
                                        f"{pointed}\n## 介入\n\n{INTERVENTION.replace(target, '- 出し先の種類: 文書')}\n",
                                        draft=True, old_difficulty="sonnet", difficulty="sonnet") is not None)

    check("intervention は5行と削るものを {名前: 値} で読む",
          taskfile.intervention(named("x", INTERVENTION + "\n- 削るもの: 旧い節")) == {
              "狙う兆候": "検証の打ち直し", "基準線": "直近28日で12回", "出し先の種類": "hook",
              "判定の期日": "送り出しから14日", "やめる条件": "打ち直しが減らなければ外す", "削るもの": "旧い節"},
          repr(taskfile.intervention(named("x", INTERVENTION))))
    check("節が無ければ intervention は None", taskfile.intervention(bare) is None)
    check("section_heading は 介入 を ## 介入 に揃える",
          taskfile.section_heading("介入") == taskfile.section_heading("## 介入") == taskfile.INTERVENTION_HEADING)

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "WORKFLOW.md"), encoding="utf-8") as f:
        lines = f.read().split("\n")

    def first_column(header: str) -> tuple[str, ...]:
        start = next((i for i, line in enumerate(lines) if line.startswith(f"| {header} |")), len(lines))
        rows: list[str] = []
        for line in lines[start + 2:]:
            if not line.startswith("| `"):
                break
            rows.append(line.split("`")[1])
        return tuple(rows)

    check("WORKFLOW.md の出し先の梯子の表が INTERVENTION_TARGETS と同じ順",
          first_column("出し先の種類") == taskfile.INTERVENTION_TARGETS, repr(first_column("出し先の種類")))
    check("WORKFLOW.md の harness の面の表が HARNESS_SURFACES と同じ",
          first_column("harness の面") == taskfile.HARNESS_SURFACES, repr(first_column("harness の面")))


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

        result_path = write(os.path.join(wt1, "result.md"), "bun run check: 5 pass\n" + RESULT_FULL)
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
        check("done 済みへの --dropped は ALREADY_DONE で終了コード4", r.returncode == 4 and r.stdout.startswith("ALREADY_DONE\tT-100\tdone"), r.stdout + r.stderr)
        labels = issue_json(wt1, "T-100").get("labels") or []
        check("印は done のまま替わらない",
              beads.SHIP_LABELS["done"] in labels and beads.SHIP_LABELS["dropped"] not in labels, repr(labels))


def test_done_required_lines() -> None:
    say("task.py done: ## 結果 の必須行の欠けは終了コード2で何も書かず、--dropped・揃った結果は通る")

    def claimed(tmp: str, body: str = BODY, verify: str | None = None) -> str:
        main_path, wt1, _wt2 = make_repo(tmp, verify=verify)
        commit_task(main_path, taskfile.Task("T-100", "必須行", "todo", "sonnet", "Y", (), body))
        run_task(wt1, "claim", "T-100")
        return wt1

    def labels(wt: str) -> list[str]:
        return issue_json(wt, "T-100").get("labels") or []

    def refused(r: subprocess.CompletedProcess, wt: str, line: str) -> bool:
        return (r.returncode == 2 and r.stdout == "" and line in r.stderr
                and beads.SHIP_LABELS["done"] not in labels(wt) and "## 結果" not in shown(wt, "T-100"))

    full = RESULT_FULL.splitlines(keepends=True)
    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp)
        for line in full:
            prefix = line.split(":")[0] + ":"
            r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin="".join(l for l in full if l != line))
            check(f"{prefix} が欠ければ拒み、欠けた行の名前を出して何も書かない", refused(r, wt1, prefix), r.stdout + r.stderr)
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=RESULT_FULL)
        check("全部揃えば、拒まれたあとの打ち直しで通る",
              r.returncode == 0 and beads.SHIP_LABELS["done"] in labels(wt1), r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp, verify="`true`")
        without_verify = "".join(l for l in full if not l.startswith("- 検証コマンド:"))
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=without_verify)
        check("台帳に verify が無ければ - 検証コマンド: を補わず拒む", refused(r, wt1, "- 検証コマンド:"), r.stdout + r.stderr)
        r = run_task(wt1, "verify")
        check("verify が通る", r.returncode == 0 and r.stdout.startswith("VERIFIED"), r.stdout + r.stderr)
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=without_verify)
        check("台帳の直近の verify が通っていれば - 検証コマンド: を補って通る",
              r.returncode == 0 and "- 検証コマンド: 通過（直近の tw verify が通過）" in shown(wt1, "T-100"),
              r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp)
        ledger.record_event(wt1, "verify", "T-100", "sonnet", result="VERIFIED_UNSTAMPED", seconds=0)
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=without_verify)
        check("直近の verify が VERIFIED_UNSTAMPED でも同じ文言で補って通る",
              r.returncode == 0 and "- 検証コマンド: 通過（直近の tw verify が通過）" in shown(wt1, "T-100"),
              r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp)
        ledger.record_event(wt1, "verify", "T-100", "sonnet", result="VERIFY_NOT_PASSED", seconds=0)
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=without_verify)
        check("直近の verify が落ちていれば補わず拒む", refused(r, wt1, "- 検証コマンド:"), r.stdout + r.stderr)

    for section, want in (("- 画面を目視で確かめる", False), ("1. 通る\n2. [目視] 狭い幅で崩れない", True),
                          ("[目視] 画面を確かめる", True), ("1. 文中の [目視] は印でない", False)):
        check(f"visual_required({section!r}) は {want}", taskfile.visual_required(section) is want)

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp, task_body(acceptance="- 画面を目視で確かめる"))
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=RESULT_FULL)
        check("完了条件に語「目視」があるだけなら - 目視: が無くても通る", r.returncode == 0, r.stdout + r.stderr)

    marked = "1. 通る\n2. [目視] 狭い幅で崩れない"
    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp, task_body(acceptance=marked))
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=RESULT_FULL)
        check("完了条件の項目の頭に印 [目視] があれば - 目視: が欠けると拒む", refused(r, wt1, "- 目視:"), r.stdout + r.stderr)
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin=RESULT_FULL + "- 目視: a.png を見比べて可\n")
        check("印があって - 目視: があれば通る", r.returncode == 0, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp, task_body(acceptance=marked))
        r = run_task(wt1, "done", "T-100", "--dropped", "--result-file", "-", stdin="- 却下: 要らない\n")
        check("印があっても --dropped は - 目視: を見ない", r.returncode == 0, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp)
        run_task(wt1, "lap", "T-100", "direct")
        r = run_task(wt1, "done", "T-100", "--result-file", "-", stdin="- 検証コマンド: 1 pass\n- 振り返り: 近道（省いた）\n")
        check("近道でも - 規約: が欠ければ拒む", refused(r, wt1, "- 規約:"), r.stdout + r.stderr)
        r = run_task(wt1, "done", "T-100", "--reflection", "skipped", "--result-file", "-",
                     stdin=RESULT_REQUIRED + "- 振り返り: 近道（省いた）\n")
        check("近道でも揃えば通る", r.returncode == 0, r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        wt1 = claimed(tmp)
        r = run_task(wt1, "done", "T-100", "--dropped", "--result-file", "-", stdin="- 却下: 別のタスクで足りる\n")
        check("--dropped は必須行が無くても通る（ドラフトの却下は test_draft_dropped）",
              r.returncode == 0 and beads.SHIP_LABELS["dropped"] in labels(wt1), r.stdout + r.stderr)


def test_done_replay_diff_notice() -> None:
    say("task.py done: harness の面を名指す計画で - タスクの再試の差: が無ければ REPLAY_DIFF_MISSING を出す（拒まない）。古い - 再演の差: の行も読む")
    replayed = INTERVENTION + "\n- タスクの再試の差: 5件 × k=2 で通過率 +1ポイント（no observed effect）"
    legacy = INTERVENTION + "\n- 再演の差: 5件 × k=2 で通過率 +1ポイント（no observed effect）"
    cases = (
        ("面を名指し、行が無い", "skills/x.md", INTERVENTION, "", True),
        ("面を名指し、行が空", "skills/x.md", INTERVENTION + "\n- タスクの再試の差:", "", True),
        ("面を名指し、古い名前の行が空", "skills/x.md", INTERVENTION + "\n- 再演の差:", "", True),
        ("面を名指し、行がある", "skills/x.md", replayed, "", False),
        ("面を名指し、古い名前の行がある", "skills/x.md", legacy, "", False),
        ("面を名指さない", "src/x.py", INTERVENTION, "", False),
        ("面を名指し、--dropped", "skills/x.md", INTERVENTION, "--dropped", False),
    )
    for label, path, intervention, flag, want in cases:
        with tempfile.TemporaryDirectory() as tmp:
            main_path, wt1, _wt2 = make_repo(tmp)
            body = task_body([("書く", "x")], [path], intervention=intervention)
            commit_task(main_path, taskfile.Task("T-100", "タスクの再試の差", "todo", "sonnet", "Y", (), body))
            run_task(wt1, "claim", "T-100")
            args = ["done", "T-100", *([flag] if flag else []), "--result-file", "-"]
            r = run_task(wt1, *args, stdin="- 却下: 要らない\n" if flag else RESULT_FULL)
            check(f"{label}: 知らせは {'出る' if want else '出ない'}、done は通る",
                  r.returncode == 0 and ("REPLAY_DIFF_MISSING\tT-100\t" in r.stdout) is want, r.stdout + r.stderr)


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
        result_path = write(os.path.join(wt1, "result.md"), "bun run check: 1 pass\n" + RESULT_FULL)
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
        result_path2 = write(os.path.join(wt2, "result2.md"), "bun run check: 1 pass\n" + RESULT_FULL)
        r2 = run_task(wt2, "done", "T-101", "--result-file", result_path2)
        check(
            "控え（task_claim_head）の無い印はコミットがあっても落ちず、知らせない",
            r2.returncode == 0 and r2.stdout.strip() == "DONE\tT-101\tbeads:t-101\tship で閉じる",
            r2.stdout,
        )


DRAFT_BODY = "## 目的・背景\n\n振り返りで見つけた改善\n"


def test_draft_register_and_refuse_claim() -> None:
    say("task.py new --draft（目的・背景だけで積み、一覧は DRAFT、着手は拒む）")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp)

        def new_draft(*extra: str, body: str = DRAFT_BODY) -> subprocess.CompletedProcess:
            return run_task(
                wt1, "new", "--summary", "ドラフト", "--difficulty", "sonnet", "--loopable", "Y", *extra,
                "--body-file", "-", stdin=body,
            )

        r = new_draft("--draft")
        check("目的・背景だけの --draft は CREATED", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
        task_id = r.stdout.split("\t")[1]
        written = shown(wt1, task_id)
        check(
            "show は status: draft で枠の7節を出す",
            "\nstatus: draft\n" in written and all(f"\n{h}" in written for h in taskfile.SECTION_HEADINGS),
            written,
        )

        r = run_task(wt1, "status")
        row = next((l.split("\t") for l in r.stdout.splitlines() if l.startswith(f"{task_id}\t")), [])
        check("status の行は draft・DRAFT", row[1:2] == ["draft"] and row[5:6] == ["DRAFT"], r.stdout)
        check("counts に draft=1、ready に数えない", "\tdraft=1" in r.stdout and "\nready\t0\n" in r.stdout, r.stdout)
        r = run_task(wt1, "status", "--check")
        check("status --check は OK", r.returncode == 0 and r.stdout.strip() == "OK", r.stdout + r.stderr)
        ready = subprocess.run(["bd", "ready", "--json"], cwd=wt1, capture_output=True, text=True)
        check("bd ready に出ない", ready.returncode == 0 and beads.to_bd_id(task_id) not in ready.stdout, ready.stdout + ready.stderr)

        r = run_task(wt1, "claim", task_id)
        check("claim は NOT_READY DRAFT", r.returncode == 4 and r.stdout.strip() == f"NOT_READY\t{task_id}\tDRAFT", r.stdout + r.stderr)

        before = issue_count(wt1)
        for label, extra, body in (
            ("--draft --hold", ("--draft", "--hold"), DRAFT_BODY),
            ("--draft --direct", ("--draft", "--direct"), DRAFT_BODY),
            ("目的・背景が空の --draft", ("--draft",), "## 目的・背景\n\nなし\n"),
            ("枠の順でない --draft", ("--draft",), "## 完了条件\n\nx\n\n## 目的・背景\n\nx\n"),
        ):
            r = new_draft(*extra, body=body)
            check(f"{label} は終了コード2", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)
        check("拒んだ登録は課題を作らない", issue_count(wt1) == before)


def test_draft_dropped() -> None:
    say("task.py done --dropped（ドラフトを着手せずにその場で閉じる）")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp)
        r = run_task(wt1, "new", "--summary", "却下するドラフト", "--difficulty", "haiku", "--loopable", "Y", "--draft",
                     "--body-file", "-", stdin=DRAFT_BODY)
        task_id = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""
        check("ドラフトを積む", task_id != "", r.stdout + r.stderr)

        r = run_task(wt1, "done", task_id, "--result-file", "-", stdin="やる\n")
        check("--dropped なしの done は NOT_READY DRAFT", r.returncode == 4
              and r.stdout.strip() == f"NOT_READY\t{task_id}\tDRAFT", r.stdout + r.stderr)

        r = run_task(wt1, "done", task_id, "--dropped", "--result-file", "-", stdin="見送る: 別のタスクで足りる\n")
        issue = issue_json(wt1, task_id)
        check("--dropped はその場で閉じて DONE", r.returncode == 0
              and r.stdout.strip() == f"DONE\t{task_id}\tbeads:{beads.to_bd_id(task_id)}\tドラフトを閉じた"
              and issue.get("status") == "closed" and beads.CANCELLED_LABEL in (issue.get("labels") or []),
              r.stdout + r.stderr + str(issue))
        r = run_task(wt1, "status", "--all")
        check("status --all で dropped", any(l.startswith(f"{task_id}\tdropped\t") for l in r.stdout.splitlines()), r.stdout)
        check("show に ## 結果 の却下の理由", "## 結果\n\n見送る: 別のタスクで足りる" in shown(wt1, task_id))


def candidate_body(color: str, viewpoint: str = "道案内") -> str:
    return f"## 目的・背景\n\n- 出どころ: 振り返り: T-001\n- 観点: {color} {viewpoint}\n\n## 介入\n\n{INTERVENTION}\n"


def test_draft_candidate() -> None:
    say("task.py new --draft --candidate（黄1件は CANDIDATE に留め、同じ兆候の2件目で DRAFT へ上げる）")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp)

        def new(*extra: str, body: str) -> subprocess.CompletedProcess:
            return run_task(
                wt1, "new", "--summary", "候補", "--difficulty", "sonnet", "--loopable", "Y", *extra,
                "--body-file", "-", stdin=body,
            )

        def status_of(task_id: str) -> tuple[list[str], str]:
            r = run_task(wt1, "status", "--all")
            row = next((l.split("\t") for l in r.stdout.splitlines() if l.startswith(f"{task_id}\t")), [])
            line = next((l for l in r.stdout.splitlines() if l.startswith("CANDIDATE\t")), "")
            return row, line

        r = new("--draft", "--candidate", "同じファイルの直し直し", "`./docs/a.md:12`", body=candidate_body("黄"))
        check("黄の --candidate は CREATED", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
        first = r.stdout.split("\t")[1]
        meta = metadata(wt1, first)
        check("場所はファイル単位で控える", meta.get(beads.CANDIDATE_PLACE_KEY) == "docs/a.md"
              and meta.get(beads.CANDIDATE_SIGNAL_KEY) == "同じファイルの直し直し", str(meta))
        row, line = status_of(first)
        check("候補は本表に出ず CANDIDATE の行に出る", row == [] and line == f"CANDIDATE\t1\t{first}", line)
        r = run_task(wt1, "status")
        check("候補は draft= に数えない", "\tdraft=0" in r.stdout, r.stdout)

        r = new("--draft", "--candidate", "同じファイルの直し直し", "docs/b.md", body=candidate_body("黄"))
        other = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""
        check("場所が違えば別の候補", other != "" and status_of(other)[1] == f"CANDIDATE\t2\t{first},{other}", r.stdout + r.stderr)

        r = new("--draft", body=candidate_body("赤"))
        red = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""
        check("赤は従来どおり DRAFT", red != "" and status_of(red)[0][5:6] == ["DRAFT"], r.stdout + r.stderr)

        before = issue_count(wt1)
        for label, extra, body in (
            ("--draft の無い --candidate", ("--hold", "--candidate", "x", "y"), candidate_body("黄")),
            ("赤の --candidate", ("--draft", "--candidate", "x", "y"), candidate_body("赤")),
            ("色の無い --candidate", ("--draft", "--candidate", "x", "y"), candidate_body("")),
            ("空の場所の --candidate", ("--draft", "--candidate", "x", "``"), candidate_body("黄")),
        ):
            r = new(*extra, body=body)
            check(f"{label} は終了コード2", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)
        check("拒んだ登録は課題を作らない", issue_count(wt1) == before)

        r = new("--draft", "--candidate", "同じファイルの直し直し", "docs/a.md×3", body=candidate_body("黄"))
        check(
            "同じ兆候の2件目は PROMOTED で、課題を作らない",
            r.returncode == 0
            and r.stdout.splitlines()[0] == f"PROMOTED\t{first}\t道案内\t同じファイルの直し直し\tdocs/a.md"
            and issue_count(wt1) == before,
            r.stdout + r.stderr,
        )
        row, line = status_of(first)
        check("上がった候補は DRAFT の行に出て CANDIDATE から外れる",
              row[5:6] == ["DRAFT"] and line == f"CANDIDATE\t1\t{other}", row and line)
        check("2件目の本文は ## 再発 の comment", "## 再発" in "".join(
            c.get("text") or "" for c in beads.comments(wt1, beads.to_bd_id(first))))

        r = new("--draft", "--candidate", "同じファイルの直し直し", "docs/a.md", body=candidate_body("黄", "機械の検査"))
        check("観点が違えば上げずに別の候補", r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)


def test_part_direct_run_refused() -> None:
    say("selftest_t_*.py・selftest_b_*.py を直に打ったときの案内（部品を glob で全部引く）")
    here = os.path.dirname(os.path.abspath(__file__))
    for prefix, entry in (("selftest_t_", "selftest_task.py"), ("selftest_b_", "selftest_beads.py")):
        parts = [
            p for p in sorted(glob.glob(os.path.join(here, f"{prefix}*.py")))
            if "refuse_direct_run(" in open(p, encoding="utf-8").read()
        ]
        check(f"{prefix}* の部品が1本以上ある", len(parts) > 0)
        for part in parts:
            r = subprocess.run([sys.executable, part], capture_output=True, text=True)
            check(
                f"{os.path.basename(part)} は {entry} を案内して 2 で終わる",
                r.returncode == 2 and entry in r.stderr,
                detail=f"exit={r.returncode}",
            )


def test_loop_check() -> None:
    say("task.py loop-check: 今日の出来事から停止規則（件数の上限・hold 連続・直近の VERIFY_FAILED）を順に見る")
    from datetime import datetime, timedelta, timezone

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp)
        config = os.path.join(wt1, ".tw", "config.toml")
        with open(config, encoding="utf-8") as f:
            original = f.read()
        flow = ledger.flow_dir(ledger.ledger_root(cwd=wt1))
        now = datetime.now(timezone.utc)

        def seed(*events: tuple[str, str], days_ago: float = 0) -> None:
            """`(出来事, 結果 か "")` を、今から数秒ずつ前に時刻順で並べて台帳に書く。"""
            shutil.rmtree(flow, ignore_errors=True)
            os.makedirs(flow)
            for i, (kind, result) in enumerate(events):
                at = (now - timedelta(days=days_ago, seconds=len(events) - i)).isoformat(timespec="seconds")
                row = {"t": at, "event": kind, "task": f"T-{100 + i}", "difficulty": "sonnet"}
                if result:
                    row["result"] = result
                with open(os.path.join(flow, f"{at[:7]}.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")

        def verdict(**kw) -> str:
            seed(*kw.pop("events"), **kw)
            r = run_task(wt1, "loop-check")
            return f"{r.returncode}|{r.stdout.splitlines()[0]}|{r.stdout.splitlines()[1] if r.stdout.count(chr(10)) > 1 else ''}"

        def head(**kw) -> str:
            return verdict(**kw).split("|")[1]

        shipped = lambda n: [("ship", "SHIPPED")] * n  # noqa: E731
        check("記録なしは CONTINUE と空の一覧", verdict(events=[]) == "0|CONTINUE|SHIPPED_TODAY\t0\t-", verdict(events=[]))
        check("今日の SHIPPED が上限-1 件なら CONTINUE", head(events=shipped(11)) == "CONTINUE")
        check("上限件で STOP max_ships と送った一覧", verdict(events=shipped(12)).startswith("0|STOP\tmax_ships\t12|SHIPPED_TODAY\t12\tT-100,T-101"),
              verdict(events=shipped(12)))
        check("昨日の SHIPPED は数えない", head(events=shipped(12), days_ago=2) == "CONTINUE")
        check("hold が連続 2 件なら CONTINUE", head(events=[("hold", "")] * 2) == "CONTINUE")
        check("hold が連続 3 件で STOP hold_streak", head(events=[("hold", "")] * 3) == "STOP\thold_streak\t3")
        check("SHIPPED を挟むと hold の連続は途切れる",
              head(events=[("hold", "")] * 2 + shipped(1) + [("hold", "")] * 2) == "CONTINUE")
        check("直近 3 件の ship に VERIFY_FAILED が 2 件で STOP verify_failed",
              head(events=[("ship", "VERIFY_FAILED"), ("ship", "SHIPPED"), ("ship", "VERIFY_FAILED")]) == "STOP\tverify_failed\t2")
        check("VERIFY_FAILED が直近 3 件の外なら CONTINUE",
              head(events=[("ship", "VERIFY_FAILED")] * 2 + shipped(3)) == "CONTINUE")
        three = [("hold", "")] * 3 + [("ship", "VERIFY_FAILED")] * 2
        check("複数当たれば max_ships が先", head(events=shipped(12) + three) == "STOP\tmax_ships\t12")
        check("複数当たれば hold_streak が verify_failed より先", head(events=[("ship", "VERIFY_FAILED")] * 2 + [("hold", "")] * 3) == "STOP\thold_streak\t3")

        write(config, original + 'loop_max_ships = "2"\n')
        check("設定 loop_max_ships = 2 で上限が変わる", head(events=shipped(2)) == "STOP\tmax_ships\t2")
        for bad in ('"0"', '"abc"'):
            write(config, original + f"loop_max_ships = {bad}\n")
            r = run_task(wt1, "loop-check")
            check(f"loop_max_ships = {bad} は INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID\t"),
                  r.stdout + r.stderr)
        write(config, original)
        rows = run_task(wt1, "config").stdout.splitlines()
        check("tw config は書かなければ loop_max_ships の既定 12 を default で出す", "loop_max_ships\t12\tdefault" in rows, repr(rows))


def test_subcommand_help_complete() -> None:
    import argparse

    import task

    say("task.py の全サブコマンドに help と description がある")
    action = next(a for a in task.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help for a in action._choices_actions}
    check("表のキーと実際のサブコマンドが同じ", set(task.SUBCOMMAND_HELP) == set(action.choices))
    for name, parser in action.choices.items():
        line = helps.get(name) or ""
        check(f"{name} の help が1行ある", bool(line.strip()) and "\n" not in line)
        check(f"{name} の description がある", bool((parser.description or "").strip()))


TESTS = (
    test_subcommand_help_complete,
    test_part_direct_run_refused,
    test_done_commits_since_claim,
    test_done_single_worktree,
    test_done_required_lines,
    test_done_replay_diff_notice,
    test_claim_and_release_single_worktree,
    test_hold,
    test_loop_check,
    test_edit_refuses_loopable_n_while_claimed,
    test_new_and_status_single_worktree,
    test_new_fixes,
    test_new_batch,
    test_kind_label,
    test_find,
    test_draft_register_and_refuse_claim,
    test_draft_dropped,
    test_draft_candidate,
    test_new_parallel_no_collision,
    test_new_avoids_history_ids,
    test_taskfile_body,
    test_taskfile_draft_viewpoint,
    test_taskfile_opus_and_viewpoint,
    test_taskfile_intervention,
)


if __name__ == "__main__":
    refuse_direct_run()
