"""`tw config-doctor`・`sync`・`backup`・`jira-closed`（置き場の手入れ）。"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time

import beads
import init
import layout
import ledger
import tracker
import tw_base


def cmd_config_doctor(toplevel: str) -> None:
    """設定と置き場のズレの点検（読むだけ・`--fix` は無い）。検査1つに1行、タブ区切りで出す
    （`task status` と同じ形。人向けの段落は出さない）。判定は書き起こさず、既存の部品
    （`ledger.base_branch`・`init.check_config`・`beads.is_initialized`）を呼ぶだけ
    （正典 WORKFLOW.md「ファイル配置と設定ファイル」）。

    `base_branch`・`config` の2行を必ず出す（途中の検査が INVALID でも残りの検査は続ける。呼び出し側が全体像を
    1回の実行で見られるようにする）。終了コードは 0（全部OK）／1（直すものがある）／
    3（INVALID）——最悪のものを返す。`main` の `ledger.NoBaseBranch`・`layout.ConfigError`
    の受け皿（呼び出し側で即 `INVALID` にする仕組み）は使わない。ここで捕まえて次の検査に進む。
    """
    exit_code = 0

    # 検査1: 主ブランチが何で決まったか（順1〜3。決まらなければ INVALID）。
    try:
        base = ledger.base_branch(toplevel)
        order = ledger.base_branch_order(toplevel)
        print(f"base_branch\tOK\t{base}\t順{order}")
    except (ledger.NoBaseBranch, layout.ConfigError) as e:
        print(f"base_branch\tINVALID\t{e}")
        exit_code = 3

    # 検査2: 設定が読めるか（`init.check_config`）。
    line, config = init.check_config(toplevel)
    kind = line.partition("\t")[0]
    print(f"config\t{line}")
    if kind == "INVALID":
        exit_code = 3
    elif kind != "OK":
        exit_code = max(exit_code, 1)

    # 検査3: 前提のコマンド（設定が読めなくても出す）。
    github = config is not None and config.tracker == "github"
    rows = prerequisite_rows(sys.version_info[:3], _tool_versions(), github)
    for row in rows:
        print(row)
    if any(r.split("\t")[1] == "MISSING" for r in rows):
        exit_code = max(exit_code, 1)

    # 検査4: Beads の用意とトラッカー。
    if config is not None and config.source is not None:
        store_code = _doctor_store(toplevel, config)
        exit_code = 3 if 3 in (exit_code, store_code) else max(exit_code, store_code)

    if exit_code:
        raise SystemExit(exit_code)


MIN_PYTHON = (3, 9)


def _tool_versions() -> dict[str, str | None]:
    """PATH 上の各コマンドの版の文字列。コマンドが無ければ `None`、版が読めなければ `""`。"""
    found: dict[str, str | None] = {}
    for name in ("git", "bd", "dolt", "gh", "tw"):
        if shutil.which(name) is None:
            found[name] = None
            continue
        try:
            r = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            found[name] = ""
            continue
        m = re.search(r"\d+(?:\.\d+)+", r.stdout + r.stderr)
        found[name] = m.group(0) if m else ""
    return found


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def prerequisite_rows(
    python_version: tuple[int, ...], versions: dict[str, str | None], github: bool
) -> list[str]:
    """前提ごとの行 `<名>\\t<OK|MISSING|OPTIONAL>\\t<必須|任意>\\t<詳細>`。必須の欠落・古さが `MISSING`。"""
    rows = []
    py = ".".join(map(str, python_version))
    if python_version[:2] >= MIN_PYTHON:
        rows.append(f"python\tOK\t必須\t{py}")
    else:
        rows.append(f"python\tMISSING\t必須\t{py}（{'.'.join(map(str, MIN_PYTHON))} 以上が要る）")
    needs = {"git": True, "bd": True, "dolt": True, "gh": github, "tw": False}
    minimum = {"bd": beads.MIN_BD_VERSION}
    for name, required in needs.items():
        kind = "必須" if required else "任意"
        version = versions.get(name)
        if version is None:
            rows.append(f"{name}\t{'MISSING' if required else 'OPTIONAL'}\t{kind}\tPATH に無い")
        elif name in minimum and version and _version_tuple(version) < minimum[name]:
            low = ".".join(map(str, minimum[name]))
            rows.append(f"{name}\tMISSING\t{kind}\t{version}（{low} 以上が要る）")
        else:
            rows.append(f"{name}\tOK\t{kind}\t{version}")
    return rows


BUSY_EXIT = 5


def cmd_sync(toplevel: str) -> None:
    started = time.monotonic()

    def progress(message: str) -> None:
        # stdout は結果の行だけ（呼ぶ側が読む）。経過は stderr へ、始めからの秒数つきで出す。
        print(f"[{time.monotonic() - started:5.1f}s] {message}", file=sys.stderr, flush=True)

    session = tracker.session(toplevel)
    session.progress = progress
    try:
        with ledger.sync_lock(toplevel):
            lines = session.sync_all(pull=True)
    except ledger.SyncBusy as e:
        print(f"BUSY\tsync\t{e}")
        raise SystemExit(BUSY_EXIT)
    if not lines:
        print("NOTHING\t(トラッカーなし)")
        return
    tw_base.print_lines(lines)
    if any(l.startswith("TRACKER\tFAILED") for l in lines):
        raise SystemExit(10)


def cmd_backup(toplevel: str) -> None:
    lines = beads.backup(toplevel)
    tw_base.print_lines(lines)
    if any("\tFAILED\t" in l for l in lines):
        raise SystemExit(10)


def cmd_jira_closed(toplevel: str, task_ids: list[str]) -> None:
    """Jira で閉じたのを人が確かめたものから `jira:close` を外す。"""
    actor = tw_base.actor(toplevel)
    for task_id in task_ids:
        bd_id = tw_base.bd_task_id(task_id)
        beads.run_ok(toplevel, ["update", bd_id, "--remove-label", beads.JIRA_CLOSE_LABEL], actor)
        print(f"CLEARED\t{beads.to_task_id(bd_id)}")


def _doctor_store(toplevel: str, config: layout.Config) -> int:
    """`config-doctor` の検査3。`store`・`beads`・`tracker` の行を出し、終了コードを返す。"""
    print(f"store\tOK\t{config.store}")
    code = 0
    if beads.is_initialized(toplevel):
        print(f"beads\tOK\t{beads.beads_dir(toplevel)}")
    else:
        print(f"beads\tMISSING\t{beads.beads_dir(toplevel)}\tinit.py（bd init --stealth）")
        code = 1
    t = tracker.read_tracker(toplevel)
    detail = f"\t{t.project_owner}/{t.project_number}" if t.kind == "github" else ""
    print(f"tracker\tOK\t{t.kind}{detail}")
    return code
