#!/usr/bin/env python3
"""`task.py` の Beads 方式（`- タスクの置き場: beads`）の自己テスト。

使い方: python3 selftest_beads.py

一時ディレクトリに git リポジトリと作業ツリー2本を作り、`bd init --stealth` で `.beads` を置いて、
`task.py` を実際に子プロセスで（取り合いは同時に）起こして確かめる。本物の `bd` を使う
（無ければ非0で終わる。家の向け先は `selftest_support.beads_home`）。

GitHub には繋がない。本物の `bd` の `bd github push`・`pull` を `GITHUB_API_URL` で
偽の HTTP サーバ（`FakeGitHub`。REST の Issue と Project の GraphQL）へ向ける。`gh` は PATH の先頭に置いた
偽のコマンドが `api` を同じサーバへ転送する。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import re
import tempfile
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import beads  # noqa: E402
import layout  # noqa: E402
import ledger  # noqa: E402
import taskfile  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_support import beads_home, check, copy_beads, finish, git, local, run_parallel, say, select, write  # noqa: E402

# 利用者の値のままだと、一時リポジトリの台帳がその置き場に積もる。
os.environ.pop(ledger.STATE_DIR_ENV, None)
# このプロセスは `task.py` の子プロセスが書いたあとに読み直すので、読みを使い回さない。
beads.READ_REUSE_SECONDS = 0

TASK_PY = os.path.join(HERE, "task.py")
INIT_PY = os.path.join(HERE, "init.py")
MATERIAL_PY = os.path.join(HERE, "..", "..", "retrospect", "scripts", "material.py")
BODY = task_body(acceptance="- 通る", caution="z")
# 登録の既定の本文。`make_repo` が主ブランチに置く `shared.txt` を名指す。
PLANNED_BODY = task_body([("書く", "x")], ["shared.txt"], acceptance="- 通る", caution="z")



def env() -> dict[str, str]:
    """テストは CPU 数の半分まで並行に走らせる。出力と環境変数はテストごとに持つ。"""
    return getattr(local, "env", None) or dict(os.environ)


def run_task(cwd: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, TASK_PY, *args], cwd=cwd, capture_output=True, text=True, input=stdin, env=env()
    )


def start_task(cwd: str, *args: str, stdin: str | None = None) -> subprocess.Popen:
    p = subprocess.Popen(
        [sys.executable, TASK_PY, *args],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env(),
    )
    return p


def bd(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bd", *args], cwd=cwd, capture_output=True, text=True, env=env())


def rows(out: str) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for line in out.split("---")[0].splitlines():
        cols = line.split("\t")
        if len(cols) == 8:
            table[cols[0]] = cols
    return table


def tail_line(out: str, key: str) -> str:
    return next((l for l in out.splitlines() if l.startswith(key + "\t")), "")


def make_repo(tmp: str, branch: str | None = "切らない", extra: str = "", verify: str | None = None,
              prefix: str | None = beads.PREFIX_LOCAL, legacy: bool = False) -> tuple[str, str, str]:
    """`(本体, 作業ツリー1, 作業ツリー2)`。`.beads` は `prefix` の作り置きを写してから `init.py` を打つ。
    `prefix` が `None` なら `init.py` が `bd init` で作る。設定は `.tw/config.toml` に書き、`extra` はその続きの行。
    `verify` はコマンドそのもの。`branch` が `None` なら `branch` を書かない。`legacy` なら旧い「## タスク運用」節を
    CLAUDE.md に書く（`extra` は節の行）。"""
    main_path = os.path.join(tmp, "base")
    os.makedirs(main_path)
    git(main_path, "init", "-q", "-b", "main")
    git(main_path, "config", "user.email", "test@example.com")
    git(main_path, "config", "user.name", "test")
    git(main_path, "config", "beads.role", "maintainer")
    if legacy:
        config = "# x\n\n## タスク運用\n\n"
        config += f"- 検証コマンド: `{verify}`\n" if verify else "- 検証コマンド: なし\n"
        config += "- 整形コマンド: なし\n" + (f"- ブランチ: {branch}\n" if branch is not None else "")
        config += f"- タスクの置き場: beads\n{extra}"
        write(os.path.join(main_path, "CLAUDE.md"), config)
        write(os.path.join(main_path, "develop", "direction.md"), "# 未対応の指示メモ\n\n## ユーザーから\n")
    else:
        config = f'verify = "{verify or "なし"}"\n' + (f'branch = "{branch}"\n' if branch is not None else "")
        config += f'store = "beads"\n{extra}'
        write(os.path.join(main_path, ".tw", "config.toml"), config)
    write(os.path.join(main_path, "shared.txt"), "line1\n")
    if prefix is not None:
        copy_beads(main_path, prefix)
    if legacy:
        write(os.path.join(main_path, ".tw", "config.toml"), 'verify = "なし"\nstore = "beads"\n')
    r = subprocess.run([sys.executable, INIT_PY], cwd=main_path, capture_output=True, text=True, env=env())
    if r.returncode != 0:
        raise RuntimeError(f"init.py 失敗: {r.stdout}{r.stderr}")
    if legacy:
        shutil.rmtree(os.path.join(main_path, ".tw"))
    git(main_path, "add", "-A")
    git(main_path, "commit", "-q", "-m", "init")
    wt1 = os.path.join(tmp, "wt1")
    wt2 = os.path.join(tmp, "wt2")
    git(main_path, "worktree", "add", "-q", "-b", "wt1", wt1, "main")
    git(main_path, "worktree", "add", "-q", "-b", "wt2", wt2, "main")
    return main_path, wt1, wt2


def test_migrate_layout() -> None:
    say("migrate-layout: Beads 方式では task/ を作らず .beads に触らず、ほかの作業ツリーの in_progress で BUSY")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp)
        a = new(main_path, "移す前")
        r = run_task(wt1, "claim", a)
        check("claim が通る", r.stdout.startswith("CLAIMED\t"), r.stdout + r.stderr)
        os.makedirs(os.path.join(main_path, "develop"))
        git(main_path, "mv", ".tw/direction.md", "develop/direction.md")
        git(main_path, "rm", "-q", "-r", "-f", ".tw")
        write(os.path.join(main_path, "CLAUDE.md"),
              "# x\n\n## タスク運用\n\n- 検証コマンド: なし\n- 整形コマンド: なし\n- タスクの置き場: beads\n")
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "旧配置")
        git(wt1, "merge", "-q", "--ff-only", "main")
        r = run_task(main_path, "migrate-layout", "--dry-run")
        check("ほかの作業ツリーの in_progress があれば BUSY（終了コード4）",
              r.returncode == 4 and r.stdout.strip() == f"BUSY\t{a}\twt1", r.stdout + r.stderr)
        r = subprocess.run(["bd", "unclaim", beads.to_bd_id(a), "--force"], cwd=wt1, capture_output=True, text=True, env=env())
        check("旧配置の status は止まるので bd で印を外す", r.returncode == 0, r.stdout + r.stderr)
        beads_dir = os.path.join(main_path, ".beads")
        before = sorted(os.listdir(beads_dir))
        r = run_task(main_path, "migrate-layout")
        lines = r.stdout.splitlines()
        check("Beads 方式は direction.md だけを移して MIGRATED",
              r.returncode == 0 and lines[-1] == "MIGRATED" and "MOVE\tdevelop/direction.md\t.tw/direction.md" in lines
              and not any(l.startswith("MOVE\tdevelop/task") for l in lines), r.stdout + r.stderr)
        config = open(os.path.join(main_path, ".tw", "config.toml"), encoding="utf-8").read()
        check(".tw/task/ を作らず、.beads は変わらず、config.toml は store = beads",
              not os.path.exists(os.path.join(main_path, ".tw", "task")) and sorted(os.listdir(beads_dir)) == before
              and 'store = "beads"\n' in config and "root" not in config, config)
        git(main_path, "commit", "-q", "-m", "移す")
        r = run_task(main_path, "status")
        check("移したあとも Beads のタスクを読む", r.returncode == 0 and any(l.startswith(f"{a}\t") for l in r.stdout.splitlines()),
              r.stdout + r.stderr)


def new(cwd: str, summary: str, *extra: str, body: str = PLANNED_BODY) -> str:
    r = run_task(cwd, "new", "--summary", summary, "--difficulty", "sonnet", "--loopable", "Y", *extra,
                 "--body-file", "-", stdin=body)
    if r.returncode != 0:
        raise RuntimeError(f"task new 失敗: {r.stdout}{r.stderr}")
    return r.stdout.split("\t")[1]


def new_unplanned(cwd: str, summary: str) -> str:
    """`## やること` の空な todo（`--hold` で登録して戻す）。"""
    task_id = new(cwd, summary, "--hold", body=BODY)
    r = run_task(cwd, "edit", task_id, "--status", "todo")
    if r.returncode != 0:
        raise RuntimeError(f"task edit --status todo 失敗: {r.stdout}{r.stderr}")
    return task_id


def work_and_done(wt: str, task_id: str, *, dropped: bool = False) -> None:
    write(os.path.join(wt, f"{task_id}.txt"), f"{task_id}\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", f"{task_id}: 作業")
    extra = ("--dropped",) if dropped else ()
    r = run_task(wt, "done", task_id, *extra, "--result-file", "-", stdin="- 検証: なし\n- 振り返り: 兆候なし\n")
    if r.returncode != 0:
        raise RuntimeError(f"task done 失敗: {r.stdout}{r.stderr}")


# --- テスト -------------------------------------------------------------------


def test_direct_mark() -> None:
    say("new・edit・claim --direct: 近道の印は label direct:Y で、基準に当たるときだけ付く")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp)

        def register(difficulty: str, body: str = PLANNED_BODY) -> subprocess.CompletedProcess:
            return run_task(main_path, "new", "--summary", "近道", "--difficulty", difficulty, "--loopable", "Y",
                            "--direct", "--body-file", "-", stdin=body)

        def shown(task_id: str) -> str:
            return run_task(main_path, "show", task_id).stdout.split("\n---\n", 1)[0]

        r = register("sonnet")
        check("difficulty が haiku でない --direct は終了コード2", r.returncode == 2 and "近道" in r.stderr,
              r.stdout + r.stderr)
        r = register("haiku", task_body([("書く", "x"), ("足す", "y")], ["shared.txt"], acceptance="- 通る"))
        check("段が2つの --direct は終了コード2", r.returncode == 2 and "近道" in r.stderr, r.stdout + r.stderr)
        r = register("haiku")
        task_id = r.stdout.split("\t")[1] if r.stdout.startswith("CREATED\t") else ""
        check("haiku・1段の --direct は CREATED で、show の front matter に direct: Y が出る",
              task_id != "" and "\nloopable: Y\ndirect: Y\n" in shown(task_id), r.stdout + r.stderr)
        r = run_task(wt1, "claim", task_id)
        check("印があり計画が古くなければ CLAIMED の行末に direct=Y",
              r.returncode == 0 and tail_line(r.stdout, "CLAIMED").endswith("\tdirect=Y"), r.stdout + r.stderr)
        r = run_task(main_path, "edit", task_id, "--direct", "N")
        check("edit --direct N は label を外す", r.returncode == 0 and "direct:" not in shown(task_id), r.stdout + r.stderr)
        r = run_task(main_path, "edit", task_id, "--direct", "Y")
        check("edit --direct Y は label を付ける", r.returncode == 0 and "direct: Y" in shown(task_id), r.stdout + r.stderr)


def test_setup_and_config_doctor() -> None:
    say("init.py・config-doctor・MISSING・設定の読み違い")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, prefix=None)
        check("init.py が .beads を作る", os.path.isdir(os.path.join(main_path, ".beads")))
        check("stealth で .beads は git に見えない", git(main_path, "status", "--porcelain").stdout.strip() == "")
        r = run_task(wt1, "config-doctor")
        check("config-doctor が store・beads・tracker の行を足す", r.returncode == 0
              and tail_line(r.stdout, "store").startswith("store\tOK\tbeads")
              and tail_line(r.stdout, "beads").startswith("beads\tOK")
              and tail_line(r.stdout, "tracker") == "tracker\tOK\tなし", r.stdout)
        r = run_task(wt1, "status")
        check("まっさらな status は集計だけ（triage 行つき）", r.returncode == 0 and r.stdout.startswith("---\n")
              and "triage\t0\t-" in r.stdout, r.stdout)
        r = run_task(wt1, "prune")
        check("prune は NOTHING", r.returncode == 0 and r.stdout.startswith("NOTHING"), r.stdout)

        shutil.rmtree(os.path.join(main_path, ".beads"))
        r = run_task(wt1, "status")
        check(".beads が無ければ MISSING（終了コード6）", r.returncode == 6 and r.stdout.startswith("MISSING"), r.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, legacy=True, extra="- バックアップ: `/tmp/keep`（git の外）\n")
        r = run_task(wt1, "config-doctor")
        check("旧い節は読まず、config は MISSING で old_section の行は出さない", r.returncode == 1
              and tail_line(r.stdout, "config").startswith("config\tMISSING\t.tw/config.toml")
              and tail_line(r.stdout, "old_section") == "", r.stdout)
        r = run_task(wt1, "config")
        check("旧配置の config は OLD_LAYOUT で止まる（終了コード5）",
              r.returncode == 5 and r.stdout == "OLD_LAYOUT\ttw migrate-layout --dry-run\n", r.stdout)
        r = run_task(wt1, "status")
        check("旧配置の status も OLD_LAYOUT で止まる（終了コード5）",
              r.returncode == 5 and r.stdout == "OLD_LAYOUT\ttw migrate-layout --dry-run\n", r.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp, extra='tracker = "gitlab"\n')
        r = run_task(wt1, "status")
        check("読めないトラッカーは INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID"), r.stdout)
        write(os.path.join(wt1, ".tw", "config.toml"), 'verify = "なし"\nstore = "どこか"\n')
        r = run_task(wt1, "status")
        check("読めない置き場は INVALID（終了コード3）", r.returncode == 3 and r.stdout.startswith("INVALID"), r.stdout)
        os.remove(os.path.join(wt1, ".tw", "config.toml"))
        write(os.path.join(wt1, "develop", "direction.md"), "# 未対応の指示メモ\n\n## ユーザーから\n")
        write(os.path.join(wt1, "CLAUDE.md"), "# x\n\n## タスク運用\n\n- ブランチ: 切らない\n- タスクの置き場: どこか\n")
        r = run_task(wt1, "status")
        check("旧配置は節の中身が読めなくても OLD_LAYOUT（終了コード5）", r.returncode == 5 and r.stdout.startswith("OLD_LAYOUT"), r.stdout)


def test_file_mode_untouched_by_beads_dir() -> None:
    say("store が無ければ .beads があってもファイル方式のまま")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _ = make_repo(tmp)
        write(os.path.join(main_path, ".tw", "config.toml"), 'verify = "なし"\nbranch = "切らない"\n')
        git(main_path, "add", "-A")
        git(main_path, "commit", "-q", "-m", "ファイル方式へ")
        r = run_task(main_path, "new", "--summary", "f", "--difficulty", "haiku", "--loopable", "Y",
                     "--body-file", "-", stdin=PLANNED_BODY)
        check("new がタスクファイルを作る", r.returncode == 0 and ".tw/task/T-001.md" in r.stdout
              and os.path.exists(os.path.join(main_path, ".tw", "task", "T-001.md")), r.stdout)
        r = run_task(main_path, "config-doctor")
        check("config-doctor は store の行を足さない（3行）", len(r.stdout.strip().splitlines()) == 3, r.stdout)
        r = run_task(main_path, "edit", "T-001", "--summary", "x")
        check("edit はファイル方式では --body-file だけ（ほかは終了コード2）", r.returncode == 2, r.stdout + r.stderr)
        r = run_task(main_path, "show", "T-001")
        check("show はファイル方式でもタスクファイルを出す", r.returncode == 0 and r.stdout.startswith("---\nid: T-001"), r.stdout)
        r = bd(main_path, "list", "--json", "--all")
        check("Beads には何も作らない", json.loads(r.stdout or "[]") == [], r.stdout)


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
        outs = [p.communicate(PLANNED_BODY)[0] for p in procs]
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
        r = run_task(loser, "release", a)
        check("印の無い release は NOT_CLAIMED（終了コード0）", r.returncode == 0 and r.stdout.startswith("NOT_CLAIMED"), r.stdout)
        write(os.path.join(loser, "dirty.txt"), "x\n")
        r = run_task(loser, "claim", h)
        check("汚れた作業ツリーでは DIRTY", r.returncode == 4 and r.stdout.startswith("DIRTY"), r.stdout)


def guard_denies(tmp: str, where: str, command: str = "git commit -m x") -> bool:
    """`tw commit-guard` を git の外（`tmp`）から打ち、拒んだか。"""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": where})
    r = run_task(tmp, "commit-guard", stdin=payload)
    if r.returncode != 0:
        raise RuntimeError(f"commit-guard が {r.returncode} で終わった: {r.stderr}")
    return '"deny"' in r.stdout


def test_commit_guard() -> None:
    say("commit-guard: 印が立って done 前の作業ツリーのコミットを拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        a = new(main_path, "拒む")
        c = new(main_path, "人が外す")

        run_task(wt1, "claim", a)
        check("claim で控えが立つ（作業ツリーごと）",
              ledger.open_claims(cwd=wt1) == [a] and ledger.open_claims(cwd=wt2) == [], str(ledger.open_claims(cwd=wt1)))
        check("印の作業ツリーの git commit は拒む", guard_denies(tmp, wt1))
        check("印の無い作業ツリーの git commit は通す", not guard_denies(tmp, wt2))
        check("別の作業ツリーへの git -C は通す", not guard_denies(tmp, wt1, f"git -C {wt2} commit -m x"))
        write(os.path.join(wt1, "work.txt"), "x\n")
        git(wt1, "add", "work.txt")
        git(wt1, "commit", "-q", "-m", "メインの手直し")
        check("hook を通らないメインのコミットは印があっても通る",
              git(wt1, "log", "-1", "--format=%s").stdout.strip() == "メインの手直し")
        r = run_task(wt1, "done", a, "--result-file", "-", stdin="- 検証: なし\n- 振り返り: 兆候なし\n")
        check("done で控えが消え、そのあとの git commit は通る",
              r.returncode == 0 and ledger.open_claims(cwd=wt1) == [] and not guard_denies(tmp, wt1), r.stdout)

        run_task(wt2, "claim", c)
        r = run_task(wt1, "release", c, "--force")
        check("人が --force で外すと持ち主の作業ツリーの控えも消える",
              r.returncode == 0 and ledger.open_claims(cwd=wt2) == [] and not guard_denies(tmp, wt2), r.stdout)


def handback_reason(tmp: str, where: str) -> str | None:
    """`tw handback-guard` に SubagentStop を渡し、block の理由。通したら `None`。"""
    payload = json.dumps({"hook_event_name": "SubagentStop", "cwd": where})
    r = run_task(tmp, "handback-guard", stdin=payload)
    if r.returncode != 0:
        raise RuntimeError(f"handback-guard が {r.returncode} で終わった: {r.stderr}")
    return json.loads(r.stdout)["reason"] if r.stdout else None


def test_handback_guard() -> None:
    say("handback-guard・pause: 作業があるのに計画か検証が欠けた返却を拒む")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp, verify="echo verified")
        a = new_unplanned(main_path, "先に計画")

        check("着手の印が無い委譲は通す", handback_reason(tmp, wt1) is None)
        run_task(wt1, "claim", a)
        check("印があっても作業が無ければ通す", handback_reason(tmp, wt1) is None)
        run_task(wt1, "edit", a, "--section", "やること", "--body-file", "-", stdin=(
            "### 1. 書く\n- 触るファイル: `src/a.py`\n"
            "### 2. 文書\n- 前の段: 1\n- 触るファイル: `docs/`\n"
        ))
        check("計画だけの回は通す", handback_reason(tmp, wt1) is None)
        write(os.path.join(wt1, "work.txt"), "x\n")
        reason = handback_reason(tmp, wt1) or ""
        check("計画があっても検証が無ければ block（NOT_VERIFIED）",
              "NOT_VERIFIED\tnone" in reason and "PLAN_NOT_FIRST" not in reason, reason)
        r = run_task(wt1, "verify")
        check("PLAN_FIRST と tw verify がそろえば通す", r.returncode == 0 and handback_reason(tmp, wt1) is None,
              r.stdout + r.stderr)


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

        work_and_done(wt1, a)
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
        check("show は枠の7節をこの順に出し、結果がその後ろに付く",
              heads == [*taskfile.SECTION_HEADINGS, "## 結果"], shown)

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


class FakeGitHub:
    """偽の GitHub。REST の Issue（本物の `bd github push`・`pull` が `GITHUB_API_URL` で叩く）と、
    Project の GraphQL（偽の `gh api graphql` が転送する）を、1つの HTTP サーバの中の状態で受ける。"""

    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self.items: dict[str, dict] = {}  # 項目 ID → {"url", "status"}
        self.option_prefix = "O-"
        self.clock_offset = 0  # 秒。GitHub 側の編集を後の時刻にする
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401
                pass

            def _reply(self, code: int, obj) -> None:
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode() or "null") if n else None
                with fake.lock:
                    code, obj = fake.route(self.command, self.path, body)
                self._reply(code, obj)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + self.clock_offset))

    def issue_json(self, i: dict) -> dict:
        n = i["number"]
        return {
            "id": 1000 + n, "node_id": f"I_{n}", "number": n, "title": i["title"], "body": i["body"],
            "state": i["state"], "labels": [{"name": l} for l in i["labels"]], "assignees": [], "assignee": None,
            "created_at": i["created_at"], "updated_at": i["updated_at"], "closed_at": None,
            "html_url": f"https://github.com/o/r/issues/{n}", "url": f"{self.url}/repos/o/r/issues/{n}",
            "user": {"login": "someone"},
        }

    def edit(self, number: int, **fields) -> None:
        """人が GitHub で Issue を直した（題・本文・state・labels）。"""
        with self.lock:
            self.issues[number].update(fields)
            self.issues[number]["updated_at"] = self.now()

    def open_issue(self, title: str, labels: list[str]) -> int:
        with self.lock:
            return self._create({"title": title, "body": "外で立てた", "labels": labels})["number"]

    def _create(self, body: dict) -> dict:
        n = len(self.issues) + 1
        issue = {"number": n, "title": body.get("title", ""), "body": body.get("body") or "", "state": "open",
                 "labels": list(body.get("labels") or []), "created_at": self.now(), "updated_at": self.now()}
        self.issues[n] = issue
        return issue

    def status_of(self, number: int) -> str | None:
        url = f"https://github.com/o/r/issues/{number}"
        return next((i["status"] for i in self.items.values() if i["url"] == url), None)

    def route(self, method: str, path: str, body):
        u = urlparse(path)
        m = re.fullmatch(r"/repos/o/r/issues/(\d+)", u.path)
        if u.path == "/graphql":
            return 200, self.answer_graphql(body["query"], body.get("variables") or {})
        if method == "POST" and u.path == "/repos/o/r/issues":
            return 201, self.issue_json(self._create(body))
        if m and method == "PATCH":
            issue = self.issues[int(m.group(1))]
            for k in ("title", "body", "state", "labels"):
                if k in body:
                    issue[k] = body[k]
            issue["updated_at"] = self.now()
            return 200, self.issue_json(issue)
        if m and method == "GET":
            issue = self.issues.get(int(m.group(1)))
            return (200, self.issue_json(issue)) if issue else (404, {"message": "Not Found"})
        if u.path == "/repos/o/r/issues" and method == "GET":
            q = parse_qs(u.query)
            state, since = (q.get("state") or ["open"])[0], (q.get("since") or [""])[0]
            found = [i for i in self.issues.values() if (state == "all" or i["state"] == state)
                     and (not since or i["updated_at"] >= since)]
            return 200, [self.issue_json(i) for i in found]
        return 404, {"message": f"fake: {method} {u.path}"}

    def answer_graphql(self, query: str, v: dict) -> dict:
        names = ["Pending", "Todo", "In progress", "Done", "Cancel"]
        if "repositoryOwner" in query:
            options = [{"id": self.option_prefix + n, "name": n} for n in names]
            return {"data": {"repositoryOwner": {"projectV2": {"id": "PID", "field": {"id": "FID", "options": options}}}}}
        if "projectItems" in query:
            url = f"https://github.com/{v['owner']}/{v['repo']}/issues/{v['number']}"
            nodes = [{"id": k, "project": {"id": "PID"}} for k, i in self.items.items() if i["url"] == url]
            return {"data": {"repository": {"issue": {"id": f"I_{v['number']}", "projectItems": {"nodes": nodes}}}}}
        if "addProjectV2ItemById" in query:
            n = v["content"].split("_", 1)[1]
            item_id = f"PVTI_{n}"
            self.items[item_id] = {"url": f"https://github.com/o/r/issues/{n}", "status": None}
            return {"data": {"addProjectV2ItemById": {"item": {"id": item_id}}}}
        if "updateProjectV2ItemFieldValue" in query:
            option, item = v["option"], self.items.get(v["item"])
            if item is None or not option.startswith(self.option_prefix):
                return {"errors": [{"message": "Could not resolve to a node"}]}
            item["status"] = option[len(self.option_prefix):]
            return {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": v["item"]}}}}
        if "items(first" in query:
            nodes = [{"id": k, "content": {"url": i["url"]},
                      "fieldValueByName": {"name": i["status"]} if i["status"] else None} for k, i in self.items.items()]
            return {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}
        return {"errors": [{"message": "fake: 知らない問い合わせ"}]}


def _fake_bin(tmp: str) -> str:
    """偽の `gh`（`api` を偽の GitHub へ転送する）。"""
    bin_dir = os.path.join(tmp, "bin")
    write(os.path.join(bin_dir, "gh"), f"""#!{sys.executable}
