"""`tw config-doctor`・`sync`・`backup`・`jira-closed`（置き場の手入れ）。"""

from __future__ import annotations

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

    # 検査3: Beads の用意とトラッカー。
    if config is not None and config.source is not None:
        store_code = _doctor_store(toplevel, config)
        exit_code = 3 if 3 in (exit_code, store_code) else max(exit_code, store_code)

    if exit_code:
        raise SystemExit(exit_code)


def cmd_sync(toplevel: str) -> None:
    lines = tracker.session(toplevel).sync_all(pull=True)
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
