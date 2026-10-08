#!/usr/bin/env python3
"""1件1ファイル＋台帳の形のタスク運用を操作する入口コマンド。

使い方: tw <status|new|claim|release|done|ship|prune|migrate|migrate-layout|config-doctor|show|edit|plan-check|verify|verify-check|pause|step|commit-guard|handback-guard> ...

正典は `docs/task-workflow-redesign.md`（5章が `task` コマンド、4章が状態と台帳、
3章がタスクファイル、6章が送り出し、5.9・10章が `migrate`）。`install.sh` が PATH 上に張る
`tw` と、plugin の `bin/tw` から呼ぶ。`commit-guard`・`handback-guard` の `--agent-scoped` は plugin の
`hooks/hooks.json` が付け、`agent_type` の末尾が `no-delegate` のときだけ関門を掛ける。スキル側の呼び方の正典は task-workflow の WORKFLOW.md「`tw` コマンドの参照」。

出力は常に stdout（先頭語で種類を判定する TSV）、stderr は使い方の誤りだけ、
終了コードは5.2の表のとおり。データの不備で traceback を出さない
（traceback は「環境の故障」の合図として取っておく）。ここは引数を解釈して、置き場の方式ごとに
`tw_*.py` のサブコマンドへ振り分けるだけ。
"""

from __future__ import annotations

import argparse
import sys

import beads
import commit_guard
import handback_guard
import layout
import ledger
import metrics
import taskfile
import tracker
import tw_base
import tw_claim
import tw_edit
import tw_handback
import tw_maint
import tw_new
import tw_ship
import tw_status
import tw_verify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tw")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("--all", dest="show_all", action="store_true")
    p_status.add_argument("--check", action="store_true")

    p_new = sub.add_parser("new")
    p_new.add_argument("--summary", required=True)
    p_new.add_argument("--difficulty", required=True, choices=taskfile.DIFFICULTY_VALUES)
    p_new.add_argument("--loopable", required=True, choices=taskfile.LOOPABLE_VALUES)
    p_new.add_argument("--deps", default="")
    p_new.add_argument("--hold", action="store_true")
    p_new.add_argument("--direct", action="store_true")
    p_new.add_argument("--body-file", required=True)

    p_claim = sub.add_parser("claim")
    p_claim.add_argument("task_id")

    p_release = sub.add_parser("release")
    p_release.add_argument("task_id")
    p_release.add_argument("--force", action="store_true")

    p_done = sub.add_parser("done")
    p_done.add_argument("task_id")
    p_done.add_argument("--dropped", action="store_true")
    p_done.add_argument("--result-file", required=True)

    sub.add_parser("ship")

    p_prune = sub.add_parser("prune")
    p_prune.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_prune.add_argument("--min", dest="minimum", type=int, default=tw_maint.PRUNE_MIN_DEFAULT)

    p_migrate = sub.add_parser("migrate")
    p_migrate.add_argument("--dry-run", dest="dry_run", action="store_true")

    p_migrate_layout = sub.add_parser("migrate-layout")
    p_migrate_layout.add_argument("--dry-run", dest="dry_run", action="store_true")

    sub.add_parser("config-doctor")
    sub.add_parser("config")

    p_show = sub.add_parser("show")
    p_show.add_argument("task_id")

    p_plan_check = sub.add_parser("plan-check")
    p_plan_check.add_argument("task_id")

    sub.add_parser("verify")
    sub.add_parser("verify-check")
    p_lap = sub.add_parser("lap")
    p_lap.add_argument("task_id")
    p_lap.add_argument("stage")
    p_metrics = sub.add_parser("metrics")
    p_metrics.add_argument("--days", type=int, default=metrics.DAYS_DEFAULT)
    p_metrics.add_argument("--stages", action="store_true")
    sub.add_parser("commit-guard").add_argument("--agent-scoped", dest="agent_scoped", action="store_true")
    p_pause = sub.add_parser("pause")
    p_pause.add_argument("task_id", nargs="?")
    p_pause.add_argument("step", nargs="?")
    p_step = sub.add_parser("step")
    p_step.add_argument("task_id")
    p_step.add_argument("step")
    sub.add_parser("handback-guard").add_argument("--agent-scoped", dest="agent_scoped", action="store_true")

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("task_id")
    p_edit.add_argument("--body-file", default=None)
    p_edit.add_argument("--section", default=None)
    p_edit.add_argument("--summary", default=None)
    p_edit.add_argument("--difficulty", default=None, choices=taskfile.DIFFICULTY_VALUES)
    p_edit.add_argument("--loopable", default=None, choices=taskfile.LOOPABLE_VALUES)
    p_edit.add_argument("--status", default=None, choices=("todo", "hold"))
    p_edit.add_argument("--add-deps", dest="add_deps", default=None)
    p_edit.add_argument("--remove-deps", dest="remove_deps", default=None)
    p_edit.add_argument("--after-work", dest="after_work", action="store_true")
    p_edit.add_argument("--change-frame", dest="change_frame", action="store_true")
    p_edit.add_argument("--direct", default=None, choices=taskfile.LOOPABLE_VALUES)

    # 以下は Beads 方式だけ。

    p_adopt = sub.add_parser("adopt")
    p_adopt.add_argument("bd_id")
    p_adopt.add_argument("--difficulty", required=True, choices=taskfile.DIFFICULTY_VALUES)
    p_adopt.add_argument("--loopable", required=True, choices=taskfile.LOOPABLE_VALUES)
    p_adopt.add_argument("--summary", default=None)
    p_adopt.add_argument("--direct", action="store_true")
    p_adopt.add_argument("--body-file", required=True)

    sub.add_parser("sync")
    sub.add_parser("backup")

    p_jira_closed = sub.add_parser("jira-closed")
    p_jira_closed.add_argument("task_ids", nargs="+")

    return parser


