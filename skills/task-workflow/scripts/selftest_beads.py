#!/usr/bin/env python3
"""`task.py` の Beads との往復（`bd`・トラッカー・設定の読み）の自己テスト。

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
import tracker  # noqa: E402
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


def claim_snapshots(cwd: str) -> dict[str, beads.Issue]:
    return beads.claim_snapshots(beads.read_kv(cwd))


def tail_line(out: str, key: str) -> str:
    return next((l for l in out.splitlines() if l.startswith(key + "\t")), "")


def make_repo(tmp: str, branch: str | None = "切らない", extra: str = "", verify: str | None = None,
              prefix: str | None = beads.PREFIX_LOCAL) -> tuple[str, str, str]:
    """`(本体, 作業ツリー1, 作業ツリー2)`。`.beads` は `prefix` の作り置きを写してから `init.py` を打つ。
    `prefix` が `None` なら `init.py` が `bd init` で作る。設定は `.tw/config.toml` に書き、`extra` はその続きの行。
    `verify` はコマンドそのもの。`branch` が `None` なら `branch` を書かない。"""
    main_path = os.path.join(tmp, "base")
    os.makedirs(main_path)
    git(main_path, "init", "-q", "-b", "main")
    git(main_path, "config", "user.email", "test@example.com")
    git(main_path, "config", "user.name", "test")
    git(main_path, "config", "beads.role", "maintainer")
    config = f'verify = "{verify or "なし"}"\n' + (f'branch = "{branch}"\n' if branch is not None else "")
    config += f'store = "beads"\n{extra}'
    write(os.path.join(main_path, ".tw", "config.toml"), config)
    write(os.path.join(main_path, "shared.txt"), "line1\n")
    if prefix is not None:
        copy_beads(main_path, prefix)
    r = subprocess.run([sys.executable, INIT_PY], cwd=main_path, capture_output=True, text=True, env=env())
    if r.returncode != 0:
        raise RuntimeError(f"init.py 失敗: {r.stdout}{r.stderr}")
    git(main_path, "add", "-A")
    git(main_path, "commit", "-q", "-m", "init")
    wt1 = os.path.join(tmp, "wt1")
    wt2 = os.path.join(tmp, "wt2")
    git(main_path, "worktree", "add", "-q", "-b", "wt1", wt1, "main")
    git(main_path, "worktree", "add", "-q", "-b", "wt2", wt2, "main")
    return main_path, wt1, wt2


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


def test_difficulty_loopable_metadata() -> None:
    say("difficulty・loopable: new・edit は metadata に書き、読みは metadata だけで、取り込みで消えたら戻す")
    import tracker  # noqa: PLC0415

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
          tracker._claim_restore_args(issue(metadata=marks), issue(), None, None)
          == ["--set-metadata", "difficulty=opus", "--set-metadata", "loopable=N"])
    check("片方だけ消えたらその1つだけ戻す",
          tracker._claim_restore_args(issue(metadata=marks), issue(metadata={"difficulty": "opus"}), None, None)
          == ["--set-metadata", "loopable=N"])
    check("閉じた課題も2キーだけ戻す",
          tracker._claim_restore_args(issue(metadata={**marks, "task_branch": "b"}), issue("closed"), None, None)
          == ["--set-metadata", "difficulty=opus", "--set-metadata", "loopable=N"])

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


def test_migrate_attributes() -> None:
    say("migrate-attributes: label の difficulty・loopable を metadata へ写して外し、打ち直しても変わらない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, _, _ = make_repo(tmp)

        def create(bd_id: str, labels: str) -> None:
            r = bd(main_path, "create", "--id", bd_id, "--title", f"課題{bd_id}", "-l", labels, "-d", "本文")
            check(f"{bd_id} を作れる", r.returncode == 0, r.stdout + r.stderr)

        def state(bd_id: str) -> tuple[dict, list[str]]:
            issue = beads.show(main_path, bd_id)
            assert issue is not None
            meta = issue.raw.get("metadata")
            return (meta if isinstance(meta, dict) else {}), sorted(issue.labels)

        create("t-801", "difficulty:opus,loopable:N,direct:Y,mytag")
        create("t-802", "difficulty:opus,loopable:N,ship:done")
        bd(main_path, "close", "t-802")
        create("t-803", "difficulty:opus,loopable:N")
        bd(main_path, "update", "t-803", "--set-metadata", "difficulty=haiku", "--set-metadata", "loopable=Y")
        create("t-804", "difficulty:gpt,loopable:N")
        create("t-805", "difficulty:sonnet")
        create("t-806", "mytag")
        before_bad = (state("t-804"), state("t-805"), state("t-806"))

        r = run_task(main_path, "migrate-attributes")
        check("INVALID があれば終了コード3", r.returncode == 3, r.stdout + r.stderr)
        meta, labels = state("t-801")
        check("label だけの課題は metadata へ移り、direct:Y と自由タグは残る",
              meta.get("difficulty") == "opus" and meta.get("loopable") == "N" and labels == ["direct:Y", "mytag"],
              str((meta, labels)))
        meta, labels = state("t-802")
        check("閉じた課題も移り、ship:done は残る",
              meta.get("difficulty") == "opus" and meta.get("loopable") == "N" and labels == ["ship:done"],
              str((meta, labels)))
        meta, labels = state("t-803")
        check("metadata が既にあれば metadata のまま label だけ外す",
              meta.get("difficulty") == "haiku" and meta.get("loopable") == "Y" and labels == [], str((meta, labels)))
        check("値が不正・片方だけ・属性なしの課題は変わらない",
              (state("t-804"), state("t-805"), state("t-806")) == before_bad, r.stdout)
        check("INVALID の行に2件が出る（ほかは出ない）",
              "INVALID\tT-804\t" in r.stdout and "INVALID\tT-805\t" in r.stdout and "T-806" not in r.stdout
              and "MIGRATED_TOTAL\t3\tINVALID\t2" in r.stdout, r.stdout)
        check("移した3件が MIGRATED の行に出る",
              all(f"MIGRATED\tT-{n}\n" in r.stdout for n in (801, 802, 803)), r.stdout)
        shown_now = run_task(main_path, "show", "T-801").stdout
        check("移したあとの show は metadata の値を読む", "\ndifficulty: opus\nloopable: N\n" in shown_now, shown_now)

        after_first = [state(f"t-80{n}") for n in (1, 2, 3)]
        r = run_task(main_path, "migrate-attributes")
        check("打ち直すと移す課題が 0 で、移した課題は変わらない",
              "MIGRATED_TOTAL\t0\tINVALID\t2" in r.stdout and [state(f"t-80{n}") for n in (1, 2, 3)] == after_first,
              r.stdout + r.stderr)


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
        write(os.path.join(wt1, "develop", "direction.md"), "# 未対応の指示メモ\n\n## ユーザーから\n")
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
        self.status_writes = 0  # Status 欄を書いた回数
        self.issue_patches: list[int] = []  # Issue を PATCH で更新した番号（順に）
        self.fetched: list[int] = []  # Issue を1件ずつ GET で取った番号（`bd github pull` が取りに来た分）
        self.clock_offset = 0  # 秒。GitHub 側の編集を後の時刻にする
        self.delay = 0.0  # 秒。応答を遅らせる
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
                time.sleep(fake.delay)
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
        login = i.get("assignee")
        user = {"login": login, "id": 5000 + n} if login else None
        return {
            "id": 1000 + n, "node_id": f"I_{n}", "number": n, "title": i["title"], "body": i["body"],
            "state": i["state"], "labels": [{"name": l} for l in i["labels"]],
            "assignees": [user] if user else [], "assignee": user,
            "created_at": i["created_at"], "updated_at": i["updated_at"], "closed_at": None,
            "html_url": f"https://github.com/o/r/issues/{n}", "url": f"{self.url}/repos/o/r/issues/{n}",
            "user": {"login": "someone"},
        }

    def edit(self, number: int, **fields) -> None:
        """人が GitHub で Issue を直した（題・本文・state・labels・担当者の login の assignee）。"""
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
            self.issue_patches.append(issue["number"])
            for k in ("title", "body", "state", "labels"):
                if k in body:
                    issue[k] = body[k]
            issue["updated_at"] = self.now()
            return 200, self.issue_json(issue)
        if m and method == "GET":
            self.fetched.append(int(m.group(1)))
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
            self.status_writes += 1
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
        fake.clock_offset = 60
        fake.edit(n, body="GitHub で直した本文")
        r = run_task(main_path, "sync")
        check("sync が GitHub で立てた Issue を取り込み、番号の ID へ付け替えて振り分け前にする", r.returncode == 0
              and "triage\t1\tGH-1" in run_task(main_path, "status").stdout, r.stdout)
        issue = beads.show(main_path, bd_id)
        check("取り込みで GitHub の本文が入り、錠の持ち主（assignee）は戻る", issue is not None
              and "GitHub で直した本文" in str(issue.raw.get("description"))
              and issue.assignee == "wt1" and issue.status == "in_progress", r.stdout + str(issue and issue.raw))


def test_tracker_timeout() -> None:
    say("トラッカー github・1件の操作は上限で打ち切って TRACKER FAILED にし、sync が打ち直す")
    with _github() as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "遅れる反映")
        local.env[tracker.OP_TIMEOUT_ENV] = "2"
        fake.delay = 5
        started = time.monotonic()
        r = run_task(main_path, "edit", a, "--difficulty", "haiku")
        elapsed = time.monotonic() - started
        edited = beads.show(main_path, beads.to_bd_id(a))
        meta = edited.raw.get("metadata") if edited is not None else {}
        check("上限を超えたら TRACKER FAILED の行を出し、終了コードは0のまま", r.returncode == 0
              and "TRACKER\tFAILED\tgithub" in r.stdout, r.stdout + r.stderr)
        check("Beads の書き込みは残る", meta.get("difficulty") == "haiku", str(edited))
        check("待ちは上限の数倍に延びない", elapsed < 12, f"{elapsed:.1f}秒")
        fake.delay = 0
        r = run_task(main_path, "sync")
        check("sync が打ち直して TRACKER OK", r.returncode == 0 and "TRACKER\tOK\tgithub" in r.stdout
              and "TRACKER\tFAILED" not in r.stdout, r.stdout + r.stderr)


def test_release_force_without_assignee() -> None:
    say("release --force: 担当者の消えた in_progress の着手の印を外せる")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        a = new(main_path, "担当者が消えた")
        bd_id = beads.to_bd_id(a)
        bd(main_path, "update", bd_id, "--status", "in_progress", "--add-label", beads.SHIP_LABELS["done"])
        stuck = beads.show(main_path, bd_id)
        check("前提: assignee 無しの in_progress", stuck is not None and stuck.status == "in_progress"
              and not stuck.assignee, str(stuck and stuck.raw))
        r = run_task(wt1, "release", a)
        check("--force 無しは NOT_OWNER（挙動は変えない）", r.returncode == 4 and r.stdout.startswith("NOT_OWNER"), r.stdout)
        r = run_task(wt1, "release", a, "--force")
        freed = beads.show(main_path, bd_id)
        check("--force は RELEASED で status が open に戻る", r.returncode == 0 and r.stdout.startswith("RELEASED")
              and freed is not None and freed.status == "open" and not freed.assignee, r.stdout + r.stderr)
        check("ship の label も残らない", freed is not None
              and not any(l in beads.SHIP_LABELS.values() for l in freed.labels), str(freed and freed.labels))
        r = run_task(wt1, "release", a, "--force")
        check("もう一度は NOT_CLAIMED", r.returncode == 0 and r.stdout.startswith("NOT_CLAIMED"), r.stdout)


def _status_labels(fake: "FakeGitHub", n: int) -> list[str]:
    return [l for l in fake.issues[n]["labels"] if l.startswith("status::")]


def test_release_force_clears_github_label() -> None:
    say("release --force: GitHub の label status::in_progress も残らず、取り込みで戻らない")
    with _github() as (fake, _tmp, main_path, wt1):
        a = new(main_path, "label が残る")
        n, bd_id = _num(a), beads.to_bd_id(a)
        bd(main_path, "update", bd_id, "--status", "in_progress")
        r = run_task(main_path, "sync")
        check("前提: GitHub に status::in_progress が付く", r.returncode == 0
              and "status::in_progress" in _status_labels(fake, n), r.stdout + repr(fake.issues[n]["labels"]))
        r = run_task(wt1, "release", a, "--force")
        check("RELEASED と TRACKER OK", r.returncode == 0 and r.stdout.startswith("RELEASED")
              and "TRACKER\tOK\tgithub" in r.stdout, r.stdout + r.stderr)
        check("GitHub の status::in_progress が外れる", "status::in_progress" not in _status_labels(fake, n),
              repr(fake.issues[n]["labels"]))
        r = run_task(main_path, "sync")
        again = beads.show(main_path, bd_id)
        check("sync のあとも open のまま", r.returncode == 0 and again is not None and again.status == "open",
              r.stdout + str(again and again.status))


def test_release_force_deadline_then_sync() -> None:
    say("release --force: 送りが上限切れでも、sync が label を外し open のまま")
    with _github() as (fake, _tmp, main_path, wt1):
        a = new(main_path, "送りが切れる")
        n, bd_id = _num(a), beads.to_bd_id(a)
        bd(main_path, "update", bd_id, "--status", "in_progress")
        r = run_task(main_path, "sync")
        check("前提: GitHub に status::in_progress が付く", r.returncode == 0
              and "status::in_progress" in _status_labels(fake, n), r.stdout + repr(fake.issues[n]["labels"]))
        local.env[tracker.OP_TIMEOUT_ENV] = "2"
        fake.delay = 5
        r = run_task(wt1, "release", a, "--force")
        check("RELEASED と TRACKER FAILED", r.returncode == 0 and r.stdout.startswith("RELEASED")
              and "TRACKER\tFAILED\tgithub" in r.stdout, r.stdout + r.stderr)
        fake.delay = 0
        r = run_task(main_path, "sync")
        again = beads.show(main_path, bd_id)
        check("sync が label を外す", r.returncode == 0 and "status::in_progress" not in _status_labels(fake, n),
              r.stdout + repr(fake.issues[n]["labels"]))
        check("Beads は open のまま", again is not None and again.status == "open", str(again and again.status))


KILL_AFTER_PULL_ENV = "SELFTEST_KILL_AFTER_PULL"


def _arm_kill_after_pull(tmp: str) -> None:
    """PATH の先頭に、`bd github pull` が返った直後に呼び手（`task.py`）へ SIGTERM を打つ包みの `bd` を置く。
    打つのは `_killed_sync` の間（`KILL_AFTER_PULL_ENV` があるとき）だけ。"""
    real = shutil.which("bd", path=local.env["PATH"])
    bin_dir = os.path.join(tmp, "killbin")
    write(os.path.join(bin_dir, "bd"), f"""#!{sys.executable}
