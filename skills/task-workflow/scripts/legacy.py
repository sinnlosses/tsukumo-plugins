"""旧形式（`develop/tasks.json` + `develop/progress.md`）の読み取りと `task migrate`。

正典は `docs/task-workflow-redesign.md` の5.9・10.1・10.2。`tw_maint.py` の `cmd_migrate` から
`migrate()` を呼ぶ（`ship.py` が `attempt()` を返し `tw_ship.py` が印字するのと同じ形。ここで
ファイル・git の実際の書き換えまで行い、`tw_maint.py` 側は結果を印字するだけにする）。

タスクの変換（10.1）は機械的な写しで、**登録時の本文の節の検査（`taskfile.validate_new_body`）
は受けない**（移行したファイルの本文はそのまま。正典3.3「移行したファイルは本文の節の検査を
受けない」）。`progress.md` の扱い（10.2）は「完了したこと」の小節を全部
`docs/history/progress.md` へ移し、「未解決」「注意」の2節と、それより前にある前置き文
（`# 現在の状態` のような、どの節にも属さない文章）は**消さずに残す**（前置き文はデータを
失う操作になるので移さない・捨てない。3つとも「人が振り分けたら消す」側に置く）。
3つとも中身が無ければ `develop/progress.md` 自体を消す。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

import ledger
import layout
import taskfile

HISTORY_DIR = "docs/history"
PROGRESS_ARCHIVE_HEADER = "# 過去セッションの「完了したこと」"
# 「## 完了したこと（このセッション）」のように後ろに補足が付いた表記が実在するので前方一致で拾う。
DONE_SECTION = "## 完了したこと"
DATE_HEADING = re.compile(r"^### (\d{4}-\d{2}-\d{2})\b")
UNRESOLVED_HEADING = "## 未解決"
NOTE_HEADING = "## 注意"
LEFTOVER_NOTICE = "移行の残り。8章の表で振り分けたら消す。\n"

RESULT_HEADING_LINE = re.compile(r"^## 結果\s*$", re.MULTILINE)


def _run_git(cwd: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


# --- 旧形式の読み取り（tasks.json・progress.md の節分け） ---------------------


def load_tasks(path: str) -> tuple[list[dict], str | None]:
    """tasks.json を読む。読めなければ `(空リスト, 理由)` を返す（例外を投げない）。

    データの不備で traceback を出すと、呼ぶ側が環境の故障（終了コード1）と取り違える。
    理由を文字列で返して `INVALID` として出せるようにする。
    """
    try:
        with open(path, encoding="utf-8") as f:
            tasks = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return [], f"JSONとして読めない（{e}）"
    except OSError as e:
        return [], f"読めない（{e}）"
    if not isinstance(tasks, list):
        return [], f"配列ではない（{type(tasks).__name__}）"
    bad = [i for i, t in enumerate(tasks) if not isinstance(t, dict)]
    if bad:
        return [], f"配列の要素がオブジェクトではない（{len(bad)}件: index {bad[:5]}）"
    return tasks, None


@dataclass(frozen=True)
class Section:
    """「完了したこと」配下の `### 〜` 小節1つ。"""

    date: str | None
    text: str


def split_done_section(text: str) -> tuple[str, list[Section] | None, str]:
    """「完了したこと」節を (前, 小節リスト, 後) に割る。節が無ければ小節リストは None。"""
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.startswith(DONE_SECTION)), None)
    if start is None:
        return text, None, ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))

    bodies: list[tuple[str | None, list[str]]] = []
    head_end = end
    for i in range(start + 1, end):
        if lines[i].startswith("### "):
            head_end = min(head_end, i)
            m = DATE_HEADING.match(lines[i])
            bodies.append((m.group(1) if m else None, [lines[i]]))
        elif bodies:
            bodies[-1][1].append(lines[i])

    sections = [Section(d, "".join(b)) for d, b in bodies]
    return "".join(lines[:head_end]), sections, "".join(lines[end:])


def split_named_section(text: str, heading: str) -> tuple[str, str, str]:
    """`## <heading>` の節を (前, その節, 後) に割る。無ければ真ん中が空文字。"""
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.startswith(heading)), None)
    if start is None:
        return text, "", ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "".join(lines[:start]), "".join(lines[start:end]), "".join(lines[end:])


def prepend_to_history(path: str, header: str, body: str) -> None:
    """履歴ファイルの見出しの直後（既存のエントリより前）に差し込む。新しいものが上に来る並びを保つ。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + "\n")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)
    cut = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), -1) + 1
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines[:cut]).rstrip() + "\n\n" + body.rstrip() + "\n\n")
        f.write("".join(lines[cut:]).lstrip())


# --- タスクの変換（10.1） ----------------------------------------------------


