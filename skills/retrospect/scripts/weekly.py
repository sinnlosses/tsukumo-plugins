#!/usr/bin/env python3
"""横断の振り返り（retrospect の detail.md「週ごとに振り返る」）の材料を、数だけで1回に出す。

使い方: weekly.py <リポジトリの根> [--days N]

期間は `--days` が無ければ、最後の記録（`layout.RETROSPECT_RECORD_PATH`）の日付から今日までの日数を
7〜28日に丸めたもの（記録が無ければ7日）。節は `===== <名前> =====` で区切り、行は先頭語で読む:

- **期間**: `PERIOD\t<N>d\t<開始日>\t<終了日>\tlast=<最後の記録の日付|->`
- **流れの数**: `<名前>\t<今>\t<前>`（`tw metrics --days N` と同じ数）。前より悪くなった数は
  `WORSE\t<名前>\t<今>\t<前>`。`WORSE` があれば、期間内に主ブランチへ入ったやり方の変更
  `CHANGE\t<project|skills>\t<短い SHA>\t<件名>`（プロジェクトは設定ファイル・`.claude/`・`docs/`
  （`docs/history/` を除く）を触ったコミット、skills はこのスクリプトのあるリポジトリ。各20件まで）
- **段の所要時間**: `STAGE\t<段>\t<difficulty>\t<道>\t<件数>\t<中央値秒>\t<最大秒>`（道は `direct`・`normal`。`tw metrics --stages --days N` と同じ行）。
  終わりの無い段は `OPEN\t<段>\t<件数>`
- **近道と通常の比較**: 期間によらず直近28日に送り出した件の道ごとの行
  `ROUTE\t<direct|normal>\t<件数>\t<着手から送り出しまでの中央値秒|->\t<fix_within_7d|->`（`fix_within_7d` は `tw metrics` と同じ形・同じ数え方で、`metrics.fix_tasks` を共有する）と、
  `DIRECT_GATE\t<direct の件数>\t20\t<reached|not_reached>`
- **揺れたテスト**: 直近28日に落ちて、同じ鍵で次に通った（`verify_flaky` と同じ判定）`verify` の `tests` を名前で数えた
  `FLAKY\t<名前>\t<回数>`（上位5件）と、3回以上の `候補\t<名前>\t<回数>`。無ければ `EMPTY`
- **規則の棚卸し**: 設定の `hook_tally` のコマンドをリポジトリの根で `sh -c` で打った標準出力を
  そのまま。無い・落ちた・60秒で終わらないときは `-\t<理由>`
- **期日を過ぎた介入**: 期日が期間の中に来た介入ごとに
  `INTERVENTION\t<ID>\t<測った名前|->\t<基準線>\t<いま>\t<判定>\t<理由|->`、続けて `no observed effect`・`hurt` の介入ごとに
  `REVERT\t<ID>\t<観点|->\t<判定>\t<出し先の種類>`（判定の決まりは `intervention_effect.py`）
- **再発しなかった候補**: 積んでから `beads.CANDIDATE_WINDOW_DAYS` 日を過ぎても候補のまま（同じ兆候の2件目が来ず上がらなかった）のドラフト
  `EXPIRED\t<ID>\t<観点>\t<SIGNAL の種類>\t<場所>\t<積んでからの日数>d`（閉じる対象。期間は上の期間によらない）
- **ドラフトの行方**: `WINDOW`・`FATE\t<観点>\t<積んだ>\t<採用>\t<却下>\t<未決>\t<絞り中|->`・
  `TOTAL\t<積んだ>\t<採用>\t<却下>\t<未決>\t<絞り中の観点|なし>`（期間は上の期間によらず `draft_fate.WINDOW_DAYS`）

材料が無い節は `EMPTY`（読めたが該当なし）・`-\t<理由>` を出す。
会話の中身は読まない（読むのは記録・台帳・設定ファイル・コミットの件名だけ）。

**データの不備で traceback を出さない。**
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
_TASK_MODEL_SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "task-model", "scripts", "core"))
if _TASK_MODEL_SCRIPTS not in sys.path:
    sys.path.insert(0, _TASK_MODEL_SCRIPTS)

import cross_review  # noqa: E402
import draft_fate  # noqa: E402
import gitcmd  # noqa: E402
import layout  # noqa: E402
import gitinfo  # noqa: E402
import intervention_effect  # noqa: E402
import ledger  # noqa: E402
import metrics  # noqa: E402

MIN_DAYS = 7
MAX_DAYS = 28
HOOK_TALLY_TIMEOUT_SECONDS = 60
CHANGE_LIMIT = 20
COMPARED_ROUTES = ("direct", "normal")
DIRECT_THRESHOLD = 20
FLAKY_TOP = 5
FLAKY_THRESHOLD = 3
PROJECT_CHANGE_PATHS = ("CLAUDE.md", "AGENTS.md", ".claude", "docs", ":(exclude)docs/history")


def main() -> None:
    root, days_arg = parse_args(sys.argv[1:])
    last = cross_review.last_date(root)
    days = days_arg if days_arg is not None else period_days(last)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    section("期間")
    today = date.today()
    print(f"PERIOD\t{days}d\t{(today - timedelta(days=days)).isoformat()}\t{today.isoformat()}\tlast={last.isoformat() if last else '-'}")

    section("流れの数")
    if print_flow(root, days):
        print_changes(root, start)

    section("段の所要時間")
    print_stages(root, days)

    section("近道と通常の比較")
    print_routes(root)

    section("揺れたテスト（直近28日）")
    print_flaky_tests(root, now)

    section("規則の棚卸し")
    print_hook_tally(root)

    intervention_effect.print_section(root, now, days)

    draft_fate.print_candidates(
        root, draft_fate.EXPIRED_SECTION_TITLE, "EXPIRED", lambda issues: draft_fate.expired_candidates(issues, now)
    )
    draft_fate.print_section(root, now)


def parse_args(argv: list[str]) -> tuple[str, int | None]:
    if not argv:
        print("USAGE\tweekly.py <root> [--days N]", file=sys.stderr)
        raise SystemExit(2)
    root, rest = argv[0], argv[1:]
    if not rest:
        return root, None
    if rest[0] != "--days" or len(rest) != 2 or not rest[1].isdigit() or int(rest[1]) < 1:
        print("USAGE\tweekly.py <root> [--days N]（N は1以上）", file=sys.stderr)
        raise SystemExit(2)
    return root, int(rest[1])


def period_days(last: date | None) -> int:
    if last is None:
        return MIN_DAYS
    return min(MAX_DAYS, max(MIN_DAYS, (date.today() - last).days))


def print_flow(root: str, days: int) -> bool:
    """数を出し、悪くなった数があれば True。"""
    try:
        events, _ = metrics.read_events(ledger.ledger_root(cwd=root))
    except (gitinfo.GitCommandError, OSError):
        print("-\t台帳が読めない（git のリポジトリでない）")
        return False
    if not events:
        print("EMPTY")
        return False
    current, previous = metrics.columns(events, days)
    for name, value in current.items():
        print(f"{name}\t{value}\t{previous[name]}")
    worse = [name for name in current if metrics.worse(name, current[name], previous[name])]
    for name in worse:
        print(f"WORSE\t{name}\t{current[name]}\t{previous[name]}")
    return bool(worse)


def print_stages(root: str, days: int) -> None:
    try:
        events, _ = metrics.read_events(ledger.ledger_root(cwd=root))
    except (gitinfo.GitCommandError, OSError):
        print("-\t台帳が読めない（git のリポジトリでない）")
        return
    print("\n".join(metrics.stage_lines(events, days)) or "EMPTY")


def route_rows(events: list[metrics.Event], days: int) -> list[str]:
    """直近 days 日に送り出したタスクの、道（direct・normal）ごとの `ROUTE` の行。"""
    now = datetime.now(timezone.utc)
    shipped = [
        e for e in events if e.kind == "ship" and e.fields.get("result") == "SHIPPED" and now - timedelta(days=days) <= e.at <= now
    ]
    by_route: dict[str, list[metrics.Event]] = {route: [] for route in COMPARED_ROUTES}
    for ship in shipped:
        run = metrics.shipped_run(events, ship)
        if run is not None and metrics.route_of(run) in COMPARED_ROUTES:
            by_route[metrics.route_of(run)].append(ship)
    rows = []
    for route, ships in by_route.items():
        leads = [s for s in (metrics.lead_seconds(events, e) for e in ships) if s is not None]
        median = str(round(statistics.median(leads))) if leads else "-"
        fixed = len(metrics.fix_tasks(events, ships))
        fix = f"{round(100 * fixed / len(ships))}% ({fixed}/{len(ships)})" if ships else "-"
        rows.append(f"ROUTE\t{route}\t{len(ships)}\t{median}\t{fix}")
    direct = len(by_route["direct"])
    rows.append(f"DIRECT_GATE\t{direct}\t{DIRECT_THRESHOLD}\t{'reached' if direct >= DIRECT_THRESHOLD else 'not_reached'}")
    return rows


def print_routes(root: str) -> None:
    try:
        events, _ = metrics.read_events(ledger.ledger_root(cwd=root))
    except (gitinfo.GitCommandError, OSError):
        print("-\t台帳が読めない（git のリポジトリでない）")
        return
    print("\n".join(route_rows(events, MAX_DAYS)) if events else "EMPTY")


def flaky_rows(events: list[metrics.Event], now: datetime) -> list[str]:
    """揺れたテストの上位 `FLAKY\t<名前>\t<回数>` と、`FLAKY_THRESHOLD` 回以上の `候補\t<名前>\t<回数>`。"""
    counts = metrics.flaky_tests(events, now - timedelta(days=MAX_DAYS), now + timedelta(seconds=1))
    rows = [f"FLAKY\t{name}\t{n}" for name, n in list(counts.items())[:FLAKY_TOP]]
    rows += [f"候補\t{name}\t{n}" for name, n in counts.items() if n >= FLAKY_THRESHOLD]
    return rows


def print_flaky_tests(root: str, now: datetime) -> None:
    try:
        events, _ = metrics.read_events(ledger.ledger_root(cwd=root))
    except (gitinfo.GitCommandError, OSError):
        print("-\t台帳が読めない（git のリポジトリでない）")
        return
    print("\n".join(flaky_rows(events, now)) or "EMPTY")


def print_changes(root: str, start: datetime) -> None:
    since = f"--since={start.isoformat()}"
    try:
        base = gitinfo.base_branch(root)
    except (gitinfo.GitCommandError, gitinfo.NoBaseBranch):
        base = "HEAD"
    project = _git_lines(root, "log", since, f"-n{CHANGE_LIMIT}", "--format=%h%x09%s", base, "--", *PROJECT_CHANGE_PATHS)
    for line in project:
        print(f"CHANGE\tproject\t{line}")
    skills_top = _git_lines(HERE, "rev-parse", "--show-toplevel")
    project_top = _git_lines(root, "rev-parse", "--show-toplevel")
    if skills_top and skills_top != project_top:
        for line in _git_lines(HERE, "log", since, f"-n{CHANGE_LIMIT}", "--format=%h%x09%s", "HEAD"):
            print(f"CHANGE\tskills\t{line}")


def print_hook_tally(root: str) -> None:
    try:
        command = layout.read_config(root).hook_tally
    except (layout.ConfigError, OSError, UnicodeDecodeError) as e:
        print(f"-\t設定ファイルが読めない（{e}）")
        return
    if command is None:
        print("-\t設定に hook_tally が無い（この観点は飛ばす）")
        return
    try:
        r = subprocess.run(
            ["sh", "-c", command], cwd=root, capture_output=True, text=True, timeout=HOOK_TALLY_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        print(f"-\t{HOOK_TALLY_TIMEOUT_SECONDS}秒で打ち切った")
        return
    except OSError as e:
        print(f"-\t打てない（{e}）")
        return
    if r.returncode != 0:
        print(f"-\t終了コード {r.returncode}")
        return
    print(r.stdout.rstrip() or "EMPTY")


def section(title: str) -> None:
    print()
    print(f"===== {title} =====")


def _git_lines(cwd: str, *args: str) -> list[str]:
    try:
        r = gitcmd.run(list(args), cwd)
    except OSError:
        return []
    return r.stdout.splitlines() if r.returncode == 0 else []


if __name__ == "__main__":
    main()
