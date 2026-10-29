from __future__ import annotations

import re
import os
import subprocess
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

from selftest_support import RESULT_FULL, check, git, refuse_direct_run, say, weight, write  # noqa: E402
import stamps  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_fixtures import BODY, commit_task, make_repo, readonly_git, run_handback_guard, run_task  # noqa: E402


@weight(3)
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

        r = run_task(wt1, "verify", "T-111")
        check("着手していない ID を渡した verify は NOT_OWNER（終了コード4）で、検証コマンドを打たない",
              r.returncode == 4 and r.stdout.strip() == "NOT_OWNER\tT-111" and "verified" not in r.stdout,
              r.stdout + r.stderr)

        r = run_task(wt2, "verify")
        check("別の作業ツリーの着手には掛からない", r.returncode == 0
              and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)

        run_task(wt1, "edit", "T-110", "--after-work", "--body-file", "-", stdin=planned)
        r = run_task(wt1, "verify")
        check("書けば打つ", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)

        r = run_task(wt1, "verify", "T-110")
        check("着手中の ID を渡した verify は引数なしと同じに打つ", r.returncode == 0
              and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)

        r = run_task(wt2, "claim", "T-111")
        write(os.path.join(wt2, "work.txt"), "x\n")
        result_path = write(os.path.join(tmp, "result.md"), RESULT_FULL)
        run_task(wt2, "done", "T-111", "--result-file", result_path)
        r = run_task(wt2, "verify")
        check("done の後は空でも打つ", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)


VERIFY_SCRIPT = (
    'echo "3 pass"\n'
    'if [ -f ignored/touch ]; then echo x > out.txt; fi\n'
    'if [ -f ignored/fail ]; then echo fail; exit 1; fi\n'
)


@weight(22)
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


COUNTING_VERIFY_SCRIPT = 'echo x >> ../verify-count.log\necho "3 pass"\n'


NOTES = "a\nb\nc\nd\ne\n"


def _fold_repo(tmp: str, preship: str | None = None, planned: bool = True) -> tuple[str, str]:
    """`(本体, 作業ツリー1)`。wt1 が T-120 を claim 済みで、検証コマンドは打たれるたびに `verify-count.log` へ1行足す。"""
    main_path, wt1, _wt2 = make_repo(tmp, branch="切らない", verify="`sh ../verify-count.sh`", preship=preship)
    write(os.path.join(tmp, "verify-count.sh"), COUNTING_VERIFY_SCRIPT)
    write(os.path.join(main_path, "notes.txt"), NOTES)
    git(main_path, "add", "notes.txt")
    git(main_path, "commit", "-q", "-m", "notes.txt を足す")
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


