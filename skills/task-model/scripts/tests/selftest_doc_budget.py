#!/usr/bin/env python3
"""毎回読まれるスキルの文書のバイト数の上限の自己テスト。

使い方: python3 selftest_doc_budget.py [--measure]

`--measure` は上限の表の各ファイルの今のバイト数と、100B 単位で切り上げた上限を出す（上限を置く・測り直すときに使う）。
標準ライブラリだけで動く。落ちたら非0で終わる。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", ".."))

REMEDY = (
    "足すなら何かを削る（上限は上げない）。出し先の梯子は 検査に移す → スクリプトに → 削る。"
    "直しの確かめは検証全体でなく単体で: python3 skills/task-model/scripts/tests/selftest_doc_budget.py"
)

# 毎回読まれる文書の上限（skills/ からの相対パス: バイト）。今の値を100B 単位で切り上げて凍らせたもの
LIMITS: dict[str, int] = {
    "accept-step/SKILL.md": 4000,
    "comment-audit/SKILL.md": 9200,
    "implement-step/SKILL.md": 4500,
    "list-tasks/SKILL.md": 5100,
    "next-task/SKILL.md": 7100,
    "plan-tasks/SKILL.md": 20500,
    "retro/SKILL.md": 6700,
    "retrospect-step/SKILL.md": 3900,
    "retrospect/SKILL.md": 2700,
    "setup-tasks/SKILL.md": 6400,
    "ship-step/SKILL.md": 2900,
    "task-model/SKILL.md": 2000,
    "task-workflow/SKILL.md": 18600,
    "task-workflow/completion-report.md": 5800,
    "task-workflow/parts/implementer/delegate.md": 11500,
    "task-workflow/parts/implementer/handback-read.md": 4500,
    "task-workflow/parts/implementer/implementer-brief.md": 21800,
    "task-workflow/parts/implementer/implementer-request.md": 9300,
    "task-workflow/parts/acceptor/accept.md": 11500,
    "task-workflow/parts/acceptor/acceptor-return.md": 6000,
    "task-workflow/parts/acceptor/acceptor-brief.md": 5500,
    "task-workflow/parts/acceptor/acceptor-request.md": 3200,
    "task-workflow/parts/acceptor/verify-check.md": 1500,
    "task-workflow/parts/retrospect/retrospect.md": 3900,
    "task-workflow/parts/ship/ship.md": 7400,
    "task-model/WORKFLOW.md": 85000,
    "task-model/tw-reference.md": 47200,
    "task-model/preflight.md": 800,
}

# 条件つき・異常系・単発で読むので上限を掛けない文書
UNLIMITED: frozenset[str] = frozenset(
    {
        "next-task/leftover-claim.md",
        "next-task/no-candidate.md",
        "next-task/start-stopped.md",
        "next-task/weekly-retrospect.md",
        "retrospect/detail.md",
        "task-model/TRACKER.md",
        "task-model/legacy.md",
        "task-workflow/flows/direct.md",
        "task-workflow/flows/hold.md",
        "task-workflow/flows/main.md",
        "task-workflow/other-repo.md",
        "task-workflow/parts/acceptor/review-rounds.md",
        "task-workflow/parts/acceptor/verify-merge.md",
        "task-workflow/parts/acceptor/visual-review.md",
        "task-workflow/parts/implementer/return-abnormal.md",
        "task-workflow/parts/implementer/rewrite-plan.md",
        "task-workflow/parts/implementer/unresponsive.md",
        "task-workflow/parts/ship/commits-since-claim.md",
        "task-workflow/parts/ship/ship-stopped.md",
        "task-workflow/review-criteria.md",
    }
)

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def over_budget(label: str, data: bytes, limit: int) -> str:
    """上限を超えたら失敗文を、収まっていれば空文字を返す。"""
    size = len(data)
    if size <= limit:
        return ""
    return f"{label} が {size}B で上限 {limit}B を超えた。{REMEDY}"


def read_bytes(rel: str) -> bytes:
    with open(os.path.join(SKILLS, rel), "rb") as f:
        return f.read()


def all_markdown() -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, files in os.walk(SKILLS):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git")]
        for name in files:
            if name.endswith(".md"):
                found.append(os.path.relpath(os.path.join(dirpath, name), SKILLS))
    return sorted(found)


def measure() -> None:
    for rel in sorted(LIMITS):
        size = len(read_bytes(rel))
        print(f'    "{rel}": {(size + 99) // 100 * 100},  # {size}')


def test_within_limits() -> None:
    print("毎回読まれる文書が上限以内")
    for rel, limit in sorted(LIMITS.items()):
        message = over_budget(rel, read_bytes(rel), limit)
        check(rel, message == "", message)


def test_one_more_line_fails() -> None:
    print("上限ちょうどは通り、1行増やすと梯子つきで落ちる")
    for rel in ("task-workflow/SKILL.md",):
        data = read_bytes(rel)
        limit = len(data)
        check(f"{rel} 上限ちょうどは通る", over_budget(rel, data, limit) == "")
        grown = data + "あ\n".encode()
        message = over_budget(rel, grown, limit)
        check("1行足すと落ちる", message != "")
        check("足すなら削ると言う", "足すなら何かを削る" in message, message)
        check("梯子を出す", "検査に移す → スクリプトに → 削る" in message, message)
        check("単体のコマンドを出す", "selftest_doc_budget.py" in message, message)
        check("超えたバイト数を出す", f"{len(grown)}B" in message, message)


def test_counts_bytes() -> None:
    print("文字数でなくバイトで数える")
    check("😀 は4B", over_budget("x", "😀".encode(), 4) == "")
    check("😀 は4B を超える", over_budget("x", "😀".encode(), 3) != "")


def unclassified(found: list[str]) -> list[str]:
    return [p for p in found if p not in LIMITS and p not in UNLIMITED]


def missing_files(paths: list[str]) -> list[str]:
    return sorted(p for p in paths if not os.path.isfile(os.path.join(SKILLS, p)))


def test_every_markdown_classified() -> None:
    print("skills/ の .md はどれかの表に載る")
    found = all_markdown()
    check("走査が表のファイルを見つける", set(LIMITS) <= set(found), str(sorted(set(LIMITS) - set(found))))
    unknown = unclassified(found)
    check(
        "未分類なし",
        not unknown,
        f"{unknown} を LIMITS（毎回読む）か UNLIMITED（条件つき）に分類する",
    )
    check("新しい .md は未分類として拾う", unclassified([*found, "x/NEW.md"]) == ["x/NEW.md"])
    both = sorted(set(LIMITS) & UNLIMITED)
    check("両方の表に載らない", not both, str(both))
    skill_md = [p for p in found if p.endswith("/SKILL.md")]
    check("SKILL.md は全部 LIMITS", all(p in LIMITS for p in skill_md), str(skill_md))


def test_tables_point_at_real_files() -> None:
    print("表のパスが実在する")
    missing = missing_files([*LIMITS, *UNLIMITED])
    check("実在", not missing, str(missing))
    check("無いパスは欠けとして拾う", missing_files(["x/NONE.md"]) == ["x/NONE.md"])


def main() -> int:
    if "--measure" in sys.argv[1:]:
        measure()
        return 0
    test_within_limits()
    test_one_more_line_fails()
    test_counts_bytes()
    test_every_markdown_classified()
    test_tables_point_at_real_files()
    if failures:
        print(f"\n{len(failures)} 件落ちた")
        return 1
    print("\nすべて通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
