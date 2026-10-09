from __future__ import annotations

import os
import shutil
import tempfile

from selftest_support import check, git, say, weight, write  # noqa: E402
import ledger  # noqa: E402
import legacy  # noqa: E402
import taskfile  # noqa: E402
from selftest_fixtures import BODY, TASK_REL, _make_legacy_repo, commit_task, make_repo, run_task  # noqa: E402


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
        check("migrate後の develop/ の置き場は旧配置なので status は OLD_LAYOUT で止まり、migrate-layout へ導く",
              r.returncode == 5 and r.stdout == "OLD_LAYOUT\ttw migrate-layout --dry-run\n", r.stdout + r.stderr)


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


MIGRATE_LAYOUT_SECTION = (
    "# x\n\n## タスク運用\n\n前置きの文章。\n\n"
    "- 検証コマンド: `./check.sh`（構文とテスト）\n  全段は --full\n"
    "- 整形コマンド: なし\n- ブランチ: 既定\n- タスクの置き場: develop/task\n\nあとがき。\n"
)


@weight(7)
def test_migrate_layout() -> None:
    say("task.py migrate-layout: 旧配置を .tw/ へ移す（git add まで）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        commit_task(main_path, taskfile.Task("T-100", "移すもの", "todo", "sonnet", "Y", (), BODY))
        run_task(wt1, "claim", "T-100")
        os.makedirs(os.path.join(main_path, "develop"), exist_ok=True)
        git(main_path, "mv", ".tw/direction.md", "develop/direction.md")
        git(main_path, "mv", ".tw/task", "develop/task")
        git(main_path, "rm", "-q", "-r", "-f", ".tw")
        write(os.path.join(main_path, "CLAUDE.md"), MIGRATE_LAYOUT_SECTION)
        write(os.path.join(main_path, "develop", "draft", "x.md"), "- **x**\n")
        write(os.path.join(main_path, "develop", "notes.txt"), "x\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "旧配置")
        git(wt1, "merge", "-q", "--ff-only", "main")
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

        r = run_task(main_path, "migrate-layout", "--dry-run")
        check("ほかの作業ツリーの着手の印があれば BUSY（終了コード4）",
              r.returncode == 4 and r.stdout.startswith("BUSY\tT-100\t") and r.stdout.rstrip().endswith("wt1"),
              r.stdout + r.stderr)
        r = run_task(wt1, "migrate-layout", "--dry-run")
        check("自分の作業ツリーの印は BUSY にしない", r.returncode == 0 and r.stdout.splitlines()[-1] == "PLAN",
              r.stdout + r.stderr)
        shutil.rmtree(ledger.claim_dir(ledger.ledger_root(cwd=wt1), "T-100"))

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


TESTS = (
    test_migrate_layout,
    test_migrate_dry_run_then_real,
    test_migrate_keeps_preamble_when_sections_empty,
    test_migrate_stops_on_doing,
    test_migrate_dirty_worktree_stops,
    test_migrate_nothing_when_no_tasks_json,
    test_legacy_convert_task,
)
