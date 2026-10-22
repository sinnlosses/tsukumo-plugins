"""タスクの形（`Task`）と本文の枠の検査、`## やること` の段・名指すファイル・作業先の解析、`## 結果` の差し替え。

`tw show` が出す front matter（`render`）は **YAML ではない** 専用の6行
（`id` / `summary` / `status` / `difficulty` / `loopable` / `dependencies`。
`loopable` の次に任意の `direct: Y` を置けば7行）。

検査は例外を投げない。呼び出し側が「INVALID＝データの不備」と
「traceback＝環境の故障」を取り違えないよう、理由の文字列（通れば `None`）を返す。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATUS_VALUES = ("todo", "hold", "done", "dropped", "draft")
DIFFICULTY_VALUES = ("haiku", "sonnet", "opus")
LOOPABLE_VALUES = ("Y", "N")
KIND_VALUES = ("bug", "feature", "refactor", "docs")

# 委譲しない近道の基準（WORKFLOW.md「difficulty とモデルの切り替え」の表）。
DIRECT_DIFFICULTY = "haiku"
DIRECT_MAX_STEPS = 1
DIRECT_MAX_PLAN_FILES = 2

# 本文の枠（WORKFLOW.md「タスクの本文」）。7つの見出しを必ずこの順で置き、要らない欄は空か「なし」にする。
PURPOSE_HEADING = "## 目的・背景"
PLAN_HEADING = "## やること"
# 登録時（`tw new`・`tw adopt`）に書いた `## やること` が名指すファイル。1行1つの「- `パス`」。
PLAN_FILES_HEADING = "### 名指すファイル"
# 名指すファイルが別のリポジトリにあるときの、そのリポジトリの根の絶対パス。1行の「- `パス`」。
PLAN_WORK_REPO_HEADING = "### 作業先"
ACCEPTANCE_HEADING = "## 完了条件"
CAUTION_HEADING = "## 注意"
QUESTION_HEADING = "## 解くべき論点"
SECTION_HEADINGS = (
    PURPOSE_HEADING,
    "## 決まっていること（蒸し返さない）",
    QUESTION_HEADING,
    PLAN_HEADING,
    ACCEPTANCE_HEADING,
    CAUTION_HEADING,
    "## 参考情報",
)
# 空・「なし」にできない欄。
FILLED_SECTIONS = (PURPOSE_HEADING, ACCEPTANCE_HEADING)
EMPTY_MARK = "なし"

RESULT_HEADING = "## 結果"  # done/dropped で必須（3.3）。常に本文の最後に置く。

OPUS_DIFFICULTY = "opus"
# ドラフトの `- 観点:` の語彙。正典は skills/retrospect/detail.md で、食い違いは retrospect の自己テストが落とす。
VIEWPOINT_COLORS = ("赤", "黄")
VIEWPOINTS = (
    "道案内",
    "機械の検査",
    "コーディング規約",
    "常に読み込まれる設定ファイル",
    "道具の経済",
    "空振り",
    "情報への手の届き",
)
WEEKLY_VIEWPOINTS = ("流れの数", "段の所要時間", "規則の棚卸し")

# 枠の7節の後ろ（`## 結果` の前）に置く任意の節。正典は WORKFLOW.md「タスクの本文」で、表と食い違えば自己テストが落とす。
INTERVENTION_HEADING = "## 介入"
INTERVENTION_SIGNAL = "狙う兆候"
INTERVENTION_BASELINE = "基準線"
INTERVENTION_TARGET = "出し先の種類"
INTERVENTION_DUE = "判定の期日"
INTERVENTION_STOP = "やめる条件"
INTERVENTION_CUT = "削るもの"
INTERVENTION_FIELDS = (
    INTERVENTION_SIGNAL,
    INTERVENTION_BASELINE,
    INTERVENTION_TARGET,
    INTERVENTION_DUE,
    INTERVENTION_STOP,
)
# 出し先の梯子。上から先に選ぶ。
INTERVENTION_TARGETS = ("hook", "検査", "スクリプト", "スキル", "サブエージェント", "常設の文章")
INTERVENTION_PROSE = "常設の文章"
# `/` で終わるものはその下、ほかは同じパス。名指すファイルの相対パス（作業先があればその根から）と比べる。
HARNESS_SURFACES = ("CLAUDE.md", ".claude/rules/", "docs/workflow.md", "skills/", ".tw/config.toml", "scripts/hook/")


@dataclass(frozen=True)
class Task:
    id: str
    summary: str
    status: str
    difficulty: str
    loopable: str  # "Y" | "N"（真偽値にしない。3.2 の文法どおりの文字を保つ）
    dependencies: tuple[str, ...]
    body: str
    direct: str = "N"  # 委譲しない近道の印。"Y" のときだけ front matter に `direct: Y` の行を置く


def render(task: Task) -> str:
    """front matter を6行ちょうど（`direct: Y` があれば7行）で書き、本文を続ける。

    本文は「閉じる `---` の次に空行1行、末尾は改行1つ」に揃える。Markdown の整形ツール
    （oxfmt・prettier）は見出しの前に空行を求めるので、`## 目的・背景` から始まる本文をそのまま
    連結すると、登録した直後のファイルが整形の検査で落ちる。
    """
    body = task.body.strip("\n")
    return (
        "---\n"
        f"id: {task.id}\n"
        f"summary: {task.summary}\n"
        f"status: {task.status}\n"
        f"difficulty: {task.difficulty}\n"
        f"loopable: {task.loopable}\n"
        + ("direct: Y\n" if task.direct == "Y" else "")
        + f"dependencies: [{', '.join(task.dependencies)}]\n"
        "---\n"
        + (f"\n{body}\n" if body else "")
    )


def validate_new_body(body: str, hold: bool, difficulty: str) -> str | None:
    """`task new`・`task adopt` の本文検査。枠の検査に加え、`difficulty` が opus なら論点を求め、`## やること` の中身・段・
    `### 名指すファイル` を求める（`### 作業先` があればその形も）。中身を空にできるのは `hold` のときだけ。問題が無ければ `None`。"""
    error = validate_body(body) or opus_refusal(difficulty, body)
    if error is not None:
        return error
    if not has_plan(body):
        if not hold:
            return f"{PLAN_HEADING} に計画を書く（空にできるのは --hold だけ）"
        return intervention_refusal(body, required=_viewpoint_match(body) is not None)
    error = plan_steps(body)[1] or plan_files(body)[1] or plan_work_repo(body)[1] or coverage_refusal(body)
    if error is not None:
        return error
    required = _viewpoint_match(body) is not None or bool(harness_paths(plan_files(body)[0]))
    return intervention_refusal(body, required=required)


def validate_draft_body(body: str) -> str | None:
    """`tw new --draft` の本文検査。見出しは枠の節を枠の順に欠けてよく、中身が要るのは `## 目的・背景` だけ。
    `## やること` に中身があれば `validate_new_body` と同じ計画の検査をかける。`- 観点:` の行があれば `## 介入` を求める。
    問題が無ければ `None`。"""
    error = validate_body(body, draft=True) or viewpoint_refusal(body)
    if error is None and has_plan(body):
        error = plan_steps(body)[1] or plan_files(body)[1] or plan_work_repo(body)[1] or coverage_refusal(body)
    if error is not None:
        return error
    return intervention_refusal(body, required=_viewpoint_match(body) is not None)


def validate_edited_body(
    old_body: str, new_body: str, *, draft: bool, old_difficulty: str, difficulty: str
) -> str | None:
    """`task edit` の本文検査。枠の検査（`draft` はドラフトの緩い枠）に加え、変えた節だけを見る:
    `## やること` を変えて中身があれば段の形、`## やること` か `## 完了条件` を変えて中身があれば完了条件の覆い、opus に上げたか論点を変えたなら論点、`## 目的・背景` を変えたなら `- 観点:` の語彙、
    `## 介入` を変えたならその形（必須かどうかは見ない）。"""
    error = validate_body(new_body, draft)
    if error is not None:
        return error
    if not draft and difficulty == OPUS_DIFFICULTY:
        if old_difficulty != OPUS_DIFFICULTY or section_changed(old_body, new_body, QUESTION_HEADING):
            error = opus_refusal(difficulty, new_body)
    if error is None and section_changed(old_body, new_body, PURPOSE_HEADING):
        error = viewpoint_refusal(new_body)
    if error is None and section_changed(old_body, new_body, INTERVENTION_HEADING):
        error = intervention_refusal(new_body, required=False)
    dropped = dropped_plan_headings(old_body, new_body) if error is None else ()
    if dropped:
        effects = " ".join(_DROPPED_EFFECT[heading] for heading in dropped)
        return f"{PLAN_HEADING} から {'・'.join(dropped)} が落ちている。書き直しでも残す。{effects}"
    if error is not None or not has_plan(new_body):
        return error
    if plan_changed(old_body, new_body):
        error = plan_steps(new_body)[1]
    if error is None and (plan_changed(old_body, new_body) or section_changed(old_body, new_body, ACCEPTANCE_HEADING)):
        error = coverage_refusal(new_body)
    return error


_DROPPED_EFFECT = {
    PLAN_WORK_REPO_HEADING: (
        f"{PLAN_WORK_REPO_HEADING}: 別のリポジトリのタスクの作業先。落とすと `tw accept` が作業先を引けず `NO_RANGE` になる。"
    ),
    PLAN_FILES_HEADING: (
        f"{PLAN_FILES_HEADING}: 落とすと `tw claim` の `plan-check` が名指したファイルの変わりを判定できず `unreadable`（古い計画）になり、"
        "`tw step` の `MAIN_TOUCHED` も名指したファイルを見なくなる。"
    ),
}


def dropped_plan_headings(old_body: str, new_body: str) -> tuple[str, ...]:
    """`## やること` を書き直して、`old_body` にあった `### 作業先`・`### 名指すファイル` が `new_body` に無いもの。"""
    if not plan_changed(old_body, new_body):
        return ()

    def headings(body: str) -> set[str]:
        return {line.rstrip() for line in dict(_frame_sections(body)[1]).get(PLAN_HEADING, "").split("\n")}

    old, new = headings(old_body), headings(new_body)
    return tuple(h for h in (PLAN_WORK_REPO_HEADING, PLAN_FILES_HEADING) if h in old and h not in new)


def opus_refusal(difficulty: str, body: str) -> str | None:
    """`difficulty` が opus で `## 解くべき論点` が空・「なし」なら、その理由。"""
    if difficulty == OPUS_DIFFICULTY and is_blank(dict(_frame_sections(body)[1]).get(QUESTION_HEADING, "")):
        return f"difficulty が {OPUS_DIFFICULTY} のときは {QUESTION_HEADING} に論点を書く（空・「{EMPTY_MARK}」にできない）"
    return None


def viewpoint_refusal(body: str) -> str | None:
    """`## 目的・背景` に `- 観点:` の行があるとき、観点が語彙の外か空なら、その理由。色は無くてよく、付くなら赤か黄。"""
    match = _viewpoint_match(body)
    if match is None:
        return None
    viewpoint = match.group(1)
    if viewpoint in VIEWPOINTS + WEEKLY_VIEWPOINTS:
        return None
    return (
        f"`- 観点:` の観点 {viewpoint!r} が語彙の外（色は {'・'.join(VIEWPOINT_COLORS)} か無し。観点は "
        + "・".join(VIEWPOINTS + WEEKLY_VIEWPOINTS)
        + "）"
    )


def intervention(body: str) -> dict[str, str] | None:
    """`## 介入` の `- <名前>: <値>` の行を `{名前: 値}` で。同じ名前が2つあれば後の値。節が無い・空・「なし」なら `None`。"""
    content = dict(_frame_sections(body)[1]).get(INTERVENTION_HEADING, "")
    if is_blank(content):
        return None
    return {m.group(1): m.group(2) for m in _INTERVENTION_LINE.finditer(content)}


def harness_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """`paths` のうち harness の面（`HARNESS_SURFACES`）に当たるもの。面のディレクトリの親は当たらない。"""

    def hit(path: str, surface: str) -> bool:
        if surface.endswith("/"):
            return path.rstrip("/") == surface.rstrip("/") or path.startswith(surface)
        return path == surface

    return tuple(p for p in paths if any(hit(p, s) for s in HARNESS_SURFACES))


def intervention_refusal(body: str, *, required: bool) -> str | None:
    """`## 介入` の形の誤り。`required` なら節が無いのも誤り。節が無く `required` でもなければ `None`。"""
    if intervention(body) is None:
        if not required:
            return None
        return (
            f"`- 観点:` の行を持つ本文か、harness の面（{'・'.join(HARNESS_SURFACES)}）を名指す計画には "
            f"{INTERVENTION_HEADING} を {'・'.join(f'`- {f}:`' for f in INTERVENTION_FIELDS)} の行で書く"
        )
    content = dict(_frame_sections(body)[1]).get(INTERVENTION_HEADING, "")
    found = [(m.group(1), m.group(2)) for m in _INTERVENTION_LINE.finditer(content)]
    names = [n for n, _ in found]
    twice = [f for f in (*INTERVENTION_FIELDS, INTERVENTION_CUT) if names.count(f) > 1]
    if twice:
        return f"{INTERVENTION_HEADING} の `- {twice[0]}:` は1行まで"
    fields = dict(found)
    missing = [f for f in INTERVENTION_FIELDS if not fields.get(f)]
    if missing:
        return f"{INTERVENTION_HEADING} に {'・'.join(f'`- {f}:`' for f in missing)} の行が無いか空"
    target = fields[INTERVENTION_TARGET]
    if target not in INTERVENTION_TARGETS:
        return f"{INTERVENTION_HEADING} の `- {INTERVENTION_TARGET}:` {target!r} が語彙の外（{'・'.join(INTERVENTION_TARGETS)} のどれか1つ）"
    if target == INTERVENTION_PROSE and not fields.get(INTERVENTION_CUT):
        return f"{INTERVENTION_HEADING} の `- {INTERVENTION_TARGET}:` が {INTERVENTION_PROSE} なら `- {INTERVENTION_CUT}:` の行を書く"
    return None


_INTERVENTION_LINE = re.compile(r"^- ([^:：\n]+?):[ \t]*(.*?)[ \t]*$", re.MULTILINE)


def section_content(body: str, heading: str) -> str:
    """`heading` の節の中身（前後の空行を落とす）。節が無ければ空。"""
    return dict(_frame_sections(body)[1]).get(heading, "")


def section_changed(old_body: str, new_body: str, heading: str) -> bool:
    """`heading` の節の中身が `old_body` と違うか。行末の空白と連続する空行の数は無視する。"""
    old = dict(_frame_sections(old_body)[1]).get(heading, "")
    new = dict(_frame_sections(new_body)[1]).get(heading, "")
    return _squeeze(old) != _squeeze(new)


@dataclass(frozen=True)
class PlanStep:
    """`## やること` の段1つ。`after` は前の段の番号（欄が無ければ直前の段）、`files` は触るファイル（欄が無ければ空）、
    `covers` は満たす完了条件の行番号（欄が無ければ `None`、`なし` なら空）。
    `no_files` は触るファイルの欄が `なし` と書かれていること。"""

    name: str
    after: tuple[int, ...]
    files: tuple[str, ...]
    covers: tuple[int, ...] | None = None
    no_files: bool = False


def plan_steps(body: str) -> tuple[tuple[str, ...], str | None]:
    """`## やること` の段（`### n. 名前` の名前）を番号の順に。形が違えば `((), 理由)`。"""
    specs, error = plan_step_specs(body)
    return tuple(s.name for s in specs), error


def plan_step_specs(body: str) -> tuple[tuple[PlanStep, ...], str | None]:
    """`## やること` の段を番号の順に、欄（`- 前の段:`・`- 触るファイル:`）ごと。形が違えば `((), 理由)`。

    段の番号は `### 1.` から1つずつ増え、段は1つ以上。`### 名指すファイル`・`### 作業先` は段に数えず、
    ほかの `### ` 見出しは拒む。欄は段の見出しから次の `### ` 見出しまでに、それぞれ1行まで置く。
    前の段の番号はその段より小さく、並列の組に入る段は触るファイルの欄を要る。最後の段は欄によらずほかの段すべてのあとに走る。
    """
    lines = dict(_frame_sections(body)[1]).get(PLAN_HEADING, "").split("\n")
    steps: list[tuple[str, dict[str, str]]] = []
    in_step = False
    for line in lines:
        if not line.startswith("### "):
            field = _PLAN_FIELD_LINE.match(line.rstrip()) if in_step else None
            if field is not None:
                fields = steps[-1][1]
                if field.group(1) in fields:
                    return (), f"段 {len(steps)} の `- {field.group(1)}:` は1行まで"
                fields[field.group(1)] = field.group(2)
            continue
        if line.rstrip() in (PLAN_FILES_HEADING, PLAN_WORK_REPO_HEADING):
            in_step = False
            continue
        m = _PLAN_STEP_LINE.match(line.rstrip())
        if m is None:
            return (), (
                f"{PLAN_HEADING} の `### ` 見出しは段（`### 1. 名前` から穴なく続く）・{PLAN_FILES_HEADING}・"
                f"{PLAN_WORK_REPO_HEADING} だけ: {line.strip()}"
            )
        if int(m.group(1)) != len(steps) + 1:
            return (), f"{PLAN_HEADING} の段の番号は `### 1.` から穴なく続ける（{len(steps) + 1} の位置に {m.group(1)} がある）"
        steps.append((m.group(2), {}))
        in_step = True
    if not steps:
        return (), f"{PLAN_HEADING} に段（`### 1. 名前` から穴なく続く見出し）が1つも無い"
    specs: list[PlanStep] = []
    for n, (name, fields) in enumerate(steps, start=1):
        after, error = _parse_after(n, fields.get(PLAN_AFTER_FIELD))
        if error is None:
            files, error = _parse_touched(n, fields.get(PLAN_TOUCHED_FIELD))
        if error is None:
            covers, error = _parse_covers(n, fields.get(PLAN_COVERS_FIELD))
        if error is not None:
            return (), error
        no_files = (fields.get(PLAN_TOUCHED_FIELD) or "").strip() == "なし"
        specs.append(PlanStep(name, after, files, covers, no_files))
    for a, b in _unordered_pairs(_joined_after(specs)):
        bare = [n for n in (a, b) if not specs[n - 1].files and not specs[n - 1].no_files]
        if bare:
            return (), f"段 {a} と段 {b} は並列になるので、段 {bare[0]} に `- {PLAN_TOUCHED_FIELD}:` を書く"
    return tuple(specs), None


PLAN_AFTER_FIELD = "前の段"
PLAN_TOUCHED_FIELD = "触るファイル"
PLAN_COVERS_FIELD = "満たす完了条件"
_PLAN_STEP_LINE = re.compile(r"^### (\d+)\. (\S.*)$")
_PLAN_FIELD_LINE = re.compile(rf"^- ({PLAN_AFTER_FIELD}|{PLAN_TOUCHED_FIELD}|{PLAN_COVERS_FIELD}):\s*(.*)$")
_CRITERION_ITEM = re.compile(r"^(?:\d+\.|[-*])\s+")
VISUAL_CRITERION_MARK = "[目視]"


def _parse_covers(n: int, raw: str | None) -> tuple[tuple[int, ...] | None, str | None]:
    if raw is None:
        return None, None
    if raw.strip() == EMPTY_MARK:
        return (), None
    items = [x.strip() for x in raw.split(",")]
    if not all(x.isdigit() for x in items):
        return None, f"段 {n} の `- {PLAN_COVERS_FIELD}:` は完了条件の行番号のコンマ区切りか `{EMPTY_MARK}`: {raw.strip()}"
    return tuple(sorted({int(x) for x in items})), None


def criteria_items(section: str) -> tuple[str, ...]:
    """`## 完了条件` の節の中身（見出しなし）の項目の本文。行頭が `1.`・`-`・`*` の項目を取り、項目が1つも無ければ空でない行を取る。"""
    lines = section.split("\n")
    items = [_CRITERION_ITEM.sub("", line, count=1).strip() for line in lines if _CRITERION_ITEM.match(line)]
    return tuple(items) or tuple(line.strip() for line in lines if line.strip())


def criteria_count(body: str) -> int:
    """`## 完了条件` の行の数（`criteria_items` の数）。"""
    return len(criteria_items(dict(_frame_sections(body)[1]).get(ACCEPTANCE_HEADING, "")))


def visual_required(section: str) -> bool:
    """`## 完了条件` の節の中身に、頭が印 `[目視]` の項目があるか。"""
    return any(item.startswith(VISUAL_CRITERION_MARK) for item in criteria_items(section))


def plan_coverage(body: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """`(どの段にも引かれていない完了条件の行番号, 存在しない行番号)`。どちらも小さい順。

    1つでも段が `- 満たす完了条件:` の欄を持つ計画だけを見る。欄が無い旧い計画・段の読めない計画・
    `## 完了条件` が空の本文は `((), ())`。
    """
    specs, error = plan_step_specs(body)
    rows = criteria_count(body)
    if error is not None or rows == 0 or all(s.covers is None for s in specs):
        return (), ()
    cited = {n for s in specs for n in (s.covers or ())}
    uncovered = tuple(n for n in range(1, rows + 1) if n not in cited)
    unknown = tuple(sorted(n for n in cited if not 1 <= n <= rows))
    return uncovered, unknown


def coverage_refusal(body: str) -> str | None:
    """段の `- 満たす完了条件:` が `## 完了条件` を覆わないとき、その理由。"""
    uncovered, unknown = plan_coverage(body)
    parts = []
    if uncovered:
        parts.append(f"完了条件の {','.join(map(str, uncovered))} 行目がどの段の `- {PLAN_COVERS_FIELD}:` にも引かれていない")
    if unknown:
        parts.append(f"`- {PLAN_COVERS_FIELD}:` の {','.join(map(str, unknown))} は完了条件に無い行")
    return "。".join(parts) or None


def _parse_after(n: int, raw: str | None) -> tuple[tuple[int, ...], str | None]:
    if raw is None:
        return ((n - 1,) if n > 1 else ()), None
    if raw.strip() == "なし":
        return (), None
    items = [x.strip() for x in raw.split(",")]
    if not all(x.isdigit() for x in items):
        return (), f"段 {n} の `- {PLAN_AFTER_FIELD}:` は番号のコンマ区切りか `なし`: {raw.strip()}"
    after = tuple(int(x) for x in items)
    if len(set(after)) != len(after) or any(not 1 <= x < n for x in after):
        return (), f"段 {n} の `- {PLAN_AFTER_FIELD}:` は {n} より小さい番号を重ねずに並べる: {raw.strip()}"
    return tuple(sorted(after)), None


def _parse_touched(n: int, raw: str | None) -> tuple[tuple[str, ...], str | None]:
    if raw is None or raw.strip() == "なし":
        return (), None
    items = [x.strip() for x in raw.split(",")]
    paths: list[str] = []
    for item in items:
        m = _PLAN_TOUCHED_ITEM.match(item)
        if m is None:
            return (), f"段 {n} の `- {PLAN_TOUCHED_FIELD}:` は `` `パス` `` のコンマ区切り: {raw.strip()}"
        path = m.group(1)
        if path.startswith(("/", "~")) or ".." in path.split("/"):
            return (), f"段 {n} の `- {PLAN_TOUCHED_FIELD}:` の {path} はリポジトリの根からの相対パスにする"
        paths.append(path)
    return tuple(paths), None


_PLAN_TOUCHED_ITEM = re.compile(r"^`([^`]+)`$")


def _joined_after(specs: tuple[PlanStep, ...] | list[PlanStep]) -> list[tuple[int, ...]]:
    """段ごとの前の段。最後の段はほかの段すべて。"""
    after = [s.after for s in specs]
    if after:
        after[-1] = tuple(range(1, len(after)))
    return after


def _ancestors(after: list[tuple[int, ...]]) -> list[set[int]]:
    """段ごとに、その段より先に済む段の番号（`after` を辿った閉包）。`after[i]` は段 `i + 1` の前の段。"""
    result: list[set[int]] = []
    for deps in after:
        found: set[int] = set()
        for d in deps:
            found.add(d)
            found |= result[d - 1]
        result.append(found)
    return result


def _unordered_pairs(after: list[tuple[int, ...]]) -> list[tuple[int, int]]:
    """どちらも他方より先に済むと決まっていない段の組 `(a, b)`（`a < b`）を番号の順に。`after[i]` は段 `i + 1` の前の段。"""
    ancestors = _ancestors(after)
    return [(a, b) for b in range(2, len(after) + 1) for a in range(1, b) if a not in ancestors[b - 1]]


def parallel_steps(
    specs: tuple[PlanStep, ...],
) -> tuple[list[tuple[int, int]], list[tuple[int, int, tuple[str, ...]]]]:
    """`(並列にできる組, 触るファイルが重なって外した組と重なったパス)`。

    重なった組は番号の小さい段を先にして1本道にし、それで前後の決まった組も並列から外す。
    """
    after, serial = _serialized_after(specs)
    return _unordered_pairs(after), serial


def _serialized_after(
    specs: tuple[PlanStep, ...],
) -> tuple[list[tuple[int, ...]], list[tuple[int, int, tuple[str, ...]]]]:
    """`(段ごとの前の段に、触るファイルの重なりで足した前後を加えたもの, 重なって外した組と重なったパス)`。"""
    after = _joined_after(specs)
    serial: list[tuple[int, int, tuple[str, ...]]] = []
    for a, b in _unordered_pairs(after):
        shared = _overlap(specs[a - 1].files, specs[b - 1].files)
        if shared:
            serial.append((a, b, shared))
            after[b - 1] = (*after[b - 1], a)
    return after, serial


def step_waits(specs: tuple[PlanStep, ...]) -> list[tuple[int, ...]]:
    """段ごとに、走る前に済んでいる要る段（`parallel_steps` の前後）から、ほかの要る段の先に済むものを除いた番号。"""
    after, _ = _serialized_after(specs)
    ancestors = _ancestors(after)
    waits: list[tuple[int, ...]] = []
    for deps in after:
        direct = set(deps)
        waits.append(tuple(sorted(d for d in direct if not any(d in ancestors[o - 1] for o in direct))))
    return waits


def parallel_files(specs: tuple[PlanStep, ...], n: int) -> tuple[str, ...]:
    """段 `n` と並列の組（`parallel_steps`）になる段の触るファイルを、段の番号の順に重ねずに。"""
    pairs, _ = parallel_steps(specs)
    others = sorted({b if a == n else a for a, b in pairs if n in (a, b)})
    return tuple(dict.fromkeys(p for o in others for p in specs[o - 1].files))


PHASE_PLAN = "計画"
PHASE_IMPLEMENT = "実装"
PHASE_ACCEPT = "受け入れ"
PHASE_RETRO = "振り返り"
PHASE_SHIP = "送り出し"
PHASE_FIX = "直す"
PHASE_VERIFY = "検証"

STEP_IMPLEMENT = "implement-step"
STEP_ACCEPT = "accept-step"
STEP_RETRO = "retrospect-step"
STEP_SHIP = "ship-step"
# 並びのスキルの全名の `:` より後ろ。この4つのほかは名前がそのまま帯の段の名前になる
DEFAULT_STEP_NAMES = (STEP_IMPLEMENT, STEP_ACCEPT, STEP_RETRO, STEP_SHIP)

FLOW_DELEGATE = "delegate"
FLOW_MAIN = "main"
FLOW_DIRECT = "direct"
PLAN_REGISTERED_STATE = "registered"
PLAN_WRITTEN_STATE = "written"
PLAN_MISSING_STATE = "missing"


def _step_groups(specs: tuple[PlanStep, ...]) -> list[list[int]]:
    """段の番号を読み順に、全組が並列の組に入る連続した段だけ1つにまとめた並び。"""
    pairs, _ = parallel_steps(specs)
    free = set(pairs)
    groups: list[list[int]] = []
    n = 1
    while n <= len(specs):
        run = [n]
        while n + 1 <= len(specs) and all((m, n + 1) in free for m in run):
            n += 1
            run.append(n)
        groups.append(run)
        n += 1
    return groups


def work_plan_phases(
    specs: tuple[PlanStep, ...],
    flow: str,
    plan_state: str,
    through: str | None,
    also: tuple[int, ...],
    steps: tuple[str, ...] = (),
    skipped: tuple[str, ...] = (),
) -> tuple[dict | None, str | None]:
    """`work_plan` の引数（`phases`・`current`・`finishedInGroup`・あれば `delegatedRange`・`skippedPhases`）。形が決まらなければ `(None, 理由)`。

    `flow` は `FLOW_DELEGATE`・`FLOW_MAIN`・`FLOW_DIRECT`。振り返りの段は `steps` に `retrospect-step` があるときだけ。`through` はその段までと、それ以前を済ませた位置（省略は何も済んでいない）。
    `also` は並列のまとまりの中で、`through` の後ろにあるが先に済んだ段の番号。
    `steps` は並びのスキルの全名で、空なら `DEFAULT_STEP_NAMES` の順。`skipped` は済んだ段のうち飛ばした段の名前。
    """
    direct = flow == FLOW_DIRECT
    entries: list[str | list[str]] = []
    keys: list[list[str]] = []
    range_out: dict | None = None
    for step in [s.rsplit(":", 1)[-1] for s in steps] or list(DEFAULT_STEP_NAMES):
        if step == STEP_IMPLEMENT and direct:
            entries += [PHASE_FIX, PHASE_VERIFY]
            keys += [[PHASE_FIX], [PHASE_VERIFY]]
        elif step == STEP_IMPLEMENT:
            if plan_state != PLAN_REGISTERED_STATE:
                entries.append(PHASE_PLAN)
                keys.append([PHASE_PLAN])
            first = len(entries)
            if plan_state == PLAN_MISSING_STATE:
                entries.append(PHASE_IMPLEMENT)
                keys.append([PHASE_IMPLEMENT])
                range_out = {"first": first}
            else:
                for group in _step_groups(specs):
                    names = [specs[n - 1].name for n in group]
                    entries.append(names if len(group) > 1 else names[0])
                    keys.append([f"#{n}" for n in group])
                range_out = {"first": first, "count": len(specs)}
        elif step == STEP_ACCEPT:
            if not direct:
                entries.append(PHASE_ACCEPT)
                keys.append([PHASE_ACCEPT])
        elif step == STEP_RETRO:
            if not direct:
                entries.append(PHASE_RETRO)
                keys.append([PHASE_RETRO])
        else:
            phase = PHASE_SHIP if step == STEP_SHIP else step
            entries.append(phase)
            keys.append([phase])
    if flow != "delegate":
        range_out = None
    if not entries:
        return None, "並びから組める段が無い"
    finished: set[str] = set()
    if through is not None:
        flat = [k for entry in keys for k in entry]
        steps = [k for k in flat if k.startswith("#")]
        if through.isdigit():
            target = f"#{through}"
        elif through == PHASE_IMPLEMENT and steps:
            target = steps[-1]
        else:
            target = through
        if target not in flat:
            return None, f"段 {through} は並びに無い（{' '.join(str(p) for p in entries)}）"
        finished = set(flat[: flat.index(target) + 1])
    for n in also:
        if f"#{n}" not in {k for entry in keys for k in entry}:
            return None, f"--also の段 {n} は並びに無い"
        finished.add(f"#{n}")
    current = 0
    while current < len(keys) and all(k in finished for k in keys[current]):
        current += 1
    if current == len(keys):
        return None, f"全部済みにする指定は渡せない（最後の段 {entries[-1]} を閉じるのは report の workPlanClosing）"
    in_group = [n for n, k in zip(entries[current], keys[current]) if k in finished] if isinstance(entries[current], list) else []
    if len([k for k in finished if k not in keys[current]]) != sum(len(e) for e in keys[:current]):
        return None, "--also の段は、今のまとまりの中のものだけ指せる"
    key_of = {
        name: key
        for entry, entry_keys in zip(entries, keys)
        for name, key in zip(entry if isinstance(entry, list) else [entry], entry_keys)
    }
    for name in skipped:
        if name not in key_of:
            return None, f"飛ばした段 {name} は並びに無い"
        if key_of[name] not in finished:
            return None, f"飛ばした段 {name} はまだ済んでいない"
    result: dict = {"phases": entries}
    if range_out is not None:
        result["delegatedRange"] = range_out
    result["current"] = current
    result["finishedInGroup"] = in_group
    if skipped:
        result["skippedPhases"] = list(dict.fromkeys(skipped))
    return result, None


def _overlap(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    """`left` のパスのうち、`right` のどれかと同じか、片方がもう片方のディレクトリの中にあるもの。"""

    def inside(p: str, q: str) -> bool:
        return p == q or p.startswith(q.rstrip("/") + "/") or q.startswith(p.rstrip("/") + "/")

    return tuple(p for p in left if any(inside(p, q) for q in right))


def plan_work_repo(body: str) -> tuple[str | None, str | None]:
    """`## やること` の `### 作業先` が名指すリポジトリの根。小見出しが無ければ `(None, None)`、形が違えば `(None, 理由)`。

    小見出しの下は、次の `### ` 見出しか節の終わりまで、空でない行がちょうど1つの「- `絶対パス`」。
    """
    lines = dict(_frame_sections(body)[1]).get(PLAN_HEADING, "").split("\n")
    starts = [i for i, line in enumerate(lines) if line.rstrip() == PLAN_WORK_REPO_HEADING]
    if not starts:
        return None, None
    if len(starts) != 1:
        return None, f"{PLAN_HEADING} に {PLAN_WORK_REPO_HEADING} の小見出しは1つまで（{len(starts)}個ある）"
    entries: list[str] = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("### "):
            break
        if line.strip():
            entries.append(line.rstrip())
    if len(entries) != 1:
        return None, f"{PLAN_WORK_REPO_HEADING} には「- `パス`」を1行だけ置く（{len(entries)}行ある）"
    m = _PLAN_FILE_LINE.match(entries[0])
    if m is None:
        return None, f"{PLAN_WORK_REPO_HEADING} の行が「- `パス`」の形でない: {entries[0].strip()}"
    path = m.group(1)
    if not path.startswith("/"):
        return None, f"{PLAN_WORK_REPO_HEADING} の {path} は絶対パスにする（`~` も展開して書く）"
    return path, None


def plan_files(body: str) -> tuple[tuple[str, ...], str | None]:
    """`## やること` の `### 名指すファイル` に並んだパス。形が違えば `((), 理由)`。

    小見出しの下は、次の `### ` 見出しか節の終わりまで、空でない行がすべて「- `パス`」
    （後ろに説明を続けてよい）。パスはリポジトリの根からの相対で、`/`・`~` で始まらず `..` を含まない。
    """
    lines = dict(_frame_sections(body)[1]).get(PLAN_HEADING, "").split("\n")
    starts = [i for i, line in enumerate(lines) if line.rstrip() == PLAN_FILES_HEADING]
    if len(starts) != 1:
        return (), f"{PLAN_HEADING} に {PLAN_FILES_HEADING} の小見出しを1つ置く（{len(starts)}個ある）"
    paths: list[str] = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("### "):
            break
        if not line.strip():
            continue
        m = _PLAN_FILE_LINE.match(line.rstrip())
        if m is None:
            return (), f"{PLAN_FILES_HEADING} の行が「- `パス`」の形でない: {line.strip()}"
        path = m.group(1)
        if path.startswith(("/", "~")) or ".." in path.split("/"):
            return (), f"{PLAN_FILES_HEADING} の {path} はリポジトリの根からの相対パスにする"
        paths.append(path)
    if not paths:
        return (), f"{PLAN_FILES_HEADING} にパスが1つも無い"
    return tuple(paths), None


PLAN_IDENTIFIER_MIN = 4
_PLAN_IDENTIFIER = re.compile(r"`([^`\n]+)`")


def plan_identifiers(body: str) -> tuple[str, ...]:
    """`## やること` のバッククォートで囲んだ識別子（出現順・重複なし）。

    `### 名指すファイル`・`### 作業先` の節は読まない。4 字（`PLAN_IDENTIFIER_MIN`）に満たない語・`/` や空白を含む語（パス・コマンド）・
    名指すパスと同じ語は識別子に数えない。
    """
    lines = dict(_frame_sections(body)[1]).get(PLAN_HEADING, "").split("\n")
    paths, _ = plan_files(body)
    skipped = False
    words: list[str] = []
    for line in lines:
        if line.startswith("### "):
            skipped = line.rstrip() in (PLAN_FILES_HEADING, PLAN_WORK_REPO_HEADING)
            continue
        if skipped:
            continue
        for word in _PLAN_IDENTIFIER.findall(line):
            plain = len(word) >= PLAN_IDENTIFIER_MIN and "/" not in word and not any(c.isspace() for c in word)
            if plain and word not in paths and word not in words:
                words.append(word)
    return tuple(words)


def direct_refusal(difficulty: str, body: str) -> str | None:
    """近道の印を置けない理由。基準に当たれば `None`。"""
    if difficulty != DIRECT_DIFFICULTY:
        return f"近道は difficulty が {DIRECT_DIFFICULTY} のときだけ（{difficulty}）"
    steps, _ = plan_steps(body)
    if not steps or len(steps) > DIRECT_MAX_STEPS:
        return f"近道は {PLAN_HEADING} の段が1〜{DIRECT_MAX_STEPS}つのときだけ（{len(steps)}つ）"
    paths, _ = plan_files(body)
    if not paths or len(paths) > DIRECT_MAX_PLAN_FILES:
        return f"近道は {PLAN_FILES_HEADING} が1〜{DIRECT_MAX_PLAN_FILES}つのときだけ（{len(paths)}つ）"
    if plan_work_repo(body) != (None, None):
        return f"近道は {PLAN_WORK_REPO_HEADING} の無いタスクだけ"
    return None


_PLAN_FILE_LINE = re.compile(r"^- `([^`]+)`.*$")


def validate_body(body: str, draft: bool = False) -> str | None:
    """本文が枠（`SECTION_HEADINGS` をこの順に1つずつ、後ろに任意の `## 介入`）に沿っているか。`## 結果` は枠の外で拒む。

    見出しは行頭の `## ` だけを数える（本文の説明文で節名に言及するのは許す）。
    `draft` なら節は枠の順のまま欠けてよく、中身が要るのは `## 目的・背景` だけ。
    """
    preamble, sections = _frame_sections(body)
    if body.startswith("---"):
        return "本文の先頭に front matter がある（front matter は除き、最初の `## ` 見出しから渡す）"
    if preamble.strip():
        return "本文の最初の見出しより前に文がある"
    headings = tuple(h for h, _ in sections)
    if RESULT_HEADING in headings:
        return f"{RESULT_HEADING} は tw done が書く（本文に入れない）"
    if headings[-1:] == (INTERVENTION_HEADING,):
        headings = headings[:-1]
    expected = tuple(h for h in SECTION_HEADINGS if h in headings) if draft else SECTION_HEADINGS
    if headings != expected:
        return "本文の見出しが枠と違う（この順に1つずつ置く）: " + "、".join(SECTION_HEADINGS)
    contents = dict(sections)
    filled = (PURPOSE_HEADING,) if draft else FILLED_SECTIONS
    blank = [h for h in filled if is_blank(contents.get(h, ""))]
    if blank:
        return f"空・「{EMPTY_MARK}」にできない節: " + "、".join(blank)
    return None


def _frame_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """`(見出しより前, [(見出しの行, 中身), ...])`。行末の空白は見出しから落とす。"""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in body.split("\n"):
        if line.startswith("## "):
            sections.append((line.rstrip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble), [(h, "\n".join(c).strip()) for h, c in sections]


def is_blank(content: str) -> bool:
    return content.strip() in ("", EMPTY_MARK)


def has_plan(body: str) -> bool:
    """`## やること` に中身があるか（空でも「なし」でもない）。"""
    return not is_blank(dict(_frame_sections(body)[1]).get(PLAN_HEADING, ""))


def draft_viewpoint(body: str) -> str | None:
    """`## 目的・背景` の最初の `- 観点:` の行の観点（先頭の色 `赤`・`黄` を落とす）。行が無い・空なら None。"""
    match = _viewpoint_match(body)
    return (match.group(1) or None) if match is not None else None


def draft_color(body: str) -> str | None:
    """`## 目的・背景` の最初の `- 観点:` の行の色（`赤`・`黄`）。行か色が無ければ None。"""
    match = _viewpoint_match(body)
    if match is None:
        return None
    rest = match.group(0)[len("- 観点:") :].strip()
    color = rest[:1]
    return color if color in VIEWPOINT_COLORS and (rest[1:2] == "" or rest[1:2].isspace()) else None


def _viewpoint_match(body: str) -> re.Match[str] | None:
    purpose = dict(_frame_sections(body)[1]).get(PURPOSE_HEADING, "")
    for line in purpose.split("\n"):
        m = _VIEWPOINT_LINE.match(line.strip())
        if m is not None:
            return m
    return None


_VIEWPOINT_LINE = re.compile(r"^- 観点:\s*(?:[赤黄](?:\s+|$))?(.*?)\s*$")


def plan_changed(old_body: str, new_body: str) -> bool:
    """`## やること` の中身が `old_body` と違うか。行末の空白と連続する空行の数は無視する。"""
    return section_changed(old_body, new_body, PLAN_HEADING)


_BACKTICKED_PATH = re.compile(r"`([^`\s()]+)`")


def report_named_paths(body: str) -> tuple[str, ...]:
    """`## やること`・`## 完了条件` のバッククォートで囲んだパスのうち、basename が `report` で始まるもの。

    パスは basename に `.` を含む（拡張子つきのファイル名）トークンだけで、末尾が `/` のディレクトリ・裸の語・識別子・テスト ID
    （`report`・`reportBody`・`report-chart`）は数えない。`/` だけを条件にすると `report.ts` が外れる。
    委譲先（サブエージェント）はそういう名のファイルを Write できない。順に重複なく返す（`### 名指すファイル` の行も含む）。
    """
    sections = dict(_frame_sections(body)[1])
    found: dict[str, None] = {}
    for heading in (PLAN_HEADING, ACCEPTANCE_HEADING):
        for path in _BACKTICKED_PATH.findall(sections.get(heading, "")):
            name = path.rsplit("/", 1)[-1]
            if name.startswith("report") and "." in name:
                found[path] = None
    return tuple(found)


def changed_frame_sections(old_body: str, new_body: str) -> list[str]:
    """`## 目的・背景`・`## 完了条件` のうち、中身が `old_body` と違う節の見出し。

    行末の空白と連続する空行の数は無視する。`old_body` に節が無いか、中身が空か「なし」のときは比べない。
    """
    old = dict(_frame_sections(old_body)[1])
    new = dict(_frame_sections(new_body)[1])
    return [
        h for h in FILLED_SECTIONS if h in old and not is_blank(old[h]) and _squeeze(old[h]) != _squeeze(new.get(h, ""))
    ]


def _squeeze(content: str) -> str:
    lines = [line.rstrip() for line in content.split("\n")]
    kept = [line for i, line in enumerate(lines) if line or (i > 0 and lines[i - 1])]
    return "\n".join(kept).strip()


def section_heading(name: str) -> str | None:
    """`やること`・`## やること` を枠の見出しの行にそろえる。枠に無い名前は `None`。"""
    heading = name.strip()
    heading = heading if heading.startswith("## ") else f"## {heading}"
    if heading in (*SECTION_HEADINGS, INTERVENTION_HEADING):
        return heading
    for candidate in SECTION_HEADINGS:
        if "（" in candidate:
            base = candidate.split("（")[0]
            if heading == base:
                return candidate
    return None


def check_section_content(content: str) -> str | None:
    """節の中身に行頭の `## ` の行があれば、その理由を返す。"""
    if any(line.startswith("## ") for line in content.split("\n")):
        return "節の中身に `## ` で始まる行がある（節の境目は渡せない）"
    return None


def replace_section(body: str, heading: str, content: str) -> str | None:
    """`heading` の節の中身だけを `content` に置き換える。ほかの行はそのまま。節が無ければ `None`。"""
    lines = body.split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("## ") and l.rstrip() == heading), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    text = content.strip("\n")
    middle = ["", *text.split("\n"), ""] if text else [""]
    return "\n".join(lines[: start + 1] + middle + lines[end:])


def put_section(body: str, heading: str, content: str) -> str | None:
    """`replace_section` に加え、`## 介入` が無ければ本文の後ろ（`## 結果` があればその前）に足す。ほかの節が無ければ `None`。"""
    replaced = replace_section(body, heading, content)
    if replaced is not None or heading != INTERVENTION_HEADING:
        return replaced
    lines = body.rstrip("\n").split("\n")
    end = next((i for i, l in enumerate(lines) if l.rstrip() == RESULT_HEADING), len(lines))
    block = [heading, "", *content.strip("\n").split("\n"), ""]
    return "\n".join([*lines[:end], "", *block, *lines[end:]]).rstrip("\n") + "\n"


def id_number(task_id: str) -> int:
    """`T-521` → `521`。呼ぶ側で `layout.ID_PATTERN` に通した値だけを渡す。"""
    return int(task_id[len("T-") :])


def format_id(number: int) -> str:
    """3桁未満はゼロ埋め、3桁以上はそのまま（3.1: ID は `T-` + 3桁以上の数字）。"""
    return f"T-{number:03d}" if number < 1000 else f"T-{number}"
