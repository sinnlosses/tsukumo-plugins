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
        check("本文の先頭の行がシェルで書かない案内", body.startswith("`sed -i`・") and "`Edit`・`Write`" in body.splitlines()[0], body)
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
        check("書き直しの依頼は、返却に tw plan-phases --through 計画 の PHASES の行を貼らせる",
              "tw plan-phases" in r.stdout and "--through 計画" in r.stdout and "PHASES" in r.stdout, r.stdout)
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


def test_brief_implementer_report_name() -> None:
    say("task.py brief implementer: 計画に report で始まる名のパスがあれば標準エラーに WARN_REPORT_NAME を出し、標準出力は変えない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, "### 1. 書く\n`docs/report-x.md` を置く\n")
        r = _brief(wt1)
        check("出す: 行は1つで、BRIEF の行と依頼文は変わらない",
              r.returncode == 0 and r.stderr.count("WARN_REPORT_NAME\tdocs/report-x.md\t") == 1
              and _head(r) == "BRIEF\tT-100\timplementer\t常に,計画どおり" and "{{" not in r.stdout, r.stdout + r.stderr)
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n`docs/notes.md` を置く\n")
        r = _brief(wt1)
        check("出さない: report で始まらない名だけ", r.returncode == 0 and "WARN_REPORT_NAME" not in r.stderr,
              r.stdout + r.stderr)
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n`docs/report-x.ts` を置く\n")
        r = _brief(wt1)
        check("出さない: report で始まる .ts だけ", r.returncode == 0 and "WARN_REPORT_NAME" not in r.stderr,
              r.stdout + r.stderr)


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
    say("task.py brief implementer: 目視 は --visual だけで決め、完了条件に「目視」の語があるか無いかでは変わらない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, "### 1. 書く\nx\n", acceptance="目視ありの依頼文を生成する")
        r = _brief(wt1)
        check("完了条件に「目視」の語があっても --visual が無ければ付かない",
              r.returncode == 0 and "目視" not in _head(r), r.stdout + r.stderr)
        r = _brief(wt1, "--visual")
        check("語があって --visual なら付く", r.returncode == 0 and _head(r).endswith(",目視"), r.stdout + r.stderr)
        check("--visual の依頼文は段の番号つきの tw pause と NOT_LAST_STEP を言い、置換語が残らない",
              "`tw pause <ID> <段の番号>`" in r.stdout and "NOT_LAST_STEP" in r.stdout and "{{" not in r.stdout,
              r.stdout + r.stderr)
        r = _brief(wt1, "--rewrite", "--visual")
        check("書き直しに --visual を付けても INVALID にならず、NOT_LAST_STEP の旨が出る",
              r.returncode == 0 and "NOT_LAST_STEP" in r.stdout and "{{" not in r.stdout, r.stdout + r.stderr)
        commit_task(main_path, taskfile.Task("T-101", "見た目なし", "todo", "sonnet", "Y", (), BODY))
        run_task(wt2, "claim", "T-101")
        run_task(wt2, "edit", "T-101", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\nx\n")
        r = run_task(wt2, "brief", "implementer", "T-101")
        check("語が無く --visual も無ければ付かない", r.returncode == 0 and "目視" not in _head(r), r.stdout + r.stderr)
        r = run_task(wt2, "brief", "implementer", "T-101", "--visual")
        check("語が無くても --visual なら付く", r.returncode == 0 and _head(r).endswith(",目視"), r.stdout + r.stderr)


def test_brief_implementer_every_combination() -> None:
    say("task.py brief implementer: 書き直し・目視・別のリポジトリ・並列の段の16組すべてで、置換語が埋まる")
    parallel_plan = task_body([
        ("書く", "- 触るファイル: `src/a.py`"),
        ("文書", "- 前の段: なし\n- 触るファイル: `docs/`"),
        ("合わせる", "x"),
    ]).split("## やること\n", 1)[1].split("\n## ", 1)[0]
    for other_repo in (False, True):
        with tempfile.TemporaryDirectory() as tmp:
            main_path, wt1, _ = make_repo(tmp, branch="切らない")
            _claim_with_plan(wt1, main_path, None)
            tail = f"\n### 作業先\n- `{_work_repo(os.path.join(tmp, 'brief-work-repo'))}`\n" if other_repo else ""
            for parallel in (False, True):
                plan = parallel_plan if parallel else "### 1. 書く\nx\n"
                run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin=plan + tail)
                for rewrite in (False, True):
                    for visual in (False, True):
                        args = ["--rewrite"] * rewrite + ["--visual"] * visual + (["--step", "2"] if parallel and not rewrite else [])
                        r = _brief(wt1, *args)
                        combo = f"書き直し={rewrite} 目視={visual} 別のリポジトリ={other_repo} 並列={parallel}"
                        check(f"{combo}: 終了コード0で INVALID も {{{{ も残らない",
                              r.returncode == 0 and "INVALID" not in r.stdout and "{{" not in r.stdout,
                              r.stdout + r.stderr)


def _acceptor(wt: str, *extra: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return run_task(wt, "brief", "acceptor", "T-100", *extra, stdin=stdin)


TREE_SHA = re.compile(r"[0-9a-f]{40}")


def test_brief_acceptor_review() -> None:
    say("task.py brief acceptor: tw accept の出力から判定と木を読み、差分のコマンド・完了条件・規約のファイル・friction の行を埋める")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, "### 1. 書く\nx\n", acceptance="1. 通る\n2. 動く")
        write(os.path.join(wt1, "CLAUDE.md"), "# 規約\n")
        write(os.path.join(wt1, "code.py"), "x = 1\n")
        r = _acceptor(wt1, "--accept-file", "-", stdin=run_task(wt1, "accept", "T-100").stdout)
        tree = r.stdout.split("1回目の木の SHA（2・3回目の差分の起点）: ", 1)[-1].split("\n", 1)[0]
        check("REVIEW は 常に・同じリポジトリ・レビュー で、SNAPSHOT の木を出す",
              r.returncode == 0 and _head(r) == "BRIEF\tT-100\tacceptor\t常に,同じリポジトリ,レビュー"
              and TREE_SHA.fullmatch(tree) is not None, r.stdout + r.stderr)
        body = r.stdout.split("\n", 1)[1]
        check("差分のコマンド・完了条件の本文・判定・CLAUDE.md の絶対パスが入り、{{ が残らない",
              "`git diff HEAD`" in body and "1. 通る\n2. 動く" in body and "`REVIEW\\tcode\\t" in body
              and f"`{os.path.join(os.path.realpath(wt1), 'CLAUDE.md')}`" in body and "{{" not in body, body)
        check("acceptor-brief.md の絶対パスが実在する", _paths_exist(body), body)
        check("申し送りが無ければ friction は missing", "`friction` の値" in body and ": `missing`" in body, body)

        selfreport = "段1でテストが1回落ちた（a.py）"
        markers = (selfreport, "何かを変える", "根拠: x", "自己点検: 外れ", "- 規約: なし")
        run_task(wt1, "relay", "T-100", "実装", "--body-file", "-",
                 stdin=f"- 実装 段1: friction log: なし\n- 黄: {selfreport}\n- 自己点検: 外れ\n"
                       "- ドラフト: 何かを変える\n  - 根拠: x\n- 規約: なし\n")
        r = _acceptor(wt1, "--accept-file", "-", stdin="SKIP\tdocs-only\n")
        body = r.stdout
        check("SKIP は レビュー を付けない", r.returncode == 0 and _head(r) == "BRIEF\tT-100\tacceptor\t常に,同じリポジトリ", body)
        check("依頼文に委譲先の自己申告（friction log の行・自己点検・ドラフト・規約の行）が含まれず、friction は some の1語だけ",
              not any(m in body for m in markers) and ": `some`" in body, body)

        for stdin, want in (("- 実装 段1: friction log: なし\n- 実装 段2: friction log: なし\n- 規約: なし\n", "none"),
                            ("- 実装 計画: 段は2つ\n", "missing"),
                            ("- 規約: 委譲なし\n", "none")):
            run_task(wt1, "relay", "T-100", "実装", "--body-file", "-", stdin=stdin)
            body = _acceptor(wt1, "--accept-file", "-", stdin="SKIP\tdocs-only\n").stdout
            check(f"friction の値は {want}（行は渡さない）", f"（`tw brief` が決めた1語。委譲先の報告は渡さない）: `{want}`" in body
                  and "委譲なし" not in body, f"{stdin!r} -> {body}")


def test_brief_acceptor_marks_and_verify_ways() -> None:
    say("task.py brief acceptor: accept の MARK の行を判定の値に印として載せ、計画の `- 確かめ方:` を依頼文に渡す（無ければ 無い）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, "### 1. 書く\n- 満たす完了条件: 1, 2\n- 確かめ方: 1 は `pytest -q`、2 は台本A\n", acceptance="1. 通る\n2. 動く")
        accept = "REVIEW\tcode\t2\nMARK\ttest-skipped\ttests/test_a.py\nMARK\texpected-only\ttest/e2e/expected/a.html\n"
        body = _acceptor(wt1, "--accept-file", "-", stdin=accept).stdout
        check("MARK の行ごとに 印 が判定の値に付く",
              "`REVIEW\\tcode\\t2` 印: `MARK\\ttest-skipped\\ttests/test_a.py` 印: `MARK\\texpected-only\\ttest/e2e/expected/a.html`" in body, body)
        check("計画の確かめ方の本文が依頼文に載り、{{ が残らない",
              "計画の確かめ方" in body and ": 1 は `pytest -q`、2 は台本A\n" in body and "{{" not in body, body)
        body = _acceptor(wt1, "--accept-file", "-", stdin="REVIEW\tcode\t2\n").stdout
        check("印が無ければ判定の値だけ", "`REVIEW\\tcode\\t2`\n" in body and "印:" not in body, body)
        run_task(wt1, "edit", "T-100", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\n- 満たす完了条件: 1, 2\n")
        body = _acceptor(wt1, "--accept-file", "-", stdin="SKIP\tdocs-only\n").stdout
        check("確かめ方の欄が無ければ 無い", "（完了条件の行ごと。打って結果を判定に使う）: 無い\n" in body, body)


def test_brief_acceptor_other_repo() -> None:
    say("task.py brief acceptor: 作業先が別のリポジトリなら、作業先の作業ツリーと主ブランチからの範囲で差分を出し、木もそこで取る")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        work = _work_repo(os.path.join(tmp, "accept-work-repo"))
        _claim_with_plan(wt1, main_path, f"### 1. 書く\nx\n\n### 作業先\n- `{work}`\n")
        r = _acceptor(wt1, "--accept-file", "-", stdin="REVIEW\tcode\t1\n")
        check("作業先の作業ツリーが無ければ INVALID", r.returncode == 3 and r.stdout.startswith("INVALID\t"), r.stdout + r.stderr)
        tree = os.path.join(tmp, "t100")
        git(work, "worktree", "add", "-q", "-b", "t-100", tree, "main")
        write(os.path.join(tree, "AGENTS.md"), "# 作業先の規約\n")
        r = _acceptor(wt1, "--accept-file", "-", stdin="REVIEW\tcode\t1\n")
        check("別のリポジトリ と レビュー、範囲つきの差分と作業先の作業ツリー・その木・その規約のファイル",
              r.returncode == 0 and _head(r) == "BRIEF\tT-100\tacceptor\t常に,別のリポジトリ,レビュー"
              and "`git diff main...t-100`" in r.stdout and "`main..t-100`" in r.stdout
              and f"作業ツリー: {os.path.realpath(tree)}" in r.stdout
              and f"`{os.path.join(os.path.realpath(tree), 'AGENTS.md')}`" in r.stdout
              and TREE_SHA.search(r.stdout) is not None, r.stdout + r.stderr)


def test_brief_acceptor_visual() -> None:
    say("task.py brief acceptor: 目視 は --image か --visual だけで決め、完了条件の「目視」の語では変わらない。--visual-wait は 目視待ち だけ")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, "### 1. 書く\nx\n", acceptance="1. 通る\n2. 目視ありの依頼文を生成する")
        plain = "1. 画面が崩れない\n2. 通る"
        commit_task(main_path, taskfile.Task("T-101", "語なし", "todo", "sonnet", "Y", (), task_body(acceptance=plain)))
        run_task(wt2, "claim", "T-101")
        run_task(wt2, "edit", "T-101", "--section", "やること", "--body-file", "-", stdin="### 1. 書く\nx\n")
        cases = ((wt1, "T-100", "語あり", "1. 通る\n2. 目視ありの依頼文を生成する"), (wt2, "T-101", "語なし", plain))
        for tree, task_id, label, criteria in cases:
            def acceptor(*extra: str, stdin: str | None = None) -> subprocess.CompletedProcess:
                return run_task(tree, "brief", "acceptor", task_id, *extra, stdin=stdin)

            r = acceptor("--accept-file", "-", stdin="SKIP\tdocs-only\n")
            check(f"{label}: 旗が無ければ 目視 は付かない", r.returncode == 0 and "目視" not in _head(r)
                  and "報告に画像のパスが無い" not in r.stdout, r.stdout + r.stderr)
            r = acceptor("--accept-file", "-", "--image", "/tmp/a-375.png", "--image", "/tmp/a-1280.png", stdin="SKIP\tdocs-only\n")
            check(f"{label}: --image なら 目視 と画像の2行", r.returncode == 0 and _head(r).endswith(",目視")
                  and "- `/tmp/a-375.png`\n- `/tmp/a-1280.png`" in r.stdout, r.stdout + r.stderr)
            r = acceptor("--accept-file", "-", "--visual", stdin="SKIP\tdocs-only\n")
            check(f"{label}: --visual だけなら 目視 で、画像が無い旨を出す", r.returncode == 0 and _head(r).endswith(",目視")
                  and "報告に画像のパスが無い" in r.stdout, r.stdout + r.stderr)
            r = acceptor("--visual-wait", "--image", "/tmp/a-375.png")
            check(f"{label}: --visual-wait は 目視待ち だけで、完了条件の本文をそのまま添える",
                  r.returncode == 0 and _head(r) == f"BRIEF\t{task_id}\tacceptor\t目視待ち"
                  and criteria in r.stdout and "{{" not in r.stdout, r.stdout + r.stderr)
        r = _acceptor(wt1, "--visual-wait")
        check("--visual-wait に画像が無ければ終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = _acceptor(wt1, "--visual-wait", "--visual", "--image", "/tmp/a-375.png")
        check("--visual は --visual-wait と打てない（終了コード2）", r.returncode == 2, r.stdout + r.stderr)
        r = _acceptor(wt1, "--round", "2", "--since", "abc", "--visual")
        check("--visual は --round と打てない（終了コード2）", r.returncode == 2, r.stdout + r.stderr)


def test_brief_acceptor_round() -> None:
    say("task.py brief acceptor: --round は前の木といまの木を添え、BRIEF の行の末尾にいまの木を出す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, branch="切らない")
        _claim_with_plan(wt1, main_path, "### 1. 書く\nx\n")
        r = _acceptor(wt1, "--round", "2", "--since", "1" * 40)
        now = _head(r).split("\t")[-1]
        check("2回目以降 だけで、末尾のいまの木が本文の git diff に入る",
              r.returncode == 0 and _head(r).startswith("BRIEF\tT-100\tacceptor\t2回目以降\t") and TREE_SHA.fullmatch(now) is not None
              and f"git diff {'1' * 40} {now}" in r.stdout and "2回目の受け入れ" in r.stdout, r.stdout + r.stderr)
        r = _acceptor(wt1, "--round", "2")
        check("--round に --since が無ければ終了コード2", r.returncode == 2, r.stdout + r.stderr)


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
        r = _acceptor(wt1, "--accept-file", "-", stdin="SKIP\tdocs-only\n")
        check("受け入れ役も MISSING", r.returncode == 6 and r.stdout.startswith("MISSING\tbrief-acceptor"), r.stdout + r.stderr)
        r = _acceptor(wt1)
        check("受け入れ役に --accept-file が無ければ終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = _acceptor(wt1, "--accept-file", "-", stdin="FORMATTED\n")
        check("--accept-file に判定の行が無ければ終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = _brief(wt1, "--image", "/tmp/a.png")
        check("実装役に受け入れ役の旗は終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = _acceptor(wt1, "--step", "1")
        check("受け入れ役に実装役の旗は終了コード2", r.returncode == 2, r.stdout + r.stderr)


TESTS = (
    test_brief_implementer_planned,
    test_brief_implementer_rewrite,
    test_brief_implementer_report_name,
    test_brief_implementer_other_repo,
    test_brief_implementer_parallel,
    test_brief_implementer_visual,
    test_brief_implementer_every_combination,
    test_brief_acceptor_review,
    test_brief_acceptor_marks_and_verify_ways,
    test_brief_acceptor_other_repo,
    test_brief_acceptor_visual,
    test_brief_acceptor_round,
    test_brief_refusals,
)


if __name__ == "__main__":
    refuse_direct_run()