def convert_task(raw: dict) -> tuple[taskfile.Task | None, str | None]:
    """旧 `tasks.json` の1要素を、新しいタスクファイルの `Task` に変換する（10.1の表）。"""
    tid = raw.get("id")
    if not isinstance(tid, str) or not taskfile.ID_PATTERN.match(tid):
        return None, f"idが無い、またはT-999の形式でない（{tid!r}）"

    status_raw = raw.get("status")
    if status_raw == "todo":
        status = "todo"
    elif status_raw == "done":
        status = "done" if raw.get("passes") else "dropped"
    else:
        # `doing` は呼び出し側（`migrate`）が先に検査して止める。それ以外の値は未知として拒む。
        return None, f"statusが未知（{status_raw!r}）"

    body = (raw.get("task") or "").strip("\n")
    body = f"{body}\n" if body else ""

    summary_raw = raw.get("summary")
    if isinstance(summary_raw, str) and summary_raw.strip():
        summary = summary_raw.replace("\n", " ").strip()
    else:
        summary = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if not summary:
        return None, "summaryが無く、taskの先頭行も取れない"

    difficulty = raw.get("difficulty")
    if difficulty not in taskfile.DIFFICULTY_VALUES:
        return None, f"difficultyが未知（{difficulty!r}）"

    loopable = raw.get("loopable") or "Y"
    if loopable not in taskfile.LOOPABLE_VALUES:
        return None, f"loopableが未知（{loopable!r}）"

    deps_raw = raw.get("dependencies") or []
    if not isinstance(deps_raw, list) or any(
        not isinstance(d, str) or not taskfile.ID_PATTERN.match(d) for d in deps_raw
    ):
        return None, "dependenciesがT-999の形式の配列でない"
    dependencies = tuple(deps_raw)

    evidence = (raw.get("evidence") or "").strip()
    if evidence:
        if RESULT_HEADING_LINE.search(body):
            return None, "本文に既に'## 結果'がある"
        body = taskfile.set_result_section(body, evidence)

    return taskfile.Task(tid, summary, status, difficulty, loopable, dependencies, body), None


# --- progress.md の振り分け（10.2） ------------------------------------------


def _count_bullets(section_text: str) -> int:
    lines = section_text.splitlines()
    return sum(1 for line in lines[1:] if line.strip().startswith("- "))


# 前置き文の終わり＝この3つの見出しのうち、ファイル中で最初に現れるものの手前まで。
_KNOWN_HEADING_PREFIXES = (DONE_SECTION, UNRESOLVED_HEADING, NOTE_HEADING)


def _preamble(text: str) -> str:
    """`text` のうち、既知の見出し（完了したこと・未解決・注意）のどれよりも前にある文章。

    **移さない・捨てない**（データを失う操作にしないため）。`split_done_section` の
    `head` を使わないのは、それが「完了したこと」の見出し行自体まで含んでしまうため
    （見出しが無いときは全文を返す実装で、`## 未解決`/`## 注意` と重複しうる）。
    """
    lines = text.splitlines(keepends=True)
    idx = next(
        (i for i, line in enumerate(lines) if any(line.startswith(p) for p in _KNOWN_HEADING_PREFIXES)),
        len(lines),
    )
    return "".join(lines[:idx])


def progress_plan(text: str) -> tuple[int, str, str | None, int, int, bool]:
    """`(移す小節数, 移す本文, 残すprogress.mdの本文（Noneなら消す）, 未解決件数, 注意件数, 前置き文の有無)`。

    「完了したこと」の小節は全部移す（残す小節は無く、
    新しい順の検査もしない——全部移すので順は関係しない）。「未解決」「注意」の2節と
    前置き文（`_preamble`）は動かさず、先頭の1行つきで残す。3つとも空（前置き文が
    空白だけ、箇条書きが無い）なら残す本文は無し（呼び出し側が `develop/progress.md`
    自体を消す）。
    """
    preamble = _preamble(text)
    has_preamble = preamble.strip() != ""

    _, sections, _ = split_done_section(text)
    sections = sections or []
    moved_text = "".join(s.text for s in sections)

    _, unresolved_section, _ = split_named_section(text, UNRESOLVED_HEADING)
    _, note_section, _ = split_named_section(text, NOTE_HEADING)
    unresolved_count = _count_bullets(unresolved_section)
    note_count = _count_bullets(note_section)

    if unresolved_count == 0 and note_count == 0 and not has_preamble:
        leftover: str | None = None
    else:
        parts = [LEFTOVER_NOTICE, "\n"]
        if has_preamble:
            parts.append(preamble.rstrip("\n"))
            parts.append("\n\n")
        parts.append(unresolved_section)
        parts.append(note_section)
        leftover = "".join(parts)

    return len(sections), moved_text, leftover, unresolved_count, note_count, has_preamble


