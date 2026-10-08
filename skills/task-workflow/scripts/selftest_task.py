#!/usr/bin/env python3
"""`task.py`（1件1ファイル＋台帳の形）の自己テスト。

使い方: python3 selftest_task.py [テストの関数名 ...]（名前を渡すとそれだけを流す）

一時ディレクトリに git リポジトリと作業ツリー2本を作り、`task.py` を実際に
子プロセスで（並行するテストは同時に）起こして検証する。正典は
`docs/task-workflow-redesign.md`。落ちたら非0で終わる（`selftest.py` と同じ形）。
"""

from __future__ import annotations

import contextlib
import json
import re
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger  # noqa: E402
import legacy  # noqa: E402
import metrics  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402

# 利用者の値のままだと、一時リポジトリの台帳がその置き場に積もる。
os.environ.pop(ledger.STATE_DIR_ENV, None)

TASK_PY = os.path.join(HERE, "task.py")
BODY = task_body()
# 登録の既定の本文。`make_repo` が主ブランチに置く `shared.txt` を名指す。
PLANNED_BODY = task_body([("書く", "x")], ["shared.txt"])
# `make_repo` の既定（`.tw/config.toml`）のときの置き場。
TASK_REL = ".tw/task"
DRAFT_REL = ".tw/draft"

failures: list[str] = []
_local = threading.local()


def say(line: str = "") -> None:
    _local.lines.append(line)


