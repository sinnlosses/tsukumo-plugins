"""Beads（`bd`）の包み。Beads 方式のタスクの読み書きを1箇所に閉じ込める。

正典は WORKFLOW.md「Beads 方式」。`bd` は Beads の CLI（1.3.0 で確かめた）で、`.beads` は
主ブランチを出している作業ツリーの根にあり、どの作業ツリーから打っても同じデータベースを読む。

タスクID の対応: Beads の中の ID の接頭辞は小文字だけなので、外に見せる ID は大文字にする
（コミットの件名・`retrospect` の割り付けと同じ形）。Beads の `issue_prefix` が `gh` なら GitHub の
Issue 番号（`gh-5` ↔ `GH-5`）、`t` なら `task` の採番（`t-123` ↔ `T-123`）で、読むときは両方を受ける
（切り替えの途中は混ざる）。Jira のキーも同じく `proj-123` ↔ `PROJ-123`。`gh` の登録の途中の仮の ID（`gh-new-<作業ツリー>-<時刻>`）と、数字でない ID
（トラッカーから取り込んだ `gh-1790…-1-4dfc` など）はそのまま見せる。

`Task` への写し方（WORKFLOW.md「Beads 方式」の対応表）:

| Beads | `Task` |
| --- | --- |
| `open` | `todo` |
| `deferred`（切り替え前の独自の状態 `pending` も） | `hold` |
| `in_progress` | `todo` ＋ 着手の印（assignee が作業ツリー名） |
| `closed` | `done`（label `cancelled` があれば `dropped`） |
| label `difficulty:<値>`・`loopable:<Y/N>` | `difficulty`・`loopable` |
| label `direct:Y`（無ければ印なし） | `direct` |
| `blocks` の依存 | `dependencies` |
| `title` | `summary` |

読み手は例外を投げない（`taskfile.parse` と同じ `(値, 理由)` の形）。`bd` そのものが落ちたときだけ
`BeadsError`（環境の故障。呼ぶ側は traceback にせず終了コード1にする）。
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

import layout
import taskfile

BD = "bd"
# `issue_prefix` の値。`gh` は GitHub の Issue 番号、`t` は `task` の採番。
PREFIX_GITHUB = "gh"
PREFIX_LOCAL = "t"
BD_ID_PATTERN = re.compile(r"^(?:t-(\d{3,})|gh-(\d+)|([a-z][a-z0-9_]+)-(\d+))$")
PROVISIONAL_PREFIX = "gh-new-"
HOLD_STATUS = "deferred"
# 切り替え前の hold（独自の状態 `pending:frozen`）。読むだけで、書くのは `deferred`。
LEGACY_HOLD_STATUS = "pending"
HOLD_STATUSES = (HOLD_STATUS, LEGACY_HOLD_STATUS)
CANCELLED_LABEL = "cancelled"
DIFFICULTY_LABEL = "difficulty:"
LOOPABLE_LABEL = "loopable:"
DIRECT_LABEL = "direct:"
DIRECT_ON = f"{DIRECT_LABEL}Y"
# `task done` が立て、`task ship` が主ブランチへ送ったあとに閉じる印（done と dropped の2つ）。
SHIP_LABELS = {"done": "ship:done", "dropped": "ship:dropped"}
# Jira の方式で、ローカルで閉じたが Jira で閉じてもらうのを待っている印。
JIRA_CLOSE_LABEL = "jira:close"
LAST_ID_KEY = "task-workflow.last-id"
CLAIM_BRANCH_KEY = "task_branch"
# claim した時点の HEAD の SHA（`task done` が委譲先のコミットを知らせるのに使う）。
CLAIM_HEAD_KEY = "task_claim_head"
# `## やること` を初めて書いた時点の判定（`first`・`after-work`）か、登録時の計画のまま進めてよい（`registered`）。
PLAN_KEY = "task_plan"
# 登録時（`tw new`・`tw adopt`）に `## やること` を書いたときの主ブランチの SHA。
PLAN_BASE_KEY = "task_plan_base"
# 登録時の計画を判定した、着手時の主ブランチの先端。
PLAN_TIP_KEY = "task_plan_tip"
# この運用が metadata に置く印（GitHub には載らないので、取り込みで消えたら戻す）。
MARK_KEYS = (CLAIM_BRANCH_KEY, CLAIM_HEAD_KEY, PLAN_KEY, PLAN_BASE_KEY, PLAN_TIP_KEY)
RESULT_HEADING = taskfile.RESULT_HEADING

# 本文の節のうち、`description` 以外へ入るもの。
ACCEPTANCE_HEADING = taskfile.ACCEPTANCE_HEADING
NOTES_HEADING = taskfile.PLAN_HEADING


class BeadsError(RuntimeError):
    """`bd` を呼んで思わぬ失敗をした（環境の故障）。"""


@dataclass(frozen=True)
class Issue:
    """`bd list --json` の1件を、この運用が読む欄だけに絞ったもの。"""

    bd_id: str
    title: str
    status: str
    labels: tuple[str, ...]
    dependencies: tuple[str, ...]  # 依存先の Beads ID（`blocks` だけ）
    assignee: str | None
    started_at: str | None
    external_ref: str | None
    raw: dict


# --- ID -------------------------------------------------------------------------


def to_bd_id(task_id: str) -> str:
    """`T-123`／`GH-5` → `t-123`／`gh-5`。ほかの ID は小文字にするだけ。"""
    return task_id.lower()


def to_task_id(bd_id: str) -> str:
    """`t-123`／`gh-5` → `T-123`／`GH-5`。番号でない ID はそのまま。"""
    return bd_id.upper() if BD_ID_PATTERN.match(bd_id) else bd_id


def is_numbered(bd_id: str) -> bool:
    return BD_ID_PATTERN.match(bd_id) is not None


def is_provisional(bd_id: str) -> bool:
    """`task new` が Issue の番号を得る前に使う仮の ID か。"""
    return bd_id.startswith(PROVISIONAL_PREFIX)


def sort_key(task_id: str) -> tuple[int, int, str]:
    """`status` の並び。`T-<n>` → `GH-<n>` → Jira のキー（キー名の順）→ そのほか（仮の ID）。"""
    m = BD_ID_PATTERN.match(task_id.lower())
    if m is None:
        return (3, 0, task_id)
    if m.group(1):
        return (0, int(m.group(1)), "")
    if m.group(2):
        return (1, int(m.group(2)), "")
    return (2, 0, f"{m.group(3)}-{int(m.group(4)):012d}")


def read_prefix(toplevel: str) -> str:
    """Beads の `issue_prefix`（`gh` か `t`）。ほかの値や未設定は `t` として扱う。"""
    value = run(toplevel, ["config", "get", "issue_prefix"]).stdout.strip()
    return PREFIX_GITHUB if value == PREFIX_GITHUB else PREFIX_LOCAL


# --- bd の呼び出し ----------------------------------------------------------------


def run(toplevel: str, args: list[str], actor: str | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    """`bd` を打つ。非0は呼ぶ側が意味を決める（取り合いの負けなど）。`bd` が無ければ `BeadsError`。"""
    cmd = [BD]
    if actor is not None:
        cmd += ["--actor", actor]
    try:
        return subprocess.run(cmd + args, cwd=toplevel, capture_output=True, text=True, input=stdin)
    except FileNotFoundError as e:
        raise BeadsError(f"bd が見つからない（{e}）") from e


def run_ok(toplevel: str, args: list[str], actor: str | None = None, stdin: str | None = None) -> str:
    r = run(toplevel, args, actor, stdin)
    if r.returncode != 0:
        raise BeadsError(f"bd {' '.join(args)} が失敗（{r.returncode}）: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def run_json(toplevel: str, args: list[str]):
    out = run_ok(toplevel, args + ["--json"])
    try:
        return json.loads(out) if out.strip() else []
    except ValueError as e:
        raise BeadsError(f"bd {' '.join(args)} の JSON が読めない: {e}") from e


def beads_dir(toplevel: str) -> str:
    """`.beads` の置き場（主ブランチを出している作業ツリーの根＝共有の `.git` の親）。"""
    r = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=toplevel,
        capture_output=True,
        text=True,
    )
    common = r.stdout.strip() if r.returncode == 0 else os.path.join(toplevel, ".git")
    return os.path.join(os.path.dirname(common), ".beads")


def is_initialized(toplevel: str) -> bool:
    return os.path.isdir(beads_dir(toplevel))


# --- 読む -------------------------------------------------------------------------


def list_issues(toplevel: str) -> list[Issue]:
    data = run_json(toplevel, ["list", "--all", "-n", "0", "--flat"])
    return [_issue(d) for d in data if isinstance(d, dict) and "id" in d]


def show(toplevel: str, bd_id: str) -> Issue | None:
    r = run(toplevel, ["show", bd_id, "--json"])
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except ValueError as e:
        raise BeadsError(f"bd show {bd_id} の JSON が読めない: {e}") from e
    items = data if isinstance(data, list) else [data]
    return _issue(items[0]) if items and isinstance(items[0], dict) else None


def comments(toplevel: str, bd_id: str) -> list[dict]:
    data = run_json(toplevel, ["comments", bd_id])
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def history(toplevel: str, bd_id: str) -> list[dict]:
    """版の一覧を古い順で返す（`bd history --json` は新しい順）。"""
    data = run_json(toplevel, ["history", bd_id])
    snaps = [h.get("Issue") for h in data if isinstance(h, dict) and isinstance(h.get("Issue"), dict)]
    return list(reversed(snaps))


def overwritten_by(toplevel: str, bd_id: str, actor: str, since: datetime) -> Issue | None:
    """`actor` が `since` 以後（秒の単位）に書いた最後の更新の、書く直前の版。無ければ（作った課題も）`None`。

    `bd history` の版には metadata が載らないので、監査の出来事（`--events`。新しい順で、`old_value` に
    書く前の課題が metadata ごと入る）から読む。出来事の種類は書いた中身で変わる（status も変えれば
    `status_changed`）ので見ない。時刻か `old_value` が読めなければ `BeadsError`。
    """
    data = run_json(toplevel, ["history", bd_id, "--events"])
    for event in data if isinstance(data, list) else []:
        if not isinstance(event, dict) or event.get("actor") != actor:
            continue
        at = parse_time(event.get("created_at"))
        if at is None:
            raise BeadsError(f"bd history {bd_id} --events の時刻が読めない: {event.get('created_at')!r}")
        if at < since or not event.get("old_value"):
            return None
        try:
            before = json.loads(str(event["old_value"]))
        except ValueError as e:
            raise BeadsError(f"bd history {bd_id} --events の old_value が読めない: {e}") from e
        if not isinstance(before, dict) or "id" not in before:
            raise BeadsError(f"bd history {bd_id} --events の old_value が課題の形でない")
        return _issue(before)
    return None


def parse_time(value) -> datetime | None:
    """`bd` の時刻（`2026-10-05T08:12:39Z`・`2026-10-05T17:02:14.78+09:00` など。小数は0〜9桁）を読む。
    Python 3.9 の `fromisoformat` は小数が3桁か6桁でないと読めないので、6桁にそろえる。"""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)?", str(value or "").strip())
    if m is None:
        return None
    fraction = (m.group(2) or "").ljust(6, "0")[:6]
    zone = "+00:00" if m.group(3) in (None, "Z") else m.group(3)
    try:
        return datetime.fromisoformat(f"{m.group(1)}.{fraction}{zone}")
    except ValueError:
        return None


def _issue(d: dict) -> Issue:
    deps = tuple(
        str(x.get("depends_on_id") or x.get("id"))
        for x in d.get("dependencies") or []
        if isinstance(x, dict) and (x.get("type") or x.get("dependency_type") or "blocks") == "blocks"
    )
    return Issue(
        bd_id=str(d["id"]),
        title=str(d.get("title") or ""),
        status=str(d.get("status") or ""),
        labels=tuple(str(l) for l in d.get("labels") or []),
        dependencies=deps,
        assignee=d.get("assignee") or None,
        started_at=d.get("started_at") or None,
        external_ref=d.get("external_ref") or None,
        raw=d,
    )


def last_result(comment_list: list[dict]) -> str | None:
    """`## 結果` で始まる comment のうち最後のものの本文（見出しを除く）。"""
    found = None
    for c in comment_list:
        text = str(c.get("text") or "")
        if text.startswith(RESULT_HEADING):
            found = text[len(RESULT_HEADING) :].strip("\n")
    return found


# --- Task への写し ----------------------------------------------------------------


def to_task(issue: Issue) -> tuple[taskfile.Task | None, str | None]:
    """`(Task, None)`・`(None, INVALID の理由)`・`(None, None)`（振り分け前＝`adopt` 待ち）。"""
    if issue.status == "closed":
        status = "dropped" if CANCELLED_LABEL in issue.labels else "done"
    elif issue.status in ("open", "in_progress"):
        status = "todo"
    elif issue.status in HOLD_STATUSES:
        status = "hold"
    else:
        return None, f"Beads の状態 {issue.status!r} はこの運用に無い（open・{HOLD_STATUS}・in_progress・closed だけ）"

    difficulty = label_values(issue.labels, DIFFICULTY_LABEL)
    loopable = label_values(issue.labels, LOOPABLE_LABEL)
    if not (is_numbered(issue.bd_id) or is_provisional(issue.bd_id)) or (not difficulty and not loopable):
        return None, None
    if len(difficulty) != 1 or difficulty[0] not in taskfile.DIFFICULTY_VALUES:
        return None, "label difficulty:<haiku|sonnet|opus> がちょうど1つでない"
    if len(loopable) != 1 or loopable[0] not in taskfile.LOOPABLE_VALUES:
        return None, "label loopable:<Y|N> がちょうど1つでない"
    direct = label_values(issue.labels, DIRECT_LABEL)
    if direct not in ([], ["Y"]):
        return None, "label direct: は direct:Y が1つだけ（印が無ければ置かない）"
    summary = issue.title.strip()
    if summary == "" or "\n" in summary:
        return None, "title が空か改行を含む"
    deps = tuple(to_task_id(d) for d in issue.dependencies)
    return taskfile.Task(to_task_id(issue.bd_id), summary, status, difficulty[0], loopable[0], deps, "", "Y" if direct else "N"), None


def label_values(labels: tuple[str, ...], prefix: str) -> list[str]:
    return [l[len(prefix) :] for l in labels if l.startswith(prefix)]


def ship_mark(issue: Issue) -> str | None:
    """`task done` が立てた印（`done`／`dropped`）。無ければ `None`。"""
    return next((k for k, v in SHIP_LABELS.items() if v in issue.labels), None)


# --- 本文の出し入れ ----------------------------------------------------------------


@dataclass(frozen=True)
class BodyParts:
    description: str
    acceptance: str
    notes: str
    result: str | None


def split_body(body: str) -> BodyParts:
    """タスクファイルの本文を Beads の欄へ分ける。見出しは `description` の中ではそのまま残す。

    `## 完了条件` → `acceptance_criteria`、`## やること` → `notes`、`## 結果` → 取り出すだけ
    （呼ぶ側が拒むか comment にする）。どれも見出しの行を除いた中身。
    """
    preamble, sections = _sections(body)
    desc_parts = [preamble] if preamble.strip() else []
    acceptance = notes = ""
    result = None
    for heading, content in sections:
        if heading == ACCEPTANCE_HEADING:
            acceptance = content.strip("\n")
        elif heading == NOTES_HEADING:
            notes = content.strip("\n")
        elif heading == RESULT_HEADING:
            result = content.strip("\n")
        else:
            desc_parts.append(f"{heading}\n{content}".rstrip("\n"))
    description = "\n\n".join(p.strip("\n") for p in desc_parts)
    return BodyParts(f"{description}\n" if description else "", acceptance, notes, result)


def compose_body(description: str, acceptance: str, notes: str, result: str | None) -> str:
    """`split_body` の逆。枠の7節（`taskfile.SECTION_HEADINGS`）を中身が空でもこの順に必ず出し、
    枠の外の見出しはそのあと、`## 結果` は最後。
    """
    preamble, sections = _sections(description or "")
    contents = {h: c.strip("\n") for h, c in sections}
    contents[ACCEPTANCE_HEADING] = acceptance.strip("\n")
    contents[NOTES_HEADING] = notes.strip("\n")
    blocks = [(h, contents.get(h, "")) for h in taskfile.SECTION_HEADINGS]
    blocks += [(h, c.strip("\n")) for h, c in sections if h not in taskfile.SECTION_HEADINGS]
    if result is not None and result.strip():
        blocks.append((RESULT_HEADING, result.strip("\n")))
    parts = [preamble.strip("\n")] if preamble.strip() else []
    parts += [f"{h}\n\n{c}" if c else h for h, c in blocks]
    text = "\n\n".join(parts)
    return f"{text}\n" if text else ""


def _sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """`(見出しより前, [(見出しの行, 中身), ...])`。見出しは行頭の `## `。"""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in text.split("\n"):
        if line.startswith("## "):
            sections.append((line.rstrip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble), [(h, "\n".join(c).strip("\n")) for h, c in sections]


def render_task(task: taskfile.Task, issue: Issue, result: str | None) -> str:
    """`task show` の出力（タスクファイルと同じ形）。"""
    raw = issue.raw
    body = compose_body(
        str(raw.get("description") or ""),
        str(raw.get("acceptance_criteria") or ""),
        str(raw.get("notes") or ""),
        result,
    )
    return taskfile.render(dataclasses.replace(task, body=body))


# --- 採番 ------------------------------------------------------------------------


def read_last_id(toplevel: str) -> int | None:
    r = run(toplevel, ["kv", "get", LAST_ID_KEY])
    text = r.stdout.strip()
    return int(text) if r.returncode == 0 and text.isdigit() else None


def write_last_id(toplevel: str, number: int) -> None:
    current = read_last_id(toplevel)
    if current is None or number > current:
        run_ok(toplevel, ["kv", "set", LAST_ID_KEY, str(number)])


def id_number(bd_id: str) -> int | None:
    """`t-<n>` の番号（`task` の採番の候補）。`gh-<n>` は Issue 番号なので数えない。"""
    m = BD_ID_PATTERN.match(bd_id)
    return int(m.group(1)) if m and m.group(1) else None


def format_bd_id(number: int) -> str:
    return to_bd_id(taskfile.format_id(number))


# --- 状態の読み替え（トラッカーの橋渡しとバックアップが使う） ------------------------


def workflow_state(issue: Issue) -> str:
    """`hold`・`open`・`in_progress`・`done`・`cancelled` のどれか（Project の Status 欄の対応に使う）。"""
    if issue.status == "closed":
        return "cancelled" if CANCELLED_LABEL in issue.labels else "done"
    if issue.status in HOLD_STATUSES:
        return "hold"
    return issue.status


# --- バックアップ（`.beads` は git の外なので、git の外の決まった場所へ写しを取る） --------


def backup_dir(toplevel: str) -> str:
    """写しの置き場。設定の `backup`、無ければ
    `${XDG_DATA_HOME:-~/.local/share}/task-workflow/<主ブランチを出している作業ツリーの名前>`。"""
    value = layout.read_config(toplevel).backup
    if value:
        return os.path.abspath(os.path.expanduser(value))
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    project = os.path.basename(os.path.dirname(beads_dir(toplevel)))
    return os.path.join(data_home, "task-workflow", project)


def backup(toplevel: str) -> list[str]:
    """`bd backup sync`（Dolt の履歴ごと）と `bd export`（JSONL）を置き場へ取る。出力の行を返す。

    置き場がリポジトリの中なら取らない（`bd export` の JSONL には作成者のメールアドレスが入るので、
    コミットされうる場所へ置かない）。失敗はタスクの操作を止めない（`BACKUP\tFAILED`）。
    """
    target = backup_dir(toplevel)
    repo_root = os.path.dirname(beads_dir(toplevel))
    real_target, real_root = os.path.realpath(target), os.path.realpath(repo_root)
    if real_target == real_root or real_target.startswith(real_root + os.sep):
        return [f"BACKUP\tFAILED\t{target}\tリポジトリの中には置かない"]
    os.makedirs(target, exist_ok=True)
    dolt_dir = os.path.join(target, "dolt")
    status = run(toplevel, ["backup", "status"])
    configured = re.search(r"Destination:\s*(\S+)", status.stdout or "")
    if configured is None:
        r = run(toplevel, ["backup", "init", dolt_dir])
        if r.returncode != 0:
            return [f"BACKUP\tFAILED\t{target}\tbd backup init: {(r.stderr or r.stdout).strip()}"]
    elif configured.group(1).rstrip("/") != f"file://{os.path.realpath(dolt_dir)}".rstrip("/") and configured.group(
        1
    ).rstrip("/") != f"file://{dolt_dir}".rstrip("/"):
        return [f"BACKUP\tFAILED\t{target}\tbd backup の置き場が別に設定されている（{configured.group(1)}）"]
    r = run(toplevel, ["backup", "sync"])
    if r.returncode != 0:
        return [f"BACKUP\tFAILED\t{target}\tbd backup sync: {(r.stderr or r.stdout).strip()}"]
    jsonl = os.path.join(target, "issues.jsonl")
    r = run(toplevel, ["export", "-o", jsonl])
    if r.returncode != 0:
        return [f"BACKUP\tFAILED\t{target}\tbd export: {(r.stderr or r.stdout).strip()}"]
    return [f"BACKUP\tOK\t{target}"]