# --- migrate（5.9） ----------------------------------------------------------


@dataclass(frozen=True)
class MigrateResult:
    kind: str  # "OK" | "DIRTY" | "INVALID" | "NOT_READY" | "NOTHING"
    detail: str = ""
    written: tuple[str, ...] = ()
    moved_sections: int = 0
    leftover_counts: tuple[int, int] | None = None  # (未解決, 注意)。Noneならprogress.md自体を消した/触っていない
    preamble_kept: bool = False  # 前置き文（どの節にも属さない文章）を残したか
    progress_removed: bool = False
    task_count: int = 0


def migrate(toplevel: str, dry_run: bool) -> MigrateResult:
    """`task migrate` の本体。`dry_run` なら判定だけ行い、何も書かない。"""
    if not ledger.is_clean(cwd=toplevel):
        return MigrateResult(kind="DIRTY")

    tasks_json_path = os.path.join(toplevel, "develop", "tasks.json")
    if not os.path.exists(tasks_json_path):
        direction_path = os.path.join(toplevel, layout.direction_path(toplevel))
        detail = "develop/tasks.json が無い" + ("（既に新形式）" if os.path.exists(direction_path) else "")
        return MigrateResult(kind="NOTHING", detail=detail)

    raw_tasks, err = load_tasks(tasks_json_path)
    if err is not None:
        return MigrateResult(kind="INVALID", detail=f"develop/tasks.json\t{err}")

    doing = next((t for t in raw_tasks if t.get("status") == "doing"), None)
    if doing is not None:
        return MigrateResult(kind="NOT_READY", detail=f"{doing.get('id', '?')}\tdoing")

    converted: list[taskfile.Task] = []
    for raw in raw_tasks:
        task, conv_err = convert_task(raw)
        if conv_err is not None:
            return MigrateResult(kind="INVALID", detail=f"{raw.get('id', '?')}\t{conv_err}")
        converted.append(task)  # type: ignore[arg-type]

    task_rel = layout.task_dir(toplevel)
    written = tuple(f"{task_rel}/{t.id}.md" for t in converted)

    progress_path = os.path.join(toplevel, "develop", "progress.md")
    moved_sections = 0
    moved_text = ""
    leftover_text: str | None = None
    unresolved = note = 0
    has_preamble = False
    if os.path.exists(progress_path):
        with open(progress_path, encoding="utf-8") as f:
            progress_text = f.read()
        moved_sections, moved_text, leftover_text, unresolved, note, has_preamble = progress_plan(progress_text)

    leftover_counts = (unresolved, note) if leftover_text is not None else None
    preamble_kept = leftover_text is not None and has_preamble
    progress_removed = os.path.exists(progress_path) and leftover_text is None

    if dry_run:
        return MigrateResult(
            kind="OK",
            written=written,
            moved_sections=moved_sections,
            leftover_counts=leftover_counts,
            preamble_kept=preamble_kept,
            progress_removed=progress_removed,
            task_count=len(converted),
        )

    task_dir = os.path.join(toplevel, task_rel)
    os.makedirs(task_dir, exist_ok=True)
    for t in converted:
        with open(taskfile.task_path(task_dir, t.id), "w", encoding="utf-8") as f:
            f.write(taskfile.render(t))
    _run_git(toplevel, ["add", task_rel])

    if moved_sections > 0:
        archive_path = os.path.join(toplevel, HISTORY_DIR, "progress.md")
        prepend_to_history(archive_path, PROGRESS_ARCHIVE_HEADER, moved_text)
        _run_git(toplevel, ["add", os.path.join(HISTORY_DIR, "progress.md")])

    if os.path.exists(progress_path):
        if leftover_text is not None:
            with open(progress_path, "w", encoding="utf-8") as f:
                f.write(leftover_text)
            _run_git(toplevel, ["add", "develop/progress.md"])
        else:
            _run_git(toplevel, ["rm", "-q", "develop/progress.md"])

    _run_git(toplevel, ["rm", "-q", "develop/tasks.json"])

    root = ledger.ledger_root_for_write(cwd=toplevel)
    history_ids = taskfile.history_ids(os.path.join(toplevel, layout.HISTORY_TASKS_PATH))
    max_id = max(
        [0] + [taskfile.id_number(t.id) for t in converted] + [taskfile.id_number(h) for h in history_ids]
    )
    ledger.write_last_id(root, max_id)

    return MigrateResult(
        kind="OK",
        written=written,
        moved_sections=moved_sections,
        leftover_counts=leftover_counts,
        preamble_kept=preamble_kept,
        progress_removed=progress_removed,
        task_count=len(converted),
    )