BEADS_ONLY_COMMANDS = ("adopt", "sync", "backup", "jira-closed")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "commit-guard":
        commit_guard.run(sys.stdin, sys.stdout, args.agent_scoped)
        return
    if args.command == "handback-guard":
        handback_guard.run(sys.stdin, sys.stdout, tw_handback.handback_refusal, args.agent_scoped)
        return

    try:
        toplevel = ledger.git_toplevel()
    except ledger.GitCommandError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)

    if args.command not in ("migrate", "config-doctor"):
        refusal = tw_base.format_refusal(toplevel)
        if refusal is not None:
            print(refusal[0])
            raise SystemExit(refusal[1])

    # 主ブランチは要る道でだけ問い合わせる（`ledger.base_branch`。決まらなければ INVALID）。
    try:
        store = layout.read_config(toplevel).store if args.command not in ("migrate", "config-doctor") else None
        if args.command == "config":
            tw_status.cmd_config(toplevel)
            return
        if args.command == "migrate-layout":
            tw_maint.cmd_migrate_layout(toplevel, args.dry_run)
            return
        if store == layout.STORE_BEADS:
            _main_beads(toplevel, args)
            return
        if args.command in BEADS_ONLY_COMMANDS:
            print(f"usage: {args.command} は Beads 方式だけ（ファイル方式では <根>/task/ を直に直す）", file=sys.stderr)
            raise SystemExit(2)
        if args.command == "status":
            tw_status.cmd_status(toplevel, args.show_all, args.check)
        elif args.command == "new":
            tw_new.cmd_new(toplevel, args)
        elif args.command == "claim":
            tw_claim.cmd_claim(toplevel, args.task_id)
        elif args.command == "release":
            tw_claim.cmd_release(toplevel, args.task_id, args.force)
        elif args.command == "done":
            tw_claim.cmd_done(toplevel, args.task_id, args.dropped, args.result_file)
        elif args.command == "ship":
            tw_ship.cmd_ship(toplevel)
        elif args.command == "prune":
            tw_maint.cmd_prune(toplevel, args.dry_run, max(args.minimum, 1))
        elif args.command == "migrate":
            tw_maint.cmd_migrate(toplevel, args.dry_run)
        elif args.command == "config-doctor":
            tw_maint.cmd_config_doctor(toplevel)
        elif args.command == "show":
            tw_status.cmd_show(toplevel, args.task_id, layout.STORE_FILES)
        elif args.command == "edit":
            tw_edit.cmd_edit(toplevel, args)
        elif args.command == "plan-check":
            tw_edit.cmd_plan_check(toplevel, args.task_id)
        elif args.command == "verify":
            tw_verify.cmd_verify(toplevel, lambda: tw_verify.file_unplanned_work(toplevel))
        elif args.command == "verify-check":
            tw_verify.cmd_verify_check(toplevel)
        elif args.command == "pause":
            tw_handback.cmd_pause(toplevel, args.task_id, args.step)
        elif args.command == "step":
            tw_handback.cmd_step(toplevel, args.task_id, args.step)
        elif args.command == "lap":
            tw_handback.cmd_lap(toplevel, args.task_id, args.stage)
        elif args.command == "metrics":
            (metrics.cmd_metrics_stages if args.stages else metrics.cmd_metrics)(toplevel, args.days)
    except (ledger.NoBaseBranch, layout.ConfigError) as e:
        print(f"INVALID\t{e}")
        raise SystemExit(3)
    except ledger.StateReadOnly as e:
        print(
            f"STATE_READ_ONLY\t{e.path}\t{ledger.STATE_DIR_ENV} を書ける場所に向けるか、{e.path} を書ける場所に足す"
            "（Codex は writable roots、Claude Code は sandbox.filesystem.allowWrite）"
        )
        raise SystemExit(12)
    except ledger.GitReadOnly as e:
        print(
            f"GIT_READ_ONLY\t{e.path}\tsandbox の外で同じコマンドを打ち直す"
            "（Claude Code は sandbox.excludedCommands に tw を足す）"
        )
        raise SystemExit(11)


