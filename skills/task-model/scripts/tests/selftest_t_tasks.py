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


def _batch_file(dirpath: str, entries: list[dict]) -> str:
    return write(os.path.join(dirpath, "batch.json"), json.dumps(entries, ensure_ascii=False))


def _batch_entry(dirpath: str, summary: str, **extra) -> dict:
    name = f"body-{summary}.md"
    body_file(dirpath, name)
    return {"summary": summary, "difficulty": "sonnet", "loopable": "Y", "body_file": name, **extra}


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


def candidate_body(color: str, viewpoint: str = "道案内") -> str:
    return f"## 目的・背景\n\n- 出どころ: 振り返り: T-001\n- 観点: {color} {viewpoint}\n\n## 介入\n\n{INTERVENTION}\n"


def test_hold_requires_unlock_condition() -> None:
    say("hold にする3つの入口（new --hold・edit --status hold・hold）は ## 解除の条件 が無い・空・「なし」・4行以上なら終了コード2")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        bad_unlocks = (
            ("節が無い", task_body()),
            ("「なし」", task_body(unlock="なし")),
            ("4行", task_body(unlock="a\nb\nc\nd")),
        )

        def new_hold(body: str) -> subprocess.CompletedProcess:
            return run_task(wt1, "new", "--summary", "保留", "--difficulty", "sonnet", "--loopable", "Y", "--hold",
                            "--body-file", "-", stdin=body)

        before = issue_count(wt1)
        for label, body in bad_unlocks:
            r = new_hold(body)
            check(f"new --hold: {label}は終了コード2で作らない",
                  r.returncode == 2 and "解除の条件" in r.stderr and issue_count(wt1) == before, r.stdout + r.stderr)
        r = new_hold(task_body(unlock="\n\n"))
        check("new --hold: 空の節は終了コード2", r.returncode == 2 and issue_count(wt1) == before, r.stdout + r.stderr)
        r = new_hold(task_body(unlock=UNLOCK, intervention=INTERVENTION))
        created = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""
        check("new --hold: 節があれば通る", created != "", r.stdout + r.stderr)
        text = shown(wt1, created)
        check("tw show は ## 解除の条件 を本文のとおり ## 参考情報 の後・## 介入 の前に出す",
              UNLOCK in text and text.index("## 参考情報") < text.index("## 解除の条件") < text.index("## 介入"), text)

        commit_task(main_path, taskfile.Task("T-300", "節なし", "todo", "sonnet", "Y", (), task_body()))
        r = run_task(wt1, "edit", "T-300", "--status", "hold")
        check("edit --status hold: 節が無ければ終了コード2で todo のまま",
              r.returncode == 2 and "解除の条件" in r.stderr and "\nstatus: todo\n" in shown(wt1, "T-300"), r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-300", "--status", "hold", "--section", "解除の条件", "--body-file", "-", stdin="なし\n")
        check("edit --status hold: 同じ呼び出しで書く節が「なし」でも終了コード2",
              r.returncode == 2 and "\nstatus: todo\n" in shown(wt1, "T-300"), r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-300", "--status", "hold", "--section", "解除の条件", "--body-file", "-", stdin=UNLOCK + "\n")
        check("edit --status hold: 同じ呼び出しで節を書けば通る", r.returncode == 0 and "\nstatus: hold\n" in shown(wt1, "T-300"),
              r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-300", "--status", "todo")
        check("todo に戻しても節は残る", r.returncode == 0 and UNLOCK in shown(wt1, "T-300"), r.stdout + r.stderr)
        r = run_task(wt1, "edit", "T-300", "--status", "hold")
        check("edit --status hold: 節が残っていれば節を渡さなくても通る", r.returncode == 0, r.stdout + r.stderr)

        commit_task(main_path, taskfile.Task("T-301", "節なし2", "todo", "sonnet", "Y", (), task_body()))
        r = run_task(wt1, "claim", "T-301")
        check("着手する", r.returncode == 0, r.stdout + r.stderr)
        r = run_task(wt1, "hold", "T-301", "--reason-file", "-", stdin="- 論点\n")
        check("hold: 節が無く --unlock-file も無ければ終了コード2で、印も外さない",
              r.returncode == 2 and "解除の条件" in r.stderr and "RELEASED" not in r.stdout
              and "\nstatus: todo\n" in shown(wt1, "T-301"), r.stdout + r.stderr)
        empty = write(os.path.join(tmp, "empty.md"), "なし\n")
        r = run_task(wt1, "hold", "T-301", "--reason-file", "-", "--unlock-file", empty, stdin="- 論点\n")
        check("hold: --unlock-file が「なし」なら終了コード2で書かない",
              r.returncode == 2 and "RELEASED" not in r.stdout and "\nstatus: todo\n" in shown(wt1, "T-301"), r.stdout + r.stderr)
        good = write(os.path.join(tmp, "unlock.md"), "- 利用者が A か B かを決める\n- 2026-11-07 以降\n")
        r = run_task(wt1, "hold", "T-301", "--reason-file", "-", "--unlock-file", good, stdin="- 論点\n")
        body = shown_body(wt1, "T-301")
        check("hold: --unlock-file の中身が節になり、論点は ## 注意 に足される",
              r.returncode == 0 and "HELD\tT-301" in r.stdout and "\nstatus: hold\n" in shown(wt1, "T-301")
              and "- 2026-11-07 以降" in taskfile.section_content(body, taskfile.UNLOCK_HEADING)
              and "- 論点" in taskfile.section_content(body, taskfile.CAUTION_HEADING), r.stdout + r.stderr + body)
        r = run_task(wt1, "hold", "T-301", "--reason-file", "-", "--unlock-file", good, stdin="- 論点2\n")
        check("hold: すでに hold に --unlock-file を渡し直しても節は2つにならない",
              r.returncode == 0 and shown(wt1, "T-301").count("## 解除の条件") == 1, r.stdout + r.stderr)


TESTS = (
    test_hold_requires_unlock_condition,
    test_done_commits_since_claim,
    test_done_single_worktree,
    test_done_required_lines,
    test_claim_and_release_single_worktree,
    test_hold,
    test_edit_refuses_loopable_n_while_claimed,
    test_new_and_status_single_worktree,
    test_draft_register_and_refuse_claim,
    test_new_parallel_no_collision,
)


if __name__ == "__main__":
    refuse_direct_run()

