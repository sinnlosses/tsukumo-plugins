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
    check("task-model 以外の実在する兄弟のパスは返す", forbidden("a", "${CLAUDE_SKILL_DIR}/../next-task/SKILL.md") == ["next-task"])

    entry = check_repo.RETROSPECT_ENTRY_REFERENCE
    check("retrospect/SKILL.md の指し方を拾う", entry.search("`retrospect/SKILL.md` の出典") is not None)
    check("retrospect の SKILL.md の指し方を拾う", entry.search("`retrospect` の SKILL.md の節") is not None)
    check("detail.md の指し方は拾わない", entry.search("`retrospect/detail.md` の出典") is None)

    edges = check_repo.forbidden_edges
    check("next-task から task-model のファイルは許す", edges("next-task", "`skills/task-model/WORKFLOW.md`", skills) == [])
    check("plan-tasks から next-task のファイルは返す",
          edges("plan-tasks", "`next-task` スキルの `ship-stopped.md` の表", skills) == ["next-task"])
    check("retrospect から next-task の手順のファイルは返す",
          edges("retrospect", "next-task/ship.md を読む", skills) == ["next-task"])
    check("task-model からほかのスキルのファイルは返す", edges("task-model", "`next-task` の `SKILL.md`", skills) == ["next-task"])
    check("retro から retrospect のファイルは許す", edges("retro", "`retrospect` の `detail.md`", skills) == [])
    check("自分自身のファイルは許す", edges("plan-tasks", "skills/plan-tasks/SKILL.md", skills) == [])
    check("名前で呼ぶだけなら拾わない", edges("next-task", "`retro` を Skill ツールで呼ぶ", skills) == [])
    check("台帳の置き場は拾わない",
          edges("task-model", "`$(git rev-parse --git-common-dir)/task-workflow/flow/<ID>.md`", skills) == [])

    upward = check_repo.upward_reference_problems
    brief = os.path.join(check_repo.SKILLS, "next-task", "ship-stopped.md")
    check("1つ上の `task-model/WORKFLOW.md` は届く", upward(brief, "このファイルの1つ上のディレクトリの `task-model/WORKFLOW.md`") == [])
    check("括弧の中の言及から2つ上の `README.md` に届く",
          upward(brief, "置き場（このファイルの2つ上のディレクトリ）の `README.md`") == [])
    check("括弧の後ろの `scripts/check_repo.py` に届く",
          upward(brief, "このファイルの2つ上のディレクトリ（`retrospect` の置き場）の `scripts/check_repo.py`") == [])
    check("3つ上の `README.md` は届かないとして返す",
          len(upward(brief, "置き場（このファイルの3つ上のディレクトリ）の `README.md`")) == 1)
    check("先のパスを読めない言及を返す", len(upward(brief, "このファイルの2つ上のディレクトリにあるスクリプト")) == 1)

    mentions = check_repo.task_model_name_mentions
    agents = check_repo.agent_names()
    check("データの名前は拾わない",
          mentions("`task-workflow.last-id`・`/task-workflow/<ID>`・`docs/history/retrospect.md`・`retrospect_due`・lap の段 retro",
                   skills, agents) == [])
    check("スキル名とエージェント名を拾う",
          mentions("`/next-task` から `no-delegate` に委譲し `/retro` を呼ぶ", skills, agents) == ["next-task", "no-delegate", "retro"])
    check("`brief` の役の語は拾わない",
          mentions("`tw brief acceptor T-xxx`・`brief implementer`", skills, agents) == [])
    check("裸の `acceptor` は拾う",
          mentions("受け入れ役は `acceptor` に渡す。brief の前の acceptor", skills, agents) == ["acceptor"])

    unknown = check_repo.unknown_qualified_names
    known = {"retrospect", "acceptor"}
    check("全名が実在する名なら問題なし", unknown("`tsukumo-workflow:retrospect` と `tsukumo-workflow:acceptor`。", known) == [])
    check("実在しない名の全名を返す", unknown("`tsukumo-workflow:retrospct` と `tsukumo-workflow:acceptor`", known) == ["retrospct"])
    check("`<name>` の雛形は拾わない", unknown("`tsukumo-workflow:<name>`", known) == [])
    check("名前を指して呼ぶ言い回しを拾う", check_repo.SHORT_NAME_CALL.search("`retrospect` を Skill ツールで名前を指して呼び") is not None)
    check("全名の呼び方は拾わない", check_repo.SHORT_NAME_CALL.search("`tsukumo-workflow:retrospect` を Skill ツールで呼び") is None)

    hard = check_repo.hardcoded_phase_titles
    check("phase() の直書きを拾う", len(hard('phase("Implement")\n')) == 1)
    check("1行の選択肢で label より前の phase: 直書きを拾う",
          len(hard('await agent(p, { agentType: "a", phase: "Accept", label: "x" })\n')) == 1)
    check("複数行の選択肢で独立した行の phase: 直書きを拾う",
          len(hard('await agent(p, {\n  agentType: "a",\n  label: "x",\n  phase: "Accept",\n})\n')) == 1)
    check("識別子の phase: は通す",
          hard('await agent(p, {\n  agentType: "a",\n  phase: acceptTitle,\n})\n') == [])
    check("戻り値の phase: 文字列は通す",
          hard('return { stopped: true, phase: "accept", reason: "x" }\n') == [])

    if failures:
        print(f"\n{len(failures)} 件落ちた: {', '.join(failures)}")
        return 1
    print("\n全部通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
