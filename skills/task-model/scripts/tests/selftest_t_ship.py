from __future__ import annotations

import os
import shutil
import tempfile

from selftest_support import RESULT_FULL, check, git, refuse_direct_run, say, weight, write  # noqa: E402
import beads  # noqa: E402
import fold  # noqa: E402
import gitinfo  # noqa: E402
import ledger  # noqa: E402
import stamps  # noqa: E402
import taskfile  # noqa: E402
from selftest_fixtures import BODY, FILE_PROTOCOL, FILE_PROTOCOL_ENV, _claim_work_and_done, advance_submodule, body_file, commit_task, flow_rows, make_repo, make_submodule_repo, metadata, run_task, set_metadata  # noqa: E402


def _no_merge_commits(repo: str, base: str = "main") -> str:
    return git(repo, "log", "--oneline", "--merges", base).stdout


def _closed(repo: str, task_id: str) -> bool:
    """`tw show` で `task_id` が done になり、作業ツリーの着手の控えも消えているか。"""
    shown = run_task(repo, "show", task_id).stdout
    return "\nstatus: done\n" in shown and task_id not in ledger.open_claims(cwd=repo)


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

        check("タスクが done で閉じ、着手の印は消える", _closed(wt1, "T-100"), run_task(wt1, "show", "T-100").stdout)


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
            stamps.is_verify_owed(cwd=wt1),
        )
        check("rebaseはabortされず作業ツリーはきれい", git(wt1, "status", "--porcelain").stdout.strip() == "")

        write(flag, "x")  # 検証コマンドが通る状態に直す。main はこれ以上進めない（付け替えは起きない）。
        r2 = run_task(wt1, "ship")
        check("打ち直しはSHIPPEDで返る", r2.returncode == 0 and r2.stdout.startswith("SHIPPED\t"), r2.stdout + r2.stderr)
        check("rebasedはno（付け替えは起きていない）", "rebased=no" in r2.stdout, r2.stdout)
        check("それでも検証はran（借りを飛ばさない）", "verify=ran" in r2.stdout, r2.stdout)
        check("検証の借りの印は消える", not stamps.is_verify_owed(cwd=wt1))
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")

        r3 = run_task(wt1, "ship")
        check("送るものが無ければ今までどおりNOTHING", r3.returncode == 0 and r3.stdout.startswith("NOTHING\t"), r3.stdout + r3.stderr)


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
        result_path = write(os.path.join(wt1, "result.md"), RESULT_FULL)
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


