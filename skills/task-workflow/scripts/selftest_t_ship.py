from __future__ import annotations

import os
import shutil
import tempfile

from selftest_support import check, git, say, weight, write  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
from selftest_fixtures import BODY, TASK_REL, _claim_work_and_done, body_file, commit_task, make_repo, run_task, task_rel  # noqa: E402


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


def test_land() -> None:
    say("task.py land: 主ブランチへ ff-only で合流して入ったことを確かめてから、作業ツリーと枝を消す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)

        def head(path: str) -> str:
            return git(path, "rev-parse", "HEAD").stdout.strip()

        def branches() -> list[str]:
            return git(main_path, "branch", "--format=%(refname:short)").stdout.split()

        write(os.path.join(wt1, "a.txt"), "a\n")
        git(wt1, "add", "a.txt")
        git(wt1, "commit", "-q", "-m", "wt1")
        write(os.path.join(main_path, "b.txt"), "b\n")
        git(main_path, "add", "b.txt")
        git(main_path, "commit", "-q", "-m", "main を進める")
        main_before = head(main_path)
        r = run_task(main_path, "land", "wt1-branch")
        check(
            "ff できない（main に入っていない）枝は NOT_LANDED(4)",
            r.returncode == 4 and r.stdout.startswith("NOT_LANDED\twt1-branch\t"),
            r.stdout + r.stderr,
        )
        check(
            "NOT_LANDED では main も作業ツリーも枝も変えない",
            head(main_path) == main_before and os.path.isdir(wt1) and "wt1-branch" in branches(),
        )

        git(wt2, "merge", "-q", "--ff-only", "main")
        write(os.path.join(wt2, "c.txt"), "c\n")
        git(wt2, "add", "c.txt")
        git(wt2, "commit", "-q", "-m", "wt2")
        write(os.path.join(wt2, "scratch.txt"), "x\n")
        r = run_task(main_path, "land", "wt2-branch")
        check(
            "作業ツリーが汚れていれば NOT_LANDED(4)",
            r.returncode == 4 and r.stdout.startswith("NOT_LANDED\twt2-branch\t"),
            r.stdout + r.stderr,
        )
        check("汚れていれば合流もしない", head(main_path) == main_before and os.path.isdir(wt2))
        os.remove(os.path.join(wt2, "scratch.txt"))

        write(os.path.join(main_path, "shared.txt"), "line1\nlocal\n")
        r = run_task(main_path, "land", "wt2-branch")
        check(
            "主ブランチを出している作業ツリーが汚れていれば、合流で重ならなくても NOT_LANDED(4)",
            r.returncode == 4 and r.stdout.startswith("NOT_LANDED\twt2-branch\t"),
            r.stdout + r.stderr,
        )
        check(
            "本体が汚れていれば合流も片付けもしない",
            head(main_path) == main_before and os.path.isdir(wt2) and "wt2-branch" in branches(),
        )
        git(main_path, "checkout", "-q", "--", "shared.txt")

        wt3 = os.path.join(tmp, "wt3")
        git(main_path, "worktree", "add", "-q", "-b", "wt3-branch", wt3, "main")
        shutil.rmtree(wt3)
        r = run_task(main_path, "land", "wt3-branch")
        check(
            "枝の作業ツリーのディレクトリが無ければ NOT_LANDED(4)",
            r.returncode == 4 and r.stdout.startswith("NOT_LANDED\twt3-branch\t"),
            r.stdout + r.stderr,
        )
        check("ディレクトリが無ければ枝を消さない", "wt3-branch" in branches())

        r = run_task(main_path, "land", "main")
        check("主ブランチを渡すと終了コード2", r.returncode == 2, r.stdout + r.stderr)
        r = run_task(main_path, "land", "no-such-branch")
        check("無い枝を渡すと終了コード2", r.returncode == 2, r.stdout + r.stderr)

        write(os.path.join(wt2, ".tw", "local", ".gitignore"), "*\n")
        write(os.path.join(wt2, ".tw", "local", "task-verify-stamp"), "x\n")
        wt2_head = head(wt2)
        r = run_task(wt2, "land", "wt2-branch")
        lines = r.stdout.splitlines()
        check(
            "入った枝は LANDED・REMOVED・DELETED（消す作業ツリーの中から打っても、無視されたファイルがあっても）",
            r.returncode == 0
            and lines[:1] == [f"LANDED\twt2-branch\tmain\t{wt2_head}"]
            and lines[1:] == [f"REMOVED\t{os.path.realpath(wt2)}", "DELETED\twt2-branch"],
            r.stdout + r.stderr,
        )
        check(
            "main が枝の先端まで進み、作業ツリーと枝が消える",
            head(main_path) == wt2_head and not os.path.exists(wt2) and "wt2-branch" not in branches(),
        )
        check("ほかの枝の作業ツリーは残る", os.path.isdir(wt1) and "wt1-branch" in branches())


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


@weight(8)
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


@weight(12)
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


TESTS = (
    test_full_cycle_on_master_repo,
    test_branch_setting_missing_is_default,
    test_ship_default_branch_leaves_feature_branch,
    test_ship_forces_verify_after_verify_failed_without_new_rebase,
    test_ship_rebases_when_main_advances,
    test_ship_race_gives_up_after_three_tries,
    test_branch_setting_reads_leading_word,
    test_ship_fast_forward,
    test_ship_stale_verify_owed_does_not_block_nothing_or_main_worktree,
    test_ship_conflict_aborts_rebase,
    test_ship_verify_failed_keeps_full_log_in_order,
    test_ship_skips_send_on_main_worktree,
    test_ship_main_dirty_stops,
    test_land,
    test_base_branch_resolution,
)
