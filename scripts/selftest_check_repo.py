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

    forbidden = check_repo.forbidden_path_references
    check("task-model のパスは許す", forbidden("a", "${CLAUDE_SKILL_DIR}/../task-model/WORKFLOW.md") == [])
    check("自分自身のパスは許す", forbidden("a", "${CLAUDE_SKILL_DIR}/../a/scripts") == [])
    check("ほかの兄弟のパスは返す", forbidden("a", "${CLAUDE_SKILL_DIR}/../task-model/x ${CLAUDE_SKILL_DIR}/../retrospect/SKILL.md") == ["retrospect"])
    check("改名前の task-workflow のパスは返す", forbidden("a", "${CLAUDE_SKILL_DIR}/../task-workflow/WORKFLOW.md") == ["task-workflow"])

    entry = check_repo.RETROSPECT_ENTRY_REFERENCE
    check("retrospect/SKILL.md の指し方を拾う", entry.search("`retrospect/SKILL.md` の出典") is not None)
    check("retrospect の SKILL.md の指し方を拾う", entry.search("`retrospect` の SKILL.md の節") is not None)
    check("detail.md の指し方は拾わない", entry.search("`retrospect/detail.md` の出典") is None)

    edges = check_repo.forbidden_edges
    check("next-task から task-model のファイルは許す", edges("next-task", "`skills/task-model/WORKFLOW.md`", skills) == [])
    check("plan-tasks から next-task のファイルは返す",
          edges("plan-tasks", "`next-task` スキルの `ship-stopped.md` の表", skills) == ["next-task"])
    check("retrospect から task-workflow の段の手順のファイルは返す",
          edges("retrospect", "task-workflow/parts/retrospect/retrospect.md を読む", skills) == ["task-workflow"])
    check("task-model からほかのスキルのファイルは返す", edges("task-model", "`next-task` の `SKILL.md`", skills) == ["next-task"])
    check("retro から retrospect のファイルは許す", edges("retro", "`retrospect` の `detail.md`", skills) == [])
    check("自分自身のファイルは許す", edges("plan-tasks", "skills/plan-tasks/SKILL.md", skills) == [])
    check("名前で呼ぶだけなら拾わない", edges("next-task", "`task-workflow` を Skill ツールで呼ぶ", skills) == [])
    check("台帳の置き場は拾わない",
          edges("task-model", "`$(git rev-parse --git-common-dir)/task-workflow/flow/<ID>.md`", skills) == [])

    upward = check_repo.upward_reference_problems
    brief = os.path.join(check_repo.SKILLS, "task-workflow", "parts", "acceptor", "acceptor-brief.md")
    check("1つ上の `ship/ship.md` は届く", upward(brief, "このファイルの1つ上のディレクトリの `ship/ship.md`") == [])
    check("括弧の中の言及から2つ上の `review-criteria.md` に届く",
          upward(brief, "置き場（このファイルの2つ上のディレクトリ）の `review-criteria.md`") == [])
    check("括弧の後ろの `scripts/self_review.py` に届く",
          upward(brief, "このファイルの2つ上のディレクトリ（`task-workflow` の置き場）の `scripts/self_review.py`") == [])
    check("3つ上の `review-criteria.md` は届かないとして返す",
          len(upward(brief, "置き場（このファイルの3つ上のディレクトリ）の `review-criteria.md`")) == 1)
    check("先のパスを読めない言及を返す", len(upward(brief, "このファイルの2つ上のディレクトリにある台本")) == 1)

    mentions = check_repo.task_model_name_mentions
    agents = check_repo.agent_names()
    check("データの名前は拾わない",
          mentions("`task-workflow.last-id`・`/task-workflow/<ID>`・`docs/history/retrospect.md`・`retrospect_due`・lap の段 retro",
                   skills, agents) == [])
    check("スキル名とエージェント名を拾う",
          mentions("`/next-task` から `no-delegate` に委譲し `/retro` を呼ぶ", skills, agents) == ["next-task", "no-delegate", "retro"])
    check("`brief` の役の語と metadata のキーは拾わない",
          mentions("`tw brief acceptor T-xxx`・`brief implementer`・`workflow-brief-acceptor`・`workflow-brief-implementer`",
                   skills, agents) == [])
    check("裸の `acceptor` は拾う",
          mentions("受け入れ役は `acceptor` に渡す。brief の前の acceptor", skills, agents) == ["acceptor"])

    marked = check_repo.is_workflow_step
    head = '---\nname: s\ndescription: "d"\nmetadata:\n  workflow-step: "true"\n---\n'
    check("metadata の workflow-step: \"true\" を印として拾う", marked(head))
    check("引用符の無い true も拾う", marked(head.replace('"true"', "true")))
    check("値が true でなければ拾わない", not marked(head.replace('"true"', '"false"')))
    check("metadata の外の workflow-step は拾わない",
          not marked('---\nname: s\nworkflow-step: "true"\nother:\n  workflow-step: "true"\n---\n'))
    check("frontmatter が無ければ拾わない", not marked("# 本文\n  workflow-step: \"true\"\n"))

    contract = check_repo.step_contract_problems
    outcomes = "`finished`・`skipped`・`stopped`"
    check("`phase_result` と3つの結果があれば問題なし", contract(head + f"`phase_result` を呼ぶ。{outcomes}\n") == [])
    check("`work_plan` でもよい", contract(head + f"`work_plan` で進める。{outcomes}\n") == [])
    check("返す口が無ければ返す", len(contract(head + f"{outcomes}\n")) == 1)
    check("結果の語が欠ければ返す", len(contract(head + "`work_plan` で進める。`finished`\n")) == 1)
    check("段の返却に外した行 `設定で外した` を渡すと言えば返す",
          len(contract(head + f"`work_plan` で進める。{outcomes}\n`skipped` は `設定で外した` を渡す\n")) == 1)
    check("廃止した `なし` の設定を指せば返す",
          len(contract(head + f"`work_plan` で進める。{outcomes}\n（`なし` の設定には無い）\n")) == 1)
    check("frontmatter の中の語は本文に数えない",
          len(contract('---\nname: s\ndescription: "`work_plan`"\n---\n' + f"{outcomes}\n")) == 1)

    if failures:
        print(f"\n{len(failures)} 件落ちた: {', '.join(failures)}")
        return 1
    print("\n全部通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