def _count_lines(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return len(f.read().splitlines())


def _verify_count(tmp: str) -> int:
    return _count_lines(os.path.join(tmp, "verify-count.log"))


def _commit_and_ship(wt: str, tmp: str, *paths: str) -> subprocess.CompletedProcess:
    result_path = write(os.path.join(tmp, "result.md"), RESULT_FULL)
    r = run_task(wt, "done", "T-120", "--result-file", result_path)
    check("done は COMMITS_SINCE_CLAIM に main のコミットを数えない",
          r.stdout.strip() == "DONE\tT-120\tbeads:t-120\tship で閉じる", r.stdout + r.stderr)
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

        r = _commit_and_ship(wt1, tmp, "notes.txt", "work.txt")
        check("ship は付け替えずに送る（verify=skipped）", r.returncode == 0 and r.stdout.startswith("SHIPPED\t")
              and "rebased=no" in r.stdout and "verify=skipped" in r.stdout, r.stdout + r.stderr)
        check("検証コマンドは1回だけ", _verify_count(tmp) == 1, str(_verify_count(tmp)))
        check("送る前の検証コマンドの行が無ければ preship は出ない", "preship=" not in r.stdout, r.stdout)


def readonly_subcommands() -> list[str]:
    """tw-reference.md の「`tw` コマンドの参照」の表で、すること欄に「読むだけ」と書かれた行のサブコマンド名。"""
    with open(os.path.join(HERE, "..", "..", "tw-reference.md"), encoding="utf-8") as f:
        text = f.read()
    section = ("\n" + text).split("\n## `tw` コマンドの参照\n", 1)[1].split("\n## ", 1)[0]
    names = []
    for row in section.splitlines():
        cells = re.split(r" (?<!\\)\| ", row)
        m = re.match(r"\| `([a-z-]+(?: --[a-z-]+)*)", cells[0])
        if m and len(cells) > 1 and "読むだけ" in cells[1]:
            names.append(m.group(1))
    return names


def _object_files(main_path: str) -> list[str]:
    top = os.path.join(main_path, ".git", "objects")
    return sorted(os.path.join(d, f) for d, _dirs, files in os.walk(top) for f in files)


def test_verify_uses_preship_command_for_stamp() -> None:
    say("task.py verify: 送る前の検証コマンドの行があっても、検証コマンド（verify）を打ち、その控えで verify-check が判定する")
    with tempfile.TemporaryDirectory() as tmp:
        write(os.path.join(tmp, "preship.sh"), "echo x >> ../preship-count.log\n")
        _main, wt1 = _fold_repo(tmp, preship="`sh ../preship.sh`")
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = run_task(wt1, "verify")
        check("verify が通る", r.returncode == 0 and r.stdout.startswith("VERIFIED\t"), r.stdout + r.stderr)
        check("検証コマンドが1回", _verify_count(tmp) == 1, str(_verify_count(tmp)))
        check("送る前の検証コマンドは打たれない", _count_lines(os.path.join(tmp, "preship-count.log")) == 0)
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


@weight(24)
def test_ship_skips_preship_verify_when_stamp_matches() -> None:
    say("task.py ship: 付け替えの無い回は送る前の検証を打ち（控えが verify なので飛ばない）、付け替えた回は verify を打って送る前の検証を打たない")

    def prepare(tmp: str) -> tuple[str, str]:
        write(os.path.join(tmp, "preship.sh"), "echo x >> ../preship-count.log\n")
        return _fold_repo(tmp, preship="`sh ../preship.sh`")

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        r = _commit_work_and_ship(wt1, "work.txt")
        check("verify の控えがあっても付け替えの無い回は preship=ran で送る",
              r.returncode == 0 and r.stdout.startswith("SHIPPED\t") and "preship=ran" in r.stdout
              and "rebased=no" in r.stdout and "verify=skipped" in r.stdout, r.stdout + r.stderr)
        check("送る前の検証コマンドは ship の1回", _preship_count(tmp) == 1, str(_preship_count(tmp)))
        check("検証コマンドは verify の1回だけ", _verify_count(tmp) == 1, str(_verify_count(tmp)))

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "work.txt")
        stamps.write_verify_stamp(stamps.content_key("sh ../preship.sh", cwd=wt1), cwd=wt1)
        r = _commit_work_and_ship(wt1, "work.txt")
        check("送る前の検証コマンドで控えた同じ中身は preship=skipped で送る",
              r.returncode == 0 and "preship=skipped" in r.stdout and "rebased=no" in r.stdout, r.stdout + r.stderr)
        check("どちらのコマンドも打たれない", _preship_count(tmp) == 0 and _verify_count(tmp) == 0)

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        r = _commit_work_and_ship(wt1, "work.txt")
        check("控えが無ければ preship=ran", "preship=ran" in r.stdout, r.stdout + r.stderr)
        check("送る前の検証コマンドは1回", _preship_count(tmp) == 1, str(_preship_count(tmp)))
        check("検証コマンドは打たれない", _verify_count(tmp) == 0, str(_verify_count(tmp)))

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        _advance_main(main_path, NOTES, extra="other.txt")
        r = _commit_work_and_ship(wt1, "work.txt")
        check("付け替えた回は rebased=yes・verify=ran・preship=skipped",
              "rebased=yes" in r.stdout and "verify=ran" in r.stdout and "preship=skipped" in r.stdout,
              r.stdout + r.stderr)
        check("送る前の検証コマンドは打たれない", _preship_count(tmp) == 0, str(_preship_count(tmp)))
        check("検証コマンドは ship でもう1回（合計2回）", _verify_count(tmp) == 2, str(_verify_count(tmp)))

    with tempfile.TemporaryDirectory() as tmp:
        _main, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        stamps.mark_verify_owed("sh ../preship.sh", cwd=wt1)
        r = _commit_work_and_ship(wt1, "work.txt")
        check("借りがあれば付け替えが無くても preship=ran", "preship=ran" in r.stdout, r.stdout + r.stderr)
        check("借りの回の送る前の検証コマンドは1回", _preship_count(tmp) == 1, str(_preship_count(tmp)))
        check("借りの印は消える", not stamps.is_verify_owed(cwd=wt1))

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1 = prepare(tmp)
        write(os.path.join(wt1, "work.txt"), "x\n")
        run_task(wt1, "verify")
        stamps.mark_verify_owed("sh ../preship.sh", cwd=wt1)
        _advance_main(main_path, NOTES, extra="other.txt")
        r = _commit_work_and_ship(wt1, "work.txt")
        check("借りと付け替えが重なっても rebased=yes・preship=ran",
              "rebased=yes" in r.stdout and "preship=ran" in r.stdout, r.stdout + r.stderr)
        check("送る前の検証コマンドが1回、検証コマンドは verify の1回だけ",
              _preship_count(tmp) == 1 and _verify_count(tmp) == 1, f"{_preship_count(tmp)} {_verify_count(tmp)}")
        check("借りの印は消える（重なる回）", not stamps.is_verify_owed(cwd=wt1))