def test_land_without_base_worktree() -> None:
    say("task.py land: 主ブランチを出している作業ツリーが無ければ何も合流せず消さない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        write(os.path.join(wt1, "a.txt"), "a\n")
        git(wt1, "add", "a.txt")
        git(wt1, "commit", "-q", "-m", "wt1")
        git(main_path, "checkout", "-q", "--detach")
        main_before = git(main_path, "rev-parse", "main").stdout.strip()
        r = run_task(wt1, "land", "wt1-branch")
        check(
            "主ブランチを出している作業ツリーが無ければ NOT_LANDED(4)",
            r.returncode == 4 and r.stdout.startswith("NOT_LANDED\twt1-branch\t"),
            r.stdout + r.stderr,
        )
        check(
            "何も合流せず作業ツリーも枝も残る",
            git(main_path, "rev-parse", "main").stdout.strip() == main_before
            and os.path.isdir(wt1)
            and "wt1-branch" in git(main_path, "branch", "--format=%(refname:short)").stdout.split(),
        )


def _commit(path: str, name: str, text: str, message: str) -> str:
    write(os.path.join(path, name), text)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", message)
    return git(path, "rev-parse", "HEAD").stdout.strip()


def test_fold_committed() -> None:
    say("fold.fold_committed: コミットのある枝へ主ブランチをマージコミットで取り込み、衝突なら何も書き換えない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        head = _commit(wt1, "a.txt", "a\n", "枝の作業")
        r = fold.fold_committed(wt1, "main")
        check("主ブランチが進んでいなければ UP_TO_DATE", r.kind == "UP_TO_DATE", repr(r))
        check("UP_TO_DATE では HEAD が変わらない", git(wt1, "rev-parse", "HEAD").stdout.strip() == head)

        base_head = _commit(main_path, "b.txt", "b\n", "主ブランチを進める")
        r = fold.fold_committed(wt1, "main")
        new_head = git(wt1, "rev-parse", "HEAD").stdout.strip()
        parents = git(wt1, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:]
        check(
            "進んでいれば FOLDED（元の HEAD と主ブランチを親に持つマージコミット）",
            r.kind == "FOLDED" and r.old_base == head and r.new_base == new_head and parents == [head, base_head],
            repr(r) + repr(parents),
        )
        check(
            "取り込んだあと作業ツリーは汚れず、主ブランチのファイルがある",
            git(wt1, "status", "--porcelain").stdout == "" and os.path.isfile(os.path.join(wt1, "b.txt")),
        )

        git(wt2, "merge", "-q", "--ff-only", "main")
        wt2_head = _commit(wt2, "shared.txt", "line1\nbranch\n", "枝が同じ行を変える")
        main_head = _commit(main_path, "shared.txt", "line1\nmain\n", "主ブランチが同じ行を変える")
        r = fold.fold_committed(wt2, "main")
        check("衝突すれば CONFLICT と衝突したファイル", r.kind == "CONFLICT" and r.conflict_files == ("shared.txt",), repr(r))
        check(
            "CONFLICT では枝・主ブランチ・作業ツリー・index を変えない",
            git(wt2, "rev-parse", "HEAD").stdout.strip() == wt2_head
            and git(main_path, "rev-parse", "main").stdout.strip() == main_head
            and git(wt2, "status", "--porcelain").stdout == ""
            and open(os.path.join(wt2, "shared.txt"), encoding="utf-8").read() == "line1\nbranch\n",
        )


def _counting_verify(tmp: str, exit_code: int = 0) -> tuple[str, str]:
    """`(make_repo に渡す verify, 打たれた回数を数えるファイル)`。"""
    count = os.path.join(tmp, "verify-count")
    script = write(os.path.join(tmp, "verify.sh"), f'echo run >> "{count}"\nexit {exit_code}\n')
    return f"`sh {script}`", count


def _lines_of(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def _verify_runs(count: str) -> int:
    return len(_lines_of(count))


def _branches(main_path: str) -> list[str]:
    return git(main_path, "branch", "--format=%(refname:short)").stdout.split()


def test_land_fold_verifies_at_most_once() -> None:
    say("task.py land --fold: 取り込みが要らず控えが今の中身と同じなら検証を省き、控えが無い・鍵が違えば1回打つ")
    with tempfile.TemporaryDirectory() as tmp:
        verify, count = _counting_verify(tmp)
        main_path, wt1, wt2 = make_repo(tmp, verify=verify)

        head = _commit(wt1, "a.txt", "a\n", "枝の作業")
        run_task(wt1, "verify")
        r = run_task(wt1, "land", "wt1-branch", "--fold")
        lines = r.stdout.splitlines()
        check(
            "控えが同じなら VERIFIED_SAME …verify=skipped を出して検証を打たず、LANDED・REMOVED・DELETED",
            r.returncode == 0
            and lines[0].startswith("VERIFIED_SAME\t")
            and lines[0].endswith("\tverify=skipped")
            and lines[1] == f"LANDED\twt1-branch\tmain\t{head}"
            and lines[2:] == [f"REMOVED\t{os.path.realpath(wt1)}", "DELETED\twt1-branch"],
            r.stdout + r.stderr,
        )
        check("検証は控えを取った1回だけ", _verify_runs(count) == 1, str(_verify_runs(count)))

        git(wt2, "merge", "-q", "--ff-only", "main")
        head = _commit(wt2, "b.txt", "b\n", "枝の作業")
        r = run_task(wt2, "land", "wt2-branch", "--fold")
        check(
            "控えが無ければ取り込みの行なしで検証を1回打ってから LANDED",
            r.returncode == 0
            and not any(line.startswith(("FOLDED", "VERIFIED_SAME")) for line in r.stdout.splitlines())
            and any(line.startswith("VERIFIED\t") for line in r.stdout.splitlines())
            and f"LANDED\twt2-branch\tmain\t{head}" in r.stdout.splitlines()
            and _verify_runs(count) == 2,
            r.stdout + r.stderr,
        )

        wt3 = os.path.join(tmp, "wt3")
        git(main_path, "worktree", "add", "-q", "-b", "wt3-branch", wt3, "main")
        _commit(wt3, "c.txt", "c\n", "枝の作業")
        run_task(wt3, "verify")
        head = _commit(wt3, "c.txt", "c2\n", "控えのあとの作業")
        r = run_task(wt3, "land", "wt3-branch", "--fold")
        check(
            "控えの鍵が今と違えば検証を1回打ってから LANDED",
            r.returncode == 0
            and any(line.startswith("VERIFIED\t") for line in r.stdout.splitlines())
            and f"LANDED\twt3-branch\tmain\t{head}" in r.stdout.splitlines()
            and _verify_runs(count) == 4,
            r.stdout + r.stderr,
        )


def test_land_fold_advanced() -> None:
    say("task.py land --fold: 主ブランチが進んでいれば取り込み → 検証1回 → 合流")
    with tempfile.TemporaryDirectory() as tmp:
        verify, count = _counting_verify(tmp)
        main_path, wt1, _wt2 = make_repo(tmp, verify=verify)
        head = _commit(wt1, "a.txt", "a\n", "枝の作業")
        run_task(wt1, "verify")
        base_head = _commit(main_path, "b.txt", "b\n", "主ブランチを進める")
        r = run_task(wt1, "land", "wt1-branch", "--fold")
        lines = r.stdout.splitlines()
        landed = git(main_path, "rev-parse", "main").stdout.strip()
        check(
            "FOLDED・検証・LANDED の順に出る",
            r.returncode == 0
            and lines[0].startswith(f"FOLDED\twt1-branch\t{head}..")
            and any(line.startswith("VERIFIED\t") for line in lines)
            and f"LANDED\twt1-branch\tmain\t{landed}" in lines
            and lines[-2:] == [f"REMOVED\t{os.path.realpath(wt1)}", "DELETED\twt1-branch"],
            r.stdout + r.stderr,
        )
        check("控えがあっても取り込んだら検証を1回打つ", _verify_runs(count) == 2, str(_verify_runs(count)))
        parents = git(main_path, "rev-list", "--parents", "-n", "1", "main").stdout.split()[1:]
        check("主ブランチの先端は枝と主ブランチを親に持つマージコミット", parents == [head, base_head], repr(parents))


def test_land_fold_conflict() -> None:
    say("task.py land --fold: 取り込みが衝突すれば何も書き換えず検証も打たずに止まる")
    with tempfile.TemporaryDirectory() as tmp:
        verify, count = _counting_verify(tmp)
        main_path, wt1, _wt2 = make_repo(tmp, verify=verify)
        head = _commit(wt1, "shared.txt", "line1\nbranch\n", "枝が同じ行を変える")
        main_head = _commit(main_path, "shared.txt", "line1\nmain\n", "主ブランチが同じ行を変える")
        r = run_task(wt1, "land", "wt1-branch", "--fold")
        lines = r.stdout.splitlines()
        check(
            "CONFLICT のあと NOT_LANDED（終了コード4）と続きの打ち方",
            r.returncode == 4
            and lines[0] == "CONFLICT\tshared.txt"
            and lines[1].startswith("NOT_LANDED\twt1-branch\t")
            and "tw land wt1-branch --fold" in lines[1],
            r.stdout + r.stderr,
        )
        check(
            "主ブランチ・枝・作業ツリーを変えず、検証も打たない",
            git(main_path, "rev-parse", "main").stdout.strip() == main_head
            and git(wt1, "rev-parse", "HEAD").stdout.strip() == head
            and git(wt1, "status", "--porcelain").stdout == ""
            and os.path.isdir(wt1)
            and "wt1-branch" in _branches(main_path)
            and _verify_runs(count) == 0,
        )


def test_land_fold_verify_fails() -> None:
    say("task.py land --fold: 検証が落ちれば合流も片付けもせず止まる")
    with tempfile.TemporaryDirectory() as tmp:
        verify, count = _counting_verify(tmp, exit_code=1)
        main_path, wt1, _wt2 = make_repo(tmp, verify=verify)
        _commit(wt1, "a.txt", "a\n", "枝の作業")
        main_head = _commit(main_path, "b.txt", "b\n", "主ブランチを進める")
        r = run_task(wt1, "land", "wt1-branch", "--fold")
        lines = r.stdout.splitlines()
        check(
            "FOLDED・VERIFY_NOT_PASSED のあと NOT_LANDED（終了コード4）",
            r.returncode == 4
            and lines[0].startswith("FOLDED\twt1-branch\t")
            and any(line.startswith("VERIFY_NOT_PASSED\t") for line in lines)
            and lines[-1].startswith("NOT_LANDED\twt1-branch\t")
            and "tw land wt1-branch --fold" in lines[-1],
            r.stdout + r.stderr,
        )
        check(
            "主ブランチは変わらず、作業ツリーと枝は残り、検証は1回",
            git(main_path, "rev-parse", "main").stdout.strip() == main_head
            and os.path.isdir(wt1)
            and "wt1-branch" in _branches(main_path)
            and _verify_runs(count) == 1,
        )


def test_land_fold_without_verify_command() -> None:
    say("task.py land --fold: 検証コマンドが無ければ何も変えずに止まる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        head = _commit(wt1, "a.txt", "a\n", "枝の作業")
        main_head = _commit(main_path, "b.txt", "b\n", "主ブランチを進める")
        r = run_task(wt1, "land", "wt1-branch", "--fold")
        check(
            "NOT_LANDED（終了コード4）",
            r.returncode == 4 and r.stdout.startswith("NOT_LANDED\twt1-branch\t"),
            r.stdout + r.stderr,
        )
        check(
            "主ブランチも枝も作業ツリーも変えない",
            git(main_path, "rev-parse", "main").stdout.strip() == main_head
            and git(wt1, "rev-parse", "HEAD").stdout.strip() == head
            and os.path.isdir(wt1),
        )


def test_ship_skips_send_on_main_worktree() -> None:
    say("task.py ship: main の作業ツリーで起こしたときは送る段を飛ばす")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _wt2 = make_repo(tmp, branch="切らない")
        commit_task(main_path, taskfile.Task("T-100", "本体で完結", "todo", "sonnet", "Y", (), BODY))

        r = run_task(main_path, "claim", "T-100")
        check("main上でもclaimできる", r.returncode == 0, r.stdout + r.stderr)
        check("branchはmainのまま", gitinfo.current_branch(cwd=main_path) == "main")

        _claim_work_and_done(main_path, "T-100")

        r = run_task(main_path, "ship")
        check(
            "SHIPPED main（送る段なし）で返る",
            r.returncode == 0 and r.stdout.startswith("SHIPPED\tmain\t(送る段なし)"),
            r.stdout + r.stderr,
        )
        check("releasedにT-100を含む", "released=T-100" in r.stdout, r.stdout)
        check("main にmerge commitが無い", _no_merge_commits(main_path).strip() == "")
        check("タスクが done で閉じ、着手の印は消える", _closed(main_path, "T-100"), run_task(main_path, "show", "T-100").stdout)


def _shipped_range(repo: str, task_id: str) -> str:
    return str(metadata(repo, task_id).get(beads.SHIPPED_KEY) or "")


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


def test_base_branch_resolution() -> None:
    say("gitinfo.base_branch: 設定の base_branch → origin/HEAD → main/master/trunk → NoBaseBranch")
    with tempfile.TemporaryDirectory() as tmp:
        gitinfo.clear_base_branch_cache()
        master_repo, _wt1, _wt2 = make_repo(tmp, base="master")
        check("順3: master しか無ければ master", gitinfo.base_branch(cwd=master_repo) == "master")

        git(master_repo, "branch", "main")
        gitinfo.clear_base_branch_cache()
        check("main も出来たら順3では main が先", gitinfo.base_branch(cwd=master_repo) == "main")
        config = os.path.join(master_repo, ".tw", "config.toml")
        with open(config, encoding="utf-8") as f:
            body = f.read()
        write(config, body + 'base_branch = "master"  # 保護ブランチ\n')
        gitinfo.clear_base_branch_cache()
        check("順1: base_branch が最優先", gitinfo.base_branch(cwd=master_repo) == "master")

    with tempfile.TemporaryDirectory() as tmp:
        # 順2: origin/HEAD の枝名。候補の順（main が先）より優先する。
        gitinfo.clear_base_branch_cache()
        repo, _wt1, _wt2 = make_repo(tmp, base="main")
        git(repo, "branch", "trunk")
        git(repo, "remote", "add", "origin", repo)
        git(repo, "update-ref", "refs/remotes/origin/trunk", "trunk")
        git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        gitinfo.clear_base_branch_cache()
        check("順2: origin/HEAD が指す枝を採る", gitinfo.base_branch(cwd=repo) == "trunk")

    with tempfile.TemporaryDirectory() as tmp:
        # 順4: 候補の枝が無ければ黙って main を作らず INVALID（終了コード3）。
        gitinfo.clear_base_branch_cache()
        repo, _wt1, _wt2 = make_repo(tmp, base="dev")
        raised = False
        try:
            gitinfo.base_branch(cwd=repo)
        except gitinfo.NoBaseBranch:
            raised = True
        check("順4: 決まらなければ NoBaseBranch", raised)
        r = run_task(repo, "status")
        check(
            "task.py は INVALID（終了コード3）で止まる",
            r.returncode == 3 and r.stdout.startswith("INVALID\t"),
            r.stdout + r.stderr,
        )
    gitinfo.clear_base_branch_cache()


@weight(12)
def test_full_cycle_on_master_repo() -> None:
    say("task.py: 主ブランチが master のリポジトリで一式（status→new→claim→done→ship）")
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
        os.remove(os.path.join(wt1, "body.md"))

        r = run_task(wt1, "claim", "T-100")
        check("claim が master から feature 枝を切る", r.returncode == 0 and "branch=feature/T-100" in r.stdout, r.stdout + r.stderr)
        check("いまの枝は feature/T-100", gitinfo.current_branch(cwd=wt1) == "feature/T-100")

        _claim_work_and_done(wt1, "T-100")
        r = run_task(wt1, "ship")
        check("ship が master へ送る", r.returncode == 0 and r.stdout.startswith("SHIPPED\t"), r.stdout + r.stderr)
        check("戻り先は claim 時点の枝", "branch=wt1-branch" in r.stdout, r.stdout)
        check("feature 枝は消える", git(base_path, "branch", "--list", "feature/T-100").stdout.strip() == "", r.stdout)
        check("タスクが done で閉じる", _closed(wt1, "T-100"), run_task(wt1, "show", "T-100").stdout)
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

        r = run_task(base_path, "status", "--check")
        check("status --check が通る", r.returncode == 0, r.stdout + r.stderr)
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
            r.returncode == 0 and r.stdout.splitlines()[0] == "NOTHING\t(master に無いコミットが無い)",
            r.stdout + r.stderr,
        )


TESTS = (
    test_full_cycle_on_master_repo,
    test_ship_forces_verify_after_verify_failed_without_new_rebase,
    test_ship_rebases_when_main_advances,
    test_ship_race_gives_up_after_three_tries,
    test_ship_fast_forward,
    test_ship_conflict_aborts_rebase,
    test_ship_skips_send_on_main_worktree,
    test_ship_main_dirty_stops,
    test_land,
    test_land_without_base_worktree,
    test_fold_committed,
    test_land_fold_verifies_at_most_once,
    test_land_fold_advanced,
    test_land_fold_conflict,
    test_land_fold_verify_fails,
    test_land_fold_without_verify_command,
    test_base_branch_resolution,
)


if __name__ == "__main__":
    refuse_direct_run()

