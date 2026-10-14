#!/usr/bin/env python3
"""`check_repo.py` の兄弟スキル参照の検査の自己テスト。

使い方: python3 scripts/selftest_check_repo.py
"""

from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_repo  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        failures.append(label)


def main() -> int:
    refs = check_repo.sibling_references
    check("兄弟を指す参照を出てきた順に拾う", refs("a", "${CLAUDE_SKILL_DIR}/../x/SKILL.md と ${CLAUDE_SKILL_DIR}/../y/scripts") == ["x", "y"])
    check("同じ兄弟は1つにまとめる", refs("a", "${CLAUDE_SKILL_DIR}/../x/a ${CLAUDE_SKILL_DIR}/../x/b") == ["x"])
    check("自分自身は拾わない", refs("a", "${CLAUDE_SKILL_DIR}/../a/scripts") == [])
    check("自分の下のパスは拾わない", refs("a", "${CLAUDE_SKILL_DIR}/scripts/x.py") == [])

    skills = check_repo.skill_names()
    check("スキルが読める", len(skills) > 0)

    unrequired = check_repo.unrequired_references
    text = "${CLAUDE_SKILL_DIR}/../x/SKILL.md ${CLAUDE_SKILL_DIR}/../y/SKILL.md"
    check("REQUIRES に無い兄弟を返す", unrequired("a", text, {"x"}) == ["y"])
    check("全部 REQUIRES にあれば空", unrequired("a", text, {"x", "y"}) == [])
    check("REQUIRES が空なら全部返す", unrequired("a", text, set()) == ["x", "y"])

    parse = ast.parse
    check("from __future__ import annotations があれば真", check_repo.has_future_annotations(parse("from __future__ import annotations\nx = 1\n")))
    check("無ければ偽", not check_repo.has_future_annotations(parse("import os\n")))
    check("annotations 以外の __future__ だけなら偽", not check_repo.has_future_annotations(parse("from __future__ import division\n")))

    check_repo.problems.clear()
    check_repo.check_python_syntax(skills)
    check("実物の .py は全部 from __future__ import annotations を持つ", check_repo.problems == [])

    forbidden = check_repo.forbidden_path_references
    check("task-model のパスは許す", forbidden("a", "${CLAUDE_SKILL_DIR}/../task-model/WORKFLOW.md") == [])
    check("自分自身のパスは許す", forbidden("a", "${CLAUDE_SKILL_DIR}/../a/scripts") == [])
    check("ほかの兄弟のパスは返す", forbidden("a", "${CLAUDE_SKILL_DIR}/../task-model/x ${CLAUDE_SKILL_DIR}/../retrospect/SKILL.md") == ["retrospect"])
    check("改名前の task-workflow のパスは返す", forbidden("a", "${CLAUDE_SKILL_DIR}/../task-workflow/WORKFLOW.md") == ["task-workflow"])

    check_repo.problems.clear()
    check_repo.check_sibling_references(skills)
    check("実物のリポジトリは通る", check_repo.problems == [])

    check_repo.problems.clear()
    check_repo.check_sibling_path_references(skills)
    check("実物のリポジトリは兄弟のパスで task-model しか指さない", check_repo.problems == [])

    entry = check_repo.RETROSPECT_ENTRY_REFERENCE
    check("retrospect/SKILL.md の指し方を拾う", entry.search("`retrospect/SKILL.md` の出典") is not None)
    check("retrospect の SKILL.md の指し方を拾う", entry.search("`retrospect` の SKILL.md の節") is not None)
    check("detail.md の指し方は拾わない", entry.search("`retrospect/detail.md` の出典") is None)

    check_repo.problems.clear()
    check_repo.check_retrospect_entry_references(skills)
    check("実物のリポジトリは retrospect の本文を detail.md で指す", check_repo.problems == [])

    edges = check_repo.forbidden_edges
    check("next-task から task-model のファイルは許す", edges("next-task", "`skills/task-model/WORKFLOW.md`", skills) == [])
    check("plan-tasks から next-task のファイルは返す",
          edges("plan-tasks", "`next-task` スキルの `ship-stopped.md` の表", skills) == ["next-task"])
    check("retrospect から task-workflow のパーツのファイルは返す",
          edges("retrospect", "task-workflow/parts/retrospect.md を読む", skills) == ["task-workflow"])
    check("task-model からほかのスキルのファイルは返す", edges("task-model", "`next-task` の `SKILL.md`", skills) == ["next-task"])
    check("retro から retrospect のファイルは許す", edges("retro", "`retrospect` の `detail.md`", skills) == [])
    check("自分自身のファイルは許す", edges("plan-tasks", "skills/plan-tasks/SKILL.md", skills) == [])
    check("名前で呼ぶだけなら拾わない", edges("next-task", "`task-workflow` を Skill ツールで呼ぶ", skills) == [])
    check("台帳の置き場は拾わない",
          edges("task-model", "`$(git rev-parse --git-common-dir)/task-workflow/flow/<ID>.md`", skills) == [])

    mentions = check_repo.task_model_name_mentions
    agents = check_repo.agent_names()
    check("データの名前は拾わない",
          mentions("`task-workflow.last-id`・`/task-workflow/<ID>`・`docs/history/retrospect.md`・`retrospect_due`・lap の段 retro",
                   skills, agents) == [])
    check("スキル名とエージェント名を拾う",
          mentions("`/next-task` から `no-delegate` に委譲し `/retro` を呼ぶ", skills, agents) == ["next-task", "no-delegate", "retro"])

    check_repo.problems.clear()
    check_repo.check_dependency_edges(skills)
    check("実物のリポジトリは許す辺に収まる", check_repo.problems == [])

    check_repo.problems.clear()
    check_repo.check_task_model_names(skills, agents)
    check("実物の task-model はスキル名とエージェント名を書かない", check_repo.problems == [])

    if failures:
        print(f"\n{len(failures)} 件落ちた: {', '.join(failures)}")
        return 1
    print("\n全部通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
