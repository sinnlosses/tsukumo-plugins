"""`task.py` の Beads との往復のうち、トラッカーを使わない部分（印・設定・着手の取り合い・1サイクル・ID）の自己テスト（`selftest_beads.py` の部品）。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(HERE, "..", "core"))
sys.path.insert(1, os.path.join(HERE, "..", "tw"))

import beads  # noqa: E402
import init  # noqa: E402
import layout  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
import tracker  # noqa: E402
import tracker_claims  # noqa: E402
import tracker_github  # noqa: E402
from selftest_b_fakegithub import github  # noqa: E402
from selftest_b_support import (  # noqa: E402
    HELD_PLANNED_BODY, PLANNED_BODY, bd, claim_snapshots, env, make_repo, new, new_unplanned, num, rows, tail_line, work_and_done,
)
from selftest_fixtures import run_task, start_task  # noqa: E402
from selftest_support import RESULT_FULL, check, git, local, refuse_direct_run, say, write  # noqa: E402

MATERIAL_PY = os.path.join(HERE, "..", "..", "..", "retrospect", "scripts", "material.py")


def test_difficulty_loopable_metadata() -> None:
    say("difficulty・loopable: new・edit は metadata に書き、読みは metadata だけで、取り込みで消えたら戻す")
    import tracker_claims  # noqa: PLC0415

    def issue(status: str = "open", labels: tuple[str, ...] = (), metadata: dict | None = None) -> beads.Issue:
        return beads.Issue("t-005", "題", status, labels, (), None, None, None, {"metadata": metadata or {}})

    both = issue(labels=("difficulty:opus", "loopable:N"), metadata={"difficulty": "haiku", "loopable": "Y"})
    task, _ = beads.to_task(both)
    check("metadata と label が両方あっても metadata を読む",
          task is not None and (task.difficulty, task.loopable) == ("haiku", "Y"), str(task))
    check("label だけの課題は振り分け前（label は読まない）",
          beads.to_task(issue(labels=("difficulty:opus", "loopable:N"))) == (None, None))
    task, reason = beads.to_task(issue(labels=("loopable:N",), metadata={"difficulty": "sonnet"}))
    check("片方だけ metadata で、もう片方が無ければ INVALID（label は読まない）",
          task is None and reason is not None and "loopable" in reason, str(reason))
    task, reason = beads.to_task(issue(metadata={"difficulty": "gpt", "loopable": "Y"}))
    check("表に無い値は INVALID", task is None and reason is not None and "difficulty" in reason, str(reason))
    check("どちらも無ければ振り分け前", beads.to_task(issue()) == (None, None))

    marks = {"difficulty": "opus", "loopable": "N"}
    check("取り込みで metadata が消えたら2キーを戻す",
          tracker_claims._claim_restore_args(issue(metadata=marks), issue(), None, None)
          == ["--set-metadata", "difficulty=opus", "--set-metadata", "loopable=N"])
    check("片方だけ消えたらその1つだけ戻す",
          tracker_claims._claim_restore_args(issue(metadata=marks), issue(metadata={"difficulty": "opus"}), None, None)
          == ["--set-metadata", "loopable=N"])
    closed_marks = {**marks, "task_branch": "b", "draft": "Y", "from_draft": "道案内"}
    check("閉じた課題は difficulty・loopable とドラフトの出自（draft・from_draft）だけ戻す",
          tracker_claims._claim_restore_args(issue(metadata=closed_marks), issue("closed"), None, None)
          == ["--set-metadata", "difficulty=opus", "--set-metadata", "loopable=N",
              "--set-metadata", "draft=Y", "--set-metadata", "from_draft=道案内"])

    with tempfile.TemporaryDirectory() as tmp:
        main_path, _, _ = make_repo(tmp)
        task_id = new(main_path, "属性")
        created = beads.show(main_path, beads.to_bd_id(task_id))
        meta = created.raw.get("metadata") if created is not None else {}
        check("new は metadata に書き、label を付けない",
              created is not None and meta.get("difficulty") == "sonnet" and meta.get("loopable") == "Y"
              and not any(l.startswith(("difficulty:", "loopable:")) for l in created.labels), str(created))
        r = run_task(main_path, "edit", task_id, "--difficulty", "haiku", "--loopable", "N")
        edited = beads.show(main_path, beads.to_bd_id(task_id))
        meta = edited.raw.get("metadata") if edited is not None else {}
        check("edit は metadata を書き換え、label を付けない",
              r.returncode == 0 and edited is not None and meta.get("difficulty") == "haiku"
              and meta.get("loopable") == "N" and not any(l.startswith(("difficulty:", "loopable:")) for l in edited.labels),
              r.stdout + r.stderr + str(edited))
        shown = run_task(main_path, "show", task_id).stdout
        check("show は metadata から読む", "\ndifficulty: haiku\nloopable: N\n" in shown, shown)

        r = bd(main_path, "create", "--id", "t-raw", "--title", "取り込み", "-l", "mytag", "--silent")
        check("振り分け前の課題（番号なし）を作れる", r.returncode == 0, r.stdout + r.stderr)
        r = run_task(main_path, "adopt", "T-RAW", "--difficulty", "sonnet", "--loopable", "Y", "--body-file", "-",
                     stdin=PLANNED_BODY)
        adopted_id = r.stdout.strip().split("\t")[-1] if r.stdout.startswith("ADOPTED\t") else ""
        adopted = beads.show(main_path, beads.to_bd_id(adopted_id)) if adopted_id else None
        meta = adopted.raw.get("metadata") if adopted is not None else {}
        check("adopt は metadata に2キーを書き、label を付けない",
              adopted is not None and meta.get("difficulty") == "sonnet" and meta.get("loopable") == "Y"
              and not any(l.startswith(("difficulty:", "loopable:")) for l in adopted.labels),
              r.stdout + r.stderr + str(adopted))


def test_setup_and_config_doctor() -> None:
    say("init.py・config-doctor・MISSING・設定の読み違い")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, prefix=None)
        check("init.py が .beads を作る", os.path.isdir(os.path.join(main_path, ".beads")))
        check("stealth で .beads は git に見えない", git(main_path, "status", "--porcelain").stdout.strip() == "")

        def init_out() -> str:
            return subprocess.run([sys.executable, os.path.join(HERE, "..", "core", "init.py")],
                                  cwd=main_path, capture_output=True, text=True, env=env()).stdout

        config_path = os.path.join(main_path, ".tw", "config.toml")
        with open(config_path, encoding="utf-8") as f:
            before = f.read()
        out = init_out()
        with open(config_path, encoding="utf-8") as f:
            after = f.read()
        check("backup が未記入なら init.py は backup UNSET の知らせを出し、設定は書き換えない",
              any(l.startswith("backup\tUNSET\t") for l in out.splitlines()) and before == after, out)
        check("UNSET の知らせは同じディスクだと言い切らない",
              "既定は" not in out and "tw config-doctor" in out, out)
        states = (
            ("OK", "backup 記入済み", before + 'backup = "/somewhere/else"\n', False),
            ("OK", "backup 未記入", before, True),
            ("MISSING", "設定ファイルなし", None, False),
            ("INVALID", "verify なし", 'branch = "既定"\n', False),
            ("INVALID", "branch が語彙の外", before + 'branch = "自分で切らない"\n', False),
            ("INVALID", "知らないキー", before + 'foo = "x"\n', False),
        )
        for state, label, body, unset in states:
            if body is None:
                os.remove(config_path)
            else:
                write(config_path, body)
            out = init_out()
            check(f"設定が {state} のとき UNSET は{'出す' if unset else '出さない'}（{label}）",
                  out.count("backup\tUNSET\t") == (1 if unset else 0)
                  and any(l.startswith(f"config\t{state}\t") for l in out.splitlines())
                  and init.config_state(main_path)[0] == state, out)
        write(config_path, before)
        r = run_task(wt1, "config-doctor")
        check("config-doctor が store・beads・tracker の行を足す", r.returncode == 0
              and tail_line(r.stdout, "store").startswith("store\tOK\tbeads")
              and tail_line(r.stdout, "beads").startswith("beads\tOK")
              and tail_line(r.stdout, "tracker") == "tracker\tOK\tなし", r.stdout)
        r = run_task(wt1, "status")
        check("まっさらな status は集計だけ（triage 行つき）", r.returncode == 0 and r.stdout.startswith("---\n")
              and "triage\t0\t-" in r.stdout, r.stdout)

        shutil.rmtree(os.path.join(main_path, ".beads"))
        r = run_task(wt1, "status")
        check(".beads が無ければ MISSING（終了コード6）", r.returncode == 6 and r.stdout.startswith("MISSING"), r.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, extra='tracker = "gitlab"\n')
        r = run_task(wt1, "status")
        check("読めないトラッカーは INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID"), r.stdout)
        write(os.path.join(wt1, ".tw", "config.toml"), 'verify = "なし"\nstore = "どこか"\n')
        r = run_task(wt1, "status")
        check("読めない置き場は INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID"), r.stdout)


def test_missing_config_ignores_old_layout() -> None:
    say("設定ファイルが無ければ、旧い置き場や節があっても MISSING")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp)
        os.remove(os.path.join(wt1, ".tw", "config.toml"))
        write(os.path.join(wt1, "develop", "tasks.json"), "[]\n")
        write(os.path.join(wt1, "CLAUDE.md"), "# x\n\n## タスク運用\n\n- 検証コマンド: なし\n- タスクの置き場: beads\n")
        for command in ("status", "config", "verify"):
            r = run_task(wt1, command)
            check(f"{command} は MISSING（終了コード6）で、OLD_LAYOUT・LEGACY を出さない",
                  r.returncode == 6 and r.stdout.startswith("MISSING")
                  and "OLD_LAYOUT" not in r.stdout and "LEGACY" not in r.stdout, r.stdout + r.stderr)
        r = run_task(wt1, "config-doctor")
        check("config-doctor は config MISSING（終了コード1）で、old_section・legacy の行を出さない", r.returncode == 1
              and tail_line(r.stdout, "config").startswith("config\tMISSING\t.tw/config.toml")
              and tail_line(r.stdout, "old_section") == "" and tail_line(r.stdout, "legacy") == "", r.stdout)


def test_store_default_is_beads() -> None:
    say("store の行が無ければ Beads 方式")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp)
        write(os.path.join(main_path, ".tw", "config.toml"), 'verify = "なし"\nbranch = "切らない"\n')
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "store の行を外す")
        task_id = new(main_path, "store なし")
        check("new は Beads に課題を作り、タスクファイルを作らない",
              not os.path.exists(os.path.join(main_path, ".tw", "task"))
              and any(i.get("id") == beads.to_bd_id(task_id) for i in json.loads(bd(main_path, "list", "--json", "--all").stdout or "[]")),
              task_id)
        r = run_task(main_path, "config-doctor")
        check("config-doctor は store OK beads の行を出す", r.returncode == 0
              and tail_line(r.stdout, "store").startswith("store\tOK\tbeads"), r.stdout)

        beads_dir = os.path.join(main_path, ".beads")
        shutil.rmtree(beads_dir)
        r = run_task(main_path, "status")
        check(".beads が無ければ MISSING と置き場（終了コード6）",
              r.returncode == 6 and r.stdout == f"MISSING\t{os.path.realpath(beads_dir)}\n", r.stdout + r.stderr)


def test_store_files_is_invalid() -> None:
    say('store = "files" は行番号つきの INVALID')
    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _ = make_repo(tmp)
        write(os.path.join(main_path, ".tw", "config.toml"), 'verify = "なし"\nstore = "files"\n')
        for command in ("status", "config"):
            r = run_task(main_path, command)
            check(f"{command} は INVALID（終了コード3）で .tw/config.toml の2行目を指す",
                  r.returncode == 3 and r.stdout.startswith("INVALID\t.tw/config.toml:2:"), r.stdout + r.stderr)
        r = run_task(main_path, "config-doctor")
        check("config-doctor は config INVALID（終了コード3）",
              r.returncode == 3 and tail_line(r.stdout, "config").startswith("config\tINVALID\t.tw/config.toml"), r.stdout)


def test_removed_commands() -> None:
    say("migrate・migrate-layout・prune は無い")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, _wt1, _ = make_repo(tmp)
        for command in ("migrate", "migrate-layout", "prune"):
            r = run_task(main_path, command)
            check(f"{command} は使い方の誤り（終了コード2）", r.returncode == 2 and r.stdout == "", r.stdout + r.stderr)


def test_claim_race_owner_and_release() -> None:
    say("new と claim の取り合い・NOT_OWNER・release")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        check("Beads は一時ディレクトリの中にあり、環境変数で本物へ向いていない",
              os.path.realpath(main_path).startswith(os.path.realpath(tmp)) and not {"BEADS_DIR", "BEADS_DB"} & set(env()),
              main_path)
        a = new(main_path, "取り合い")
        procs = [start_task(w, "new", "--summary", f"並行{i}", "--difficulty", "haiku", "--loopable", "Y",
                            *(("--hold",) if i == 2 else ()), "--body-file", "-")
                 for i, w in enumerate((wt1, wt2, main_path))]
        outs = [p.communicate(HELD_PLANNED_BODY if i == 2 else PLANNED_BODY)[0] for i, p in enumerate(procs)]
        ids = [o.split("\t")[1] for o in outs if o.startswith("CREATED")]
        check("3つ同時の new で番号が重ならない", len(ids) == 3 and len(set(ids)) == 3, repr(outs))
        h = ids[2] if outs[2].startswith("CREATED") else ""
        r = bd(main_path, "kv", "get", beads.LAST_ID_KEY)
        check("最後の番号を bd kv に残す", r.stdout.strip().isdigit() and int(r.stdout.strip()) >= 4, r.stdout)
        procs = [start_task(w, "claim", a) for w in (wt1, wt2)]
        outs = [(p.communicate()[0], p.returncode) for p in procs]
        claimed = [o for o, c in outs if o.startswith("CLAIMED") and c == 0]
        taken = [o for o, c in outs if o.startswith("TAKEN") and c == 4]
        check("同時の claim は1つだけ CLAIMED、残りは TAKEN", len(claimed) == 1 and len(taken) == 1, repr(outs))
        winner = wt1 if "CLAIMED" in outs[0][0] else wt2
        loser = wt2 if winner == wt1 else wt1
        check("CLAIMED の3列目は beads:t-xxx", claimed[0].split("\t")[2] == f"beads:{beads.to_bd_id(a)}", claimed[0])
        issue = beads.show(main_path, beads.to_bd_id(a))
        check("負けた側は印の持ち主も戻り先の枝も書き換えない", issue is not None
              and issue.assignee == os.path.basename(winner)
              and (issue.raw.get("metadata") or {}).get(beads.CLAIM_BRANCH_KEY) == os.path.basename(winner),
              str(issue and issue.raw))
        kept = claim_snapshots(main_path).get(beads.to_bd_id(a))
        check("claim は着手の印を bd kv へ控える", kept is not None and issue is not None
              and kept.assignee == issue.assignee and kept.started_at == issue.started_at
              and beads.mark_values(kept) == beads.mark_values(issue), str(kept and kept.raw))

        r = run_task(loser, "done", a, "--result-file", "-", stdin="x\n")
        check("他人の印の done は NOT_OWNER（終了コード4）", r.returncode == 4 and r.stdout.startswith("NOT_OWNER"), r.stdout)
        r = run_task(loser, "release", a)
        check("他人の印の release は NOT_OWNER", r.returncode == 4 and r.stdout.startswith("NOT_OWNER"), r.stdout)
        r = run_task(winner, "status")
        marker = rows(r.stdout).get(a, [""] * 8)[6]
        check("印の列は作業ツリー名と経過", marker.startswith(os.path.basename(winner) + " "), r.stdout)
        r = run_task(winner, "claim", h)
        check("hold は claim できない（NOT_READY）", r.returncode == 4 and r.stdout.startswith("NOT_READY"), r.stdout)
        r = run_task(loser, "release", a, "--force")
        check("--force の release は RELEASED", r.returncode == 0 and r.stdout.startswith("RELEASED"), r.stdout)
        check("release は控えを消す", beads.to_bd_id(a) not in claim_snapshots(main_path), r.stdout)
        r = run_task(loser, "release", a)
        check("印の無い release は NOT_CLAIMED（終了コード0）", r.returncode == 0 and r.stdout.startswith("NOT_CLAIMED"), r.stdout)
        write(os.path.join(loser, "dirty.txt"), "x\n")
        r = run_task(loser, "claim", h)
        check("汚れた作業ツリーでは DIRTY", r.returncode == 4 and r.stdout.startswith("DIRTY"), r.stdout)


def test_cycle_done_ship_and_dropped() -> None:
    say("1サイクル（claim → edit → done → ship）と見送り")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp, branch="既定")
        a = new_unplanned(main_path, "前段")
        b = new(main_path, "後段", "--deps", a)
        c = new(main_path, "見送る")

        r = run_task(wt1, "claim", a)
        check("既定の枝の設定なら feature/T-xxx を切る", r.stdout.strip().endswith(f"branch=feature/{a}"), r.stdout)
        shown = run_task(wt1, "show", a).stdout
        body = shown.split("---\n", 2)[2].replace("## やること\n", "## やること\n\n### 1. 書く\n")
        r = run_task(wt1, "edit", a, "--body-file", "-", stdin=body)
        check("edit が本文を受ける", r.returncode == 0 and r.stdout.startswith("EDITED"), r.stdout + r.stderr)
        issue = beads.show(main_path, beads.to_bd_id(a))
        check("## やること は notes へ", issue is not None and issue.raw.get("notes") == "### 1. 書く", str(issue and issue.raw))

        check("claim のあとは控えがある", beads.to_bd_id(a) in claim_snapshots(main_path), "")
        work_and_done(wt1, a)
        check("done は控えを消す", beads.to_bd_id(a) not in claim_snapshots(main_path), "")
        done_issue = beads.show(main_path, beads.to_bd_id(a))
        if done_issue is not None:
            beads.write_claim_snapshot(main_path, done_issue)
        flow = ledger.flow_dir(ledger.ledger_root(cwd=main_path))
        events = [json.loads(l) for f in sorted(os.listdir(flow)) for l in open(os.path.join(flow, f), encoding="utf-8")]
        check("claim・done の記録に difficulty が入る", {(e["event"], e["difficulty"]) for e in events if e["task"] == a}
              >= {("claim", "sonnet"), ("done", "sonnet")}, repr(events))
        r = run_task(wt2, "status")
        check("done のあとも ship までは印が残り、後段は BLOCKED", rows(r.stdout).get(b, [""] * 8)[5] == f"BLOCKED:{a}"
              and rows(r.stdout).get(a, [""] * 8)[5] == "CLAIMED", r.stdout)
        r = run_task(wt1, "ship")
        first = r.stdout.splitlines()[0] if r.stdout else ""
        check("ship は SHIPPED で閉じた ID を released に出す", r.returncode == 0 and first.startswith("SHIPPED")
              and f"released={a}" in first, r.stdout + r.stderr)
        check("ship は閉じた課題の控えを消す（done のあとに書き戻された控えも）",
              beads.to_bd_id(a) not in claim_snapshots(main_path), r.stdout)
        check("claim した枝（wt1）へ戻って feature 枝を消す", "branch=wt1" in first
              and git(wt1, "branch", "--list", f"feature/{a}").stdout.strip() == "", first)
        check("ship のあとにバックアップを取る", any(l.startswith("BACKUP\tOK") for l in r.stdout.splitlines()), r.stdout)
        r = run_task(wt2, "status", "--all")
        t = rows(r.stdout)
        check("閉じたものは done、後段は READY", t.get(a, [""] * 8)[1] == "done" and t.get(b, [""] * 8)[5] == "READY", r.stdout)
        shown = run_task(wt2, "show", a).stdout
        heads = [l for l in shown.split("\n") if l.startswith("## ")]
        check("show の末尾に ## 結果（comment から）", shown.rstrip().endswith("- 振り返り: 兆候なし")
              and "status: done" in shown, shown)
        check("show は枠の7節をこの順に出し、todo に戻しても残った ## 解除の条件 のあとに結果が付く",
              heads == [*taskfile.SECTION_HEADINGS, taskfile.UNLOCK_HEADING, "## 結果"], shown)

        r = run_task(wt2, "claim", c)
        work_and_done(wt2, c, dropped=True)
        r = run_task(wt2, "ship")
        r = run_task(wt1, "status", "--all")
        t = rows(r.stdout)
        check("dropped は closed ＋ label cancelled", t.get(c, [""] * 8)[1] == "dropped", r.stdout)
        issue = beads.show(main_path, beads.to_bd_id(c))
        check("閉じたあと ship: の印は残らない", issue is not None and issue.status == "closed"
              and "cancelled" in issue.labels and not any(l.startswith("ship:") for l in issue.labels),
              str(issue and issue.labels))

        # retrospect の材料（Beads の comment と版）
        mat = subprocess.run([sys.executable, MATERIAL_PY, ".", a], cwd=main_path, capture_output=True, text=True, env=env())
        check("material.py は Beads の本文と版の差を出す", f"出典\tBeads {beads.to_bd_id(a)}" in mat.stdout
              and "+### 1. 書く" in mat.stdout, mat.stdout + mat.stderr)


def test_backup() -> None:
    say("バックアップ（git の外の決まった場所）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp)
        new(main_path, "残す")
        r = run_task(wt1, "backup")
        target = os.path.join(env()["XDG_DATA_HOME"], "task-workflow", "base")
        check("既定の置き場へ bd backup と bd export を取る", r.returncode == 0 and r.stdout.startswith(f"BACKUP\tOK\t{target}")
              and os.path.exists(os.path.join(target, "issues.jsonl"))
              and os.path.isdir(os.path.join(target, "dolt")), r.stdout + r.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, extra='backup = "./keep"\n')
        r = run_task(main_path, "backup")
        check("リポジトリの中へは取らない（終了コード10）", r.returncode == 10 and "BACKUP\tFAILED" in r.stdout, r.stdout)


def test_id_forms() -> None:
    """ID の形: `T-<n>`・`GH-<n>`・Jira のキー（`PROJ-123`）を読み、前の2つを Jira のキーと取り違えない。"""
    forms = {"T-123": "t-123", "GH-5": "gh-5", "PROJ-123": "proj-123", "AB2_C-7": "ab2_c-7"}
    for shown, inner in forms.items():
        check(f"{shown} は Beads の中で {inner} と行き来する",
              beads.to_bd_id(shown) == inner and beads.to_task_id(inner) == shown and beads.is_numbered(inner))
        check(f"{shown} は --deps・retrospect の引数・ブランチ・文中の検索が受ける",
              layout.ANY_ID_PATTERN.match(shown) is not None
              and layout.FEATURE_BRANCH_PATTERN.fullmatch(f"feature/{shown}") is not None
              and (layout.ID_SEARCH_PATTERN.search(f"{shown}: 直す") or [None])[0] == shown)
    for bad in ("T-12", "P-123", "proj-123", "PROJ-", "1AB-3", "PROJ-12a"):
        check(f"{bad} は ID の形でない", layout.ANY_ID_PATTERN.match(bad) is None)
    check("T-<n>・GH-<n> は Jira の枝で読まれない（番号の意味が変わらない）",
          beads.id_number("t-123") == 123 and beads.id_number("gh-5") is None and beads.id_number("proj-123") is None
          and beads.BD_ID_PATTERN.match("t-123").group(3) is None and beads.BD_ID_PATTERN.match("gh-5").group(3) is None)
    check("仮の ID・取り込んだままの ID は番号付きでない",
          not any(beads.is_numbered(i) for i in ("gh-new-wt-1700000000", "gh-1790123-1-4dfc", "t-12")))
    ordered = sorted(["PROJ-2", "GH-new-x", "ABC-9", "GH-5", "T-123", "PROJ-1", "T-045"], key=beads.sort_key)
    check("status の並びは T → GH → Jira のキー（キー名・番号）→ 仮の ID",
          ordered == ["T-045", "T-123", "GH-5", "ABC-9", "PROJ-1", "PROJ-2", "GH-new-x"], repr(ordered))


def test_bd_time_forms() -> None:
    """`bd` の時刻は小数の桁（0〜9桁）と `Z`・時差によらず読め、時刻でないものは `None`。"""
    forms = {
        "2026-10-05T08:12:39Z": 0,
        "2026-10-05T08:12:39.7Z": 700000,
        "2026-10-05T17:12:39.78+09:00": 780000,
        "2026-10-05T08:12:39.1234Z": 123400,
        "2026-10-05T08:12:39.123456789Z": 123456,
    }
    for text, micro in forms.items():
        check(f"{text} を読む", beads.parse_time(text) == datetime(2026, 10, 5, 8, 12, 39, micro, tzinfo=timezone.utc),
              repr(beads.parse_time(text)))
    check("時刻でないものは None", beads.parse_time("昨日") is None and beads.parse_time(None) is None)


def test_issue_list_reuse_boundary() -> None:
    say("課題の一覧の使い回し: 書き込みの前は同じ一覧、あとは読み直す（読み専用の動詞ではずらさない）")
    with tempfile.TemporaryDirectory() as tmp:
        main_path = make_repo(tmp)[0]
        task_id = new(main_path, "使い回し")
        bd_id = beads.to_bd_id(task_id)
        session = tracker.Session(main_path, tracker.Tracker("github"))
        first = session._issues()
        check("書き込み無しで読み直すと同じ一覧", session._issues() is first)
        session._begin(None)
        check("呼び出しの始め（_begin）は一覧を捨てない", session._issues() is first)

        count = beads.writes(main_path)
        for args in (["show", bd_id, "--json"], ["list", "--all", "-n", "0", "--flat", "--json"],
                     ["kv", "get", "gh853-probe"], ["kv", "list", "--json"], ["config", "get", "issue_prefix"],
                     ["config", "list", "--json"], ["history", bd_id, "--events", "--json"]):
            beads.run(main_path, args)
        check("読み専用の動詞（show・list・kv get・kv list・config get・config list・history）は書いた回数を進めない",
              beads.writes(main_path) == count and session._issues() is first)
        beads.run(main_path, ["kv", "set", "gh853-probe", "1"])
        check("kv set は課題を変えないので進めない", beads.writes(main_path) == count and session._issues() is first)

        r = beads.run(main_path, ["update", bd_id, "--title", "書き換えた"], "wt1")
        check("bd update は書いた回数を進める", r.returncode == 0 and beads.writes(main_path) == count + 1)
        reread = session._issues()
        check("書き込みのあとは読み直し、新しい題が見える",
              reread is not first and any(i.bd_id == bd_id and i.title == "書き換えた" for i in reread))
        check("読み直したあとはまた使い回す", session._issues() is reread)

        count = beads.writes(main_path)
        beads.run(main_path, ["frobnicate-unknown-verb"])
        check("表に無い動詞は書き込みとして扱う", beads.writes(main_path) == count + 1 and session._issues() is not reread)

        count = beads.writes(main_path)
        env = dict(os.environ)
        tracker_github._bd(main_path, ["show", bd_id, "--json"], env, None, keeps_issues=True)
        check("番号の付いた課題の送り（keeps_issues）は書いた回数を進めない", beads.writes(main_path) == count)
        tracker_github._bd(main_path, ["show", bd_id, "--json"], env)
        check("keeps_issues でない bd github（pull・番号の無い push）は進める", beads.writes(main_path) == count + 1)
        other = tracker.Session(os.path.join(tmp, "elsewhere"), tracker.Tracker("github"))
        check("別の作業ツリーの数は混ざらない", beads.writes(other.toplevel) == 0)


def test_bd_config_reads() -> None:
    say("bd の設定: issue_prefix とリポジトリは bd config list の1回の読みから引く")
    with tempfile.TemporaryDirectory() as tmp:
        main_path = make_repo(tmp)[0]
        bd(main_path, "config", "set", "github.repository", "o/r")
        beads.forget_reads(main_path)
        config = beads.read_bd_config(main_path)
        check("config list の欄から prefix とリポジトリを引く",
              config.get("issue_prefix") == beads.PREFIX_LOCAL and beads.prefix_of(config) == beads.PREFIX_LOCAL
              and tracker_github._repo_of(config) == "o/r" and beads.read_prefix(main_path) == beads.PREFIX_LOCAL,
              repr(config))
        nowhere = os.path.join(tmp, "no-beads")
        os.makedirs(nowhere)
        empty = beads.read_bd_config(nowhere)
        check(".beads の無い所では空（prefix は t・リポジトリは無し）", empty == {}
              and beads.prefix_of(empty) == beads.PREFIX_LOCAL and tracker_github._repo_of(empty) is None, repr(empty))
    check("issue_prefix が gh なら gh・ほかの値なら t", beads.prefix_of({"issue_prefix": "gh"}) == beads.PREFIX_GITHUB
          and beads.prefix_of({"issue_prefix": "x"}) == beads.PREFIX_LOCAL)
    check("github.repository が owner/repo の形でなければ github.owner・github.repo から作る",
          tracker_github._repo_of({"github.repository": "no-slash", "github.owner": "x", "github.repo": "y"}) == "x/y")
    check("github.repository が空白を含み、owner・repo も無ければ無し",
          tracker_github._repo_of({"github.repository": "a b/c"}) is None)


def test_tracker_command_bd_calls() -> None:
    say("トラッカー github・new・claim・edit・done の1回の bd の起動（設定の読みは1回、claim の Project の控えは kv list から）")
    verbs = {"--actor", "config", "kv", "show", "list", "history", "github", "update", "create", "comment",
             "comments", "close", "rename", "export", "backup", "dep", "label", "reopen"}
    with github() as (_fake, tmp, main_path, wt1):
        a = new(main_path, "数える課題")
        counted = os.path.join(tmp, "bd-count.txt")
        local.env = {**local.env, "BD_COUNT_FILE": counted}

        def counted_run(label: str, cwd: str, *args: str, stdin: str | None = None, writes: bool = False) -> list[str]:
            open(counted, "w").close()
            r = run_task(cwd, *args, stdin=stdin)
            calls = [c for c in open(counted).read().splitlines() if c.split(" ", 1)[0] in verbs]
            say(f"  {label}: bd {len(calls)} 回")
            sent = any(c.startswith(("github push", "github sync")) for c in calls)
            check(f"{label} は成功し、GitHub へ{'送る' if writes else '送らない'}",
                  r.returncode == 0 and ("TRACKER\tOK\tgithub" in r.stdout) == writes and sent == writes,
                  r.stdout + r.stderr + "\n".join(calls))
            check(f"{label}: config get を打たず、config list は1回以下",
                  not any(c.startswith("config get") for c in calls)
                  and sum(c.startswith("config list") for c in calls) <= 1, "\n".join(calls))
            return calls

        counted_run("new", main_path, "new", "--summary", "数える2", "--difficulty", "sonnet", "--loopable", "Y",
                    "--body-file", "-", stdin=PLANNED_BODY, writes=True)
        calls = counted_run("claim", wt1, "claim", a)
        check("claim: Project の控えを kv get で読まず、kv list は1回（GitHub が変わっていなければ取り込まず、送りの分も無い）",
              not any(c.startswith("kv get task-workflow.project") for c in calls)
              and calls.count("kv list --json") == 1, "\n".join(calls))
        counted_run("edit", wt1, "edit", a, "--section", "参考情報", "--body-file", "-", stdin="メモ\n")
        write(os.path.join(wt1, f"{a}.txt"), f"{a}\n")
        git(wt1, "add", f"{a}.txt")
        git(wt1, "commit", "-q", "-m", f"{a}: 作業")
        counted_run("done", wt1, "done", a, "--result-file", "-", stdin=RESULT_FULL)


def test_new_bd_calls() -> None:
    say("new: 双方向で --deps なしなら全件読み（bd list）を打たず、--deps は依存先だけを引く。双方向でない道は全件を読み採番は変わらない")
    local_prefix = beads.PREFIX_LOCAL
    for prefix, bidirectional in ((beads.PREFIX_GITHUB, True), (local_prefix, False)):
        kind = "双方向" if bidirectional else "双方向でない"
        with github(prefix=prefix) as (_fake, tmp, main_path, _wt1):
            first = new(main_path, "先にある課題")
            counted = os.path.join(tmp, "bd-count.txt")
            local.env = {**local.env, "BD_COUNT_FILE": counted}

            def register(*extra: str, summary: str = "数える") -> tuple[subprocess.CompletedProcess, list[str]]:
                open(counted, "w").close()
                r = run_task(main_path, "new", "--summary", summary, "--difficulty", "sonnet", "--loopable", "Y",
                             *extra, "--body-file", "-", stdin=PLANNED_BODY)
                return r, open(counted).read().splitlines()

            r, calls = register()
            lists = [c for c in calls if c.startswith("list ")]
            say(f"  {kind}・--deps なし: bd {len(calls)} 回（うち list {len(lists)} 回）")
            check(f"{kind}・--deps なしは CREATED", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
            same_title = r.stdout.split("\t")[1]
            if bidirectional:
                check("双方向・--deps なしで GitHub に合う Issue が無ければ list を打たない", len(lists) == 0, "\n".join(calls))
            else:
                check("双方向でない道は採番のため list を1回打つ", len(lists) == 1, "\n".join(calls))
                created = r.stdout.split("\t")[1]
                check("双方向でない道の採番は最大の次", num(created) == num(first) + 1, r.stdout)

            r, calls = register("--deps", first)
            lists = [c for c in calls if c.startswith("list ")]
            say(f"  {kind}・--deps 1件: bd {len(calls)} 回（うち list {len(lists)} 回）")
            check(f"{kind}・--deps ありは CREATED", r.returncode == 0 and r.stdout.startswith("CREATED\t"), r.stdout + r.stderr)
            if bidirectional:
                check("双方向・--deps ありは依存先を show で引き、題・本文が同じ Issue が既にあるので taken の list を1回読む",
                      f"show {beads.to_bd_id(first)} --json" in calls and len(lists) == 1, "\n".join(calls))
                twin = r.stdout.split("\t")[1]
                check("題・本文が同じ別の課題の Issue は取り違えず、別の番号で立つ",
                      twin != same_title and num(twin) != num(same_title), f"{same_title} {twin}")
                many = [first, *(new(main_path, f"依存{i}") for i in range(3))]
                r, calls = register("--deps", ",".join(many), summary="数える3")
                lists = [c for c in calls if c.startswith("list ")]
                say(f"  {kind}・--deps {len(many)}件: bd {len(calls)} 回（うち list {len(lists)} 回）")
                check("双方向・--deps が3件を超えたら全件読み1回で確かめ、依存先を show で引かない",
                      r.returncode == 0 and r.stdout.startswith("CREATED\t") and len(lists) == 1
                      and not any(f"show {beads.to_bd_id(d)} --json" in calls for d in many), r.stdout + r.stderr + "\n".join(calls))
                r, calls = register("--deps", ",".join([*many, "GH-99999"]), summary="数える4")
                check("双方向・3件を超える --deps に無い ID があれば終了コード2で拒む",
                      r.returncode == 2 and "GH-99999" in r.stderr and not any(c.startswith("create ") for c in calls),
                      r.stdout + r.stderr + "\n".join(calls))

            r, calls = register("--deps", "GH-99999" if bidirectional else "T-99999")
            check(f"{kind}・無い依存先は終了コード2で拒み、何も作らない",
                  r.returncode == 2 and "Beads に無い" in r.stderr and not any(c.startswith("create ") for c in calls),
                  r.stdout + r.stderr + "\n".join(calls))


def test_ship_bd_calls() -> None:
    say("ship: 送る課題がどれも番号つきなら送りのあとに全件読みと show を打たず、番号の無い課題を含めば読み直す")
    with github() as (fake, tmp, main_path, wt1):
        a = new(main_path, "送る課題")
        r = run_task(wt1, "claim", a)
        check("claim", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
        work_and_done(wt1, a)
        counted = os.path.join(tmp, "bd-count.txt")
        local.env = {**local.env, "BD_COUNT_FILE": counted}

        def counted_ship() -> tuple[subprocess.CompletedProcess, list[str], str | None, list[str]]:
            """`ship` の結果、bd の起動の全部、`github sync --push-only` の行（打たれなければ `None`）、その後ろの起動。"""
            open(counted, "w").close()
            r = run_task(wt1, "ship")
            calls = open(counted).read().splitlines()
            sent = next((n for n, c in enumerate(calls) if c.startswith("github sync --push-only")), None)
            return r, calls, None if sent is None else calls[sent], [] if sent is None else calls[sent + 1:]

        r, calls, sent_line, after = counted_ship()
        lists = [c for c in calls if c.startswith("list ")]
        shows = [c for c in calls if c.startswith(f"show {beads.to_bd_id(a)} ")]
        say(f"  番号つきの ship: bd {len(calls)} 回（うち list {len(lists)} 回・show {len(shows)} 回）")
        check("番号つきだけの ship は成功し、その課題だけを --issues で送る",
              r.returncode == 0 and "SHIPPED" in r.stdout and "TRACKER\tOK\tgithub" in r.stdout
              and sent_line == f"github sync --push-only --issues {beads.to_bd_id(a)} --json"
              and sum(c.startswith("github sync --push-only") for c in calls) == 1,
              f"{sent_line!r}\n{r.stdout}{r.stderr}" + "\n".join(calls))
        check("番号つきだけの ship の list は2回以下、送った課題の show は1回以下",
              len(lists) <= 2 and len(shows) <= 1, "\n".join(calls))
        check("番号つきだけの ship は送りのあとに同期の控えを書き（空振りでない）、list・show を打たない",
              sent_line is not None
              and any(c.startswith(f"kv set {tracker_claims.SYNCED_ITEM_PREFIX}") for c in after)
              and not any(c.startswith(("list ", "show ")) for c in after), "\n".join(after))

        provisional = f"{beads.PROVISIONAL_PREFIX}wt1-1700000000"
        bd(main_path, "create", "--id", provisional, "--title", "仮の課題", "--description", "まだ送っていない", "--silent")
        r, calls, sent_line, after = counted_ship()
        renamed = beads.show(main_path, "gh-2")
        check("番号の無い課題を含む ship は読み直し、Issue が1つ立って仮の ID が gh-2 へ付け替わり、Status が揃う",
              r.returncode == 0 and "NOTHING" in r.stdout and len(fake.issues) == 2 and renamed is not None
              and sent_line is not None and provisional in sent_line
              and not any(beads.is_provisional(i.bd_id) for i in beads.list_issues(main_path))
              and fake.status_of(2) is not None and any(c.startswith("list ") for c in after),
              r.stdout + r.stderr + "\n".join(calls))


TESTS = (
    test_ship_bd_calls,
    test_tracker_command_bd_calls,
    test_new_bd_calls,
    test_issue_list_reuse_boundary,
    test_cycle_done_ship_and_dropped,
    test_claim_race_owner_and_release,
    test_setup_and_config_doctor,
    test_backup,
    test_difficulty_loopable_metadata,
    test_missing_config_ignores_old_layout,
    test_store_default_is_beads,
    test_store_files_is_invalid,
    test_removed_commands,
    test_id_forms,
    test_bd_time_forms,
    test_bd_config_reads,
)


if __name__ == "__main__":
    refuse_direct_run("selftest_beads.py")
