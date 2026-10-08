"""`tw prune`・`migrate`・`migrate-layout`・`config-doctor` と、Beads 方式だけの `sync`・`backup`・`jira-closed`（置き場の手入れ）。"""

from __future__ import annotations

import os
import re

import beads
import init
import layout
import ledger
import legacy
import relayout
import taskfile
import tracker
import tw_base


# 1件ごとの振り返りが済んだ印。`## 結果` の中のこの形の行（WORKFLOW.md「結果の書き方と知見の置き場」）。
REVIEWED_LINE = re.compile(r"^- 振り返り:")


# 消せるものがこの件数に届くまでは消さない。毎サイクル1件ずつ消すと削除だけのコミットが
# タスクと同じ数だけ積もるため、まとめて1コミットにする。
PRUNE_MIN_DEFAULT = 10


def cmd_prune(toplevel: str, dry_run: bool, minimum: int) -> None:
    """振り返りが済んだ done/dropped のタスクファイルを `git rm` して stage する（コミットしない）。

    判定は `HEAD` の版で done/dropped・台帳に印が無い・`## 結果` に `- 振り返り:` の行がある
    （`reviewed` ＝1件ごとの振り返り済み）。印のあるタスクを除くのは、`ship` が主ブランチの版で
    done を見て印を消すため。対象が `minimum` 件に届かなければ何もしない（`NOTHING`）。
    """
    if not dry_run and not ledger.is_clean(cwd=toplevel):
        print("DIRTY")
        raise SystemExit(4)

    claims = set(ledger.list_claims(ledger.ledger_root(cwd=toplevel)))
    targets: list[tuple[str, str]] = []
    for tid, task in sorted(_tasks_at(toplevel, "HEAD").items(), key=lambda kv: taskfile.id_number(kv[0])):
        if task.status not in ("done", "dropped") or tid in claims:
            continue
        if _has_review_line(task.body):
            targets.append((tid, "reviewed"))

    if not targets:
        print("NOTHING\t(消せるタスクファイルが無い)")
        return
    if len(targets) < minimum:
        print(f"NOTHING\t(消せるのは{len(targets)}件で、{minimum}件に届くまで溜める)")
        return
    if not dry_run:
        ledger.require_git_writable(toplevel)
    for tid, reason in targets:
        print(f"PRUNE\t{tid}\t{reason}")
    if dry_run:
        print(f"PLAN\t{len(targets)}")
        return
    paths = [f"{layout.task_dir(toplevel)}/{tid}.md" for tid, _ in targets]
    r = tw_base.run_git(toplevel, ["rm", "-q", "--", *paths])
    if r.returncode != 0:
        raise ledger.GitCommandError(f"git rm が失敗した: {r.stderr.strip()}")
    print(f"PRUNED\t{len(targets)}")


def _tasks_at(toplevel: str, rev: str) -> dict[str, taskfile.Task]:
    r = tw_base.run_git(toplevel, ["ls-tree", "--name-only", rev, f"{layout.task_dir(toplevel)}/"])
    tasks: dict[str, taskfile.Task] = {}
    for path in r.stdout.splitlines() if r.returncode == 0 else []:
        stem = os.path.splitext(os.path.basename(path))[0]
        if not path.endswith(".md") or not taskfile.ID_PATTERN.match(stem):
            continue
        shown = tw_base.run_git(toplevel, ["show", f"{rev}:{path}"])
        parsed, err = taskfile.parse(shown.stdout) if shown.returncode == 0 else (None, "読めない")
        if err is None and parsed is not None and parsed.id == stem:
            tasks[stem] = parsed
    return tasks


def _has_review_line(body: str) -> bool:
    in_result = False
    for line in body.splitlines():
        if line.startswith("## "):
            in_result = line.strip() == taskfile.RESULT_HEADING
        elif in_result and REVIEWED_LINE.match(line):
            return True
    return False


def cmd_migrate(toplevel: str, dry_run: bool) -> None:
    if not dry_run:
        ledger.require_git_writable(toplevel)
    result = legacy.migrate(toplevel, dry_run)
    if result.kind == "DIRTY":
        print("DIRTY")
        raise SystemExit(4)
    if result.kind == "NOTHING":
        print(f"NOTHING\t{result.detail}")
        return
    if result.kind == "INVALID":
        print(f"INVALID\t{result.detail}")
        raise SystemExit(3)
    if result.kind == "NOT_READY":
        print(f"NOT_READY\t{result.detail}")
        raise SystemExit(4)

    for path in result.written:
        print(f"WRITE\t{path}")
    if result.moved_sections > 0:
        print(f"MOVE\tprogress 完了したこと {result.moved_sections}小節 → docs/history/progress.md")
    if result.leftover_counts is not None:
        unresolved, note = result.leftover_counts
        print(f"LEFTOVER\tdevelop/progress.md\t未解決 {unresolved} / 注意 {note}")
        if result.preamble_kept:
            print("LEFTOVER\tdevelop/progress.md\t前置き文を残した")
    elif result.progress_removed:
        print("REMOVE\tdevelop/progress.md")
    print("REMOVE\tdevelop/tasks.json")
    print(f"{'PLAN' if dry_run else 'MIGRATED'}\t{result.task_count}")


