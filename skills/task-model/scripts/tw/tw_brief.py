"""`tw brief`: 委譲先・受け入れ役への依頼文を、並びのスキルが持つ型から場合に当たる節を選んで出す。

型は `## <節名>` の節の並びで、`{{名前}}` を値で埋める。どの節を選ぶかと値はここが決め、文面は型が持つ。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

import gitinfo
import layout
import taskfile
import tw_config
import tw_edit
import tw_plan

ROLE_IMPLEMENTER = "implementer"
TASK_MODEL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
VISUAL_WORD = "目視"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

IMPLEMENTER_SECTIONS = ("常に", "計画どおり", "書き直し", "古い計画", "別のリポジトリ", "並列の段", "目視")


@dataclass(frozen=True)
class WorkRepo:
    root: str
    branch: str
    worktree: str
    base: str


def cmd_brief_implementer(toplevel: str, task_id: str, step: int | None, rewrite: bool, visual: bool) -> None:
    shown, issue, plan_body, mark, _base, drift = tw_edit.own_plan(toplevel, task_id)
    template_path, sections = _template(toplevel, ROLE_IMPLEMENTER, IMPLEMENTER_SECTIONS)
    specs, steps_error = taskfile.plan_step_specs(plan_body) if taskfile.has_plan(plan_body) else ((), "missing")
    uncovered, unknown = taskfile.plan_coverage(plan_body) if specs else ((), ())
    planned = (
        not rewrite
        and steps_error is None
        and mark in (tw_plan.PLAN_REGISTERED, tw_plan.PLAN_FIRST)
        and not uncovered
        and not unknown
    )
    if not planned and step is not None:
        _usage("brief implementer: --step は計画どおりに渡すときだけ（書き直しの依頼は段を持たない）")
    if planned and step is not None and not 1 <= step <= len(specs):
        _usage(f"brief implementer: --step は 1〜{len(specs)}")
    step = step or 1
    values = {"task_id": shown, "here": os.path.dirname(template_path), "task_model": TASK_MODEL_DIR, "claimed": toplevel}
    chosen = ["常に"]
    if planned:
        chosen.append("計画どおり")
        values["step"] = str(step)
        pairs, _serial = taskfile.parallel_steps(specs)
        if any(step in pair for pair in pairs):
            chosen.append("並列の段")
            values["touched"] = "、".join(f"`{p}`" for p in specs[step - 1].files) or "（欄が空）"
    else:
        chosen.append("書き直し")
        if taskfile.has_plan(plan_body) and mark is None and drift is not None:
            chosen.append("古い計画")
            values["stale_files"] = ",".join(drift[0]) or "?"
    work = work_repo(toplevel, shown, plan_body)
    if work is not None:
        chosen.append("別のリポジトリ")
        values.update(work_repo=work.root, branch=work.branch, worktree=work.worktree, base=work.base)
    if visual or VISUAL_WORD in str(issue.raw.get("acceptance_criteria") or ""):
        chosen.append("目視")
    _print_brief(shown, ROLE_IMPLEMENTER, sections, chosen, values)


def work_repo(toplevel: str, shown: str, plan_body: str) -> WorkRepo | None:
    """`### 作業先` が別のリポジトリなら、その作業先と枝と作業ツリーの道。同じリポジトリか無ければ `None`。

    作業ツリーの道は、枝（タスクIDの小文字）の作業ツリーがあればそのパス。無ければ、着手した作業ツリーが本体でなければ
    `<着手した作業ツリーの親の親>/<作業先の名前>/<枝>`、本体なら `<作業先の親>/<作業先の名前>-<枝>`。
    決めた道に別のものがある・作業先の中に入るなら `INVALID`（終了コード3）。
    """
    root, _ = taskfile.plan_work_repo(plan_body)
    if root is None or not os.path.isdir(root):
        return None
    try:
        if gitinfo.git_common_dir(root) == gitinfo.git_common_dir(toplevel):
            return None
        branch = shown.lower()
        existing = [w.path for w in gitinfo.list_worktrees(cwd=root) if w.branch == branch]
        base = gitinfo.base_branch(root)
        if existing:
            return WorkRepo(root, branch, existing[0], base)
        claimed_trees = gitinfo.list_worktrees(cwd=toplevel)
    except gitinfo.GitCommandError as e:
        print(f"INVALID\t作業先 {root} が読めない（{e}）")
        raise SystemExit(3)
    name = os.path.basename(os.path.normpath(root))
    is_main = bool(claimed_trees) and os.path.realpath(claimed_trees[0].path) == os.path.realpath(toplevel)
    if is_main:
        path = os.path.join(os.path.dirname(os.path.normpath(root)), f"{name}-{branch}")
    else:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.normpath(toplevel))), name, branch)
    real_root = os.path.realpath(root)
    if os.path.lexists(path) or os.path.realpath(path).startswith(real_root + os.sep):
        print(f"INVALID\t作業先の作業ツリーの道 {path} に別のものがあるか、作業先の中に入る（枝 {branch} の作業ツリーを先に作る）")
        raise SystemExit(3)
    return WorkRepo(root, branch, path, base)


def _template(toplevel: str, role: str, known: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    """並び（`steps`、無ければ既定の並び）の順で最初に役の型を持つスキルの型の `(パス, 節名 → 本文)`。

    無い・読めなければ `MISSING`（終了コード6）、知らない節名があれば `INVALID`（終了コード3）。
    """
    config = layout.read_config(toplevel)
    steps = list(config.steps) if config.steps is not None else tw_config.steps_default(toplevel)
    by_value = {c.value: c for c in tw_config.step_candidates(toplevel)}
    path = next((by_value[s].brief(role) for s in steps if s in by_value and by_value[s].brief(role)), None)
    if path is None:
        print(f"MISSING\tbrief-{role}\t並びのスキルの metadata に {layout.BRIEF_KEY_PREFIX}{role} が無い")
        raise SystemExit(6)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        print(f"MISSING\t{path}")
        raise SystemExit(6)
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in [*text.splitlines(), "## "]:
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip("\n")
            current, lines = line[3:].strip(), []
        elif current is not None:
            lines.append(line)
    strange = [s for s in sections if s not in known]
    if strange:
        print(f"INVALID\t{path}: 知らない節 {', '.join(strange)}")
        raise SystemExit(3)
    return path, sections


def _print_brief(shown: str, role: str, sections: dict[str, str], chosen: list[str], values: dict[str, str]) -> None:
    missing_sections = [s for s in chosen if s not in sections]
    if missing_sections:
        print(f"INVALID\t型に節 {', '.join(missing_sections)} が無い")
        raise SystemExit(3)
    text = "\n\n".join(sections[s] for s in chosen)
    unknown = sorted({m.group(1) for m in _PLACEHOLDER.finditer(text) if m.group(1) not in values})
    if unknown:
        print(f"INVALID\t型の値 {', '.join(unknown)} が埋まらない")
        raise SystemExit(3)
    print(f"BRIEF\t{shown}\t{role}\t{','.join(chosen)}")
    print(_PLACEHOLDER.sub(lambda m: values[m.group(1)], text))


def _usage(message: str) -> None:
    print(f"usage: {message}", file=sys.stderr)
    raise SystemExit(2)