def _run_one(test) -> list[str]:
    _local.lines = []
    try:
        test()
    except Exception as e:  # noqa: BLE001  1件の故障で残りのテストを止めない
        check(f"{test.__name__} が落ちずに終わる", False, repr(e))
    return _local.lines


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        say(f"  ok   {label}")
    else:
        say(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def write(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失敗: {r.stderr}")
    return r


def run_task(
    cwd: str, *args: str, stdin: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, TASK_PY, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        input=stdin,
        env={**os.environ, **env} if env is not None else None,
    )


def start_task(cwd: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, TASK_PY, *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


def _toml_value(value: str) -> str:
    """`` `cmd` `` か `なし` を TOML の文字列にする。"""
    inner = value[1:-1] if value.startswith("`") and value.endswith("`") else value
    return '"' + inner.replace("\\", "\\\\").replace('"', '\\"') + '"'


def make_repo(
    tmp: str,
    branch: str | None = "既定",
    verify: str | None = None,
    base: str = "main",
    config_filename: str | None = None,
    format_command: str | None = None,
    preship: str | None = None,
    section: bool = True,
) -> tuple[str, str, str]:
    """`(本体, 作業ツリー1, 作業ツリー2)`。本体だけが主ブランチを出す。

    設定は `.tw/config.toml` に書く。`verify`・`format_command`・`preship` は `` `cmd` `` か `なし` の形で渡し、
    `verify` を省略すると `verify = "なし"`。`branch` が `None` なら `branch` を書かない。`base` は主ブランチの
    名前——リモートを持たない足場なので `ledger.base_branch` の順3で決まる。
    `config_filename`（`CLAUDE.md`・`AGENTS.md`）を渡すと、代わりに旧い「## タスク運用」節をそのファイルに書く
    （互換の読み。値はそのまま行に書き、`verify` を省略すると行を書かない）。`section` が偽なら設定をどこにも書かない。
    `direction.md` は `.tw/config.toml` を書くときは `.tw/` に、そうでなければ `develop/` に置く。
    """
    main_path = os.path.join(tmp, "base")
    os.makedirs(main_path)
    git(main_path, "init", "-q", "-b", base)
    git(main_path, "config", "user.email", "test@example.com")
    git(main_path, "config", "user.name", "test")
    write(
        os.path.join(main_path, ".tw" if config_filename is None and section else "develop", "direction.md"),
        "# 未対応の指示メモ\n\n## ユーザーから\n\n## エージェントのドラフト\n",
    )
    write(os.path.join(main_path, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
    if config_filename is not None:
        config_md = "# x\n\n## タスク運用\n\n"
        if verify is not None:
            config_md += f"- 検証コマンド: {verify}\n"
        if format_command is not None:
            config_md += f"- 整形コマンド: {format_command}\n"
        if preship is not None:
            config_md += f"- 送る前の検証コマンド: {preship}\n"
        if branch is not None:
            config_md += f"- ブランチ: {branch}\n"
        write(os.path.join(main_path, config_filename), config_md if section else "# x\n")
    elif section:
        toml = f"verify = {_toml_value(verify or 'なし')}\n"
        if format_command is not None:
            toml += f"format = {_toml_value(format_command)}\n"
        if preship is not None:
            toml += f"verify_before_ship = {_toml_value(preship)}\n"
        if branch is not None:
            toml += f'branch = "{branch}"\n'
        write(os.path.join(main_path, ".tw", "config.toml"), toml)
    write(os.path.join(main_path, "shared.txt"), "line1\n")
    git(main_path, "add", "-A")
    git(main_path, "commit", "-q", "-m", "init")

    wt1 = os.path.join(tmp, "wt1")
    wt2 = os.path.join(tmp, "wt2")
    git(main_path, "worktree", "add", "-q", "-b", "wt1-branch", wt1, base)
    git(main_path, "worktree", "add", "-q", "-b", "wt2-branch", wt2, base)
    return main_path, wt1, wt2


def body_file(dirpath: str, name: str = "body.md") -> str:
    return write(os.path.join(dirpath, name), PLANNED_BODY)


def task_rel(repo: str) -> str:
    """`repo` の置き場（`.tw/config.toml` があれば `TASK_REL`、無ければ互換の `develop/task`）。"""
    return TASK_REL if os.path.exists(os.path.join(repo, ".tw", "config.toml")) else "develop/task"


def commit_task(main_path: str, task: taskfile.Task) -> None:
    write(os.path.join(main_path, task_rel(main_path), f"{task.id}.md"), taskfile.render(task))
    git(main_path, "add", "-A")
    git(main_path, "commit", "-q", "-m", f"{task.id}を足す")


# --- taskfile.py ----------------------------------------------------------


def test_taskfile_parse() -> None:
    say("taskfile.parse / render / validate_new_body")
    ok_text = (
        "---\nid: T-001\nsummary: 例\nstatus: todo\ndifficulty: sonnet\nloopable: Y\n"
        "dependencies: []\n---\n本文\n"
    )
    task, err = taskfile.parse(ok_text)
    check("正しい6行は読める", err is None and task is not None and task.id == "T-001", str(err))
    check("本文がそのまま残る", task is not None and task.body == "本文\n")

    task, err = taskfile.parse(ok_text.replace("dependencies: []", "dependencies: [T-001, T-002]"))
    check(
        "dependenciesは', '区切りで読める",
        err is None and task is not None and task.dependencies == ("T-001", "T-002"),
        str(err),
    )

    _, err = taskfile.parse(ok_text.replace("dependencies: []", "dependencies: [T-001,T-002]"))
    check("区切りが','だけだとINVALID", err is not None, str(err))

    check("全角括弧を省いた「決まっていること」が枠の見出しに当たる",
          taskfile.section_heading("決まっていること") in taskfile.SECTION_HEADINGS)

    _, err = taskfile.parse(ok_text.replace("loopable: Y", "loopable: y"))
    check("loopableの小文字はINVALID", err is not None, str(err))

    _, err = taskfile.parse(ok_text.replace("status: todo", "status: doing"))
    check("着手中(doing)はファイルに書けないのでINVALID", err is not None, str(err))

    _, err = taskfile.parse(ok_text.replace("\n", "\r\n"))
    check("CRLFはINVALID", err is not None, str(err))

    task, err = taskfile.parse(ok_text.replace("summary: 例", "summary: `a: b` # c [d]"))
    check(
        "summaryは記号を含んでもそのまま",
        err is None and task is not None and task.summary == "`a: b` # c [d]",
        str(err),
    )

    task, err = taskfile.parse(ok_text.replace("summary: 例", "summary:   前後に空白   "))
    check(
        "summaryの前後の空白は落ちる", err is None and task is not None and task.summary == "前後に空白", str(err)
    )
    assert task is not None
    task2, err2 = taskfile.parse(taskfile.render(task))
    check("render→parseで往復する", err2 is None and task2 == task)

    direct_text = ok_text.replace("loopable: Y\n", "loopable: Y\ndirect: Y\n")
    task, err = taskfile.parse(direct_text)
    check("loopable の次の direct: Y は近道の印として読める", err is None and task is not None and task.direct == "Y"
          and task.body == "本文\n", str(err))
    check("direct: Y を持つタスクは render→parse で往復する",
          task is not None and taskfile.parse(taskfile.render(task)) == (task, None))
    task, _ = taskfile.parse(ok_text)
    check("direct 行が無ければ印なし（N）で、render は行を書かない",
          task is not None and task.direct == "N" and "direct:" not in taskfile.render(task))
    _, err = taskfile.parse(ok_text.replace("loopable: Y\n", "loopable: Y\ndirect: N\n"))
    check("direct: N はINVALID（印が無ければ行を置かない）", err is not None, str(err))
    _, err = taskfile.parse(ok_text.replace("dependencies: []\n", "dependencies: []\ndirect: Y\n"))
    check("dependencies の後ろの direct 行はINVALID", err is not None, str(err))

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


# --- task.py: 単独の作業ツリー ---------------------------------------------


def test_new_and_status_single_worktree() -> None:
    say("task.py new/status（単独の作業ツリー）")
    with tempfile.TemporaryDirectory() as tmp:
        _main_path, wt1, _wt2 = make_repo(tmp)

        r = run_task(
            wt1, "new", "--summary", "1件目", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", body_file(wt1)
        )
        check("CREATEDで返る", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
        task_id = r.stdout.split("\t")[1]
        check(
            ".tw/task/T-xxx.md ができる",
            os.path.exists(os.path.join(wt1, task_rel(wt1), f"{task_id}.md")),
        )
        with open(os.path.join(wt1, task_rel(wt1), f"{task_id}.md"), encoding="utf-8") as f:
            written = f.read()
        check(
            "閉じる --- の次は空行1行で、末尾は改行1つ（整形ツールの検査に合う）",
            "\n---\n\n## " in written and "\n---\n\n\n" not in written and written.endswith("\n") and not written.endswith("\n\n"),
            written,
        )

        r = run_task(wt1, "status")
        check(
            "statusにlocalの印が出る",
            any(l.startswith(f"{task_id}\t") and "\tlocal\t" in l for l in r.stdout.splitlines()),
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


def test_new_missing_and_legacy() -> None:
    say("task.py: MISSING/LEGACY の判定")
    with tempfile.TemporaryDirectory() as tmp:
        empty_repo = os.path.join(tmp, "empty")
        os.makedirs(empty_repo)
        git(empty_repo, "init", "-q", "-b", "main")
        git(empty_repo, "config", "user.email", "test@example.com")
        git(empty_repo, "config", "user.name", "test")
        write(os.path.join(empty_repo, ".gitkeep"), "")
        git(empty_repo, "add", "-A")
        git(empty_repo, "commit", "-q", "-m", "init")
        r = run_task(empty_repo, "status")
        check("develop/direction.mdも無ければMISSING", r.returncode == 6 and r.stdout.strip() == "MISSING", r.stdout)

        write(os.path.join(empty_repo, "develop", "tasks.json"), "[]\n")
        git(empty_repo, "add", "-A")
        git(empty_repo, "commit", "-q", "-m", "legacy")
        r = run_task(empty_repo, "status")
        check("tasks.jsonがあればLEGACY", r.returncode == 5 and r.stdout.startswith("LEGACY\t"), r.stdout)


# --- legacy.py / task.py migrate（5.9・10章） -------------------------------
#
# **架空のタスクだけを使う**（実際のプロジェクトの tasks.json をフィクスチャにしない）。


def _make_legacy_repo(tmp: str, tasks: list[dict] | None = None, progress: str | None = None) -> str:
    """旧形式（`develop/tasks.json` あり）の一時リポジトリを1つ作る。`develop/direction.md` は
    実在のプロジェクトと同じく最初から置く（無いと移行後に `NEW` ではなく `MISSING` になる）。
    """
    repo = os.path.join(tmp, "legacy")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    write(
        os.path.join(repo, "develop", "direction.md"),
        "# 未対応の指示メモ\n\n## ユーザーから\n\n## エージェントのドラフト\n",
    )
    write(os.path.join(repo, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
    if tasks is not None:
        write(os.path.join(repo, "develop", "tasks.json"), json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")
    if progress is not None:
        write(os.path.join(repo, "develop", "progress.md"), progress)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


LEGACY_TASKS = [
    {
        "id": "T-001",
        "summary": "架空1件目",
        "task": "## やること\n\n架空のタスク本文1行目\n2行目\n",
        "status": "todo",
        "difficulty": "sonnet",
        "loopable": "Y",
        "dependencies": [],
        "passes": False,
        "evidence": "",
    },
    {
        "id": "T-002",
        "summary": "架空2件目（依存あり・完了）",
        "task": "架空のタスク本文2\n",
        "status": "done",
        "difficulty": "haiku",
        "loopable": "N",
        "dependencies": ["T-001"],
        "passes": True,
        "evidence": "bun test: 3 pass",
    },
    {
        "id": "T-003",
        "summary": "架空3件目（着手しない判断で閉じた）",
        "task": "架空のタスク本文3\n",
        "status": "done",
        "difficulty": "opus",
        "loopable": "Y",
        "dependencies": [],
        "passes": False,
        "evidence": "",
    },
]

LEGACY_PROGRESS = (
    "# 現在の状態\n\n(架空の前置きの説明文)\n\n"
    "## 完了したこと（このセッション）\n\n"
    "### 2026-01-02 架空のこと2\n\n本文2\n\n"
    "### 2026-01-01 架空のこと1\n\n本文1\n\n"
    "## 未解決\n\n- 架空の未解決事項\n\n"
    "## 注意\n\n- 架空の注意1\n- 架空の注意2\n"
)

LEGACY_PROGRESS_PREAMBLE_ONLY = (
    "# 現在の状態\n\n(架空の前置き文だけが残るケース)\n\n"
    "## 完了したこと（このセッション）\n\n"
    "### 2026-01-01 架空のこと\n\n本文\n\n"
    "## 未解決\n\n## 注意\n"
)


def test_legacy_convert_task() -> None:
    say("legacy.convert_task: 架空タスクの変換（10.1の表）")
    task, err = legacy.convert_task(
        {
            "id": "T-010",
            "summary": "todo",
            "task": "本文\n",
            "status": "todo",
            "difficulty": "sonnet",
            "dependencies": [],
            "passes": False,
            "evidence": "",
        }
    )
    check("loopableが無ければYで補う", err is None and task is not None and task.loopable == "Y", str(err))

    task, err = legacy.convert_task(
        {
            "id": "T-011",
            "summary": "done true",
            "task": "本文\n",
            "status": "done",
            "difficulty": "haiku",
            "loopable": "N",
            "dependencies": ["T-010"],
            "passes": True,
            "evidence": "証拠",
        }
    )
    check(
        "done+passes:trueはdoneで結果節が付く",
        err is None
        and task is not None
        and task.status == "done"
        and task.dependencies == ("T-010",)
        and task.body.endswith("## 結果\n\n証拠\n"),
        str(err),
    )

    task, err = legacy.convert_task(
        {
            "id": "T-012",
            "summary": "done false",
            "task": "本文\n",
            "status": "done",
            "difficulty": "opus",
            "loopable": "Y",
            "dependencies": [],
            "passes": False,
            "evidence": "",
        }
    )
    check(
        "done+passes:falseはdroppedで結果節は付かない（evidenceが空）",
        err is None and task is not None and task.status == "dropped" and "## 結果" not in task.body,
        str(err),
    )

    task, err = legacy.convert_task(
        {
            "id": "T-013",
            "summary": None,
            "task": "先頭行がsummaryになる\n2行目\n",
            "status": "todo",
            "difficulty": "sonnet",
            "dependencies": [],
            "passes": False,
            "evidence": "",
        }
    )
    check(
        "summaryが無ければtaskの先頭行を使う",
        err is None and task is not None and task.summary == "先頭行がsummaryになる",
        str(err),
    )

    _, err = legacy.convert_task(
        {
            "id": "T-014",
            "summary": "壊れる",
            "task": "本文\n## 結果\n既にある\n",
            "status": "done",
            "difficulty": "sonnet",
            "dependencies": [],
            "passes": True,
            "evidence": "証拠",
        }
    )
    check("本文に既に'## 結果'があればINVALID", err is not None, str(err))

    _, err = legacy.convert_task(
        {
            "id": "T-015",
            "summary": "不明",
            "task": "x\n",
            "status": "doing",
            "difficulty": "sonnet",
            "dependencies": [],
            "passes": False,
        }
    )
    check("statusがdoingならINVALID（migrateは呼び出し側で先に止める）", err is not None, str(err))

    task, conv_err = legacy.convert_task(LEGACY_TASKS[1])
    check("架空タスクのconvert_taskが通る", conv_err is None and task is not None, str(conv_err))
    assert task is not None
    round_tripped, render_err = taskfile.parse(taskfile.render(task))
    check("convert_task→render→parseで往復する", render_err is None and round_tripped == task, str(render_err))


def test_migrate_dry_run_then_real() -> None:
    say("task.py migrate: 架空のtasks.json/progress.mdの変換の往復")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_legacy_repo(tmp, tasks=LEGACY_TASKS, progress=LEGACY_PROGRESS)

        r = run_task(repo, "migrate", "--dry-run")
        check("dry-runは終了コード0", r.returncode == 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        check("WRITEが3件出る", sum(1 for l in lines if l.startswith("WRITE\t")) == 3, r.stdout)
        check("MOVEに2小節と出る", any(l.startswith("MOVE\t") and "2小節" in l for l in lines), r.stdout)
        check(
            "LEFTOVERに未解決1・注意2と出る",
            any(l.startswith("LEFTOVER\t") and "未解決 1 / 注意 2" in l for l in lines),
            r.stdout,
        )
        check(
            "前置き文を残したLEFTOVER行も出る",
            "LEFTOVER\tdevelop/progress.md\t前置き文を残した" in lines,
            r.stdout,
        )
        check("REMOVEにtasks.jsonが出る", "REMOVE\tdevelop/tasks.json" in lines, r.stdout)
        check("末尾はPLAN\\t3", lines[-1] == "PLAN\t3", r.stdout)
        check("dry-runはファイルを作らない", not os.path.isdir(os.path.join(repo, "develop", "task")))
        check("dry-runはtasks.jsonを消さない", os.path.exists(os.path.join(repo, "develop", "tasks.json")))

        r = run_task(repo, "migrate")
        check("実行は終了コード0", r.returncode == 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        check("末尾はMIGRATED\\t3", lines[-1] == "MIGRATED\t3", r.stdout)
        check("tasks.jsonがファイルから消える", not os.path.exists(os.path.join(repo, "develop", "tasks.json")))

        task_dir = os.path.join(repo, "develop", "task")
        t1, err1 = taskfile.read_task_file(os.path.join(task_dir, "T-001.md"))
        check("todoはtodoのまま", err1 is None and t1 is not None and t1.status == "todo", str(err1))

        t2, err2 = taskfile.read_task_file(os.path.join(task_dir, "T-002.md"))
        check(
            "done+passes:trueはdoneでevidenceが結果節に入る",
            err2 is None and t2 is not None and t2.status == "done" and "bun test: 3 pass" in t2.body,
            str(err2),
        )
        check("dependenciesもそのまま写る", t2 is not None and t2.dependencies == ("T-001",))
        check("loopableもそのまま写る", t2 is not None and t2.loopable == "N")

        t3, err3 = taskfile.read_task_file(os.path.join(task_dir, "T-003.md"))
        check(
            "done+passes:falseはdroppedになる",
            err3 is None and t3 is not None and t3.status == "dropped" and "## 結果" not in t3.body,
            str(err3),
        )

        progress_path = os.path.join(repo, "develop", "progress.md")
        with open(progress_path, encoding="utf-8") as f:
            leftover = f.read()
        check("未解決が残る", "架空の未解決事項" in leftover, leftover)
        check("注意が残る", "架空の注意1" in leftover and "架空の注意2" in leftover, leftover)
        check("完了したこと節の本文は残らない（履歴へ移した）", "架空のこと1" not in leftover and "架空のこと2" not in leftover, leftover)
        check("前置き文は消さずに残る（データを失わない）", "架空の前置きの説明文" in leftover, leftover)
        check("移行の残りの注記が先頭に付く", leftover.startswith("移行の残り。"), leftover)

        history_progress_path = os.path.join(repo, "docs", "history", "progress.md")
        with open(history_progress_path, encoding="utf-8") as f:
            history_text = f.read()
        check(
            "完了したことの小節が履歴へ移る",
            "架空のこと1" in history_text and "架空のこと2" in history_text,
            history_text,
        )

        staged = git(repo, "diff", "--cached", "--name-only").stdout
        check("tasks.jsonの削除がstageされる", "develop/tasks.json" in staged, staged)
        check("develop/task/以下がstageされる", "develop/task/T-001.md" in staged, staged)
        check("progress.mdの更新がstageされる", "develop/progress.md" in staged, staged)
        check("history/progress.mdの追記もstageされる", "docs/history/progress.md" in staged, staged)
        check(
            "migrateはコミットしない（initの1件のまま）",
            git(repo, "log", "--oneline").stdout.strip().count("\n") == 0,
        )

        r = run_task(repo, "status", "--all")
        check("migrate後はstatusが読める（NEW形式になる）", r.returncode == 0, r.stdout + r.stderr)
        ids_out = {l.split("\t")[0] for l in r.stdout.splitlines() if l.startswith("T-0")}
        check("3件とも一覧に出る", ids_out == {"T-001", "T-002", "T-003"}, r.stdout)


def test_migrate_keeps_preamble_when_sections_empty() -> None:
    say("task.py migrate: 未解決・注意が空でも前置き文があればprogress.mdを消さない")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_legacy_repo(tmp, tasks=LEGACY_TASKS, progress=LEGACY_PROGRESS_PREAMBLE_ONLY)

        r = run_task(repo, "migrate")
        check("実行は終了コード0", r.returncode == 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        check(
            "LEFTOVERに未解決0・注意0と出る",
            any(l.startswith("LEFTOVER\t") and "未解決 0 / 注意 0" in l for l in lines),
            r.stdout,
        )
        check(
            "前置き文を残したLEFTOVER行が出る",
            "LEFTOVER\tdevelop/progress.md\t前置き文を残した" in lines,
            r.stdout,
        )
        check("REMOVEにprogress.mdは出ない（消さない）", "REMOVE\tdevelop/progress.md" not in lines, r.stdout)

        progress_path = os.path.join(repo, "develop", "progress.md")
        check("progress.mdは消えない", os.path.exists(progress_path))
        with open(progress_path, encoding="utf-8") as f:
            leftover = f.read()
        check("前置き文がそのまま残る", "架空の前置き文だけが残るケース" in leftover, leftover)
        check("完了したことの本文は残らない（履歴へ移した）", "架空のこと" not in leftover, leftover)
        check("移行の残りの注記が先頭に付く", leftover.startswith("移行の残り。"), leftover)


def test_migrate_stops_on_doing() -> None:
    say("task.py migrate: doingが残っていればNOT_READYで止まる")
    with tempfile.TemporaryDirectory() as tmp:
        tasks = [dict(LEGACY_TASKS[0], status="doing")]
        repo = _make_legacy_repo(tmp, tasks=tasks)
        r = run_task(repo, "migrate", "--dry-run")
        check(
            "NOT_READYで終了コード4",
            r.returncode == 4 and r.stdout.strip() == "NOT_READY\tT-001\tdoing",
            r.stdout + r.stderr,
        )
        check("develop/task/は作られない", not os.path.isdir(os.path.join(repo, "develop", "task")))


def test_migrate_dirty_worktree_stops() -> None:
    say("task.py migrate: 作業ツリーが汚れていればDIRTYで止まる")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_legacy_repo(tmp, tasks=LEGACY_TASKS)
        write(os.path.join(repo, "dirty.txt"), "x")
        r = run_task(repo, "migrate")
        check("DIRTYで終了コード4", r.returncode == 4 and r.stdout.strip() == "DIRTY", r.stdout + r.stderr)


def test_migrate_nothing_when_no_tasks_json() -> None:
    say("task.py migrate: develop/tasks.jsonが無ければNOTHING")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_legacy_repo(tmp, tasks=None)
        r = run_task(repo, "migrate", "--dry-run")
        check(
            "NOTHINGで終了コード0",
            r.returncode == 0 and r.stdout.startswith("NOTHING\t"),
            r.stdout + r.stderr,
        )


# --- 3.4 節が要求する読み取りの見本を、実際の CLI 経由でも確かめる -----------


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
        check(
            "2本ともそれぞれの作業ツリーにファイルができる",
            os.path.exists(os.path.join(wt1, task_rel(wt1), f"{id1}.md"))
            and os.path.exists(os.path.join(wt2, task_rel(wt2), f"{id2}.md")),
        )


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


# --- taskfile.py: `## 結果` 節（5.7） ---------------------------------------


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


# --- task.py: done（5.7） ----------------------------------------------------


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
            "DONEで返りstaged",
            r.returncode == 0 and r.stdout.strip() == f"DONE\tT-100\t{TASK_REL}/T-100.md\tstaged",
            r.stdout,
        )

        staged = git(wt1, "diff", "--cached", "--name-only").stdout
        check(".tw/task/T-100.mdがstageされる", f"{TASK_REL}/T-100.md" in staged, staged)

        task_path = os.path.join(wt1, task_rel(wt1), "T-100.md")
        task, err = taskfile.read_task_file(task_path)
        check("statusがdoneになる", err is None and task is not None and task.status == "done", str(err))
        check(
            "結果節が入る",
            task is not None and "bun run check: 5 pass" in task.body,
            task.body if task else "",
        )

        r = run_task(wt1, "done", "T-100", "--dropped", "--result-file", result_path)
        check("--droppedもDONEで返る", r.returncode == 0, r.stdout + r.stderr)
        task2, _err2 = taskfile.read_task_file(task_path)
        check("--droppedでdroppedになる", task2 is not None and task2.status == "dropped")


def test_body_frame_check() -> None:
    say("task.py done・status --check: 本文の枠を検査する")
    bad = BODY.replace("## 注意\n\n## 参考情報\n", "## 参考情報\n\n## 注意\n")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "枠が崩れた", "todo", "sonnet", "Y", (), bad))
        commit_task(main_path, taskfile.Task("T-101", "枠が崩れた完了済み", "done", "sonnet", "Y", (), bad))
        r = run_task(main_path, "status", "--check")
        check(
            "status --check は todo の崩れだけを invalid に数える（done は見ない）",
            r.returncode == 3 and "T-100:" in r.stdout and "T-101" not in r.stdout,
            r.stdout,
        )

        run_task(wt1, "claim", "T-100")
        result_path = write(os.path.join(tmp, "result.md"), "結果\n")
        task_path = os.path.join(wt1, task_rel(wt1), "T-100.md")
        with open(task_path, encoding="utf-8") as f:
            before = f.read()
        r = run_task(wt1, "done", "T-100", "--result-file", result_path)
        with open(task_path, encoding="utf-8") as f:
            after = f.read()
        check("done は枠の違う本文を INVALID（終了コード3）で拒む", r.returncode == 3 and r.stdout.startswith("INVALID\t"), r.stdout)
        check("拒んだときファイルを書き換えない", before == after)


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
            and lines[0] == f"DONE\tT-100\t{TASK_REL}/T-100.md\tstaged"
            and lines[1:] == [f"COMMITS_SINCE_CLAIM\tT-100\t{sha}"],
            r.stdout,
        )

        run_task(wt2, "claim", "T-101")
        root = ledger.ledger_root(cwd=wt2)
        owner_path = os.path.join(ledger.claim_dir(root, "T-101"), "owner")
        with open(owner_path, encoding="utf-8") as f:
            kept = [line for line in f.read().splitlines() if not line.startswith("head=")]
        write(owner_path, "\n".join(kept) + "\n")
        write(os.path.join(wt2, "illicit2.txt"), "x\n")
        git(wt2, "add", "-A")
        git(wt2, "commit", "-q", "-m", "illicit2")
        result_path2 = write(os.path.join(wt2, "result2.md"), "bun run check: 1 pass\n")
        r2 = run_task(wt2, "done", "T-101", "--result-file", result_path2)
        check(
            "控え（head=）の無い印はコミットがあっても落ちず、知らせない",
            r2.returncode == 0 and r2.stdout.strip() == f"DONE\tT-101\t{TASK_REL}/T-101.md\tstaged",
            r2.stdout,
        )


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
            "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin",
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
        return subprocess.run(["sh", "-c", hook], input=payload, capture_output=True, text=True, env={"PATH": path})

    r = run_hook(f'#!/bin/sh\nexec {sys.executable} {TASK_PY} "$@"\n')
    check(f"hook の行は tw {subcommand} を呼んで拒む", r.returncode == 0 and refused in r.stdout, r.stdout + r.stderr)
    r = run_hook(None)
    check(f"tw が PATH に無くても {subcommand} の hook は何も出さず0で通す",
          r.returncode == 0 and r.stdout == "", r.stdout + r.stderr)
    r = run_hook(f"#!/bin/sh\nexec {sys.executable} -c 'raise RuntimeError(\"x\")'\n")
    check(f"tw {subcommand} が例外で落ちても hook は何も出さず0で通す",
          r.returncode == 0 and r.stdout == "", r.stdout + r.stderr)


def run_handback_guard(tmp: str, where: str, event: str = "SubagentStop", tool: str | None = None) -> dict | None:
    """`tw handback-guard` を git の外（`tmp`）から打つ。拒んだら出した JSON、通したら `None`。"""
    fields: dict = {"hook_event_name": event, "cwd": where}
    if tool is not None:
        fields["tool_name"] = tool
    r = run_task(tmp, "handback-guard", stdin=json.dumps(fields))
    if r.returncode != 0:
        raise RuntimeError(f"handback-guard が {r.returncode} で終わった: {r.stderr}")
    return json.loads(r.stdout) if r.stdout else None


def _block_reason(out: dict | None) -> str:
    return str(out.get("reason", "")) if out is not None and out.get("decision") == "block" else ""


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
        check("develop/direction.md が無く検証コマンドの行だけがある作業先では、tw verify が MISSING でも最後の段の返却を通す",
              r.returncode == 6 and r.stdout.strip() == "MISSING" and run_handback_guard(tmp, wt2) is None,
              r.stdout + r.stderr)


def test_worktree_state_dir() -> None:
    say("ledger.py .tw/local/: 作業ツリーごとの控えとログを .tw/local/ に置き、旧い置き場の控えも読んで移す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, verify="`true`", config_filename="CLAUDE.md")
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
        shutil.rmtree(tw2)
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
        commit_task(main_path, taskfile.Task("T-100", "local/ だけを外す .tw/", "todo", "sonnet", "Y", (), BODY))
        git(wt1, "merge", "-q", "--ff-only", "main")
        run_task(wt1, "claim", "T-100")
        r = run_task(wt1, "verify")
        with open(os.path.join(wt1, ".tw", ".gitignore"), encoding="utf-8") as f:
            ignore = f.read()
        check(".tw/.gitignore が local/ の作業ツリーで、控えとログが git status に出ず .tw/.gitignore は変わらない",
              ignore == "local/\n" and git(wt1, "status", "--porcelain").stdout == "",
              r.stdout + git(wt1, "status", "--porcelain").stdout)


@contextlib.contextmanager
def readonly_git(main_path: str):
    """本体の `.git` を丸ごと書けなくする（`chmod -R a-w` と同じ）。抜けるときに書けるよう戻す。"""
    common = os.path.join(main_path, ".git")
    _chmod_tree(common, writable=False)
    try:
        yield
    finally:
        _chmod_tree(common, writable=True)


def _chmod_tree(top: str, writable: bool) -> None:
    for d, _dirs, files in os.walk(top):
        for p in [d, *(os.path.join(d, f) for f in files)]:
            mode = os.lstat(p).st_mode
            if not stat.S_ISLNK(mode):
                os.chmod(p, mode | stat.S_IWUSR if writable else mode & ~0o222)


def snapshot(*paths: str) -> dict[str, bytes]:
    """`paths` の下のファイル（`.git` の中は除く）のパスと中身。打つ前後で何も変わらないことを見る。"""
    out: dict[str, bytes] = {}
    for top in paths:
        for d, dirs, files in os.walk(top):
            dirs[:] = [x for x in dirs if x != ".git"]
            for f in files:
                p = os.path.join(d, f)
                if f != ".git":
                    with open(p, "rb") as fh:
                        out[p] = fh.read()
    return out


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
        roots = os.listdir(state) if os.path.isdir(state) else []
        check("枝を切る claim は印を立てる前に GIT_READ_ONLY（終了コード11）で止まる",
              r.returncode == 11 and r.stdout.startswith("GIT_READ_ONLY\t")
              and not any(os.path.isdir(ledger.claim_dir(os.path.join(state, x), "T-100")) for x in roots)
              and not os.path.exists(os.path.join(wt1, ".tw", "local", "task-open-claims"))
              and git(wt1, "rev-parse", "--abbrev-ref", "HEAD").stdout == branch, r.stdout + r.stderr)


def test_readonly_git_stops_writers() -> None:
    say("task.py: .git が読み取り専用なら done・ship・migrate は GIT_READ_ONLY で止まり、何も変えない")
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

        before = unchanged()
        with readonly_git(main_path):
            r = run_task(wt1, "done", "T-100", "--result-file", result_path, env=env)
        check("done は GIT_READ_ONLY（終了コード11）で、タスクファイル・index・台帳・.tw/ を変えない",
              r.returncode == 11 and r.stdout.startswith("GIT_READ_ONLY\t") and unchanged() == before,
              r.stdout + r.stderr)

        r = run_task(wt1, "done", "T-100", "--result-file", result_path, env=env)
        git(wt1, "commit", "-q", "-m", "T-100: 完了")
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

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_legacy_repo(tmp)
        before = snapshot(repo)
        with readonly_git(repo):
            r = run_task(repo, "migrate")
        check("migrate は GIT_READ_ONLY（終了コード11）で何も書かない",
              r.returncode == 11 and r.stdout.startswith("GIT_READ_ONLY\t") and snapshot(repo) == before,
              r.stdout + r.stderr)


def test_state_dir() -> None:
    say("ledger.py TW_STATE_DIR: 台帳の置き場を変え、古い台帳を写し、分かれた印と書けない置き場を知らせる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        for tid in ("T-100", "T-101", "T-102"):
            commit_task(main_path, taskfile.Task(tid, "置き場", "todo", "sonnet", "Y", (), BODY))
        state = os.path.join(tmp, "state")
        env = {ledger.STATE_DIR_ENV: state}
        old_root = ledger.ledger_root(cwd=main_path)

        run_task(wt2, "claim", "T-100")
        r = run_task(wt1, "claim", "T-101", env=env)
        roots = os.listdir(state) if os.path.isdir(state) else []
        new_root = os.path.join(state, roots[0]) if len(roots) == 1 else ""
        check("TW_STATE_DIR の下のクローンごとの置き場に印を立て、古い台帳には立てない",
              r.returncode == 0 and roots[:1] != [] and roots[0].startswith("base-")
              and os.path.isdir(ledger.claim_dir(new_root, "T-101"))
              and not os.path.isdir(ledger.claim_dir(old_root, "T-101")), r.stdout + r.stderr + str(roots))
        r = run_task(wt1, "claim", "T-100", env=env)
        check("初めて書くときに古い台帳を写し、別の作業ツリーの印は TAKEN",
              r.returncode == 4 and r.stdout.startswith(f"TAKEN\tT-100\t{os.path.realpath(wt2)}\t"), r.stdout + r.stderr)

        run_task(wt2, "release", "T-100", env=env)
        run_task(wt2, "claim", "T-102")
        r = run_task(wt1, "status", env=env)
        check("写したあとに古い台帳にだけ立った印を status が split_claims の行で知らせる（写した印は数えない）",
              "split_claims\t1\tT-102:wt2" in r.stdout.splitlines(), r.stdout + r.stderr)
        r = run_task(wt1, "claim", "T-102", env=env)
        check("古い台帳にだけ印のあるタスクの claim は、印を立てずに TAKEN（終了コード4）",
              r.returncode == 4 and r.stdout.startswith(f"TAKEN\tT-102\t{os.path.realpath(wt2)}\t")
              and not os.path.isdir(ledger.claim_dir(new_root, "T-102")), r.stdout + r.stderr)
        r = run_task(wt1, "status")
        check("TW_STATE_DIR が無ければ split_claims の行を出さない", "split_claims" not in r.stdout, r.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "書けない", "todo", "sonnet", "Y", (), BODY))
        root = ledger.ledger_root(cwd=main_path)
        os.makedirs(root, exist_ok=True)
        os.chmod(root, 0o555)
        try:
            r = run_task(wt1, "claim", "T-100")
        finally:
            os.chmod(root, 0o755)
        check("台帳に書けなければ STATE_READ_ONLY（終了コード12）で足す置き場と TW_STATE_DIR を言い、何も立てない",
              r.returncode == 12 and r.stdout.startswith(f"STATE_READ_ONLY\t{root}\t") and "TW_STATE_DIR" in r.stdout
              and not os.path.isdir(ledger.claim_dir(root, "T-100"))
              and not os.path.exists(os.path.join(wt1, ".tw", "local", "task-open-claims", "T-100")), r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない", verify="`true`")
        for tid in ("T-100", "T-101"):
            commit_task(main_path, taskfile.Task(tid, "途中から", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n\n### 2. 試す\n")
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt2, "claim", "T-101")
        write(os.path.join(wt2, "work.txt"), "x\n")
        state = os.path.join(tmp, "state")
        env = {ledger.STATE_DIR_ENV: state}

        r = run_task(wt1, "status", env=env)
        check("TW_STATE_DIR を途中から付けても、初めて書くまでは status が古い台帳の印を数える",
              any(line.startswith("counts\t") and "claimed=2" in line for line in r.stdout.splitlines()), r.stdout)
        r = run_task(wt1, "plan-check", "T-100", env=env)
        check("書く前の plan-check が古い台帳の印と計画の記録を読む", r.stdout.startswith("PLAN_FIRST\tT-100"), r.stdout)
        r = run_task(wt1, "step", "T-100", "1", env=env)
        check("書く前の step が古い台帳の印を持ち主と見る", r.stdout.startswith("STEPPED\tT-100\t1/2\t"),
              r.stdout + r.stderr)
        r = run_task(wt2, "verify", env=env)
        check("書く前の verify が古い台帳の印で計画の欠けを見る（PLAN_MISSING）",
              r.returncode == 10 and r.stdout.startswith("PLAN_MISSING\tT-101\t"), r.stdout + r.stderr)
        result_path = write(os.path.join(tmp, "result.md"), "- 検証コマンド: 1 pass\n")
        r = run_task(wt1, "done", "T-100", "--result-file", result_path, env=env)
        check("書く前の done が古い台帳の印を持ち主と見て DONE、読むだけでは新しい台帳を作らない",
              r.returncode == 0 and r.stdout.startswith("DONE\tT-100\t") and not os.path.exists(state),
              r.stdout + r.stderr)


# --- task.py: edit・plan-check ----------------------------------------------


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


def test_verify_refuses_unplanned_work() -> None:
    say("task.py verify: 着手中のタスクの ## やること が空のまま作業が始まっていたら検証を打たない")
    planned = task_body([("書く", "")])
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない", verify="`echo verified`")
        for tid in ("T-110", "T-111"):
            commit_task(main_path, taskfile.Task(tid, "検証の関門", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-110")
        r = run_task(wt1, "verify")
        check("作業が始まっていなければ（dropped の報告など）空でも打つ", r.returncode == 0
              and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)

        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "verify")
        r2 = run_task(wt1, "verify-check")
        check("空のまま作業があれば PLAN_MISSING（終了コード10）で、検証コマンドを打たず控えを消す", r.returncode == 10
              and r.stdout.startswith("PLAN_MISSING\tT-110\t") and "--after-work" in r.stdout
              and "verified" not in r.stdout and r2.stdout.strip() == "NOT_VERIFIED\tnone", r.stdout + r2.stdout + r.stderr)

        r = run_task(wt2, "verify")
        check("別の作業ツリーの着手には掛からない（遅れていた main は取り込んでから打つ）", r.returncode == 0
              and r.stdout.startswith("FOLDED\t") and "\nVERIFIED\t" in r.stdout, r.stdout + r.stderr)

        run_task(wt1, "edit", "T-110", "--after-work", "--body-file", "-", stdin=planned)
        r = run_task(wt1, "verify")
        check("書けば打つ", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)

        r = run_task(wt2, "claim", "T-111")
        write(os.path.join(wt2, "work.txt"), "x\n")
        result_path = write(os.path.join(tmp, "result.md"), "検証OK\n")
        run_task(wt2, "done", "T-111", "--result-file", result_path)
        r = run_task(wt2, "verify")
        check("done の後は空でも打つ", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)


# --- task.py: verify・verify-check -------------------------------------------

VERIFY_SCRIPT = (
    'echo "3 pass"\n'
    'if [ -f ignored/touch ]; then echo x > out.txt; fi\n'
    'if [ -f ignored/fail ]; then echo fail; exit 1; fi\n'
)


def test_verify_stamp() -> None:
    say("task.py verify・verify-check: 検証が通った中身の鍵を控え、同じなら省いてよいと判定する")
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1, wt2 = make_repo(tmp, branch="切らない", verify="`sh verify.sh`")
        write(os.path.join(wt1, "verify.sh"), VERIFY_SCRIPT)
        write(os.path.join(wt1, ".gitignore"), "ignored/\n")
        write(os.path.join(wt1, "pnpm-lock.yaml"), "lockfileVersion: 9\n")
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "検証の足場")

        def verify_check(cwd: str = wt1) -> str:
            r = run_task(cwd, "verify-check")
            if r.returncode != 0:
                raise RuntimeError(f"verify-check が失敗: {r.stdout}{r.stderr}")
            return r.stdout.strip()

        check("控えが無ければ NOT_VERIFIED none", verify_check() == "NOT_VERIFIED\tnone")

        write(os.path.join(wt1, "staged.txt"), "stage\n")
        git(wt1, "add", "staged.txt")
        write(os.path.join(wt1, "staged.txt"), "stage\nworktree\n")
        write(os.path.join(wt1, "shared.txt"), "line1\nline2\n")
        write(os.path.join(wt1, "untracked.txt"), "u\n")
        index_path = git(wt1, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout.strip()

        def index_state() -> tuple[bytes, str, str]:
            with open(index_path, "rb") as f:
                raw = f.read()
            return raw, git(wt1, "ls-files", "--stage").stdout, git(wt1, "diff", "--cached", "--name-status").stdout

        before = index_state()
        r = run_task(wt1, "verify")
        first = r.stdout.splitlines()[0] if r.stdout else ""
        check("通れば VERIFIED と木の SHA とログのパス（終了コード0）", r.returncode == 0 and first.startswith("VERIFIED\t")
              and len(first.split("\t")) == 3 and os.path.exists(first.split("\t")[2]), r.stdout + r.stderr)
        check("出力の末尾を続ける", "3 pass" in r.stdout, r.stdout)
        check("最後の行だけで判定が取れる", r.stdout.splitlines()[-1] == first, r.stdout)
        check("本物の index（中身・stage）は変わらない", index_state() == before)
        tree = first.split("\t")[1]
        check("控えた中身と同じなら VERIFIED_SAME", verify_check() == f"VERIFIED_SAME\t{tree}")

        write(os.path.join(wt1, "ignored", "cache.bin"), "x\n")
        check("gitignore の対象を足しても鍵は変わらない", verify_check() == f"VERIFIED_SAME\t{tree}")
        draft = write(os.path.join(wt1, DRAFT_REL, "x.md"), "- **x**\n")
        check(".tw/draft/ のファイルを足しても鍵は変わらない", verify_check() == f"VERIFIED_SAME\t{tree}")
        os.remove(draft)

        cases = (
            ("追跡中のファイルの変更", "shared.txt", "line1\nline2\nline3\n"),
            ("stage 済みのファイルの作業ツリー側の変更", "staged.txt", "stage\n"),
            ("未追跡のファイル", "untracked.txt", "u2\n"),
            ("未追跡のファイルの追加", "new.txt", "n\n"),
            ("lock ファイルの変更", "pnpm-lock.yaml", "lockfileVersion: 10\n"),
        )
        for label, name, content in cases:
            path = os.path.join(wt1, name)
            original = open(path, encoding="utf-8").read() if os.path.exists(path) else None
            write(path, content)
            check(f"{label}で鍵が変わる（NOT_VERIFIED content）", verify_check() == "NOT_VERIFIED\tcontent")
            if original is None:
                os.remove(path)
            else:
                write(path, original)
            check(f"{label}を戻せば VERIFIED_SAME", verify_check() == f"VERIFIED_SAME\t{tree}")

        os.remove(os.path.join(wt1, "shared.txt"))
        check("追跡中のファイルの削除で鍵が変わる", verify_check() == "NOT_VERIFIED\tcontent")
        write(os.path.join(wt1, "shared.txt"), "line1\nline2\n")
        check("index はここまでの照合でも変わらない", index_state() == before)
        check("別の作業ツリーは控えを共有しない", verify_check(wt2) == "NOT_VERIFIED\tnone")

        write(os.path.join(wt1, "ignored", "fail"), "x\n")
        r = run_task(wt1, "verify")
        check("落ちれば VERIFY_NOT_PASSED（終了コード10）と出力の末尾", r.returncode == 10
              and r.stdout.startswith("VERIFY_NOT_PASSED\t") and "fail" in r.stdout, r.stdout + r.stderr)
        check("落ちた回も最後の行だけで判定が取れる", r.stdout.splitlines()[-1] == r.stdout.splitlines()[0]
              and r.stdout.splitlines()[-1].startswith("VERIFY_NOT_PASSED\t"), r.stdout)
        check("落ちた回は控えを消す", verify_check() == "NOT_VERIFIED\tnone")
        os.remove(os.path.join(wt1, "ignored", "fail"))
        r = run_task(wt1, "verify")
        check("打ち直して通れば同じ木で控える", r.returncode == 0 and r.stdout.startswith(f"VERIFIED\t{tree}\t"), r.stdout)

        write(os.path.join(wt1, "ignored", "touch"), "x\n")
        r = run_task(wt1, "verify")
        check("検証のあいだに中身が変われば VERIFIED_UNSTAMPED（控えない）", r.returncode == 0
              and r.stdout.startswith("VERIFIED_UNSTAMPED\t"), r.stdout + r.stderr)
        check("VERIFIED_UNSTAMPED も最後の行だけで判定が取れる",
              r.stdout.splitlines()[-1].startswith("VERIFIED_UNSTAMPED\t"), r.stdout)
        check("VERIFIED_UNSTAMPED のあとは控えが無い", verify_check() == "NOT_VERIFIED\tnone")
        os.remove(os.path.join(wt1, "ignored", "touch"))
        os.remove(os.path.join(wt1, "out.txt"))

        run_task(wt1, "verify")
        git(wt1, "commit", "-q", "--allow-empty", "-m", "空")
        check("HEAD が動けば NOT_VERIFIED head", verify_check() == "NOT_VERIFIED\thead")

        run_task(wt1, "verify")
        config = os.path.join(wt1, ".tw", "config.toml")
        write(config, open(config, encoding="utf-8").read().replace('"sh verify.sh"', '"sh ./verify.sh"'))
        check("検証コマンドが変われば NOT_VERIFIED command", verify_check() == "NOT_VERIFIED\tcommand")

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1, _wt2 = make_repo(tmp, branch="切らない")
        r1 = run_task(wt1, "verify")
        r2 = run_task(wt1, "verify-check")
        check("検証コマンドが無ければ verify・verify-check とも NOTHING", r1.returncode == 0 and r2.returncode == 0
              and r1.stdout.startswith("NOTHING\t") and r2.stdout.startswith("NOTHING\t"), r1.stdout + r2.stdout)


# --- task.py: verify が検証の前に main を取り込む ---------------------------

COUNTING_VERIFY_SCRIPT = 'echo x >> ../verify-count.log\necho "3 pass"\n'
NOTES = "a\nb\nc\nd\ne\n"


def _fold_repo(tmp: str, preship: str | None = None, planned: bool = True) -> tuple[str, str]:
    """`(本体, 作業ツリー1)`。wt1 が T-120 を claim 済みで、検証コマンドは打たれるたびに `verify-count.log` へ1行足す。"""
    main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`sh ../verify-count.sh`", preship=preship)
    write(os.path.join(tmp, "verify-count.sh"), COUNTING_VERIFY_SCRIPT)
    write(os.path.join(main_path, "notes.txt"), NOTES)
    body = task_body([("書く", "")]) if planned else BODY
    commit_task(main_path, taskfile.Task("T-120", "取り込み", "todo", "sonnet", "Y", (), body))
    r = run_task(wt1, "claim", "T-120")
    if not r.stdout.startswith("CLAIMED\t"):
        raise RuntimeError(f"claim が失敗: {r.stdout}{r.stderr}")
    return main_path, wt1


def _advance_main(main_path: str, notes: str, extra: str | None = None) -> str:
    write(os.path.join(main_path, "notes.txt"), notes)
    if extra is not None:
        write(os.path.join(main_path, extra), "main\n")
    git(main_path, "add", "-A")
    git(main_path, "commit", "-q", "-m", "mainだけの変更")
    return git(main_path, "rev-parse", "HEAD").stdout.strip()


def test_verify_runs_format_first() -> None:
    say("task.py verify: 取り込みのあと・検証の前に整形コマンドを打ち、整形のあとの中身で鍵を控える")
    fix = "echo fixed > formatted.txt\n"
    saw = 'cat formatted.txt >> saw.log\necho "ok"\n'
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1, _wt2 = make_repo(
            tmp, branch="切らない", verify="`sh verify.sh`", format_command="`sh format.sh`"
        )
        write(os.path.join(wt1, "format.sh"), fix)
        write(os.path.join(wt1, "verify.sh"), saw)
        write(os.path.join(wt1, ".gitignore"), "saw.log\n")
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "足場")
        r = run_task(wt1, "verify")
        check("整形が直したうえで検証が通り VERIFIED", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)
        with open(os.path.join(wt1, "saw.log"), encoding="utf-8") as f:
            check("検証は整形のあとの中身を見る", f.read() == "fixed\n")
        r = run_task(wt1, "verify-check")
        check("整形で変わった中身でも続く verify-check は VERIFIED_SAME", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`sh verify.sh`", format_command="なし")
        write(os.path.join(wt1, "format.sh"), fix)
        write(os.path.join(wt1, "verify.sh"), 'echo "ok"\n')
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "足場")
        r = run_task(wt1, "verify")
        check("整形コマンドが なし なら整形を打たない", r.returncode == 0 and not os.path.exists(os.path.join(wt1, "formatted.txt")), r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1, _wt2 = make_repo(
            tmp, branch="切らない", verify="`sh verify.sh`", format_command="`sh format.sh`"
        )
        write(os.path.join(wt1, "format.sh"), "echo broken\nexit 1\n")
        write(os.path.join(wt1, "verify.sh"), "echo ran > verify-ran.txt\n")
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "足場")
        r = run_task(wt1, "verify")
        check("整形が落ちれば FORMAT_FAILED（終了コード10）で検証を打たない", r.returncode == 10
              and r.stdout.startswith("FORMAT_FAILED\t") and "broken" in r.stdout
              and not os.path.exists(os.path.join(wt1, "verify-ran.txt")), r.stdout + r.stderr)
        r = run_task(wt1, "verify-check")
        check("整形が落ちた回は控えを消す", r.stdout.strip() == "NOT_VERIFIED\tnone", r.stdout)


def _count_lines(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return len(f.read().splitlines())


def test_verify_keeps_failed_logs() -> None:
    say("task.py verify: 落ちた回のログが時刻つきで直近3本残り、整形と検証の出力が task-verify.log に並ぶ")
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1, _wt2 = make_repo(
            tmp, branch="切らない", verify="`sh verify.sh`", format_command="`sh format.sh`"
        )
        write(os.path.join(wt1, "format.sh"), "echo fmt-out\n")
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "足場")

        def verify_with(script: str) -> tuple[subprocess.CompletedProcess, str]:
            write(os.path.join(wt1, "verify.sh"), script)
            r = run_task(wt1, "verify")
            return r, r.stdout.splitlines()[-1].split("\t")[-1]

        def read(path: str) -> str:
            with open(path, encoding="utf-8") as f:
                return f.read()

        r1, p1 = verify_with("echo fail-1\nexit 1\n")
        r2, p2 = verify_with("echo fail-2\nexit 1\n")
        r3, p3 = verify_with("echo pass-3\n")
        tw = ledger.worktree_state_dir(wt1)
        failed = lambda: sorted(n for n in os.listdir(tw) if n.startswith("task-verify.failed-"))
        check("落ちた2回は終了コード10で、別々の失敗ログのパスが判定行に出る",
              r1.returncode == 10 and r2.returncode == 10 and p1 != p2
              and r1.stdout.splitlines()[-1].startswith("VERIFY_NOT_PASSED\t"), r1.stdout + r2.stdout)
        check("失敗ログが2本残り中身は各回の出力", len(failed()) == 2
              and "fail-1" in read(p1) and "fail-2" in read(p2) and "fail-2" not in read(p1), str(failed()))
        check("通った回は task-verify.log を上書きし、整形の出力と検証の出力が並ぶ",
              r3.returncode == 0 and p3 == ledger.verify_log_path(cwd=wt1)
              and read(p3).split() == ["fmt-out", "pass-3"], read(p3))
        check("通った回は失敗ログを増やさない", len(failed()) == 2)
        _r4, p4 = verify_with("echo fail-4\nexit 1\n")
        _r5, _p5 = verify_with("echo fail-5\nexit 1\n")
        check("4本目で最古が消えて3本になる", len(failed()) == 3 and not os.path.exists(p1)
              and os.path.exists(p2) and os.path.exists(p4), str(failed()))


def _verify_count(tmp: str) -> int:
    return _count_lines(os.path.join(tmp, "verify-count.log"))


def _commit_and_ship(wt: str, tmp: str, *paths: str) -> subprocess.CompletedProcess:
    result_path = write(os.path.join(tmp, "result.md"), "検証OK\n")
    r = run_task(wt, "done", "T-120", "--result-file", result_path)
    check("done は COMMITS_SINCE_CLAIM に main のコミットを数えない",
          r.stdout.strip() == f"DONE\tT-120\t{TASK_REL}/T-120.md\tstaged", r.stdout + r.stderr)
    git(wt, "add", *paths)
    git(wt, "commit", "-q", "-m", "T-120: 完了")
    return run_task(wt, "ship")


def test_verify_folds_base_before_check() -> None:
    say("task.py verify: main が進んでいれば未コミットの中身ごと取り込んでから検証し、受け入れの検証は1回で済む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _fold_repo(tmp)
        claim_head = git(wt1, "rev-parse", "HEAD").stdout.strip()
        new_base = _advance_main(main_path, NOTES.replace("a\n", "A\n"), extra="other.txt")
        write(os.path.join(wt1, "notes.txt"), NOTES.replace("e\n", "E\n"))
        write(os.path.join(wt1, "work.txt"), "x\n")

        r = run_task(wt1, "verify")
        lines = r.stdout.splitlines()
        check("FOLDED のあとに VERIFIED", r.returncode == 0 and lines[0] == f"FOLDED\t{claim_head}..{new_base}"
              and lines[1].startswith("VERIFIED\t"), r.stdout + r.stderr)
        check("最後の2行で FOLDED と判定が取れる", lines[-2] == lines[0] and lines[-1] == lines[1], r.stdout)
        check("HEAD は main と同じ", git(wt1, "rev-parse", "HEAD").stdout.strip() == new_base)
        status = git(wt1, "status", "--short").stdout
        check("作業は未コミットのまま残る", status.splitlines() == [" M notes.txt", "?? work.txt"], status)
        with open(os.path.join(wt1, "notes.txt"), encoding="utf-8") as f:
            notes = f.read()
        check("同じファイルの別の行の変更は両方残る", notes == "A\nb\nc\nd\nE\n", notes)
        check("main だけのファイルも取り込む", os.path.exists(os.path.join(wt1, "other.txt")))
        r = run_task(wt1, "verify-check")
        check("取り込んだあとの中身の控えで VERIFIED_SAME", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout)

        r = _commit_and_ship(wt1, tmp, "notes.txt", "work.txt", f"{TASK_REL}/T-120.md")
        check("ship は付け替えずに送る（verify=skipped）", r.returncode == 0 and r.stdout.startswith("SHIPPED\t")
              and "rebased=no" in r.stdout and "verify=skipped" in r.stdout, r.stdout + r.stderr)
        check("検証コマンドは1回だけ", _verify_count(tmp) == 1, str(_verify_count(tmp)))
        check("送る前の検証コマンドの行が無ければ preship は出ない", "preship=" not in r.stdout, r.stdout)


def readonly_subcommands() -> list[str]:
    """WORKFLOW.md の「`tw` コマンドの参照」の表で、すること欄に「読むだけ」と書かれた行のサブコマンド名。"""
    with open(os.path.join(HERE, "..", "WORKFLOW.md"), encoding="utf-8") as f:
        text = f.read()
    section = text.split("\n## `tw` コマンドの参照\n", 1)[1].split("\n## ", 1)[0]
    names = []
    for row in section.splitlines():
        cells = re.split(r" (?<!\\)\| ", row)
        m = re.match(r"\| `([a-z-]+)", cells[0])
        if m and len(cells) > 1 and "読むだけ" in cells[1]:
            names.append(m.group(1))
    return names


def _object_files(main_path: str) -> list[str]:
    top = os.path.join(main_path, ".git", "objects")
    return sorted(os.path.join(d, f) for d, _dirs, files in os.walk(top) for f in files)


def test_readonly_commands_stay_out_of_git() -> None:
    say("task.py: 読むだけのサブコマンドは、未コミット・主ブランチが進んだ・衝突の各状態で、読み取り専用の .git でも落ちず object を足さない")
    args_of = {
        "status": ["status"],
        "show": ["show", "T-120"],
        "plan-check": ["plan-check", "T-120"],
        "verify-check": ["verify-check"],
        "metrics": ["metrics"],
        "config": ["config"],
        "config-doctor": ["config-doctor"],
    }
    names = readonly_subcommands()
    check("WORKFLOW.md の読むだけの行が、テストの引数表と同じ", sorted(names) == sorted(args_of), str(names))

    def probe(main_path: str, wt1: str, state: str, base_ahead: bool, verify_check: str) -> None:
        r = subprocess.run(["git", "merge-base", "--is-ancestor", "main", "HEAD"], cwd=wt1)
        check(f"{state}: 主ブランチが {'HEAD より先にいる' if base_ahead else 'HEAD の祖先のまま'}",
              (r.returncode != 0) == base_ahead)
        r = run_task(wt1, "verify-check")
        check(f"{state}: 書ける .git で verify-check が {verify_check}",
              r.stdout.startswith(verify_check), r.stdout + r.stderr)
        before = _object_files(main_path)
        with readonly_git(main_path):
            for name in [*names, "pause"]:
                r = run_task(wt1, *args_of.get(name, [name]))
                if name == "verify-check":
                    check(f"{state}: 読み取り専用の .git でも verify-check が {verify_check}",
                          r.stdout.startswith(verify_check), r.stdout + r.stderr)
                check(f"{state}: {name} が GIT_READ_ONLY でも書き込みの失敗でもない",
                      r.returncode != 11 and "GIT_READ_ONLY" not in r.stdout + r.stderr
                      and "Traceback" not in r.stderr and r.stdout != "", r.stdout + r.stderr)
        check(f"{state}: .git/objects が増えない", _object_files(main_path) == before)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _fold_repo(tmp, preship="`true`", planned=False)
        r = run_task(wt1, "edit", "T-120", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n")
        check("計画を書く", r.returncode == 0, r.stdout + r.stderr)
        write(os.path.join(wt1, "work.txt"), "x\n")
        write(os.path.join(wt1, "notes.txt"), "a\nB\nc\nd\ne\n")
        r = run_task(wt1, "verify")
        check("verify が通る", r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)

        write(os.path.join(wt1, "work.txt"), "y\n")
        probe(main_path, wt1, "未コミットの変更", False, "NOT_VERIFIED\tcontent")

        write(os.path.join(wt1, "work.txt"), "x\n")
        _advance_main(main_path, NOTES, extra="other.txt")
        probe(main_path, wt1, "主ブランチが進んだ", True, "VERIFIED_SAME\t")

        _advance_main(main_path, "a\nM\nc\nd\ne\n")
        probe(main_path, wt1, "衝突する", True, "NOT_VERIFIED\tbase")


def test_verify_uses_preship_command_for_stamp() -> None:
    say("task.py verify: 送る前の検証コマンドの行があれば、検証コマンドではなくそれを打ち、その控えで verify-check が判定する")
    with tempfile.TemporaryDirectory() as tmp:
        write(os.path.join(tmp, "preship.sh"), "echo x >> ../preship-count.log\n")
        _main, wt1 = _fold_repo(tmp, preship="`sh ../preship.sh`")
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "verify")
        check("verify が通る", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)
        check("送る前の検証コマンドが1回", _count_lines(os.path.join(tmp, "preship-count.log")) == 1)
        check("検証コマンドは打たれない", _verify_count(tmp) == 0, str(_verify_count(tmp)))
        r = run_task(wt1, "verify-check")
        check("verify-check は VERIFIED_SAME", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout)
        write(os.path.join(wt1, "work.txt"), "y\n")
        check("中身が変われば NOT_VERIFIED content",
              run_task(wt1, "verify-check").stdout.strip() == "NOT_VERIFIED\tcontent")


def _preship_count(tmp: str) -> int:
    return _count_lines(os.path.join(tmp, "preship-count.log"))


def _commit_work_and_ship(wt: str, *paths: str) -> subprocess.CompletedProcess:
    git(wt, "add", *paths)
    git(wt, "commit", "-q", "-m", "作業")
    return run_task(wt, "ship")


def test_ship_skips_preship_verify_when_stamp_matches() -> None:
    say("task.py ship: 控えの中身をそのままコミットして送れば送る前の検証を飛ばし、中身が違う・付け替えた・借りがある・控えが無ければ打つ")

    def prepare(tmp: str) -> tuple[str, str]:
        write(os.path.join(tmp, "preship.sh"), "echo x >> ../preship-count.log\n")
        return _fold_repo(tmp, preship="`sh ../preship.sh`")

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        r = _commit_work_and_ship(wt1, "work.txt")
        check("同じ中身のコミットは preship=skipped で送る",
              r.returncode == 0 and r.stdout.startswith("SHIPPED\t") and "preship=skipped" in r.stdout
              and "rebased=no" in r.stdout, r.stdout + r.stderr)
        check("送る前の検証コマンドは verify の1回だけ", _preship_count(tmp) == 1, str(_preship_count(tmp)))
        check("検証コマンドは打たれない", _verify_count(tmp) == 0, str(_verify_count(tmp)))

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        write(os.path.join(wt1, DRAFT_REL, "x.md"), "- **x**\n")
        r = run_task(wt1, "done", "T-120", "--result-file", "-", stdin="- 振り返り: 兆候なし\n")
        check("done が通る", r.returncode == 0 and r.stdout.startswith("DONE\t"), r.stdout + r.stderr)
        r = _commit_work_and_ship(wt1, "work.txt", f"{DRAFT_REL}/x.md", f"{TASK_REL}/T-120.md")
        check("verify のあとのドラフトと done のタスクファイルも一緒にコミットしても preship=skipped",
              r.returncode == 0 and r.stdout.startswith("SHIPPED\t") and "preship=skipped" in r.stdout, r.stdout + r.stderr)
        check("ドラフトと done の回も送る前の検証コマンドは verify の1回だけ", _preship_count(tmp) == 1, str(_preship_count(tmp)))

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        write(os.path.join(wt1, "work.txt"), "y\n")
        r = _commit_work_and_ship(wt1, "work.txt")
        check("控えのあとに中身を変えたら preship=ran", "preship=ran" in r.stdout, r.stdout + r.stderr)
        check("送る前の検証コマンドが ship でもう1回", _preship_count(tmp) == 2, str(_preship_count(tmp)))

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = _commit_work_and_ship(wt1, "work.txt")
        check("控えが無ければ preship=ran", "preship=ran" in r.stdout, r.stdout + r.stderr)
        check("送る前の検証コマンドは1回", _preship_count(tmp) == 1, str(_preship_count(tmp)))

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        _advance_main(main_path, NOTES, extra="other.txt")
        r = _commit_work_and_ship(wt1, "work.txt")
        check("付け替えた回は rebased=yes・preship=ran",
              "rebased=yes" in r.stdout and "preship=ran" in r.stdout, r.stdout + r.stderr)
        check("送る前の検証コマンドだけが ship で1回（合計2回）", _preship_count(tmp) == 2, str(_preship_count(tmp)))
        check("検証コマンドは重ねて打たれない", _verify_count(tmp) == 0, str(_verify_count(tmp)))

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        ledger.mark_verify_owed("sh ../preship.sh", cwd=wt1)
        r = _commit_work_and_ship(wt1, "work.txt")
        check("借りがあれば控えが同じでも preship=ran", "preship=ran" in r.stdout, r.stdout + r.stderr)
        check("借りの回の送る前の検証コマンドは1回（合計2回）", _preship_count(tmp) == 2, str(_preship_count(tmp)))
        check("借りの印は消える", not ledger.is_verify_owed(cwd=wt1))


def test_ship_runs_preship_verify_when_work_changes_tree() -> None:
    say("task.py ship: 送る前の検証コマンドの行があり、控えのあとに作業の木が変わったなら、主ブランチへ入れる直前に打つ")
    for passes in (True, False):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "preship.sh"), "echo x >> ../preship-count.log\n[ ! -f ../preship-fail ]\n")
            main_path, wt1 = _fold_repo(tmp, preship="`sh ../preship.sh`")
            write(os.path.join(wt1, "work.txt"), "x\n")
            r = run_task(wt1, "verify")
            check("verify は VERIFIED（控えた）", r.returncode == 0 and "VERIFIED\t" in r.stdout, r.stdout + r.stderr)
            check("verify は送る前の検証コマンドを1回打つ", _count_lines(os.path.join(tmp, "preship-count.log")) == 1)
            r = run_task(wt1, "verify-check")
            check("控えは VERIFIED_SAME", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout)

            write(os.path.join(wt1, "work.txt"), "y\n")
            if not passes:
                write(os.path.join(tmp, "preship-fail"), "")
            head_before = git(main_path, "rev-parse", "HEAD").stdout.strip()
            r = _commit_and_ship(wt1, tmp, "work.txt", f"{TASK_REL}/T-120.md")
            preship_count = _count_lines(os.path.join(tmp, "preship-count.log"))
            check("送る前の検証コマンドは ship でもう1回打たれる", preship_count == 2, str(preship_count))
            check("通常の検証コマンドは打たれない", _verify_count(tmp) == 0, str(_verify_count(tmp)))
            head_after = git(main_path, "rev-parse", "HEAD").stdout.strip()
            if passes:
                check("通れば verify=skipped のまま preship=ran で送る",
                      r.returncode == 0 and r.stdout.startswith("SHIPPED\t") and "verify=skipped" in r.stdout
                      and "preship=ran" in r.stdout, r.stdout + r.stderr)
                check("主ブランチへ入った", head_after != head_before)
            else:
                check("落ちれば VERIFY_FAILED で終了コード8（送る前のコマンドを名指す）",
                      r.returncode == 8 and r.stdout.startswith("VERIFY_FAILED\tsh ../preship.sh"), r.stdout + r.stderr)
                check("主ブランチは進んでいない", head_after == head_before)
                check("作業ツリーはきれい", git(wt1, "status", "--porcelain").stdout.strip() == "")


def test_worktree_tree_sees_same_size_edit_after_second_boundary() -> None:
    say("ledger.worktree_tree: index の書き込みと同じ秒にした同サイズの書き換えを、秒をまたいでから測っても拾う")
    with tempfile.TemporaryDirectory() as tmp:
        git(tmp, "init", "-q")
        write(os.path.join(tmp, "n"), NOTES)
        git(tmp, "add", "n")
        git(tmp, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "初期")
        git(tmp, "checkout", "-q", "--", "n")
        git(tmp, "status", "--short")
        write(os.path.join(tmp, "n"), NOTES.replace("e\n", "E\n"))
        time.sleep(1.2)
        head_tree = git(tmp, "rev-parse", "HEAD^{tree}").stdout.strip()
        check("書き換えた木は HEAD の木と違う", ledger.worktree_tree(tmp) != head_tree)


def test_verify_conflict_before_check() -> None:
    say("task.py verify: 取り込みが衝突すれば、何も書き換えず検証コマンドを打たずに CONFLICT で止まる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _fold_repo(tmp)
        write(os.path.join(wt1, "notes.txt"), NOTES.replace("e\n", "E\n"))
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        _advance_main(main_path, NOTES.replace("e\n", "Z\n"))
        head = git(wt1, "rev-parse", "HEAD").stdout.strip()
        status = git(wt1, "status", "--short").stdout

        r = run_task(wt1, "verify")
        check("終了コード7で CONFLICT と衝突したファイル", r.returncode == 7 and r.stdout == "CONFLICT\tnotes.txt\n",
              f"{r.returncode} {r.stdout}{r.stderr}")
        check("検証コマンドを打たない", _verify_count(tmp) == 1, str(_verify_count(tmp)))
        check("HEAD は動かない", git(wt1, "rev-parse", "HEAD").stdout.strip() == head)
        check("作業ツリーは変わらない", git(wt1, "status", "--short").stdout == status)
        with open(os.path.join(wt1, "notes.txt"), encoding="utf-8") as f:
            check("手元の変更はそのまま", f.read() == NOTES.replace("e\n", "E\n"))
        r = run_task(wt1, "verify-check")
        check("前の控えを消す", r.stdout.strip() == "NOT_VERIFIED\tnone", r.stdout)


def test_verify_check_reports_base() -> None:
    say("task.py verify-check: 控えのあとに main が進めば NOT_VERIFIED base で、verify が取り込んで打てば ship は打たない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _fold_repo(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "verify")
        check("main が進んでいなければ取り込まない", r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)
        check("取り込まない回は末尾に FOLDED が出ない", "FOLDED" not in r.stdout
              and r.stdout.splitlines()[-1].startswith("VERIFIED\t"), r.stdout)
        _advance_main(main_path, NOTES, extra="other.txt")

        r = run_task(wt1, "verify-check")
        check("NOT_VERIFIED base", r.stdout.strip() == "NOT_VERIFIED\tbase", r.stdout)
        r = run_task(wt1, "verify")
        check("取り込んでから打つ", r.stdout.startswith("FOLDED\t") and "\nVERIFIED\t" in r.stdout, r.stdout + r.stderr)

        r = _commit_and_ship(wt1, tmp, "work.txt", f"{TASK_REL}/T-120.md")
        check("ship は verify=skipped", r.returncode == 0 and "verify=skipped" in r.stdout, r.stdout + r.stderr)
        check("検証コマンドは委譲先の1回と受け入れの1回", _verify_count(tmp) == 2, str(_verify_count(tmp)))


def test_verify_check_passes_base_with_preship() -> None:
    say("task.py verify-check: 送る前の検証コマンドがあれば、main が衝突なく進んだだけでは VERIFIED_SAME で、衝突すれば NOT_VERIFIED base")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _fold_repo(tmp, preship="`true`", planned=False)
        r = run_task(wt1, "edit", "T-120", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n")
        check("計画を書く", r.returncode == 0, r.stdout + r.stderr)
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "verify")
        check("verify が通る", r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)
        _advance_main(main_path, NOTES, extra="other.txt")
        r = run_task(wt1, "verify-check")
        check("衝突しなければ VERIFIED_SAME", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout)
        check("返却は拒まれない", run_handback_guard(tmp, wt1) is None)
        objects = os.path.join(main_path, ".git", "objects")
        before = sorted(os.listdir(objects))
        with readonly_git(main_path):
            r = run_task(wt1, "verify-check")
            check("読み取り専用の .git でも VERIFIED_SAME", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout + r.stderr)
            check("読み取り専用の .git でも返却は拒まれない", run_handback_guard(tmp, wt1) is None)
        check("verify-check は .git に object を足さない", sorted(os.listdir(objects)) == before)
        write(os.path.join(wt1, "work.txt"), "y\n")
        check("中身が変われば NOT_VERIFIED content",
              run_task(wt1, "verify-check").stdout.strip() == "NOT_VERIFIED\tcontent")

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = _fold_repo(tmp, preship="`true`")
        write(os.path.join(wt1, "notes.txt"), "a\nB\nc\nd\ne\n")
        r = run_task(wt1, "verify")
        check("verify が通る（衝突の回）", r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)
        _advance_main(main_path, "a\nM\nc\nd\ne\n")
        r = run_task(wt1, "verify-check")
        check("取り込みが衝突すれば NOT_VERIFIED base", r.stdout.strip() == "NOT_VERIFIED\tbase", r.stdout)


# --- task.py: ship（5.8・6章） -----------------------------------------------


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


MIGRATE_LAYOUT_SECTION = (
    "# x\n\n## タスク運用\n\n前置きの文章。\n\n"
    "- 検証コマンド: `./check.sh`（構文とテスト）\n  全段は --full\n"
    "- 整形コマンド: なし\n- ブランチ: 既定\n- タスクの置き場: develop/task\n\nあとがき。\n"
)


def test_migrate_layout() -> None:
    say("task.py migrate-layout: 旧配置を .tw/ へ移す（git add まで）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, config_filename="CLAUDE.md")
        write(os.path.join(main_path, "CLAUDE.md"), MIGRATE_LAYOUT_SECTION)
        write(os.path.join(main_path, "develop", "draft", "x.md"), "- **x**\n")
        write(os.path.join(main_path, "develop", "notes.txt"), "x\n")
        commit_task(main_path, taskfile.Task("T-100", "移すもの", "todo", "sonnet", "Y", (), BODY))
        tw = os.path.join(main_path, ".tw")
        write(os.path.join(tw, ".gitignore"), "*\n")
        write(os.path.join(tw, "task-verify-stamp"), "x\ny\nz\n")
        write(os.path.join(tw, "task-open-claims", "T-999"), "")
        write(os.path.join(tw, "task-step-stamp"), "old\n")
        head = git(main_path, "rev-parse", "HEAD").stdout

        write(os.path.join(main_path, "dirt.txt"), "x\n")
        r = run_task(main_path, "migrate-layout", "--dry-run")
        check("汚れていれば DIRTY（終了コード4）", r.returncode == 4 and r.stdout.strip() == "DIRTY", r.stdout + r.stderr)
        os.remove(os.path.join(main_path, "dirt.txt"))

        run_task(wt1, "claim", "T-100")
        r = run_task(main_path, "migrate-layout", "--dry-run")
        check("ほかの作業ツリーの着手の印があれば BUSY（終了コード4）",
              r.returncode == 4 and r.stdout.startswith("BUSY\tT-100\t") and r.stdout.rstrip().endswith("wt1"),
              r.stdout + r.stderr)
        r = run_task(wt1, "migrate-layout", "--dry-run")
        check("自分の作業ツリーの印は BUSY にしない", r.returncode == 0 and r.stdout.splitlines()[-1] == "PLAN",
              r.stdout + r.stderr)
        run_task(wt1, "release", "T-100")

        r = run_task(main_path, "migrate-layout", "--dry-run")
        lines = r.stdout.splitlines()
        expected = {
            "MOVE\t.tw/task-open-claims\t.tw/local/task-open-claims",
            "MOVE\t.tw/task-verify-stamp\t.tw/local/task-verify-stamp",
            "WRITE\t.tw/.gitignore",
            "MOVE\tdevelop/direction.md\t.tw/direction.md",
            "MOVE\tdevelop/draft\t.tw/draft",
            "MOVE\tdevelop/task\t.tw/task",
            "WRITE\t.tw/config.toml",
            "KEPT_PROSE\tCLAUDE.md",
            "LEFTOVER\tdevelop/notes.txt",
            "LEFTOVER\t.tw/task-step-stamp",
        }
        check("dry-run は移すものを並べて PLAN", r.returncode == 0 and set(lines[:-1]) == expected and lines[-1] == "PLAN",
              r.stdout + r.stderr)
        check("dry-run は何も変えない",
              os.path.exists(os.path.join(main_path, "develop", "direction.md"))
              and not os.path.exists(os.path.join(tw, "config.toml")) and not os.path.exists(os.path.join(tw, "local"))
              and open(os.path.join(main_path, "CLAUDE.md"), encoding="utf-8").read() == MIGRATE_LAYOUT_SECTION
              and git(main_path, "status", "--porcelain").stdout == "")

        r = run_task(main_path, "migrate-layout")
        check("本番は MIGRATED", r.returncode == 0 and r.stdout.splitlines()[-1] == "MIGRATED", r.stdout + r.stderr)
        config = open(os.path.join(tw, "config.toml"), encoding="utf-8").read()
        check("config.toml に旧い節の値を写し、説明はコメント行、整形の なし と root は書かない",
              config == '# タスク運用の設定（tw が読む）\n# （構文とテスト）\n# 全段は --full\nverify = "./check.sh"\n'
              'branch = "既定"\nstore = "files"\n', config)
        check("節からは値の行と続きの字下げ行だけを消し、見出しと文章を残す",
              open(os.path.join(main_path, "CLAUDE.md"), encoding="utf-8").read()
              == "# x\n\n## タスク運用\n\n前置きの文章。\n\n\nあとがき。\n")
        staged = set(git(main_path, "diff", "--cached", "--name-only").stdout.split())
        check("移したものと設定が stage され、コミットは増えない",
              {".tw/config.toml", ".tw/.gitignore", "CLAUDE.md", ".tw/direction.md", ".tw/draft/x.md", ".tw/task/T-100.md"}
              <= staged and git(main_path, "rev-parse", "HEAD").stdout == head, str(staged))
        check("控えを .tw/local/ へ移し、.tw/.gitignore は local/",
              os.path.isfile(os.path.join(tw, "local", "task-verify-stamp"))
              and os.path.isfile(os.path.join(tw, "local", "task-open-claims", "T-999"))
              and not os.path.exists(os.path.join(tw, "task-verify-stamp"))
              and open(os.path.join(tw, ".gitignore"), encoding="utf-8").read() == "local/\n")
        status = git(main_path, "status", "--porcelain").stdout.splitlines()
        check("一覧に無い .tw/ 直下のファイルは移さず、git status に出る", "?? .tw/task-step-stamp" in status, str(status))
        os.remove(os.path.join(tw, "task-step-stamp"))
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "移す")
        lines = run_task(main_path, "config").stdout.splitlines()
        check("移したあとは .tw/config.toml を読み、置き場は .tw/",
              lines[:1] == ["CONFIG\t.tw/config.toml"] and "task\t.tw/task" in lines and "direction\t.tw/direction.md" in lines,
              "\n".join(lines))
        r = run_task(main_path, "status")
        check("移したあとも status がタスクを読む", any(l.startswith("T-100\ttodo") for l in r.stdout.splitlines()),
              r.stdout + r.stderr)
        r = run_task(main_path, "migrate-layout")
        check("移し終えていれば NOTHING", r.returncode == 0 and r.stdout.startswith("NOTHING\t"), r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp)
        os.makedirs(os.path.join(main_path, "develop"), exist_ok=True)
        git(main_path, "mv", ".tw/direction.md", "develop/direction.md")
        write(os.path.join(main_path, "develop", "task", "T-100.md"),
              taskfile.render(taskfile.Task("T-100", "develop に残った", "todo", "sonnet", "Y", (), BODY)))
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "T-055 の時代の配置")
        r = run_task(main_path, "status")
        check("config.toml があっても develop/ に置き場が残っていれば status は old_layout を出す",
              "old_layout\tdevelop/\ttw migrate-layout --dry-run" in r.stdout.splitlines(), r.stdout + r.stderr)
        r = run_task(main_path, "migrate-layout")
        lines = r.stdout.splitlines()
        check("config.toml があっても develop/ の置き場を根の下へ git mv し、config.toml は書かない",
              r.returncode == 0 and lines[-1] == "MIGRATED"
              and "MOVE\tdevelop/direction.md\t.tw/direction.md" in lines and "MOVE\tdevelop/task\t.tw/task" in lines
              and "WRITE\t.tw/config.toml" not in lines
              and os.path.isfile(os.path.join(main_path, TASK_REL, "T-100.md"))
              and not os.path.exists(os.path.join(main_path, "develop", "task")), r.stdout + r.stderr)
        git(main_path, "commit", "-q", "-m", "移す")
        r = run_task(main_path, "status")
        check("移したあとは old_layout を出さずにタスクを読む",
              "old_layout" not in r.stdout and any(l.startswith("T-100\ttodo") for l in r.stdout.splitlines()),
              r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp)
        write(os.path.join(main_path, "develop", "direction.md"), "# 未対応の指示メモ\n\n## ユーザーから\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "両方にある")
        r = run_task(main_path, "status")
        check("根の下にも同じ名前があっても、develop/ に残った置き場を old_layout で知らせる",
              "old_layout\tdevelop/\ttw migrate-layout --dry-run" in r.stdout.splitlines(), r.stdout + r.stderr)
        r = run_task(main_path, "migrate-layout")
        check("根の下にも同じ名前があれば INVALID（終了コード3）で何も変えない",
              r.returncode == 3 and r.stdout.strip() == "INVALID\t.tw/direction.md が既にある（develop/direction.md を移せない）"
              and git(main_path, "status", "--porcelain").stdout == "", r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp)
        tw = os.path.join(main_path, ".tw")
        write(os.path.join(tw, ".gitignore"), "*\n!config.toml\n")
        write(os.path.join(tw, "task-verify-stamp"), "x\ny\nz\n")
        r = run_task(main_path, "migrate-layout")
        check("config.toml がある .tw/ でも、旧い .gitignore の書き換えと控えの移動だけを行う",
              r.returncode == 0 and r.stdout.splitlines() == [
                  "MOVE\t.tw/task-verify-stamp\t.tw/local/task-verify-stamp", "WRITE\t.tw/.gitignore", "MIGRATED"]
              and open(os.path.join(tw, ".gitignore"), encoding="utf-8").read() == "local/\n"
              and os.path.isfile(os.path.join(tw, "local", "task-verify-stamp"))
              and git(main_path, "diff", "--cached", "--name-only").stdout.split() == [".tw/.gitignore"],
              r.stdout + r.stderr)


def _claim_work_and_done(wt: str, task_id: str, note: str = "") -> None:
    """`claim` 済みのタスクに1件コミットぶんの作業をして `done` にする（コミットはしない）。"""
    write(os.path.join(wt, f"work{note}.txt"), "x")
    git(wt, "add", "-A")
    result_path = write(os.path.join(wt, f"result{note}.md"), "検証OK\n")
    r = run_task(wt, "done", task_id, "--result-file", result_path)
    if r.returncode != 0:
        raise RuntimeError(f"task done が失敗: {r.stdout}{r.stderr}")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", f"{task_id}: 完了")


def _no_merge_commits(repo: str, base: str = "main") -> str:
    return git(repo, "log", "--oneline", "--merges", base).stdout


def test_ship_fast_forward() -> None:
    say("task.py ship: main が進んでいなければ追い付くだけで送る")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "追い付くだけ", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-100")
        _claim_work_and_done(wt1, "T-100")

        r = run_task(wt1, "ship")
        check("SHIPPEDで返る", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)
        check("rebasedはno", "rebased=no" in r.stdout, r.stdout)
        check("releasedにT-100を含む", "released=T-100" in r.stdout, r.stdout)
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")

        head_task = git(main_path, "show", f"main:{TASK_REL}/T-100.md").stdout
        check("mainのタスクファイルがdoneになる", "status: done" in head_task, head_task)
        check(
            "着手の印は消える",
            not os.path.isdir(ledger.claim_dir(ledger.ledger_root(cwd=wt1), "T-100")),
        )


def test_ship_rebases_when_main_advances() -> None:
    say("task.py ship: main が先に進んでいれば付け替えてから送る")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`echo verified`")
        commit_task(main_path, taskfile.Task("T-100", "付け替え", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-100")

        write(os.path.join(main_path, "unrelated.txt"), "x")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "mainだけの変更")

        _claim_work_and_done(wt1, "T-100")
        r = run_task(wt1, "verify")
        check("付け替え前の中身を控える", r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)

        r = run_task(wt1, "ship")
        check("SHIPPEDで返る", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)
        check("rebasedはyes", "rebased=yes" in r.stdout, r.stdout)
        check("verifyはran（控えがあっても付け替えたら打つ）", "verify=ran" in r.stdout, r.stdout)
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")

        log = git(main_path, "log", "--oneline", "main").stdout
        check("mainだけの変更がmainに残る", "mainだけの変更" in log, log)
        check("T-100の完了もmainに乗る", "T-100: 完了" in log, log)


def test_ship_forces_verify_after_verify_failed_without_new_rebase() -> None:
    say("task.py ship: VERIFY_FAILEDのあと打ち直すと、付け替えが無くても検証を飛ばさない（T-777）")
    with tempfile.TemporaryDirectory() as tmp:
        flag = os.path.join(tmp, "verify-ok")
        verify_script = write(
            os.path.join(tmp, "verify.sh"),
            f'if [ -f "{flag}" ]; then echo ok; exit 0; else echo fail; exit 1; fi\n',
        )
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify=f"`sh {verify_script}`")
        commit_task(main_path, taskfile.Task("T-100", "打ち直し", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-100")
        write(os.path.join(main_path, "unrelated.txt"), "x")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "mainだけの変更")
        _claim_work_and_done(wt1, "T-100")

        r1 = run_task(wt1, "ship")
        check("1回目はVERIFY_FAILEDで終了コード8", r1.returncode == 8 and r1.stdout.startswith("VERIFY_FAILED\t"), r1.stdout + r1.stderr)
        check(
            "検証の借りの印が立つ",
            ledger.is_verify_owed(cwd=wt1),
        )
        check("rebaseはabortされず作業ツリーはきれい", git(wt1, "status", "--porcelain").stdout.strip() == "")

        write(flag, "x")  # 検証コマンドが通る状態に直す。main はこれ以上進めない（付け替えは起きない）。
        r2 = run_task(wt1, "ship")
        check("打ち直しはSHIPPEDで返る", r2.returncode == 0 and r2.stdout.startswith("SHIPPED\t"), r2.stdout + r2.stderr)
        check("rebasedはno（付け替えは起きていない）", "rebased=no" in r2.stdout, r2.stdout)
        check("それでも検証はran（借りを飛ばさない）", "verify=ran" in r2.stdout, r2.stdout)
        check("検証の借りの印は消える", not ledger.is_verify_owed(cwd=wt1))
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")

        r3 = run_task(wt1, "ship")
        check("送るものが無ければ今までどおりNOTHING", r3.returncode == 0 and r3.stdout.startswith("NOTHING\t"), r3.stdout + r3.stderr)


def test_ship_verify_failed_keeps_full_log_in_order() -> None:
    say("task.py ship: 検証が落ちると、全文が出た順のログに残り、VERIFY_FAILED の行にそのパスが出る")
    with tempfile.TemporaryDirectory() as tmp:
        verify_script = write(
            os.path.join(tmp, "verify.sh"),
            "echo out-1\necho err-1 >&2\necho out-2\necho err-2 >&2\nexit 1\n",
        )
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify=f"`sh {verify_script}`")
        commit_task(main_path, taskfile.Task("T-100", "ログ", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-100")
        write(os.path.join(main_path, "unrelated.txt"), "x")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "mainだけの変更")
        _claim_work_and_done(wt1, "T-100")

        r = run_task(wt1, "ship")
        first = r.stdout.splitlines()[0] if r.stdout else ""
        check("VERIFY_FAILED で終了コード8", r.returncode == 8 and first.startswith("VERIFY_FAILED\t"), r.stdout + r.stderr)
        log_path = first.split("\t")[-1]
        check("行の末尾が落ちた回を残したログのパス",
              os.path.dirname(log_path) == os.path.dirname(ledger.ship_verify_log_path(cwd=wt1))
              and os.path.basename(log_path).startswith("task-ship-verify.failed-"), first)
        with open(log_path, encoding="utf-8") as f:
            log = f.read()
        check("stdout と stderr が出た順に残る", log.split() == ["out-1", "err-1", "out-2", "err-2"], log)


def flow_rows(wt: str) -> list[dict]:
    """台帳の `flow/` の記録を、ファイル名の順に読む。"""
    d = ledger.flow_dir(ledger.ledger_root(cwd=wt))
    out: list[dict] = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        with open(os.path.join(d, name), encoding="utf-8") as f:
            out += [json.loads(line) for line in f if line.strip()]
    return out


def test_flow_records_and_metrics() -> None:
    say("task.py: 着手・検証・送り出し・完了が台帳の flow/ に残り、metrics が数を出す")
    with tempfile.TemporaryDirectory() as tmp:
        flag = os.path.join(tmp, "verify-ok")
        verify_script = write(os.path.join(tmp, "verify.sh"), f'[ -f "{flag}" ] && echo ok || {{ echo fail; exit 1; }}\n')
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify=f"`sh {verify_script}`")
        commit_task(main_path, taskfile.Task("T-100", "流れ", "todo", "sonnet", "Y", (), PLANNED_BODY))
        commit_task(main_path, taskfile.Task("T-101", "やり直し", "todo", "haiku", "Y", (), PLANNED_BODY))

        r = run_task(wt1, "metrics")
        check("記録が無ければ EMPTY", r.returncode == 0 and r.stdout.strip() == "EMPTY", r.stdout + r.stderr)

        run_task(wt1, "claim", "T-101")
        run_task(wt1, "release", "T-101")
        run_task(wt1, "claim", "T-101")
        run_task(wt1, "release", "T-101")
        run_task(wt1, "claim", "T-100")
        write(os.path.join(wt1, "work.txt"), "x")
        r = run_task(wt1, "verify")
        check("検証が落ちれば VERIFY_NOT_PASSED", r.returncode == 10, r.stdout + r.stderr)
        write(flag, "x")
        r = run_task(wt1, "verify")
        check("通れば VERIFIED", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)
        git(wt1, "add", "-A")
        result_path = write(os.path.join(tmp, "result.md"), "- 検証: x\n- 振り返り: 兆候なし\n")
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "T-100: 完了")
        write(os.path.join(main_path, "unrelated.txt"), "x")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "mainだけの変更")
        os.remove(flag)
        r = run_task(wt1, "ship")
        check("ship の検証が落ちれば VERIFY_FAILED", r.returncode == 8, r.stdout + r.stderr)
        write(flag, "x")
        r = run_task(wt1, "ship")
        check("打ち直せば SHIPPED", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)

        rows_ = flow_rows(wt1)
        steps = [(e["event"], e["task"], e.get("result")) for e in rows_]
        check(
            "出来事が起きた順に1行ずつ増える",
            steps
            == [
                ("claim", "T-101", None),
                ("release", "T-101", None),
                ("claim", "T-101", None),
                ("release", "T-101", None),
                ("claim", "T-100", None),
                ("verify", "T-100", "VERIFY_NOT_PASSED"),
                ("verify", "T-100", "VERIFIED"),
                ("done", "T-100", None),
                ("ship", "T-100", "VERIFY_FAILED"),
                ("ship", "T-100", "SHIPPED"),
            ],
            repr(steps),
        )
        check(
            "各行に difficulty と UTC の時刻があり、verify は所要秒を持つ",
            {e["difficulty"] for e in rows_ if e["task"] == "T-100"} == {"sonnet"}
            and all(e["t"].endswith("+00:00") for e in rows_)
            and all(isinstance(e["seconds"], int) for e in rows_ if e["event"] == "verify"),
            repr(rows_),
        )
        done = next(e for e in rows_ if e["event"] == "done")
        check("done は dropped と振り返りの有無だけを持つ", done["dropped"] is False and done["reflection"] == "none", repr(done))
        flow_text = json.dumps(rows_, ensure_ascii=False)
        check("パスもコマンドも記録に入らない", tmp not in flow_text and "verify.sh" not in flow_text, flow_text)

        # 前の期間の値（8日前に着手して7日前に送った1件）
        old = lambda t, ev, **kw: json.dumps({"t": t, "event": ev, "task": "T-050", "difficulty": "sonnet", **kw})
        stamp = lambda days: (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        d = ledger.flow_dir(ledger.ledger_root(cwd=wt1))
        write(os.path.join(d, "2000-01.jsonl"), "\n".join([old(stamp(9), "claim"), old(stamp(8), "ship", result="SHIPPED")]) + "\n")
        r = run_task(wt1, "metrics")
        table = {l.split("\t")[0]: l.split("\t")[1:] for l in r.stdout.splitlines()}
        say("  --- tw metrics の出力 ---")
        for line in r.stdout.splitlines():
            say(f"  | {line}")
        check(
            "metrics が今の期間と前の期間の数を並べる",
            r.returncode == 0
            and table["shipped"] == ["1", "1"]
            and table["lead_median_seconds"][1] == "86400"
            and table["lead_max_seconds"][0] != "-"
            and table["verify_per_task"] == ["2.0", "-"]
            and table["verify_failed"] == ["1", "0"]
            and table["ship_verify_failed"] == ["1", "0"]
            and table["reclaim"] == ["1", "0"]
            and table["reflection_none_ratio"] == ["100% (1/1)", "-"],
            r.stdout + r.stderr,
        )
        with open(os.path.join(d, "2000-01.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": stamp(0), "event": "done", "task": "T-051", "difficulty": "haiku",
                                "dropped": False, "reflection": "skipped"}) + "\n")
        r = run_task(wt1, "metrics")
        check("振り返りを近道で省いた done は reflection_none_ratio の分母に入らない",
              "reflection_none_ratio\t100% (1/1)\t-" in r.stdout.splitlines(), r.stdout)
        r = run_task(wt1, "metrics", "--days", "30")
        check("--days で期間が変わる（前の期間の1件が今の期間に入る）", r.stdout.splitlines()[1].split("\t")[1:] == ["2", "0"], r.stdout)

        with open(os.path.join(d, "2000-01.jsonl"), "a", encoding="utf-8") as f:
            f.write("壊れた行\n{\"t\": \"x\"}\n")
        r = run_task(wt1, "metrics")
        check("壊れた行は読み飛ばして件数を添える", r.returncode == 0 and r.stdout.splitlines()[-1] == "SKIPPED\t2", r.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "書けない", "todo", "sonnet", "Y", (), PLANNED_BODY))
        write(ledger.flow_dir(ledger.ledger_root(cwd=wt1)), "ファイルがディレクトリの場所を塞ぐ\n")
        r = run_task(wt1, "claim", "T-100")
        check(
            "記録を書けなくても claim の出力と終了コードは変わらず、標準エラーに1行だけ出る",
            r.returncode == 0 and r.stdout.startswith("CLAIMED\tT-100\t") and r.stderr.strip().startswith("flow:")
            and len(r.stderr.strip().splitlines()) == 1,
            r.stdout + r.stderr,
        )
        result_path = write(os.path.join(tmp, "result.md"), "x\n")
        r = run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done も変わらない", r.returncode == 0 and r.stdout.startswith("DONE\tT-100\t"), r.stdout + r.stderr)


def test_metrics_stages() -> None:
    say("task.py metrics --stages: lap を含む flow から段×difficulty×道の件数・中央値・最大が出る")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        d = ledger.flow_dir(ledger.ledger_root(cwd=wt1))
        r = run_task(wt1, "metrics", "--stages")
        check("記録が無ければ EMPTY", r.returncode == 0 and r.stdout.strip() == "EMPTY", r.stdout + r.stderr)
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        row = lambda task, sec, ev, **kw: json.dumps(
            {"t": (base + timedelta(seconds=sec)).isoformat(timespec="seconds"), "event": ev, "task": task, "difficulty": "sonnet", **kw}
        )
        lines = [
            row("T-060", 0, "claim"), row("T-060", 100, "lap", stage="delegate"), row("T-060", 400, "verify", result="VERIFIED", seconds=50),
            row("T-060", 500, "lap", stage="accept"), row("T-060", 560, "lap", stage="review"), row("T-060", 700, "lap", stage="retro"),
            row("T-060", 760, "done"), row("T-060", 790, "ship", result="VERIFY_FAILED"), row("T-060", 800, "ship", result="SHIPPED"),
            row("T-061", 0, "claim"), row("T-061", 300, "step", step=1, steps=2), row("T-061", 900, "done"), row("T-061", 930, "ship", result="SHIPPED"),
            row("T-062", 0, "claim"), row("T-062", 10, "done"),
        ]
        haiku = lambda task, sec, ev, **kw: json.dumps(
            {"t": (base + timedelta(seconds=sec)).isoformat(timespec="seconds"), "event": ev, "task": task, "difficulty": "haiku", **kw}
        )
        lines += [
            haiku("T-063", 0, "claim"), haiku("T-063", 20, "lap", stage="direct"),
            haiku("T-063", 100, "verify", result="VERIFIED", seconds=30), haiku("T-063", 200, "done"),
            haiku("T-063", 220, "ship", result="SHIPPED"),
            haiku("T-064", 0, "claim"), haiku("T-064", 10, "lap", stage="direct"), haiku("T-064", 50, "lap", stage="delegate"),
            haiku("T-064", 300, "done"), haiku("T-064", 310, "ship", result="SHIPPED"),
        ]
        opus = lambda task, sec, ev, **kw: json.dumps(
            {"t": (base + timedelta(seconds=sec)).isoformat(timespec="seconds"), "event": ev, "task": task, "difficulty": "opus", **kw}
        )
        lines += [
            opus("T-065", 0, "claim"), opus("T-065", 10, "lap", stage="delegate"), opus("T-065", 100, "lap", stage="review"),
            opus("T-065", 200, "lap", stage="retro"), opus("T-065", 340, "verify", result="VERIFIED", seconds=120),
            opus("T-065", 400, "done"), opus("T-065", 410, "ship", result="SHIPPED"),
        ]
        write(os.path.join(d, "2000-01.jsonl"), "\n".join(lines) + "\n")
        r = run_task(wt1, "metrics", "--stages")
        got = [tuple(l.split("\t")) for l in r.stdout.splitlines()]
        check(
            "段×difficulty×道ごとに件数・中央値・最大の STAGE の行が段の順に出る（送り出していないタスクは入らない。"
            "lap direct のあとに lap delegate があれば normal。検証でない段は次の検証でない出来事までから検証と重なった秒を引く）",
            r.returncode == 0
            and got
            == [
                ("STAGE", "計画", "haiku", "direct", "1", "20", "20"),
                ("STAGE", "計画", "haiku", "normal", "1", "10", "10"),
                ("STAGE", "計画", "opus", "normal", "1", "10", "10"),
                ("STAGE", "計画", "sonnet", "normal", "2", "200", "300"),
                ("STAGE", "直し", "haiku", "direct", "1", "150", "150"),
                ("STAGE", "直し", "haiku", "normal", "1", "40", "40"),
                ("STAGE", "委譲", "haiku", "normal", "1", "250", "250"),
                ("STAGE", "委譲", "opus", "normal", "1", "90", "90"),
                ("STAGE", "委譲", "sonnet", "normal", "2", "475", "600"),
                ("STAGE", "検証", "haiku", "direct", "1", "30", "30"),
                ("STAGE", "検証", "opus", "normal", "1", "120", "120"),
                ("STAGE", "検証", "sonnet", "normal", "1", "50", "50"),
                ("STAGE", "受け入れ", "sonnet", "normal", "1", "60", "60"),
                ("STAGE", "レビュー", "opus", "normal", "1", "100", "100"),
                ("STAGE", "レビュー", "sonnet", "normal", "1", "140", "140"),
                ("STAGE", "振り返り", "opus", "normal", "1", "80", "80"),
                ("STAGE", "振り返り", "sonnet", "normal", "1", "60", "60"),
                ("STAGE", "送り出し", "haiku", "direct", "1", "20", "20"),
                ("STAGE", "送り出し", "haiku", "normal", "1", "10", "10"),
                ("STAGE", "送り出し", "opus", "normal", "1", "10", "10"),
                ("STAGE", "送り出し", "sonnet", "normal", "2", "35", "40"),
            ],
            r.stdout + r.stderr,
        )
        say("  --- tw metrics --stages の出力 ---")
        for line in r.stdout.splitlines():
            say(f"  | {line}")


def test_metrics_stages_parallel_steps() -> None:
    say("task.py metrics --stages: 並列の段の返却が重なっても、委譲の時間を段ごとの和で数えない")
    base = datetime.now(timezone.utc) - timedelta(hours=2)
    row = lambda sec, ev, **kw: metrics.Event(base + timedelta(seconds=sec), ev, "T-070", {"difficulty": "opus", **kw})
    events = [
        row(0, "claim"), row(10, "lap", stage="delegate"),
        row(200, "step", step=1, steps=3), row(210, "step", step=2, steps=3),
        row(400, "verify", result="VERIFIED", seconds=50), row(410, "lap", stage="accept"),
        row(500, "done"), row(510, "ship", result="SHIPPED"),
    ]
    delegated = metrics.stage_durations(events, 1)[("委譲", "opus", "normal")]
    check("委譲の時間の和は lap delegate から受け入れまでの壁時計から検証の秒を引いたもので、段1（190秒）と段2（200秒）の和を超えない",
          sum(delegated) == 410 - 10 - 50, repr(delegated))


def test_retrospect_due() -> None:
    say("task.py status: 横断の振り返りの時期に retrospect_due の行を出す")
    due_line = lambda out: next((l for l in out.splitlines() if l.startswith("retrospect_due\t")), None)
    record = lambda day: f"# 横断の振り返りの記録\n\n## {day}（x〜y）\n\n- ドラフト: 0件（なし）\n"
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)

        r = run_task(wt1, "status")
        check("記録も flow/ も無い初回は出さない", r.returncode == 0 and due_line(r.stdout) is None, r.stdout + r.stderr)

        d = ledger.flow_dir(ledger.ledger_root(cwd=wt1))
        claim = lambda days: json.dumps({"t": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds"), "event": "claim", "task": "T-001", "difficulty": "haiku"}) + "\n"
        write(os.path.join(d, "2000-01.jsonl"), claim(3))
        r = run_task(wt1, "status")
        check("記録が無く flow/ の最古が7日未満なら出さない", due_line(r.stdout) is None, r.stdout)

        write(os.path.join(d, "2000-01.jsonl"), claim(8) + claim(3))
        r = run_task(wt1, "status")
        check("記録が無く flow/ の最古が8日前なら -\\t8d で出る", due_line(r.stdout) == "retrospect_due\t-\t8d", r.stdout)

        write(os.path.join(main_path, "docs", "history", "retrospect.md"), record("2000-01-01"))
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "古い記録")
        r = run_task(wt1, "status")
        line = due_line(r.stdout) or ""
        check("主ブランチの記録が7日以上前なら日付つきで出る", line.startswith("retrospect_due\t2000-01-01\t"), r.stdout)

        today = datetime.now().date().isoformat()
        write(os.path.join(wt1, "docs", "history", "retrospect.md"), record(today))
        r = run_task(wt1, "status")
        check("作業ツリーに今日の記録を書くと出なくなる", due_line(r.stdout) is None, r.stdout)
        r = run_task(wt2, "status")
        check("主ブランチへ入れるまでは、ほかの作業ツリーには出る", (due_line(r.stdout) or "").startswith("retrospect_due\t2000-01-01\t"), r.stdout)

        write(os.path.join(main_path, "docs", "history", "retrospect.md"), record(today) + record("2000-01-01").split("\n", 2)[2])
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "今日の記録")
        r = run_task(wt2, "status")
        check("主ブランチへ入れれば、ほかの作業ツリーでも出なくなる", due_line(r.stdout) is None, r.stdout)


def test_ship_stale_verify_owed_does_not_block_nothing_or_main_worktree() -> None:
    say("task.py ship: 検証の借りの印が残っていても、main に送るものが無い・main上で起こしたときは今までどおり動く")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`false`")

        # 送るものが無い（NOTHING）側: 古い印が残っていても検証コマンド（`false`）は打たれない。
        ledger.mark_verify_owed("`false`", cwd=wt1)
        r1 = run_task(wt1, "ship")
        check("NOTHINGで返る（検証は打たれない）", r1.returncode == 0 and r1.stdout.startswith("NOTHING\t"), r1.stdout + r1.stderr)
        check("古い印は消える", not ledger.is_verify_owed(cwd=wt1))

        # main の作業ツリーで起こした（送る段なし）側。
        ledger.mark_verify_owed("`false`", cwd=main_path)
        commit_task(main_path, taskfile.Task("T-100", "本体で完結", "todo", "sonnet", "Y", (), BODY))
        run_task(main_path, "claim", "T-100")
        _claim_work_and_done(main_path, "T-100")
        r2 = run_task(main_path, "ship")
        check(
            "SHIPPED main（送る段なし）で返る",
            r2.returncode == 0 and r2.stdout.startswith("SHIPPED\tmain\t(送る段なし)"),
            r2.stdout + r2.stderr,
        )
        check("古い印は消える", not ledger.is_verify_owed(cwd=main_path))


def test_ship_conflict_aborts_rebase() -> None:
    say("task.py ship: 衝突すればrebase --abortして止まる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "衝突", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-100")

        write(os.path.join(main_path, "shared.txt"), "main側の変更\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "main側でshared.txtを変える")

        write(os.path.join(wt1, "shared.txt"), "wt1側の変更\n")
        git(wt1, "add", "-A")
        result_path = write(os.path.join(wt1, "result.md"), "検証OK\n")
        run_task(wt1, "done", "T-100", "--result-file", result_path)
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "T-100: 完了")

        r = run_task(wt1, "ship")
        check("CONFLICTで終了コード7", r.returncode == 7 and r.stdout.startswith("CONFLICT\t"), r.stdout + r.stderr)
        check("衝突ファイルにshared.txtが出る", "shared.txt" in r.stdout, r.stdout)
        check(
            "rebase --abort済みで作業ツリーがきれい",
            git(wt1, "status", "--porcelain").stdout.strip() == "",
        )
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")


def test_ship_main_dirty_stops() -> None:
    say("task.py ship: 本体が汚れていれば送らずに止まる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "本体汚れ", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-100")
        _claim_work_and_done(wt1, "T-100")

        write(os.path.join(main_path, "dirty.txt"), "汚れ")

        r = run_task(wt1, "ship")
        check("MAIN_DIRTYで終了コード4", r.returncode == 4 and r.stdout.startswith("MAIN_DIRTY\t"), r.stdout + r.stderr)
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")


def test_ship_skips_send_on_main_worktree() -> None:
    say("task.py ship: main の作業ツリーで起こしたときは送る段を飛ばす")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "本体で完結", "todo", "sonnet", "Y", (), BODY))

        r = run_task(main_path, "claim", "T-100")
        check("main上でもclaimできる", r.returncode == 0, r.stdout + r.stderr)
        check("branchはmainのまま", ledger.current_branch(cwd=main_path) == "main")

        _claim_work_and_done(main_path, "T-100")

        r = run_task(main_path, "ship")
        check(
            "SHIPPED main（送る段なし）で返る",
            r.returncode == 0 and r.stdout.startswith("SHIPPED\tmain\t(送る段なし)"),
            r.stdout + r.stderr,
        )
        check("releasedにT-100を含む", "released=T-100" in r.stdout, r.stdout)
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")
        check(
            "着手の印は消える",
            not os.path.isdir(ledger.claim_dir(ledger.ledger_root(cwd=main_path), "T-100")),
        )


def test_ship_race_gives_up_after_three_tries() -> None:
    say("task.py ship: 相手に先を越され続けるとRACEで終わる")
    with tempfile.TemporaryDirectory() as tmp:
        # 相手役は**検証コマンドそのもの**にする。rebase の直後・送る直前に必ず本体が1コミット
        # 進むので `--ff-only` は毎回落ちる。（別スレッドから一定間隔で commit する形は、
        # 機械の混み具合で窓を外すと送れてしまい、落ち方が日によって変わった。）
        racer = os.path.join(tmp, "racer.sh")
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify=f"`sh {racer}`")
        write(
            racer,
            "set -e\n"
            f'count="{os.path.join(tmp, "race-count")}"\n'
            'i=$(cat "$count" 2>/dev/null || echo 0)\n'
            "i=$((i + 1))\n"
            'echo "$i" > "$count"\n'
            f'cd "{main_path}"\n'
            'printf x > "race-$i.txt"\n'
            'git add "race-$i.txt"\n'
            'git commit -q -m "race $i"\n',
        )
        commit_task(main_path, taskfile.Task("T-100", "競争", "todo", "sonnet", "Y", (), BODY))

        run_task(wt1, "claim", "T-100")
        _claim_work_and_done(wt1, "T-100")
        # 1回めから rebase が起きるように、送る前に本体を1つ進めておく（検証は付け替えた回だけ走る）。
        write(os.path.join(main_path, "head-start.txt"), "x")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "本体が先に1つ進む")

        r = run_task(wt1, "ship")

        check("RACEで終了コード9", r.returncode == 9 and r.stdout.strip() == "RACE\t3", r.stdout + r.stderr)
        with open(os.path.join(tmp, "race-count"), encoding="utf-8") as f:
            tries = f.read().strip()
        check("3回とも rebase → 検証 → 送るを試した", tries == "3", tries)
        check(
            "3回試したあとも作業ツリーはきれい（rebaseは完了、送るのだけ失敗）",
            git(wt1, "status", "--porcelain").stdout.strip() == "",
        )
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")


def test_ship_default_branch_leaves_feature_branch() -> None:
    say("task.py ship: 既定の枝設定で本体が main を出していても feature 枝を残さない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="既定")
        commit_task(main_path, taskfile.Task("T-100", "戻り先あり", "todo", "sonnet", "Y", (), BODY))
        commit_task(main_path, taskfile.Task("T-101", "戻り先なし", "todo", "sonnet", "Y", (), BODY))

        r = run_task(wt1, "claim", "T-100")
        check("claim が feature/T-100 を切る", "branch=feature/T-100" in r.stdout, r.stdout + r.stderr)
        _claim_work_and_done(wt1, "T-100")
        r = run_task(wt1, "ship")
        check("SHIPPEDで返る", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)
        check("claim した時点の枝へ戻る", r.stdout.rstrip().endswith("branch=wt1-branch"), r.stdout)
        check("戻った枝は main に追い付いている", git(wt1, "rev-parse", "HEAD").stdout == git(main_path, "rev-parse", "main").stdout)
        check(
            "feature/T-100 は消える",
            git(main_path, "branch", "--list", "feature/T-100").stdout.strip() == "",
        )

        # 戻り先が無い（detached で claim した）ときは main の位置で detached にして枝を消す。
        git(wt2, "checkout", "-q", "--detach", "main")
        r = run_task(wt2, "claim", "T-101")
        check("detached からでも claim できる", r.returncode == 0 and "branch=feature/T-101" in r.stdout, r.stdout + r.stderr)
        _claim_work_and_done(wt2, "T-101", note="2")
        r = run_task(wt2, "ship")
        check("SHIPPEDで返る（detached）", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)
        check("戻れなければ detached と出す", r.stdout.rstrip().endswith("branch=detached"), r.stdout)
        check(
            "feature/T-101 も消える",
            git(main_path, "branch", "--list", "feature/T-101").stdout.strip() == "",
        )
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")


def test_branch_setting_reads_leading_word() -> None:
    say("task.py claim: 旧い節の - ブランチ: は先頭語だけを読み、config.toml の branch は語彙の外なら INVALID")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(
            tmp, branch="切らない。作業ツリーの枝のまま ship で送る", config_filename="CLAUDE.md"
        )
        commit_task(main_path, taskfile.Task("T-100", "先頭語", "todo", "sonnet", "Y", (), BODY))
        r = run_task(wt1, "claim", "T-100")
        check("旧い節は句読点で続いても切らない として読む", r.returncode == 0 and "branch=wt1-branch" in r.stdout,
              r.stdout + r.stderr)

    for where, kwargs in (("旧い節", {"config_filename": "CLAUDE.md"}), ("config.toml", {})):
        with tempfile.TemporaryDirectory() as tmp:
            main_path, wt1, _wt2 = make_repo(tmp, branch="自分で切らない", **kwargs)
            commit_task(main_path, taskfile.Task("T-100", "語彙外", "todo", "sonnet", "Y", (), BODY))
            r = run_task(wt1, "claim", "T-100")
            check(f"{where}: 語彙に無い値は INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID\t"),
                  r.stdout + r.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, branch="切らない。説明")
        r = run_task(wt1, "status")
        check("config.toml は値の後ろの説明文を許さず INVALID", r.returncode == 3 and r.stdout.startswith("INVALID\t.tw/config.toml:"),
              r.stdout + r.stderr)
        write(os.path.join(wt1, ".tw", "config.toml"), 'verify = "なし"\nverify_cmd = "x"\n')
        r = run_task(wt1, "status")
        check("知らないキーは行番号付きで INVALID（終了コード3）",
              r.returncode == 3 and r.stdout.startswith("INVALID\t.tw/config.toml:2:") and "verify_cmd" in r.stdout,
              r.stdout + r.stderr)


def test_branch_setting_missing_is_default() -> None:
    say("task.py claim・ship: branch が無ければ `既定` として枝を切り、ship は送る")
    for reason, kwargs in (
        ("config.toml に branch が無い", {"branch": None}),
        ("旧い節に - ブランチ: 行が無い", {"branch": None, "config_filename": "CLAUDE.md"}),
        ("設定がどこにも無い", {"section": False}),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            main_path, wt1, _wt2 = make_repo(tmp, **kwargs)
            commit_task(main_path, taskfile.Task("T-100", "作業ツリーで", "todo", "sonnet", "Y", (), BODY))
            git(wt1, "merge", "-q", "--ff-only", "main")

            r = run_task(wt1, "claim", "T-100")
            check(f"{reason}: claim は feature/T-100 を切る",
                  r.returncode == 0 and r.stdout.startswith("CLAIMED\tT-100\t") and "branch=feature/T-100" in r.stdout,
                  r.stdout + r.stderr)
            _claim_work_and_done(wt1, "T-100")
            r = run_task(wt1, "ship")
            check(f"{reason}: ship は送る（SHIPPED）", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"),
                  r.stdout + r.stderr)
            check(f"{reason}: 主ブランチが作業のコミットまで進む",
                  git(main_path, "log", "-1", "--format=%s", "main").stdout.strip() == "T-100: 完了")


def test_prune() -> None:
    say("task.py prune: 振り返り済みの done/dropped だけを git rm して stage する")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp, branch="切らない")
        reviewed_body = BODY + "\n## 結果\n\n- 検証: x\n- 振り返り: 兆候なし\n"
        plain_body = BODY + "\n## 結果\n\n- 検証: x\n"
        commit_task(main_path, taskfile.Task("T-102", "振り返りの印が無い dropped", "dropped", "sonnet", "Y", (), plain_body))
        commit_task(main_path, taskfile.Task("T-101", "1件ごとに振り返り済み", "done", "sonnet", "Y", (), reviewed_body))
        commit_task(main_path, taskfile.Task("T-103", "振り返りの印が無い done", "done", "sonnet", "Y", (), plain_body))
        commit_task(main_path, taskfile.Task("T-104", "T-101 を待つ", "todo", "sonnet", "Y", ("T-101",), BODY))
        commit_task(main_path, taskfile.Task("T-105", "印が立っている", "done", "sonnet", "Y", (), reviewed_body))
        ledger.try_claim(ledger.ledger_root(cwd=main_path), "T-105", main_path, "main")

        r = run_task(main_path, "prune")
        check("既定のしきい値（10件）に届かなければ NOTHING", r.returncode == 0 and r.stdout.startswith("NOTHING\t"), r.stdout + r.stderr)
        check("しきい値未満では消さない", os.path.exists(os.path.join(main_path, task_rel(main_path), "T-101.md")))
        r = run_task(main_path, "prune", "--min", "2", "--dry-run")
        check("--min 2 でも1件なら --dry-run も NOTHING", r.returncode == 0 and r.stdout.startswith("NOTHING\t"), r.stdout + r.stderr)

        r = run_task(main_path, "prune", "--min", "1", "--dry-run")
        check("--dry-run は PLAN で1件", r.returncode == 0 and r.stdout.splitlines()[-1] == "PLAN\t1", r.stdout + r.stderr)
        check(
            "対象と理由（振り返りの印が無い T-102・T-103 は対象にならない）",
            r.stdout.splitlines()[:1] == ["PRUNE\tT-101\treviewed"],
            r.stdout,
        )
        check("--dry-run は消さない", os.path.exists(os.path.join(main_path, task_rel(main_path), "T-101.md")))

        write(os.path.join(main_path, "scratch.txt"), "x\n")
        r = run_task(main_path, "prune", "--min", "1")
        check("汚れていれば DIRTY(4)", r.returncode == 4 and r.stdout.strip() == "DIRTY", r.stdout + r.stderr)
        r = run_task(main_path, "prune", "--min", "1", "--dry-run")
        check("--dry-run は汚れていても打てる", r.returncode == 0, r.stdout + r.stderr)
        os.remove(os.path.join(main_path, "scratch.txt"))

        r = run_task(main_path, "prune", "--min", "1")
        check("PRUNED で1件", r.returncode == 0 and r.stdout.splitlines()[-1] == "PRUNED\t1", r.stdout + r.stderr)
        staged = git(main_path, "diff", "--cached", "--name-status").stdout.split()
        check(
            "1件の削除だけが stage される（振り返りの印が無いものは残る）",
            staged == ["D", f"{TASK_REL}/T-101.md"],
            str(staged),
        )
        git(main_path, "commit", "-q", "-m", "振り返り済みのタスクファイルを消す（1件）")
        status = run_task(main_path, "status").stdout
        check("消した依存は解決済みのまま", any(l.startswith("T-104\ttodo") and "\tREADY\t" in l for l in status.splitlines()), status)
        check("status --check が通る", run_task(main_path, "status", "--check").returncode == 0)
        r = run_task(main_path, "prune", "--min", "1")
        check("2回目は NOTHING", r.returncode == 0 and r.stdout.startswith("NOTHING"), r.stdout + r.stderr)


# --- 主ブランチ（`main` 固定をやめた分） -------------------------------------


def test_base_branch_resolution() -> None:
    say("ledger.base_branch: 設定の base_branch → origin/HEAD → main/master/trunk → NoBaseBranch")
    with tempfile.TemporaryDirectory() as tmp:
        ledger.clear_base_branch_cache()
        master_repo, _wt1, _wt2 = make_repo(tmp, base="master")
        check("順3: master しか無ければ master", ledger.base_branch(cwd=master_repo) == "master")

        git(master_repo, "branch", "main")
        ledger.clear_base_branch_cache()
        check("main も出来たら順3では main が先", ledger.base_branch(cwd=master_repo) == "main")
        config = os.path.join(master_repo, ".tw", "config.toml")
        with open(config, encoding="utf-8") as f:
            body = f.read()
        write(config, body + 'base_branch = "master"  # 保護ブランチ\n')
        ledger.clear_base_branch_cache()
        check("順1: base_branch が最優先", ledger.base_branch(cwd=master_repo) == "master")

    with tempfile.TemporaryDirectory() as tmp:
        ledger.clear_base_branch_cache()
        master_repo, _wt1, _wt2 = make_repo(tmp, base="master", config_filename="CLAUDE.md")
        git(master_repo, "branch", "main")
        claude_md = os.path.join(master_repo, "CLAUDE.md")
        with open(claude_md, encoding="utf-8") as f:
            body = f.read()
        write(claude_md, body.replace("- ブランチ:", "- 主ブランチ: `master`（保護ブランチ）\n- ブランチ:"))
        ledger.clear_base_branch_cache()
        check("旧い節の `- 主ブランチ:` 行も順1（バッククォートも落ちる）", ledger.base_branch(cwd=master_repo) == "master")

    with tempfile.TemporaryDirectory() as tmp:
        # 順2: origin/HEAD の枝名。候補の順（main が先）より優先する。
        ledger.clear_base_branch_cache()
        repo, _wt1, _wt2 = make_repo(tmp, base="main")
        git(repo, "branch", "trunk")
        git(repo, "remote", "add", "origin", repo)
        git(repo, "update-ref", "refs/remotes/origin/trunk", "trunk")
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        ledger.clear_base_branch_cache()
        check("順2: origin/HEAD が指す枝を採る", ledger.base_branch(cwd=repo) == "trunk")

    with tempfile.TemporaryDirectory() as tmp:
        # 順4: 候補の枝が無ければ黙って main を作らず INVALID（終了コード3）。
        ledger.clear_base_branch_cache()
        repo, _wt1, _wt2 = make_repo(tmp, base="dev")
        raised = False
        try:
            ledger.base_branch(cwd=repo)
        except ledger.NoBaseBranch:
            raised = True
        check("順4: 決まらなければ NoBaseBranch", raised)
        r = run_task(repo, "status")
        check(
            "task.py は INVALID（終了コード3）で止まる",
            r.returncode == 3 and r.stdout.startswith("INVALID\t"),
            r.stdout + r.stderr,
        )
    ledger.clear_base_branch_cache()


def test_config_file_agents_md_and_conflict() -> None:
    say("設定ファイルの探索（T-020: AGENTS.md → CLAUDE.md の順、両方あれば INVALID）")
    with tempfile.TemporaryDirectory() as tmp:
        # AGENTS.md だけのリポジトリ: claim・ship まで CLAUDE.md と同じ形で通る。
        main_path, wt1, _wt2 = make_repo(tmp, config_filename="AGENTS.md", verify="`echo verified`")
        commit_task(main_path, taskfile.Task("T-100", "AGENTS.md だけ", "todo", "sonnet", "Y", (), BODY))
        r = run_task(wt1, "claim", "T-100")
        check("AGENTS.md だけでも claim できる", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
        result_path = write(os.path.join(tmp, "result.md"), "- 検証: x\n")
        r = run_task(wt1, "done", "T-100", "--result-file", result_path)
        check("done できる", r.returncode == 0, r.stdout + r.stderr)
        git(wt1, "add", "-A")
        git(wt1, "commit", "-q", "-m", "T-100: AGENTS.md だけ")
        r = run_task(wt1, "ship")
        check(
            "ship も AGENTS.md の検証コマンドを読む（verify=none にならない）",
            r.returncode == 0 and r.stdout.startswith("SHIPPED") and "verify=none" not in r.stdout,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        # 両方に「## タスク運用」節があるリポジトリ: どちらが正か機械が決められないので INVALID。
        # `- 主ブランチ:` は無くても main が実在するので順3で決まる（順1・順2が動く前に検査が要る）。
        main_path, _wt1, _wt2 = make_repo(tmp, config_filename="AGENTS.md")
        write(os.path.join(main_path, "CLAUDE.md"), "# y\n\n## タスク運用\n\n- ブランチ: 既定\n")
        r = run_task(main_path, "status")
        check(
            "AGENTS.md と CLAUDE.md の両方に節があれば INVALID（終了コード3）",
            r.returncode == 3 and r.stdout.startswith("INVALID\t") and "AGENTS.md" in r.stdout and "CLAUDE.md" in r.stdout,
            r.stdout + r.stderr,
        )
        write(os.path.join(main_path, ".tw", "config.toml"), 'verify = "なし"\n')
        r = run_task(main_path, "status")
        check(".tw/config.toml があれば旧い節を読まない（両方に節があっても通り、節の old_layout も出ない）",
              r.returncode == 0 and not any(l.startswith("old_layout\t") and ".md" in l for l in r.stdout.splitlines()),
              r.stdout + r.stderr)


def _make_config_doctor_repo(tmp: str, name: str) -> str:
    """`task config-doctor`・`task config` のフィクスチャ用の最小リポジトリ（旧い節の CLAUDE.md）。"""
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    write(
        os.path.join(repo, "develop", "direction.md"),
        "# 未対応の指示メモ\n\n## ユーザーから\n\n## エージェントのドラフト\n",
    )
    write(os.path.join(repo, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
    write(
        os.path.join(repo, "CLAUDE.md"),
        "# x\n\n## タスク運用\n\n- 検証コマンド: なし\n- 整形コマンド: なし\n- ブランチ: 既定\n",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def test_config_doctor() -> None:
    say("task.py config-doctor（T-021: 設定と形式のズレの点検。読むだけ）")

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "ok")
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "OKのリポジトリは終了コード0で検査がOK、旧い節で読んでいれば old_section の行も出す",
            r.returncode == 0
            and any(l.startswith("base_branch\tOK\tmain\t順3") for l in lines)
            and any(l.startswith("config\tOK\tCLAUDE.md") for l in lines)
            and "old_section\tFOUND\tCLAUDE.md" in lines
            and "legacy\tOK" in lines,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        # 旧形式の残り（develop/tasks.json・develop/progress.md）だけがあるケース。
        # 他の3検査はOKでも、残りがあれば全体は「直すものがある」＝終了コード1。
        repo = _make_config_doctor_repo(tmp, "leftover")
        write(os.path.join(repo, "develop", "tasks.json"), "[]\n")
        write(os.path.join(repo, "develop", "progress.md"), "## 未解決\n\n- x\n")
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "旧形式の残りがあれば終了コード1で案内する",
            r.returncode == 1
            and any(
                l.startswith("legacy\tFOUND\t") and "develop/tasks.json" in l and "develop/progress.md" in l
                and l.endswith("tw migrate --dry-run")
                for l in lines
            )
            and any(l.startswith("base_branch\tOK") for l in lines)
            and any(l.startswith("config\tOK") for l in lines),
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        # AGENTS.md と CLAUDE.md の両方に「## タスク運用」節があるケース。
        repo = _make_config_doctor_repo(tmp, "conflict")
        write(os.path.join(repo, "AGENTS.md"), "# a\n\n## タスク運用\n\n- ブランチ: 既定\n")
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "両方に節があれば終了コード3で全検査がINVALIDと言う",
            r.returncode == 3
            and any(l.startswith("base_branch\tINVALID\t") for l in lines)
            and any(l.startswith("config\tINVALID\t") for l in lines)
            and "legacy\tOK" in lines,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "bad-branch")
        write(
            os.path.join(repo, "CLAUDE.md"),
            "# x\n\n## タスク運用\n\n- 検証コマンド: なし\n- ブランチ: 自分で切らない\n- タスクの置き場: develop/task\n",
        )
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            "旧い節のブランチが語彙の外なら config INVALID で、store の検査は続ける",
            r.returncode == 3
            and any(l.startswith("config\tINVALID\tCLAUDE.md\t") for l in lines)
            and "store\tOK\tfiles" in lines,
            r.stdout + r.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "toml")
        write(os.path.join(repo, "CLAUDE.md"), "# x\n")
        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "なし"\n')
        r = run_task(repo, "config-doctor")
        lines = r.stdout.splitlines()
        check(
            ".tw/config.toml なら config は OK で old_section の行は出ない",
            r.returncode == 0
            and any(l.startswith("config\tOK\t.tw/config.toml") for l in lines)
            and not any(l.startswith("old_section") for l in lines),
            r.stdout + r.stderr,
        )
        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "なし"\nfoo = "x"\n')
        r = run_task(repo, "config-doctor")
        check(
            "知らないキーは行番号付きで config INVALID（終了コード3）",
            r.returncode == 3 and any(l.startswith("config\tINVALID\t.tw/config.toml\t") and ":2:" in l
                                      for l in r.stdout.splitlines()),
            r.stdout + r.stderr,
        )


def test_config_command() -> None:
    say("task.py config: 解けた設定を出す")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "legacy")
        r = run_task(repo, "config")
        lines = r.stdout.splitlines()
        check(
            "旧い節なら CONFIG に（旧節）を付け、書いたキーは config・無いキーは default",
            r.returncode == 0
            and lines[:1] == ["CONFIG\tCLAUDE.md（旧節）"]
            and "verify\tなし\tconfig" in lines
            and "branch\t既定\tconfig" in lines
            and "base_branch\tmain\tdefault" in lines
            and "task\tdevelop/task" in lines,
            r.stdout + r.stderr,
        )
        r = run_task(repo, "status")
        check("status は old_layout の行を出す",
              "old_layout\tCLAUDE.md\ttw migrate-layout --dry-run" in r.stdout.splitlines(), r.stdout + r.stderr)

        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "./check.sh"  # 説明\nbranch = "切らない"\n')
        r = run_task(repo, "config")
        lines = r.stdout.splitlines()
        check(
            ".tw/config.toml の値を出す",
            r.returncode == 0
            and lines[:1] == ["CONFIG\t.tw/config.toml"]
            and "verify\t./check.sh\tconfig" in lines
            and "branch\t切らない\tconfig" in lines
            and "format\tなし\tdefault" in lines,
            r.stdout + r.stderr,
        )
        r = run_task(repo, "status")
        check("status は節の old_layout を出さず、develop/ に残った置き場を old_layout で知らせる",
              "old_layout\tCLAUDE.md\ttw migrate-layout --dry-run" not in r.stdout.splitlines()
              and "old_layout\tdevelop/\ttw migrate-layout --dry-run" in r.stdout.splitlines(), r.stdout + r.stderr)

        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "x"\nbranch = 1\n')
        r = run_task(repo, "config")
        check("読めなければ INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID\t.tw/config.toml:2:"),
              r.stdout + r.stderr)


def test_full_cycle_on_master_repo() -> None:
    say("task.py: 主ブランチが master のリポジトリで一式（status→new→claim→done→ship→prune）")
    with tempfile.TemporaryDirectory() as tmp:
        base_path, wt1, wt2 = make_repo(tmp, branch="既定", verify="`echo verified`", base="master")
        commit_task(base_path, taskfile.Task("T-100", "master で一式", "todo", "sonnet", "Y", (), BODY))

        status = run_task(wt1, "status")
        check(
            "status が master の版のタスクを READY で見せる",
            status.returncode == 0 and any(l.startswith("T-100\ttodo") and "\tREADY\t" in l for l in status.stdout.splitlines()),
            status.stdout + status.stderr,
        )

        r = run_task(wt1, "new", "--summary", "master で採番", "--difficulty", "haiku", "--loopable", "Y", "--body-file", body_file(wt1))
        check("new が採番できる（master の履歴を読む）", r.returncode == 0 and r.stdout.startswith("CREATED\tT-101\t"), r.stdout + r.stderr)
        os.remove(os.path.join(wt1, task_rel(wt1), "T-101.md"))
        os.remove(os.path.join(wt1, "body.md"))

        r = run_task(wt1, "claim", "T-100")
        check("claim が master から feature 枝を切る", r.returncode == 0 and "branch=feature/T-100" in r.stdout, r.stdout + r.stderr)
        check("いまの枝は feature/T-100", ledger.current_branch(cwd=wt1) == "feature/T-100")

        _claim_work_and_done(wt1, "T-100")
        r = run_task(wt1, "ship")
        check("ship が master へ送る", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)
        check("戻り先は claim 時点の枝", "branch=wt1-branch" in r.stdout, r.stdout)
        check("feature 枝は消える", git(base_path, "branch", "--list", "feature/T-100").stdout.strip() == "", r.stdout)
        shipped = git(base_path, "show", f"master:{TASK_REL}/T-100.md").stdout
        check("master のタスクファイルが done になる", "status: done" in shipped, shipped)
        check("master に merge commit が無い", _no_merge_commits(base_path, "master").strip() == "")

        # master が先に進んでいる側から送ると、付け替えて検証してから送る。
        commit_task(base_path, taskfile.Task("T-102", "付け替え", "todo", "sonnet", "Y", (), BODY))
        run_task(wt2, "claim", "T-102")
        _claim_work_and_done(wt2, "T-102", note="2")
        write(os.path.join(base_path, "unrelated.txt"), "x")
        git(base_path, "add", "-A")
        git(base_path, "commit", "-q", "-m", "master だけの変更")
        r = run_task(wt2, "ship")
        check(
            "rebase してから送り、付け替えた回だけ検証が走る",
            r.returncode == 0 and "rebased=yes" in r.stdout and "verify=ran" in r.stdout,
            r.stdout + r.stderr,
        )
        log = git(base_path, "log", "--oneline", "master").stdout
        check("両方の変更が master に乗る", "master だけの変更" in log and "T-102: 完了" in log, log)

        # prune も master の版で見る。
        reviewed = BODY + "\n## 結果\n\n- 検証: x\n- 振り返り: 兆候なし\n"
        commit_task(base_path, taskfile.Task("T-104", "振り返り済み", "done", "sonnet", "Y", (), reviewed))
        r = run_task(base_path, "prune", "--min", "1")
        check("prune が1件消して stage する", r.returncode == 0 and r.stdout.splitlines()[-1] == "PRUNED\t1", r.stdout + r.stderr)
        git(base_path, "commit", "-q", "-m", "振り返り済みのタスクファイルを消す（1件）")
        check("status --check が通る", run_task(base_path, "status", "--check").returncode == 0)
        check("master に merge commit が無い", _no_merge_commits(base_path, "master").strip() == "")

    # 本体（master を出している作業ツリー）で枝を切らずに起こしたときは送る段が無い。
    with tempfile.TemporaryDirectory() as tmp:
        base_path, _wt1, _wt2 = make_repo(tmp, branch="切らない", base="master")
        commit_task(base_path, taskfile.Task("T-100", "本体で完結", "todo", "sonnet", "Y", (), BODY))
        run_task(base_path, "claim", "T-100")
        _claim_work_and_done(base_path, "T-100")
        r = run_task(base_path, "ship")
        check(
            "SHIPPED master（送る段なし）で返る",
            r.returncode == 0 and r.stdout.startswith("SHIPPED\tmaster\t(送る段なし)") and "released=T-100" in r.stdout,
            r.stdout + r.stderr,
        )
        r = run_task(_wt1, "ship")
        check(
            "送るものが無い側は NOTHING（枝名を埋め込む）",
            r.returncode == 0 and r.stdout.strip() == "NOTHING\t(master に無いコミットが無い)",
            r.stdout + r.stderr,
        )


def main() -> None:
    only = sys.argv[1:]
    # 長いものから並列に乗せる（後ろに残ると全体がその分延びる）。
    tests = (
        test_ship_skips_preship_verify_when_stamp_matches,
        test_registered_plan,
        test_verify_stamp,
        test_edit_and_plan_check,
        test_handback_guard,
        test_readonly_commands_stay_out_of_git,
        test_handback_guard_other_repo,
        test_worktree_state_dir,
        test_state_dir,
        test_handback_guard_parallel_steps,
        test_flow_records_and_metrics,
        test_edit_deps,
        test_full_cycle_on_master_repo,
        test_handback_guard_step,
        test_verify_check_passes_base_with_preship,
        test_commit_guard,
        test_branch_setting_missing_is_default,
        test_migrate_layout,
        test_agent_scoped_guard,
        test_ship_runs_preship_verify_when_work_changes_tree,
        test_edit_section,
        test_verify_refuses_unplanned_work,
        test_root_setting,
        test_direct_mark,
        test_readonly_git,
        test_lap,
        test_verify_check_reports_base,
        test_ship_default_branch_leaves_feature_branch,
        test_verify_runs_format_first,
        test_prune,
        test_verify_folds_base_before_check,
        test_readonly_git_stops_writers,
        test_worktree_tree_sees_same_size_edit_after_second_boundary,
        test_verify_keeps_failed_logs,
        test_ship_forces_verify_after_verify_failed_without_new_rebase,
        test_config_file_agents_md_and_conflict,
        test_ship_rebases_when_main_advances,
        test_ship_race_gives_up_after_three_tries,
        test_done_commits_since_claim,
        test_retrospect_due,
        test_branch_setting_reads_leading_word,
        test_verify_conflict_before_check,
        test_done_single_worktree,
        test_ship_fast_forward,
        test_verify_uses_preship_command_for_stamp,
        test_config_doctor,
        test_claim_and_release_single_worktree,
        test_ship_stale_verify_owed_does_not_block_nothing_or_main_worktree,
        test_ship_conflict_aborts_rebase,
        test_ship_verify_failed_keeps_full_log_in_order,
        test_ship_skips_send_on_main_worktree,
        test_body_frame_check,
        test_ship_main_dirty_stops,
        test_base_branch_resolution,
        test_new_and_status_single_worktree,
        test_claim_race,
        test_config_command,
        test_migrate_dry_run_then_real,
        test_new_parallel_no_collision,
        test_new_avoids_history_ids,
        test_migrate_keeps_preamble_when_sections_empty,
        test_metrics_stages,
        test_new_missing_and_legacy,
        test_migrate_stops_on_doing,
        test_migrate_dirty_worktree_stops,
        test_migrate_nothing_when_no_tasks_json,
        test_taskfile_parse,
        test_legacy_convert_task,
        test_taskfile_set_result_section,
        test_metrics_stages_parallel_steps,
    )
    tests = tuple(t for t in tests if not only or t.__name__ in only)
    if not tests:
        print("該当するテストが無い: " + ", ".join(only))
        raise SystemExit(1)
    with ThreadPoolExecutor(max_workers=min(len(tests), max(1, (os.cpu_count() or 2) // 2))) as pool:
        outputs = list(pool.map(_run_one, tests))
    for lines in outputs:
        print("\n".join(lines))
    print()
    if failures:
        print(f"FAILED {len(failures)}件: " + ", ".join(failures))
        raise SystemExit(1)
    print("すべて通った")


if __name__ == "__main__":
    main()
