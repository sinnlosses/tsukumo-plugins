"""置き場と ID の形と、設定（`.tw/config.toml`）を読む口。

正典は WORKFLOW.md「ファイル配置と設定ファイル」。

別のスキルのスクリプトは、`sys.path` にこのファイルのディレクトリを足してから `import layout` する。
`task-model` が入っていない環境では
`ImportError` で止まる（データの不備ではなく環境の不備として扱う。他のスクリプトの docstring の
「環境の故障」の扱いと同じ）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

# 設定・作業ツリーごとの控えの置き場
TW_DIR = ".tw"
LOCAL_DIR = ".tw/local"
TW_GITIGNORE_PATH = ".tw/.gitignore"
TW_GITIGNORE = "local/\n"
# `.tw/` 直下の控えを git の外に置いていたころの `.tw/.gitignore`
OLD_TW_GITIGNORES = ("*", "*\n", "*\n!config.toml", "*\n!config.toml\n")

# docs/history/tasks.md（旧形式の履歴・採番の下限。正典5.3）
HISTORY_TASKS_PATH = "docs/history/tasks.md"

# 横断の振り返りの記録（1回ごとに `## YYYY-MM-DD（開始日〜終了日）` の見出しを1つ。retrospect の detail.md「週ごとに振り返る」）
RETROSPECT_RECORD_PATH = "docs/history/retrospect.md"
RETROSPECT_RECORD_HEADING_PATTERN = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)

# タスクID: "T-" + 3桁以上の数字（正典3.1）
ID_FRAGMENT = r"T-\d{3,}"
ID_PATTERN = re.compile(rf"^{ID_FRAGMENT}$")  # 全体一致
# トラッカーが github なら、タスクID は Issue 番号の `GH-<n>`（ゼロ埋めしない）。
GH_ID_FRAGMENT = r"GH-\d+"
# Jira のキー（`PROJ-123`。プロジェクトキーは2文字以上・英大文字始まり）。
JIRA_ID_FRAGMENT = r"[A-Z][A-Z0-9_]+-\d+"
ANY_ID_FRAGMENT = rf"(?:{ID_FRAGMENT}|{GH_ID_FRAGMENT}|{JIRA_ID_FRAGMENT})"
ANY_ID_PATTERN = re.compile(rf"^{ANY_ID_FRAGMENT}$")  # --deps・retrospect の引数
ID_SEARCH_PATTERN = re.compile(rf"\b{ANY_ID_FRAGMENT}\b")  # 文中から拾う（コミット件名・トランスクリプト）
HISTORY_HEADING_PATTERN = re.compile(rf"^## ({ID_FRAGMENT})\b", re.MULTILINE)  # docs/history/tasks.md の見出し

# 作業ブランチの接頭辞（`claim` が切る `feature/T-xxx`。正典「ファイル配置と設定ファイル」6.1）
FEATURE_BRANCH_PREFIX = "feature/"
FEATURE_BRANCH_PATTERN = re.compile(rf"{FEATURE_BRANCH_PREFIX}({ANY_ID_FRAGMENT})")

# --- 設定（`.tw/config.toml`。正典「ファイル配置と設定ファイル」） ---------------

CONFIG_PATH = ".tw/config.toml"

STORE_BEADS = "beads"
STORE_VALUES = (STORE_BEADS,)
TRACKER_VALUES = ("なし", "github", "jira")
BRANCH_VALUES = ("既定", "作業ブランチを切る", "切らない")
DEFAULT_BRANCH = "既定"
NO_COMMAND = "なし"

CONFIG_KEYS = (
    "verify",
    "verify_before_ship",
    "format",
    "branch",
    "base_branch",
    "store",
    "tracker",
    "github_project",
    "backup",
    "hook_tally",
    "flow",
)

# 流れの種類（`flow`）。近道と `hold` はタスクごとに決まるので入れない。
FLOW_DELEGATE = "delegate"
FLOW_MAIN = "main"
DEFAULT_FLOW = FLOW_DELEGATE


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    description: str


FLOW_VALUES = (
    Choice(FLOW_DELEGATE, "委譲する", "段ごとに実装役へ委譲し、受け入れ・振り返り・送り出しへ進める（近道の印と loopable: N のタスクは別の流れに分かれる）"),
    Choice(FLOW_MAIN, "メインで行う", "どのタスクも委譲せずメインで進める（受け入れ・振り返り・送り出しは同じ）"),
)

# パーツの枠（`[parts]` の表）。値は `PART_DEFAULT`（オーケストレーターの既定のパーツ）・全名・`PART_NONE`（`allow_none` の枠だけ）。
PARTS_TABLE = "parts"
PART_DEFAULT = "既定"
PART_NONE = "なし"
PART_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?")


@dataclass(frozen=True)
class PartSlot:
    name: str
    label: str
    candidates: Literal["agent", "skill"]  # 候補を出す一覧（Agent ツールのエージェントか、スキル）
    allow_none: bool
    description: str

    @property
    def key(self) -> str:
        return f"{PARTS_TABLE}.{self.name}"


PART_SLOTS = (
    PartSlot("implementer", "実装役", "agent", False, "委譲を受けて `## やること` の段を1つずつ実装して返すエージェント"),
    PartSlot("acceptor", "受け入れ役", "agent", False, "差分と完了条件を読んで受け入れの判定と振り返りの材料を返すエージェント"),
    PartSlot("retrospect", "振り返り", "skill", True, "受け入れ役の材料から改善の候補をドラフトに積むスキル（なし で外す）"),
    PartSlot("ship", "送り出し", "skill", False, "タスクを完了にしてコミットし主ブランチへ送るスキル（既定は tw finish の手順）"),
)
PART_KEYS = tuple(slot.key for slot in PART_SLOTS)

# 置き場の根を選んでいたころのキー。書いてあっても読み捨てる。
IGNORED_KEYS = ("root",)
GITHUB_PROJECT_PATTERN = re.compile(r"([^/\s]+)/(\d+)")


class ConfigError(RuntimeError):
    """設定が読めない（呼ぶ側が `INVALID`・終了コード3にする）。"""


@dataclass(frozen=True)
class Config:
    """解けた設定。`source` は読んだファイル（`CONFIG_PATH`。無ければ `None`）。

    `written` は設定に書いてあったキー。
    """

    source: str | None
    verify: str | None = None
    verify_before_ship: str | None = None
    format: str | None = None
    branch: str = DEFAULT_BRANCH
    base_branch: str | None = None
    store: str = STORE_BEADS
    tracker: str = "なし"
    github_project: str | None = None
    backup: str | None = None
    hook_tally: str | None = None
    flow: str = DEFAULT_FLOW
    parts: tuple[tuple[str, str], ...] = tuple((slot.name, PART_DEFAULT) for slot in PART_SLOTS)
    written: frozenset = frozenset()

    @property
    def project_owner_number(self) -> tuple[str, str] | None:
        m = GITHUB_PROJECT_PATTERN.fullmatch(self.github_project or "")
        return (m.group(1), m.group(2)) if m else None

    def part(self, name: str) -> str:
        """枠 `name` の値（`PART_DEFAULT`・`PART_NONE`・全名）。"""
        return dict(self.parts)[name]


_config_cache: dict[str, tuple[tuple, Config]] = {}


def read_config(toplevel: str) -> Config:
    """`toplevel` の設定。`.tw/config.toml` が無ければ既定の `Config(source=None)`。

    根ごとに覚え、設定のファイルが変わっていれば読み直す。読めなければ `ConfigError`。
    """
    root = os.path.realpath(toplevel)
    stamp = _mtime(os.path.join(root, CONFIG_PATH))
    cached = _config_cache.get(root)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    path = os.path.join(root, CONFIG_PATH)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            config = _config_from_values(CONFIG_PATH, parse_config_text(f.read(), CONFIG_PATH))
    else:
        config = Config(source=None)
    _config_cache[root] = (stamp, config)
    return config


def _mtime(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


_LINE_PATTERN = re.compile(r"([A-Za-z0-9_-]+)[ \t]*=[ \t]*(.*)")
_TABLE_PATTERN = re.compile(r"\[[ \t]*([A-Za-z0-9_-]+)[ \t]*\][ \t]*(?:#.*)?")


def parse_config_text(text: str, label: str) -> list[tuple[int, str, str]]:
    """TOML の部分集合（空行・`# コメント`・`key = "値"`／`key = '値'`・`[parts]` の表）を `(行番号, キー, 値)` にする。

    `[parts]` の後ろのキーは `parts.<キー>` として返す。
    """
    entries: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    table: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        t = _TABLE_PATTERN.fullmatch(line)
        if t is not None:
            name = t.group(1)
            if name != PARTS_TABLE:
                raise ConfigError(f"{label}:{lineno}: 知らない表 [{name}]（表は [{PARTS_TABLE}] だけ）")
            if name in seen:
                raise ConfigError(f"{label}:{lineno}: 表 [{name}] が {seen[name]} 行目にもある")
            seen[name] = lineno
            table = name
            continue
        m = _LINE_PATTERN.fullmatch(line)
        if m is None:
            raise ConfigError(f"{label}:{lineno}: `key = \"値\"` の形でない: {line}")
        key, rest = m.group(1), m.group(2)
        if table is not None:
            key = f"{table}.{key}"
            if key not in PART_KEYS:
                hint = f"（{m.group(1)} は [{table}] より前に書く）" if m.group(1) in CONFIG_KEYS else ""
                raise ConfigError(f"{label}:{lineno}: [{table}] の知らない枠 {m.group(1)!r}{hint}")
        elif key not in CONFIG_KEYS and key not in IGNORED_KEYS:
            raise ConfigError(f"{label}:{lineno}: 知らないキー {key!r}")
        if key in seen:
            raise ConfigError(f"{label}:{lineno}: キー {key!r} が {seen[key]} 行目にもある")
        seen[key] = lineno
        value = _parse_string(rest, f"{label}:{lineno}")
        if key not in IGNORED_KEYS:
            entries.append((lineno, key, value))
    return entries


def _parse_string(rest: str, where: str) -> str:
    if not rest or rest[0] not in "\"'":
        raise ConfigError(f"{where}: 値は \"…\" か '…' の文字列だけ: {rest}")
    quote = rest[0]
    chars: list[str] = []
    i = 1
    while i < len(rest):
        c = rest[i]
        if c == quote:
            break
        if c == "\\" and quote == '"':
            nxt = rest[i + 1 : i + 2]
            if nxt not in ('"', "\\"):
                raise ConfigError(f"{where}: 使えるエスケープは \\\" と \\\\ だけ")
            chars.append(nxt)
            i += 2
            continue
        chars.append(c)
        i += 1
    else:
        raise ConfigError(f"{where}: 文字列が閉じていない")
    tail = rest[i + 1 :].strip()
    if tail and not tail.startswith("#"):
        raise ConfigError(f"{where}: 値の後ろに続きがある（説明は # コメントに書く）: {tail}")
    return "".join(chars)


def _config_from_values(source: str, entries: list[tuple[int, str, str]]) -> Config:
    values: dict[str, str | None] = {}
    lines: dict[str, int] = {}
    for lineno, key, value in entries:
        if value == "":
            raise ConfigError(f"{source}:{lineno}: {key} の値が空")
        if key in ("verify", "verify_before_ship", "format") and value == NO_COMMAND:
            values[key] = None
        else:
            values[key] = value
        lines[key] = lineno
    if "verify" not in values:
        raise ConfigError(f"{source}: verify が無い（打つものが無ければ verify = \"{NO_COMMAND}\"）")
    for key, vocabulary in (("branch", BRANCH_VALUES), ("store", STORE_VALUES), ("tracker", TRACKER_VALUES)):
        if key in values and values[key] not in vocabulary:
            raise ConfigError(
                f"{source}:{lines[key]}: {key} の値 {values[key]!r} が {' / '.join(vocabulary)} のどれでもない"
            )
    for key in ("flow", *PART_KEYS):
        if key in values:
            problem = value_problem(key, values[key] or "")
            if problem is not None:
                raise ConfigError(f"{source}:{lines[key]}: {problem}")
    return build_config(source, values)


def value_problem(key: str, value: str) -> str | None:
    """`flow`・`parts.<枠>` に `value` を置けない理由。置けるなら `None`。"""
    if key == "flow":
        names = [c.value for c in FLOW_VALUES]
        return None if value in names else f"flow の値 {value!r} が {' / '.join(names)} のどれでもない"
    slot = next(s for s in PART_SLOTS if s.key == key)
    if value == PART_NONE:
        return None if slot.allow_none else f"{key} は {PART_NONE} で外せない"
    if value == PART_DEFAULT or PART_NAME_PATTERN.fullmatch(value):
        return None
    return f"{key} の値 {value!r} が {PART_DEFAULT} でもパーツの全名（<プラグイン>:<名前> か <名前>）でもない"


def build_config(source: str | None, values: dict[str, str | None]) -> Config:
    defaults = Config(source=source)
    config = Config(
        source=source,
        verify=values.get("verify"),
        verify_before_ship=values.get("verify_before_ship"),
        format=values.get("format"),
        branch=values["branch"] if values.get("branch") is not None else defaults.branch,
        base_branch=values.get("base_branch"),
        store=values.get("store") or defaults.store,
        tracker=values.get("tracker") or defaults.tracker,
        github_project=values.get("github_project"),
        backup=values.get("backup"),
        hook_tally=values.get("hook_tally"),
        flow=values.get("flow") or defaults.flow,
        parts=tuple((slot.name, values.get(slot.key) or PART_DEFAULT) for slot in PART_SLOTS),
        written=frozenset(values),
    )
    if config.github_project is not None and config.project_owner_number is None:
        raise ConfigError(f"{source}: github_project の値 {config.github_project!r} が \"<owner>/<番号>\" の形でない")
    if config.tracker == "github" and config.github_project is None:
        raise ConfigError(f"{source}: tracker = \"github\" には github_project = \"<owner>/<番号>\" が要る")
    return config


# --- 設定の欄の形（`tw config --schema`）と書く口（`tw config set`） -----------------

SCHEMA_VERSION = 1
SETTABLE_KEYS = ("flow", *PART_KEYS)


class ConfigValueError(ValueError):
    """`set` に渡したキー・値が書けない（呼ぶ側が引数の誤り・終了コード2にする）。"""


def config_schema() -> dict:
    """`tw config --schema` が出す欄の形。`schema` は形の版で、欄を消す・意味を変えるときだけ上げる。"""
    keys: list[dict] = [
        {
            "key": "flow",
            "label": "流れ",
            "description": "着手したタスクを進める流れの種類",
            "kind": "choice",
            "default": DEFAULT_FLOW,
            "values": [{"value": c.value, "label": c.label, "description": c.description} for c in FLOW_VALUES],
        }
    ]
    for slot in PART_SLOTS:
        keys.append(
            {
                "key": slot.key,
                "label": slot.label,
                "description": slot.description,
                "kind": "part",
                "default": PART_DEFAULT,
                "candidates": slot.candidates,
                "allow_none": slot.allow_none,
            }
        )
    return {"schema": SCHEMA_VERSION, "default_word": PART_DEFAULT, "none_word": PART_NONE, "keys": keys}


_VALUE_LINE_PATTERN = re.compile(r"""([ \t]*([A-Za-z0-9_-]+)[ \t]*=[ \t]*)("(?:[^"\\]|\\.)*"|'[^']*')(.*)""", re.DOTALL)