@weight(5)
def test_ship_runs_preship_verify_when_work_changes_tree() -> None:
    say("task.py ship: 送る前の検証コマンドの行があり、控えのあとに作業の木が変わったなら、主ブランチへ入れる直前に打つ")
    for passes in (True, False):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "preship.sh"), "echo x >> ../preship-count.log\n[ ! -f ../preship-fail ]\n")
            main_path, wt1 = _fold_repo(tmp, preship="`sh ../preship.sh`")
            write(os.path.join(wt1, "work.txt"), "x\n")
            r = run_task(wt1, "verify")
            check("verify は VERIFIED（控えた）", r.returncode == 0 and "VERIFIED\t" in r.stdout, r.stdout + r.stderr)
            check("verify は検証コマンドを1回打つ", _verify_count(tmp) == 1, str(_verify_count(tmp)))
            check("verify は送る前の検証コマンドを打たない", _preship_count(tmp) == 0)
            r = run_task(wt1, "verify-check")
            check("控えは VERIFIED_SAME", r.stdout.startswith("VERIFIED_SAME\t"), r.stdout)

            write(os.path.join(wt1, "work.txt"), "y\n")
            if not passes:
                write(os.path.join(tmp, "preship-fail"), "")
            head_before = git(main_path, "rev-parse", "HEAD").stdout.strip()
            r = _commit_and_ship(wt1, tmp, "work.txt")
            preship_count = _count_lines(os.path.join(tmp, "preship-count.log"))
            check("送る前の検証コマンドは ship で1回打たれる", preship_count == 1, str(preship_count))
            check("検証コマンドは ship で重ねて打たれない", _verify_count(tmp) == 1, str(_verify_count(tmp)))
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
    say("stamps.worktree_tree: index の書き込みと同じ秒にした同サイズの書き換えを、秒をまたいでから測っても拾う")
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
        check("書き換えた木は HEAD の木と違う", stamps.worktree_tree(tmp) != head_tree)


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

        r = _commit_and_ship(wt1, tmp, "work.txt")
        check("ship は verify=skipped", r.returncode == 0 and "verify=skipped" in r.stdout, r.stdout + r.stderr)
        check("検証コマンドは委譲先の1回と受け入れの1回", _verify_count(tmp) == 2, str(_verify_count(tmp)))


@weight(10)
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


TESTS = (
    test_ship_skips_preship_verify_when_stamp_matches,
    test_verify_stamp,
    test_verify_check_passes_base_with_preship,
    test_ship_runs_preship_verify_when_work_changes_tree,
    test_verify_refuses_unplanned_work,
    test_verify_check_reports_base,
    test_verify_folds_base_before_check,
    test_worktree_tree_sees_same_size_edit_after_second_boundary,
    test_verify_conflict_before_check,
    test_verify_uses_preship_command_for_stamp,
)


if __name__ == "__main__":
    refuse_direct_run()