import os, signal, subprocess, sys
r = subprocess.run([{real!r}, *sys.argv[1:]])
if os.environ.get({KILL_AFTER_PULL_ENV!r}) and "github" in sys.argv and "pull" in sys.argv:
    os.kill(os.getppid(), signal.SIGTERM)
sys.exit(r.returncode)
""")
    os.chmod(os.path.join(bin_dir, "bd"), 0o755)
    local.env["PATH"] = bin_dir + os.pathsep + local.env["PATH"]


def _killed_sync(cwd: str) -> subprocess.CompletedProcess:
    local.env[KILL_AFTER_PULL_ENV] = "1"
    try:
        return run_task(cwd, "sync")
    finally:
        del local.env[KILL_AFTER_PULL_ENV]


def _claim_and_push(fake: "FakeGitHub", main_path: str, wt: str, summary: str) -> tuple[str, str, int]:
    """`wt` が着手して本体の sync で送り、GitHub の側の更新時刻を先へずらす。`(タスクID, Beads ID, Issue 番号)`。"""
    a = new(main_path, summary)
    r = run_task(wt, "claim", a)
    if r.returncode != 0:
        raise RuntimeError(f"claim 失敗: {r.stdout}{r.stderr}")
    run_task(main_path, "sync")
    fake.clock_offset = 60
    return a, beads.to_bd_id(a), _num(a)


def test_claim_restored_after_killed_sync() -> None:
    say("着手の印の控え: sync が bd github pull の直後に止まっても、次の plan-check・sync・edit が控えから戻す")
    with _github() as (fake, tmp, main_path, wt1):
        _arm_kill_after_pull(tmp)
        a, bd_id, n = _claim_and_push(fake, main_path, wt1, "取り込みで消える")
        marks = beads.mark_values(beads.show(main_path, bd_id))
        nexts = (("plan-check", wt1, ("plan-check", a)), ("sync", main_path, ("sync",)),
                 ("edit の before", wt1, ("edit", a, "--loopable", "Y")))
        for label, where, args in nexts:
            # 前の回に戻した版は Beads の側が新しく、送るまでは取り込みが飛ばすので、先に送っておく。
            run_task(main_path, "sync")
            fake.edit(n, body=f"{label} の前に GitHub で直した")
            r = _killed_sync(main_path)
            lost = beads.show(main_path, bd_id)
            check(f"前提（{label}）: 止めた sync が担当者と印を空にしたまま残す", r.returncode != 0 and lost is not None
                  and not lost.assignee and not beads.mark_values(lost), r.stdout + str(lost and lost.raw))
            r = run_task(where, *args)
            got = beads.show(main_path, bd_id)
            check(f"{label} が assignee と MARK_KEYS を戻し、TRACKER RESTORED を出す", r.returncode == 0
                  and f"TRACKER\tRESTORED\t{a}" in r.stdout + r.stderr and got is not None
                  and got.status == "in_progress" and got.assignee == "wt1" and beads.mark_values(got) == marks,
                  r.stdout + r.stderr + str(got and got.raw))
        r = run_task(wt1, "plan-check", a)
        check("戻したあとは plan-check が戻さずに通る", r.returncode == 0 and "RESTORED" not in r.stdout + r.stderr,
              r.stdout + r.stderr)


def test_claim_not_restored_when_released_on_github() -> None:
    say("着手の印の控え: GitHub で手放された着手（担当者が付いた・label が外れた）は控えがあっても戻さない")
    with _github() as (fake, tmp, main_path, wt1):
        _arm_kill_after_pull(tmp)
        a, a_id, na = _claim_and_push(fake, main_path, wt1, "label を外される")
        b, b_id, nb = _claim_and_push(fake, main_path, wt1, "担当者を付けられる")
        fake.edit(na, labels=[l for l in fake.issues[na]["labels"] if l != "status::in_progress"])
        fake.edit(nb, assignee="someone")
        _killed_sync(main_path)
        for task_id in (a, b):
            r = run_task(wt1, "plan-check", task_id)
            check(f"{task_id}: plan-check は戻さず NOT_OWNER", r.returncode == 4 and r.stdout.startswith("NOT_OWNER")
                  and "RESTORED" not in r.stdout + r.stderr, r.stdout + r.stderr)
        check("手放された課題の控えは消える", not {a_id, b_id} & set(claim_snapshots(main_path)), str(claim_snapshots(main_path)))
        r = run_task(main_path, "sync")
        got_a, got_b = beads.show(main_path, a_id), beads.show(main_path, b_id)
        check("sync も戻さない（label を外された課題は open・担当者を付けられた課題は wt1 でない）", "RESTORED" not in r.stdout
              and got_a is not None and got_a.status == "open" and got_b is not None and got_b.assignee != "wt1",
              r.stdout + str(got_a and got_a.raw) + str(got_b and got_b.raw))


def test_claim_not_restored_after_release_done_ship() -> None:
    say("着手の印の控え: release・done・ship で手放した課題は、取り込みが止まっても控えから戻らない")
    with _github() as (fake, tmp, main_path, wt1):
        _arm_kill_after_pull(tmp)
        wt2 = os.path.join(tmp, "wt2")
        a, a_id, na = _claim_and_push(fake, main_path, wt1, "release する")
        r = run_task(wt1, "release", a)
        check("前提: release", r.stdout.startswith("RELEASED"), r.stdout + r.stderr)
        b, b_id, nb = _claim_and_push(fake, main_path, wt1, "done する")
        work_and_done(wt1, b)
        c, c_id, nc = _claim_and_push(fake, main_path, wt2, "ship する")
        work_and_done(wt2, c)
        r = run_task(wt2, "ship")
        check("前提: ship", r.returncode == 0 and r.stdout.startswith("SHIPPED"), r.stdout + r.stderr)
        check("控えは3つとも無い", not {a_id, b_id, c_id} & set(claim_snapshots(main_path)), str(claim_snapshots(main_path)))
        for n in (na, nb, nc):
            fake.edit(n, body="手放したあとに GitHub で直した")
        _killed_sync(main_path)
        for task_id, where in ((a, wt1), (b, wt1), (c, wt2)):
            r = run_task(where, "plan-check", task_id)
            check(f"{task_id}: plan-check は控えから戻さない", r.returncode == 4 and "RESTORED" not in r.stdout + r.stderr,
                  r.stdout + r.stderr)
        r = run_task(main_path, "sync")
        got_a, got_c = beads.show(main_path, a_id), beads.show(main_path, c_id)
        check("sync も戻さない（release は open・ship は closed のまま）", "RESTORED" not in r.stdout
              and got_a is not None and got_a.status == "open" and got_c is not None and got_c.status == "closed",
              r.stdout + str(got_a and got_a.raw) + str(got_c and got_c.raw))


def test_claim_kept_through_concurrent_syncs() -> None:
    say("着手の印: 2本・3本を同時に打つ sync で消えない")
    with _github() as (fake, tmp, main_path, wt1):
        wt2 = os.path.join(tmp, "wt2")
        a, bd_id, n = _claim_and_push(fake, main_path, wt1, "同時の sync")
        marks = beads.mark_values(beads.show(main_path, bd_id))
        for trees in ((main_path, wt2), (main_path, wt1, wt2)):
            fake.edit(n, body=f"{len(trees)}本の前に GitHub で直した")
            procs = [start_task(w, "sync") for w in trees]
            outs = [p.communicate()[0] for p in procs]
            got = beads.show(main_path, bd_id)
            check(f"{len(trees)}本の同時の sync のあとも assignee と MARK_KEYS が残る", got is not None
                  and got.status == "in_progress" and got.assignee == "wt1" and beads.mark_values(got) == marks,
                  repr(outs) + str(got and got.raw))


def _kv(cwd: str) -> dict[str, str]:
    return json.loads(bd(cwd, "kv", "list", "--json").stdout or "{}")


def test_tracker_project_items_per_issue() -> None:
    say("トラッカー github・Project の控えは課題ごとのキー（旧い1つの塊は最初の読み込みで移して消す）")
    prefix, legacy_key = tracker.ITEMS_KEY_PREFIX, tracker.LEGACY_ITEMS_KEY
    with _github() as (fake, _tmp, main_path, _wt1):
        a, b = new(main_path, "控えを移す1"), new(main_path, "控えを移す2")
        na, nb = _num(a), _num(b)
        check("new のあと控えは課題ごとのキーにある", {f"{prefix}{na}", f"{prefix}{nb}"} <= set(_kv(main_path)), str(_kv(main_path)))
        old = {k: json.loads(v) for k, v in _kv(main_path).items() if k.startswith(prefix)}
        for k in old:
            bd(main_path, "kv", "clear", k)
        legacy = {f"https://github.com/o/r/issues/{int(k[len(prefix):])}": v for k, v in old.items()}
        legacy.update({f"https://github.com/o/r/issues/{1000 + i}": {"item": f"PVTI_{1000 + i}", "status": "Todo"}
                       for i in range(150)})
        bd(main_path, "kv", "set", legacy_key, json.dumps(legacy))
        r = run_task(main_path, "sync")
        kv = _kv(main_path)
        items = {k: v for k, v in kv.items() if k.startswith(prefix)}
        check("旧い塊は課題ごとのキーへ移って消える", r.returncode == 0 and legacy_key not in kv
              and len(items) == len(legacy), r.stdout + r.stderr + str(sorted(kv)))
        check("移した値は課題の数に依らず小さい", max(len(v) for v in items.values()) < 100, str(max(items.values(), key=len)))
        check("移した値は元のまま", json.loads(items[f"{prefix}{na}"]) == legacy[f"https://github.com/o/r/issues/{na}"])

        fake.status_writes = 0
        r = run_task(main_path, "sync")
        check("移したあとの sync は同じ Status の課題へ触らない", r.returncode == 0 and fake.status_writes == 0,
              f"{fake.status_writes}\n{r.stdout}{r.stderr}")

        before = _kv(main_path)
        bd(main_path, "update", beads.to_bd_id(a), "--status", "deferred")
        r = run_task(main_path, "sync")
        after = _kv(main_path)
        changed = sorted(k for k in after if k.startswith(prefix) and before.get(k) != after[k])
        check("Status が変わった課題だけ1キー書き換わる", fake.status_writes == 1 and fake.status_of(na) == "Pending"
              and fake.status_of(nb) == "Todo" and changed == [f"{prefix}{na}"], f"{fake.status_writes} {changed}\n{r.stdout}{r.stderr}")


def test_tracker_sync_pushes_only_changed() -> None:
    say("トラッカー github・sync は変えた課題だけを送り、変わりが無ければ送らない")
    with _github() as (fake, _tmp, main_path, _wt1):
        a, b = new(main_path, "変えない"), new(main_path, "変える")
        na, nb = _num(a), _num(b)
        r = run_task(main_path, "sync")
        check("最初の sync は成功する", r.returncode == 0 and "TRACKER\tOK\tgithub" in r.stdout, r.stdout + r.stderr)
        fake.issue_patches.clear()
        r = run_task(main_path, "sync")
        check("変わりが無ければ Issue を送らず pushed=0", r.returncode == 0 and "pushed=0\t" in r.stdout
              and fake.issue_patches == [], f"{fake.issue_patches}\n{r.stdout}{r.stderr}")
        time.sleep(1.1)
        u = bd(main_path, "update", beads.to_bd_id(b), "--title", "Beads で直した題")
        check("bd update", u.returncode == 0, u.stdout + u.stderr)
        r = run_task(main_path, "sync")
        check("変えた1件だけを送る", r.returncode == 0 and "pushed=1\t" in r.stdout
              and set(fake.issue_patches) == {nb} and fake.issues[nb]["title"] == "Beads で直した題"
              and fake.issues[na]["title"] == "変えない",
              f"{fake.issue_patches}\n{r.stdout}{r.stderr}")


def test_tracker_sync_skips_echo() -> None:
    say("トラッカー github・自分の送りの反響は取り込みに渡さず、あとから GitHub で直したものは渡す")
    with _github(prefix=None) as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "反響を確かめる")
        n = _num(a)
        r = run_task(main_path, "sync")
        check("最初の sync は成功する", r.returncode == 0, r.stdout + r.stderr)
        fake.fetched.clear()
        r = run_task(main_path, "sync")
        check("自分の送りで更新時刻が進んだだけの Issue は取りに行かない", r.returncode == 0 and n not in fake.fetched,
              f"{fake.fetched}\n{r.stdout}{r.stderr}")
        fake.clock_offset = 60
        fake.edit(n, body="あとから GitHub で直した")
        fake.fetched.clear()
        r = run_task(main_path, "sync")
        issue = beads.show(main_path, beads.to_bd_id(a))
        check("送った時刻より後に GitHub で直した Issue は取り込む", n in fake.fetched and issue is not None
              and "あとから GitHub で直した" in str(issue.raw.get("description")), f"{fake.fetched}\n{r.stdout}{r.stderr}")


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


def test_tracker_github_draft() -> None:
    say("トラッカー github・ドラフトは Status が Pending で、GitHub で直したあとの取り込みでもドラフトのまま")
    with _github() as (fake, _tmp, main_path, _wt1):
        a = new(main_path, "ドラフト", "--draft", body="## 目的・背景\n\n直したい改善\n")
        n, bd_id = _num(a), beads.to_bd_id(a)
        check("ドラフトの Status は Pending", fake.status_of(n) == "Pending", repr(fake.items))
        check("GitHub では label status::deferred", "status::deferred" in fake.issues[n]["labels"], repr(fake.issues[n]))
        time.sleep(1.1)
        fake.clock_offset = 60
        fake.edit(n, body="GitHub で直した本文")
        r = run_task(main_path, "sync")
        issue = beads.show(main_path, bd_id)
        meta = issue.raw.get("metadata") if issue is not None else None
        check("取り込みのあとも deferred で metadata draft が戻る", issue is not None and issue.status == "deferred"
              and isinstance(meta, dict) and meta.get(beads.DRAFT_KEY) == beads.DRAFT_ON
              and "GitHub で直した本文" in str(issue.raw.get("description")), r.stdout + r.stderr + str(issue and issue.raw))
        check("status は DRAFT のまま", rows(run_task(main_path, "status").stdout).get(a, [""] * 8)[5] == "DRAFT")
        check("Status は Pending のまま", fake.status_of(n) == "Pending", repr(fake.items))


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
            test_claim_not_restored_after_release_done_ship,
            test_claim_restored_after_killed_sync,
            test_claim_not_restored_when_released_on_github,
            test_claim_kept_through_concurrent_syncs,
            test_tracker_github_bidirectional,
            test_tracker_sync_skips_echo,
            test_tracker_github_conflict,
            test_tracker_github_draft,
            test_tracker_sync_pushes_only_changed,
            test_tracker_timeout,
            test_tracker_project_items_per_issue,
            test_cycle_done_ship_and_dropped,
            test_commit_guard,
            test_claim_race_owner_and_release,
            test_release_force_without_assignee,
            test_release_force_clears_github_label,
            test_release_force_deadline_then_sync,
            test_handback_guard,
            test_setup_and_config_doctor,
            test_backup,
            test_direct_mark,
            test_difficulty_loopable_metadata,
            test_migrate_attributes,
            test_missing_config_ignores_old_layout,
            test_store_default_is_beads,
            test_store_files_is_invalid,
            test_removed_commands,
            test_id_forms,
            test_bd_time_forms,
        )
        outputs = run_parallel(select(tests, only))
    finish([*outputs, local.lines])


if __name__ == "__main__":
    main()
