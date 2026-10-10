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

STATUS_VALUES = ("todo", "hold", "done", "dropped")
DIFFICULTY_VALUES = ("haiku", "sonnet", "opus")
LOOPABLE_VALUES = ("Y", "N")

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
SECTION_HEADINGS = (
    PURPOSE_HEADING,
    "## 決まっていること（蒸し返さない）",
    "## 解くべき論点",
    PLAN_HEADING,
    ACCEPTANCE_HEADING,
    CAUTION_HEADING,
    "## 参考情報",
)
# 空・「なし」にできない欄。
FILLED_SECTIONS = (PURPOSE_HEADING, ACCEPTANCE_HEADING)
EMPTY_MARK = "なし"

RESULT_HEADING = "## 結果"  # done/dropped で必須（3.3）。常に本文の最後に置く。


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


def validate_new_body(body: str, hold: bool) -> str | None:
    """`task new`・`task adopt` の本文検査。枠の検査に加え、`## やること` の中身・段・`### 名指すファイル` を
    求める（`### 作業先` があればその形も）。中身を空にできるのは `hold` のときだけ。問題が無ければ `None`。"""
    error = validate_body(body)
    if error is not None:
        return error
    if not has_plan(body):
        return None if hold else f"{PLAN_HEADING} に計画を書く（空にできるのは --hold だけ）"
    return plan_steps(body)[1] or plan_files(body)[1] or plan_work_repo(body)[1]


def validate_edited_body(old_body: str, new_body: str) -> str | None:
    """`task edit` の本文検査。枠の検査に加え、`## やること` を変えて中身があるなら段の形を求める。"""
    error = validate_body(new_body)
    if error is not None or not has_plan(new_body) or not plan_changed(old_body, new_body):
        return error
    return plan_steps(new_body)[1]


@dataclass(frozen=True)
class PlanStep:
    """`## やること` の段1つ。`after` は前の段の番号（欄が無ければ直前の段）、`files` は触るファイル（欄が無ければ空）。"""

    name: str
    after: tuple[int, ...]
    files: tuple[str, ...]


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
        if error is not None:
            return (), error
        specs.append(PlanStep(name, after, files))
    for a, b in _unordered_pairs(_joined_after(specs)):
        bare = [n for n in (a, b) if not specs[n - 1].files]
        if bare:
            return (), f"段 {a} と段 {b} は並列になるので、段 {bare[0]} に `- {PLAN_TOUCHED_FIELD}:` を書く"
    return tuple(specs), None


PLAN_AFTER_FIELD = "前の段"
PLAN_TOUCHED_FIELD = "触るファイル"
_PLAN_STEP_LINE = re.compile(r"^### (\d+)\. (\S.*)$")
_PLAN_FIELD_LINE = re.compile(rf"^- ({PLAN_AFTER_FIELD}|{PLAN_TOUCHED_FIELD}):\s*(.*)$")


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
    if raw is None:
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


def validate_body(body: str) -> str | None:
    """本文が枠（`SECTION_HEADINGS` をこの順に1つずつ）に沿っているか。`## 結果` は枠の外で拒む。

    見出しは行頭の `## ` だけを数える（本文の説明文で節名に言及するのは許す）。
    """
    preamble, sections = _frame_sections(body)
    if body.startswith("---"):
        return "本文の先頭に front matter がある（front matter は除き、最初の `## ` 見出しから渡す）"
    if preamble.strip():
        return "本文の最初の見出しより前に文がある"
    headings = tuple(h for h, _ in sections)
    if RESULT_HEADING in headings:
        return f"{RESULT_HEADING} は tw done が書く（本文に入れない）"
    if headings != SECTION_HEADINGS:
        return "本文の見出しが枠と違う（この順に1つずつ置く）: " + "、".join(SECTION_HEADINGS)
    contents = dict(sections)
    blank = [h for h in FILLED_SECTIONS if is_blank(contents[h])]
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


def plan_changed(old_body: str, new_body: str) -> bool:
    """`## やること` の中身が `old_body` と違うか。行末の空白と連続する空行の数は無視する。"""
    old = dict(_frame_sections(old_body)[1]).get(PLAN_HEADING, "")
    new = dict(_frame_sections(new_body)[1]).get(PLAN_HEADING, "")
    return _squeeze(old) != _squeeze(new)


def changed_frame_sections(old_body: str, new_body: str) -> list[str]:
    """`## 目的・背景`・`## 完了条件` のうち、中身が `old_body` と違う節の見出し。

    行末の空白と連続する空行の数は無視する。`old_body` に節が無いときは比べない。
    """
    old = dict(_frame_sections(old_body)[1])
    new = dict(_frame_sections(new_body)[1])
    return [h for h in FILLED_SECTIONS if h in old and _squeeze(old[h]) != _squeeze(new.get(h, ""))]


def _squeeze(content: str) -> str:
    lines = [line.rstrip() for line in content.split("\n")]
    kept = [line for i, line in enumerate(lines) if line or (i > 0 and lines[i - 1])]
    return "\n".join(kept).strip()


def set_result_section(body: str, content: str) -> str:
    """本文の `## 結果` 節を `content` に置き換える（無ければ末尾に足す。3.3・3.4・5.7）。

    節の並びは固定で `## 結果` は常に最後（3.3）なので、既存の節があれば丸ごと外し、
    改めて末尾に置き直す（順の入れ替えは起きない）。
    """
    content = content.strip("\n")
    body = strip_result_section(body).rstrip("\n")
    prefix = f"{body}\n\n" if body else ""
    return f"{prefix}{RESULT_HEADING}\n\n{content}\n"


def strip_result_section(body: str) -> str:
    """本文から `## 結果` 節を丸ごと外す（無ければそのまま）。"""
    lines = body.split("\n")
    start = next((i for i, l in enumerate(lines) if l == RESULT_HEADING), None)
    if start is None:
        return body
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[:start] + lines[end:])


def section_heading(name: str) -> str | None:
    """`やること`・`## やること` を枠の見出しの行にそろえる。枠に無い名前は `None`。"""
    heading = name.strip()
    heading = heading if heading.startswith("## ") else f"## {heading}"
    if heading in SECTION_HEADINGS:
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


def id_number(task_id: str) -> int:
    """`T-521` → `521`。呼ぶ側で `layout.ID_PATTERN` に通した値だけを渡す。"""
    return int(task_id[len("T-") :])


def format_id(number: int) -> str:
    """3桁未満はゼロ埋め、3桁以上はそのまま（3.1: ID は `T-` + 3桁以上の数字）。"""
    return f"T-{number:03d}" if number < 1000 else f"T-{number}"