def _main_beads(toplevel: str, args: argparse.Namespace) -> None:
    if not beads.is_initialized(toplevel):
        print(f"MISSING\t{beads.beads_dir(toplevel)}")
        raise SystemExit(6)
    tracker.read_tracker(toplevel)  # 読めない設定はどのサブコマンドでも先に INVALID にする
    try:
        if args.command == "status":
            tw_status.cmd_beads_status(toplevel, args.show_all, args.check)
        elif args.command == "new":
            tw_new.cmd_beads_new(toplevel, args)
        elif args.command == "claim":
            tw_claim.cmd_beads_claim(toplevel, args.task_id)
        elif args.command == "release":
            tw_claim.cmd_beads_release(toplevel, args.task_id, args.force)
        elif args.command == "done":
            tw_claim.cmd_beads_done(toplevel, args.task_id, args.dropped, args.result_file)
        elif args.command == "ship":
            tw_ship.cmd_ship(toplevel, tw_ship.beads_ship_hooks(toplevel))
        elif args.command == "prune":
            print("NOTHING\t(Beads 方式には消すタスクファイルが無い)")
        elif args.command == "show":
            tw_status.cmd_show(toplevel, args.task_id, layout.STORE_BEADS)
        elif args.command == "edit":
            tw_edit.cmd_beads_edit(toplevel, args)
        elif args.command == "plan-check":
            tw_edit.cmd_beads_plan_check(toplevel, args.task_id)
        elif args.command == "verify":
            tw_verify.cmd_verify(toplevel, lambda: tw_verify.beads_unplanned_work(toplevel))
        elif args.command == "verify-check":
            tw_verify.cmd_verify_check(toplevel)
        elif args.command == "pause":
            tw_handback.cmd_pause(toplevel, args.task_id, args.step)
        elif args.command == "step":
            tw_handback.cmd_step(toplevel, args.task_id, args.step)
        elif args.command == "lap":
            tw_handback.cmd_lap(toplevel, args.task_id, args.stage)
        elif args.command == "metrics":
            (metrics.cmd_metrics_stages if args.stages else metrics.cmd_metrics)(toplevel, args.days)
        elif args.command == "adopt":
            tw_new.cmd_beads_adopt(toplevel, args)
        elif args.command == "sync":
            tw_maint.cmd_beads_sync(toplevel)
        elif args.command == "backup":
            tw_maint.cmd_beads_backup(toplevel)
        elif args.command == "jira-closed":
            tw_maint.cmd_beads_jira_closed(toplevel, args.task_ids)
    except beads.BeadsError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