def config_text_with(text: str, key: str, value: str, label: str = CONFIG_PATH) -> str:
    """設定の全文 `text` の `key` を `value` にした全文。ほかの行・並び・コメントは変えない。

    キーの行があれば値の文字列だけを差し替え、無ければ `flow` は最初の表より前の最後のキーの行の次へ、
    `parts.<枠>` は `[parts]` の最後のキーの行の次（表が無ければ末尾に表ごと）へ足す。書けないキー・値は
    `ConfigValueError`、書いた結果が読めなければ `ConfigError`。
    """
    if key not in SETTABLE_KEYS:
        raise ConfigValueError(f"書けるキーは {' / '.join(SETTABLE_KEYS)} だけ: {key!r}")
    problem = value_problem(key, value)
    if problem is not None:
        raise ConfigValueError(problem)
    quoted = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    table, _, name = key.rpartition(".")
    lines = text.splitlines(keepends=True)
    current: str | None = None
    insert_at: int | None = None  # 足す位置（その表・最上位の最後のキーの行の次）
    table_seen = False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        t = _TABLE_PATTERN.fullmatch(stripped)
        if t is not None:
            current = t.group(1)
            if current == table:
                table_seen = True
                insert_at = i + 1
            continue
        m = _VALUE_LINE_PATTERN.fullmatch(raw)
        if (current or "") != table or m is None:
            continue
        if m.group(2) == name:
            lines[i] = m.group(1) + quoted + m.group(4)
            return _checked("".join(lines), label)
        insert_at = i + 1
    new_line = f"{name} = {quoted}\n"
    if table and not table_seen:
        head = "".join(lines)
        if head and not head.endswith("\n"):
            head += "\n"
        return _checked(head + ("\n" if head else "") + f"[{table}]\n" + new_line, label)
    if insert_at is None:
        insert_at = 0
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    lines.insert(insert_at, new_line)
    return _checked("".join(lines), label)


def _checked(text: str, label: str) -> str:
    _config_from_values(label, parse_config_text(text, label))
    return text