def cmd_migrate_layout(toplevel: str, dry_run: bool) -> None:
    if not dry_run:
        ledger.require_git_writable(toplevel)
    outcome = relayout.migrate_layout(toplevel, dry_run)
    if outcome.kind == "DIRTY":
        print("DIRTY")
        raise SystemExit(4)
    for line in outcome.lines:
        print(line)
    if outcome.kind == "BUSY":
        raise SystemExit(4)
    if outcome.kind == "INVALID":
        raise SystemExit(3)
    if outcome.kind in ("PLAN", "MIGRATED"):
        print(outcome.kind)


def cmd_config_doctor(toplevel: str) -> None:
    """設定と形式のズレの点検（読むだけ・`--fix` は無い）。検査1つに1行、タブ区切りで出す
    （`task status` と同じ形。人向けの段落は出さない）。判定は書き起こさず、既存の部品
    （`ledger.base_branch`・`init.check_config`・旧形式の残りの
    直接の検出）を呼ぶだけ（正典 WORKFLOW.md「ファイル配置と設定ファイル」）。

    `base_branch`・`config`・`legacy` の3行を必ず出す（途中の検査が INVALID でも残りの検査は続ける。呼び出し側が全体像を
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

    # 検査2: 設定が読めるか（`init.check_config`）。旧い節で読んでいればその行も出す。
    line, config = init.check_config(toplevel)
    kind = line.partition("\t")[0]
    print(f"config\t{line}")
    if kind == "INVALID":
        exit_code = 3
    elif kind != "OK":
        exit_code = max(exit_code, 1)
    if config is not None and config.legacy:
        print(f"old_section\tFOUND\t{config.source}")

    # 検査3: 旧形式の残り（develop/tasks.json・develop/progress.md）があるか。
    tasks_json = os.path.exists(os.path.join(toplevel, "develop", "tasks.json"))
    progress_md = os.path.exists(os.path.join(toplevel, "develop", "progress.md"))
    if tasks_json or progress_md:
        leftover = ", ".join(
            p
            for p, present in (("develop/tasks.json", tasks_json), ("develop/progress.md", progress_md))
            if present
        )
        print(f"legacy\tFOUND\t{leftover}\ttw migrate --dry-run")
        exit_code = max(exit_code, 1)
    else:
        print("legacy\tOK")

    # 検査4: Beads 方式の用意とトラッカー。
    if config is not None and "store" in config.written:
        store_code = _doctor_store(toplevel, config)
        exit_code = 3 if 3 in (exit_code, store_code) else max(exit_code, store_code)

    if exit_code:
        raise SystemExit(exit_code)


def cmd_beads_sync(toplevel: str) -> None:
    lines = tracker.session(toplevel).sync_all(pull=True)
    if not lines:
        print("NOTHING\t(トラッカーなし)")
        return
    tw_base.print_lines(lines)
    if any(l.startswith("TRACKER\tFAILED") for l in lines):
        raise SystemExit(10)


def cmd_beads_backup(toplevel: str) -> None:
    lines = beads.backup(toplevel)
    tw_base.print_lines(lines)
    if any("\tFAILED\t" in l for l in lines):
        raise SystemExit(10)


def cmd_beads_jira_closed(toplevel: str, task_ids: list[str]) -> None:
    """Jira で閉じたのを人が確かめたものから `jira:close` を外す。"""
    actor = tw_base.actor(toplevel)
    for task_id in task_ids:
        bd_id = tw_base.bd_task_id(task_id)
        beads.run_ok(toplevel, ["update", bd_id, "--remove-label", beads.JIRA_CLOSE_LABEL], actor)
        print(f"CLEARED\t{beads.to_task_id(bd_id)}")


def _doctor_store(toplevel: str, config: layout.Config) -> int:
    """`config-doctor` の検査4。`store`・（Beads 方式なら）`beads`・`tracker` の行を出し、終了コードを返す。"""
    print(f"store\tOK\t{config.store}")
    if config.store != layout.STORE_BEADS:
        return 0
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
