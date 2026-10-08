"""自己テストが使う、タスク本文の組み立て。

正しい本文はここで組み、`## やること` や `### 名指すファイル` を自己テストに直書きしない。
壊れた本文を作るテストは、ここで組んだ本文を変形して作る。
"""

from __future__ import annotations

from collections.abc import Sequence

import taskfile


def task_body(
    steps: Sequence[tuple[str, str]] = (),
    files: Sequence[str] = (),
    work_repo: str | None = None,
    *,
    purpose: str = "x",
    acceptance: str = "x",
    caution: str = "",
) -> str:
    """7つの見出しを正典の順に並べた本文。`steps` は（段の見出し, 本文）の並びで、`### 1.` から番号を振る。

    `steps`・`files` が空なら空の `## やること`（`--hold` 用）になる。
    """
    plan = [f"### {n}. {title}\n" + (f"{text}\n" if text else "") for n, (title, text) in enumerate(steps, 1)]
    if work_repo is not None:
        plan.append(f"{taskfile.PLAN_WORK_REPO_HEADING}\n- `{work_repo}`\n")
    if files:
        listed = "".join(f"- `{path}`\n" for path in files)
        plan.append(f"{taskfile.PLAN_FILES_HEADING}\n{listed}")
    contents = {
        taskfile.PURPOSE_HEADING: f"{purpose}\n",
        "## 解くべき論点": "なし\n",
        taskfile.PLAN_HEADING: "\n".join(plan),
        taskfile.ACCEPTANCE_HEADING: f"{acceptance}\n",
        taskfile.CAUTION_HEADING: f"{caution}\n" if caution else "",
    }
    return "\n".join(f"{heading}\n{contents.get(heading, '')}" for heading in taskfile.SECTION_HEADINGS)