import json, os, sys, urllib.request
args = sys.argv[1:]
server = os.environ["GITHUB_API_URL"]
def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(server + path, data=data, method=method, headers={{"Content-Type": "application/json"}})
    try:
        return json.load(urllib.request.urlopen(req, timeout=10))
    except OSError as e:
        sys.stderr.write(f"fake gh: {{e}}\\n")
        sys.exit(1)
if args[:2] == ["auth", "token"]:
    print("fake-token")
elif args[:2] == ["api", "graphql"]:
    query, variables = "", {{}}
    for flag, kv in zip(args[2::2], args[3::2]):
        k, v = kv.split("=", 1)
        if k == "query":
            query = v
        else:
            variables[k] = int(v) if flag == "-F" else v
    print(json.dumps(call("POST", "/graphql", {{"query": query, "variables": variables}})))
elif args[:1] == ["api"]:
    path = "/" + [a for a in args[1:] if not a.startswith("--")][0]
    out = call("GET", path)
    print(json.dumps([out] if "--slurp" in args else out))
else:
    sys.stderr.write("fake gh: 知らない呼び出し " + " ".join(args) + "\\n")
    sys.exit(1)
""")
    os.chmod(os.path.join(bin_dir, "gh"), 0o755)
    return bin_dir


def _with_fakes(tmp: str, fake: FakeGitHub) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = _fake_bin(tmp) + os.pathsep + env.get("PATH", "")
    env["GITHUB_API_URL"] = fake.url
    env.pop("GITHUB_TOKEN", None)
    return env


@contextlib.contextmanager
def _github(prefix: str | None = beads.PREFIX_GITHUB) -> Iterator[tuple["FakeGitHub", str, str, str]]:
    """偽の GitHub へ向けた `(偽の GitHub, 一時ディレクトリ, 本体, 作業ツリー1)`。
    `prefix` が `None` なら `.beads` は `init.py` がトラッカーの行から prefix を選んで作る。"""
    fake = FakeGitHub()
    with tempfile.TemporaryDirectory() as tmp:
        local.env = _with_fakes(tmp, fake)
        try:
            main_path, wt1, _ = make_repo(tmp, extra='tracker = "github"\ngithub_project = "sinnlosses/1"\n', prefix=prefix)
            bd(main_path, "config", "set", "github.repository", "o/r")
            yield fake, tmp, main_path, wt1
        finally:
            fake.close()
            del local.env


def _num(task_id: str) -> int:
    return int(beads.to_bd_id(task_id).split("-")[1])


def test_tracker_github_bidirectional() -> None:
    say("トラッカー github・issue_prefix gh（Issue 番号の ID・送りと取り込みの往復）")
    with _github() as (fake, _tmp, main_path, wt1):
        fake.open_issue("先にある PR 以外の Issue", [])
        a = new(main_path, "Issue を先に立てる")
        check("new は Issue を立てて GH-<番号> を返す", a == "GH-2" and beads.show(main_path, "gh-2") is not None, a)
        check("仮の ID は残らない", not any(i.bd_id.startswith("gh-new-") for i in beads.list_issues(main_path)))
        check("Status 欄も書く", fake.status_of(2) == "Todo", repr(fake.items))

        n, bd_id = _num(a), beads.to_bd_id(a)
        r = run_task(wt1, "claim", a)
        check("claim", r.returncode == 0 and r.stdout.startswith("CLAIMED"), r.stdout + r.stderr)
        fake.edit(n, body="GitHub で直した本文")
        r = run_task(main_path, "sync")
        check("sync が GitHub で立てた Issue を取り込み、番号の ID へ付け替えて振り分け前にする", r.returncode == 0
              and "triage\t1\tGH-1" in run_task(main_path, "status").stdout, r.stdout)
        issue = beads.show(main_path, bd_id)
        check("取り込みで GitHub の本文が入り、錠の持ち主（assignee）は戻る", issue is not None
              and "GitHub で直した本文" in str(issue.raw.get("description"))
              and issue.assignee == "wt1" and issue.status == "in_progress", r.stdout + str(issue and issue.raw))


def test_tracker_github_conflict() -> None:
    say("トラッカー github・issue_prefix gh（init.py が gh で作る・両側で変えたら Beads が勝つ）")
    with _github(prefix=None) as (fake, _tmp, main_path, _wt1):
        check("トラッカーが github なら init.py は issue_prefix gh で .beads を作る",
              beads.read_prefix(main_path) == beads.PREFIX_GITHUB)
        a = new(main_path, "両側で直す")
        n, bd_id = _num(a), beads.to_bd_id(a)
        time.sleep(1.1)
        bd(main_path, "update", bd_id, "--title", "Beads で直した題")
        fake.clock_offset = 60
        fake.edit(n, body="GitHub でも直した")
        r = run_task(main_path, "sync")
        check("両側で変えたら CONFLICT の行を出し、Beads が勝つ", f"TRACKER\tCONFLICT\t{a}" in r.stdout
              and fake.issues[n]["title"] == "Beads で直した題", r.stdout + repr(fake.issues[n]))


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


def main() -> None:
    only = sys.argv[1:]  # テストの関数名を渡すとそれだけを走らせる（手で直すとき）
    with beads_home((beads.PREFIX_LOCAL, beads.PREFIX_GITHUB)):
        # 長いものから並列に乗せる（後ろに残ると全体がその分延びる）。
        tests = (
            test_tracker_github_bidirectional,
            test_tracker_github_conflict,
            test_cycle_done_ship_and_dropped,
            test_commit_guard,
            test_claim_race_owner_and_release,
            test_handback_guard,
            test_setup_and_config_doctor,
            test_backup,
            test_direct_mark,
            test_file_mode_untouched_by_beads_dir,
            test_migrate_layout,
            test_id_forms,
            test_bd_time_forms,
        )
        outputs = run_parallel(select(tests, only))
    finish([*outputs, local.lines])


if __name__ == "__main__":
    main()
