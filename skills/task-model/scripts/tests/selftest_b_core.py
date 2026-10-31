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


def test_importance_urgency_read() -> None:
    say("重要度・緊急度の読み（値あり・欠け・語彙の外）")

    def read(**metadata: str) -> tuple[taskfile.Task | None, str | None]:
        meta = {"difficulty": "sonnet", "loopable": "Y", **metadata}
        issue = beads.Issue("gh-1", "題", "open", (), (), None, None, None, {"metadata": meta})
        return beads.to_task(issue)

    task, _ = read(importance="high", urgency="low")
    check("値ありは読める", task is not None and (task.importance, task.urgency) == ("high", "low"), str(task))
    task, error = read()
    check("欠けは normal で、振り分け前にも不正にもならない", task is not None and error is None
          and (task.importance, task.urgency) == ("normal", "normal"), f"{task} {error}")
    task, error = read(urgency="urgent")
    check("語彙の外は INVALID の理由が返る（振り分け前ではない）", task is None and error is not None and "urgency" in error, str(error))


def test_importance_urgency_flags() -> None:
    say("tw new・edit の --importance・--urgency と tw show")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp)
        plain = new(main_path, "既定")
        shown = run_task(wt1, "show", plain).stdout
        check("省いた課題は metadata に書かず、show にも出さない", "importance:" not in shown and "urgency:" not in shown, shown)
        a = new(main_path, "旗つき", "--importance", "high", "--urgency", "low")
        meta = beads.show(main_path, beads.to_bd_id(a)).raw.get("metadata", {})
        shown = run_task(wt1, "show", a).stdout
        check("new の旗が metadata と show に出る", (meta.get("importance"), meta.get("urgency")) == ("high", "low")
              and "importance: high\n" in shown and "urgency: low\n" in shown, f"{meta}\n{shown}")
        r = run_task(wt1, "edit", a, "--importance", "normal", "--urgency", "high")
        shown = run_task(wt1, "show", a).stdout
        check("edit で変えられ、normal に戻すと show から消える", r.returncode == 0 and "importance:" not in shown
              and "urgency: high\n" in shown, r.stdout + r.stderr + shown)
        r = run_task(wt1, "new", "--summary", "外", "--difficulty", "sonnet", "--loopable", "Y", "--importance", "urgent",
                     "--body-file", "-", stdin=PLANNED_BODY)
        check("new は語彙の外を終了コード2で拒む", r.returncode == 2, r.stdout + r.stderr)
        r = run_task(wt1, "edit", a, "--urgency", "urgent")
        check("edit は語彙の外を終了コード2で拒む", r.returncode == 2, r.stdout + r.stderr)
        bd(main_path, "create", "--id", "t-adopt", "--title", "取り込み", "--silent")
        r = run_task(main_path, "adopt", "T-ADOPT", "--difficulty", "sonnet", "--loopable", "Y", "--urgency", "high",
                     "--body-file", "-", stdin=PLANNED_BODY)
        adopted = beads.show(main_path, beads.to_bd_id(r.stdout.split("\t")[2].strip())) if r.returncode == 0 else None
        meta = adopted.raw.get("metadata", {}) if adopted else {}
        check("adopt の旗が metadata に書かれる", r.returncode == 0 and meta.get("urgency") == "high"
              and "importance" not in meta, r.stdout + r.stderr + str(meta))


TESTS = (
    test_importance_urgency_read,
    test_importance_urgency_flags,
    test_cycle_done_ship_and_dropped,
    test_claim_race_owner_and_release,
    test_backup,
)


if __name__ == "__main__":
    refuse_direct_run("selftest_beads.py")

