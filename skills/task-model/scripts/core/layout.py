"""置き場と ID の形と、設定（`.tw/config.toml`）を読む口。

正典は WORKFLOW.md「ファイル配置と設定ファイル」。

別のスキルのスクリプトは、`sys.path` にこのファイルのディレクトリを足してから `import layout` する。
`task-model` が入っていない環境では
`ImportError` で止まる（データの不備ではなく環境の不備として扱う。他のスクリプトの docstring の
「環境の故障」の扱いと同じ）。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

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
STEPS_KEY = "steps"
REVIEW_CODE_GLOBS_KEY = "review_code_globs"
SHELL_WRITE_GUARD_KEY = "shell_write_guard"
SHELL_WRITE_GUARD_TW = "tw"
SHELL_WRITE_GUARD_PROJECT = "project"
SHELL_WRITE_GUARD_VALUES = (SHELL_WRITE_GUARD_TW, SHELL_WRITE_GUARD_PROJECT)
DEFAULT_LOOP_MAX_SHIPS = 12

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
    "step_check",
    "branch",
    "base_branch",
    "store",
    "tracker",
    "github_project",
    "backup",
    "hook_tally",
    "steps",
    "review_code_globs",
    "shell_write_guard",
    "loop_max_ships",
)

# 廃止したキーと表。設定に残っていれば `ConfigError` で知らせる。
REMOVED_FLOW_KEY = "flow"
REMOVED_PARTS_TABLE = "parts"

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
    step_check: str | None = None
    branch: str = DEFAULT_BRANCH
    base_branch: str | None = None
    store: str = STORE_BEADS
    tracker: str = "なし"
    github_project: str | None = None
    backup: str | None = None
    hook_tally: str | None = None
    steps: tuple[str, ...] | None = None
    review_code_globs: tuple[str, ...] = ()
    shell_write_guard: str = SHELL_WRITE_GUARD_TW
    loop_max_ships: int = DEFAULT_LOOP_MAX_SHIPS
    written: frozenset = frozenset()

    @property
    def project_owner_number(self) -> tuple[str, str] | None:
        m = GITHUB_PROJECT_PATTERN.fullmatch(self.github_project or "")
        return (m.group(1), m.group(2)) if m else None


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
    """TOML の部分集合（空行・`# コメント`・`key = "値"`／`key = '値'`）を `(行番号, キー, 値)` にする。

    表（`[…]`）は読まない。`flow` と `[parts]` は廃止したキーとして `ConfigError` にする。
    """
    entries: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        t = _TABLE_PATTERN.fullmatch(line)
        if t is not None:
            name = t.group(1)
            if name == REMOVED_PARTS_TABLE:
                raise ConfigError(f"{label}:{lineno}: [{name}] は廃止した（スキルの差し替えは steps に書く。tw config set steps）")
            raise ConfigError(f"{label}:{lineno}: 知らない表 [{name}]（表は使わない）")
        m = _LINE_PATTERN.fullmatch(line)
        if m is None:
            raise ConfigError(f"{label}:{lineno}: `key = \"値\"` の形でない: {line}")
        key, rest = m.group(1), m.group(2)
        if key == REMOVED_FLOW_KEY:
            raise ConfigError(f"{label}:{lineno}: {key} は廃止した（並びは steps に書く。tw config set steps）")
        if key not in CONFIG_KEYS and key not in IGNORED_KEYS:
            raise ConfigError(f"{label}:{lineno}: 知らないキー {key!r}")
        if key in seen:
            raise ConfigError(f"{label}:{lineno}: キー {key!r} が {seen[key]} 行目にもある")
        seen[key] = lineno
        if key == STEPS_KEY:
            value = _parse_steps_array(rest, f"{label}:{lineno}")
        elif key == REVIEW_CODE_GLOBS_KEY:
            value = _parse_globs_array(rest, f"{label}:{lineno}")
        else:
            value = _parse_string(rest, f"{label}:{lineno}")
        if key not in IGNORED_KEYS:
            entries.append((lineno, key, value))
    return entries


def _parse_array(rest: str, where: str, key: str) -> list:
    if not rest.startswith("["):
        raise ConfigError(f"{where}: {key} の値は [\"…\", \"…\"] の文字列の配列だけ: {rest}")
    try:
        items, end = json.JSONDecoder().raw_decode(rest)
    except ValueError:
        raise ConfigError(f"{where}: {key} の配列が読めない: {rest}")
    tail = rest[end:].strip()
    if tail and not tail.startswith("#"):
        raise ConfigError(f"{where}: 値の後ろに続きがある（説明は # コメントに書く）: {tail}")
    return items


def _parse_steps_array(rest: str, where: str) -> str:
    """`steps = ["…", "…"]` の右辺を、1行の JSON の配列（`steps_text`）にして返す。"""
    items = _parse_array(rest, where, STEPS_KEY)
    problem = steps_problem(items)
    if problem is not None:
        raise ConfigError(f"{where}: {problem}")
    return steps_text(items)


def _parse_globs_array(rest: str, where: str) -> str:
    """`review_code_globs = ["…", "…"]` の右辺を、1行の JSON の配列にして返す。空の配列は書ける。"""
    items = _parse_array(rest, where, REVIEW_CODE_GLOBS_KEY)
    if not all(isinstance(i, str) and i != "" and i.strip() == i for i in items):
        raise ConfigError(f"{where}: {REVIEW_CODE_GLOBS_KEY} は空でない glob の文字列の配列でなければならない")
    return steps_text(items)


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
        if key in ("verify", "verify_before_ship", "format", "step_check") and value == NO_COMMAND:
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
    return build_config(source, values)


def _loop_max_ships(source: str | None, text: str | None, default: int) -> int:
    if text is None:
        return default
    if not re.fullmatch(r"[0-9]+", text) or int(text) < 1:
        raise ConfigError(f"{source}: loop_max_ships の値 {text!r} が 1 以上の整数の文字列でない")
    return int(text)


def build_config(source: str | None, values: dict[str, str | None]) -> Config:
    defaults = Config(source=source)
    config = Config(
        source=source,
        verify=values.get("verify"),
        verify_before_ship=values.get("verify_before_ship"),
        format=values.get("format"),
        step_check=values.get("step_check"),
        branch=values["branch"] if values.get("branch") is not None else defaults.branch,
        base_branch=values.get("base_branch"),
        store=values.get("store") or defaults.store,
        tracker=values.get("tracker") or defaults.tracker,
        github_project=values.get("github_project"),
        backup=values.get("backup"),
        hook_tally=values.get("hook_tally"),
        steps=tuple(json.loads(values["steps"])) if values.get("steps") else None,
        review_code_globs=tuple(json.loads(values.get(REVIEW_CODE_GLOBS_KEY) or "[]")),
        shell_write_guard=values.get(SHELL_WRITE_GUARD_KEY) or defaults.shell_write_guard,
        loop_max_ships=_loop_max_ships(source, values.get("loop_max_ships"), defaults.loop_max_ships),
        written=frozenset(values),
    )
    if config.shell_write_guard not in SHELL_WRITE_GUARD_VALUES:
        raise ConfigError(
            f"{source}: {SHELL_WRITE_GUARD_KEY} の値 {config.shell_write_guard!r} は {' / '.join(SHELL_WRITE_GUARD_VALUES)} のどちらか"
        )
    if config.github_project is not None and config.project_owner_number is None:
        raise ConfigError(f"{source}: github_project の値 {config.github_project!r} が \"<owner>/<番号>\" の形でない")
    if config.tracker == "github" and config.github_project is None:
        raise ConfigError(f"{source}: tracker = \"github\" には github_project = \"<owner>/<番号>\" が要る")
    return config


# --- 設定の欄の形（`tw config --schema`）と書く口（`tw config set`） -----------------

COMMAND_SETTABLE_KEY = "step_check"
SETTABLE_KEYS = (STEPS_KEY, COMMAND_SETTABLE_KEY, SHELL_WRITE_GUARD_KEY)


class ConfigValueError(ValueError):
    """`set` に渡したキー・値が書けない（呼ぶ側が引数の誤り・終了コード2にする）。"""


# --- 並び（`steps`）。候補は frontmatter の `metadata` に `workflow-step: "true"` を持つスキル ---

STEP_MARK_KEY = "workflow-step"
STEP_ORDER_KEY = "workflow-step-order"  # 既定の並びでの位置（整数の文字）。`tw` と同じ plugin のスキルだけが使う
STEP_TITLE_KEY = "workflow-step-title"
BRIEF_KEY_PREFIX = "workflow-brief-"  # 後ろに役の名。値は SKILL.md のディレクトリからの依頼文の型のパス
BRIEF_ROLES = ("implementer", "acceptor")
ENTRY_MARK_KEY = "workflow-entry"  # タスクの実行の入口にできるスキル（ID を渡すと着手する）の印
ENTRY_TITLE_KEY = "workflow-entry-title"
PLUGIN_MANIFEST_PATH = os.path.join(".claude-plugin", "plugin.json")


@dataclass(frozen=True)
class StepCandidate:
    value: str  # Skill ツールで呼ぶ全名
    label: str
    description: str
    own: bool  # `tw` と同じチェックアウトの plugin のスキルか
    order: int | None = None
    title: str = ""  # 日本語の段の名前。`workflow-step-title` が無ければ `label` と同じ
    briefs: tuple[tuple[str, str], ...] = ()  # (役, 依頼文の型の絶対パス)

    def brief(self, role: str) -> str | None:
        return next((path for r, path in self.briefs if r == role), None)


def _scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            return json.loads(text)
        except ValueError:
            return text[1:-1]
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def parse_skill_frontmatter(text: str) -> dict[str, str | dict[str, str]]:
    """SKILL.md 先頭の frontmatter を、最上位は文字列、`metadata` のような入れ子は文字列の対応にして返す。

    読むのは YAML の部分集合（`key: 値`・`>`／`|` の複数行・字下げした `k: v` の入れ子）。frontmatter が無ければ空。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}
    out: dict[str, str | dict[str, str]] = {}
    i = 1
    while i < end:
        m = re.fullmatch(r"([A-Za-z0-9_-]+):[ \t]*(.*)", lines[i])
        i += 1
        if m is None:
            continue
        children: list[str] = []
        while i < end and (not lines[i].strip() or lines[i][0] in " \t"):
            children.append(lines[i])
            i += 1
        key, rest = m.group(1), m.group(2).strip()
        if rest == "":
            nested = {}
            for child in children:
                cm = re.fullmatch(r"[ \t]+([A-Za-z0-9_-]+):[ \t]*(.*)", child)
                if cm is not None:
                    nested[cm.group(1)] = _scalar(cm.group(2))
            out[key] = nested
        elif rest[0] in ">|":
            out[key] = " ".join(c.strip() for c in children if c.strip())
        else:
            out[key] = _scalar(" ".join([rest, *(c.strip() for c in children if c.strip())]))
    return out


def _skill_dirs(skills_root: str) -> list[str]:
    try:
        names = sorted(os.listdir(skills_root))
    except OSError:
        return []
    return [os.path.join(skills_root, n) for n in names if os.path.isfile(os.path.join(skills_root, n, "SKILL.md"))]


@dataclass(frozen=True)
class _Skill:
    name: str
    description: str
    step_marked: bool
    order: int | None  # 既定の並びでの位置。整数でなければ `None`
    step_title: str | None
    briefs: tuple[tuple[str, str], ...]  # 依頼文の型
    entry_marked: bool
    entry_title: str | None


def _read_skill(skill_dir: str) -> _Skill | None:
    """SKILL.md を読んだ `_Skill`。読めなければ `None`。"""
    try:
        with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as f:
            front = parse_skill_frontmatter(f.read())
    except (OSError, UnicodeDecodeError):
        return None
    name = front.get("name")
    description = front.get("description")
    metadata = front.get("metadata")
    marked = isinstance(metadata, dict) and metadata.get(STEP_MARK_KEY) == "true"
    order_text = metadata.get(STEP_ORDER_KEY) if isinstance(metadata, dict) else None
    order = int(order_text) if isinstance(order_text, str) and re.fullmatch(r"-?[0-9]+", order_text) else None
    title_text = metadata.get(STEP_TITLE_KEY) if isinstance(metadata, dict) else None
    briefs: list[tuple[str, str]] = []
    for role in BRIEF_ROLES:
        rel = metadata.get(BRIEF_KEY_PREFIX + role) if isinstance(metadata, dict) else None
        if isinstance(rel, str) and rel:
            briefs.append((role, os.path.realpath(os.path.join(os.path.realpath(skill_dir), rel))))
    entry_marked = isinstance(metadata, dict) and metadata.get(ENTRY_MARK_KEY) == "true"
    entry_title = metadata.get(ENTRY_TITLE_KEY) if isinstance(metadata, dict) else None
    return _Skill(
        name=name if isinstance(name, str) and name else os.path.basename(skill_dir),
        description=description if isinstance(description, str) else "",
        step_marked=marked,
        order=order,
        step_title=title_text if isinstance(title_text, str) and title_text else None,
        briefs=tuple(briefs),
        entry_marked=entry_marked,
        entry_title=entry_title if isinstance(entry_title, str) and entry_title else None,
    )


def own_plugin_root() -> tuple[str, str] | None:
    """このファイルと同じチェックアウトの plugin の `(根, plugin の名前)`。上へたどって `.claude-plugin/plugin.json` が無ければ `None`。"""
    here = os.path.dirname(os.path.realpath(__file__))
    while True:
        manifest = os.path.join(here, PLUGIN_MANIFEST_PATH)
        if os.path.isfile(manifest):
            try:
                with open(manifest, encoding="utf-8") as f:
                    name = json.load(f).get("name")
            except (OSError, ValueError, AttributeError):
                return None
            return (here, name) if isinstance(name, str) and name else None
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def _marked_skills(
    project_root: str | None,
    home: str | None,
    plugin: tuple[str, str] | None,
    wanted: Callable[[_Skill], bool],
) -> list[tuple[str, bool, _Skill]]:
    """走査先（作業ツリー・`home` の `.claude/skills/` と plugin の `skills/` の順）から、`wanted` が真のスキルを `(全名, own, _Skill)` で返す。

    同じスキル（実体が同じ）は先に見つけた1つにする。リンクで入れたものは `.claude/skills/` で先に当たるので `<名前>`、
    入っていなければ plugin の `<プラグイン>:<名前>`。全名が同じで実体が違うものも先の1つだけ。
    """
    roots: list[tuple[str, str | None]] = []
    if project_root is not None:
        roots.append((os.path.join(project_root, ".claude", "skills"), None))
    if home is not None:
        roots.append((os.path.join(home, ".claude", "skills"), None))
    if plugin is not None:
        roots.append((os.path.join(plugin[0], "skills"), plugin[1]))
    found: list[tuple[str, bool, _Skill]] = []
    real_seen: set[str] = set()
    value_seen: set[str] = set()
    for skills_root, prefix in roots:
        for skill_dir in _skill_dirs(skills_root):
            real = os.path.realpath(skill_dir)
            if real in real_seen:
                continue
            skill = _read_skill(skill_dir)
            if skill is None or not wanted(skill):
                continue
            value = f"{prefix}:{skill.name}" if prefix else skill.name
            if value in value_seen:
                continue
            real_seen.add(real)
            value_seen.add(value)
            own = plugin is not None and real.startswith(os.path.realpath(plugin[0]) + os.sep)
            found.append((value, own, skill))
    return found


def step_candidates(
    project_root: str | None,
    home: str | None,
    plugin: tuple[str, str] | None,
) -> list[StepCandidate]:
    """並びに置ける候補（`workflow-step` の印のあるスキル）。走査の決まりは `_marked_skills`。"""
    return [
        StepCandidate(value, skill.name, skill.description, own, skill.order, skill.step_title or skill.name, skill.briefs)
        for value, own, skill in _marked_skills(project_root, home, plugin, lambda s: s.step_marked)
    ]


def entry_candidates(
    project_root: str | None,
    home: str | None,
    plugin: tuple[str, str] | None,
) -> list[StepCandidate]:
    """タスクの実行の入口にできるスキル（`workflow-entry` の印）。走査の決まりは `_marked_skills`、名前は `workflow-entry-title`。"""
    return [
        StepCandidate(value, skill.name, skill.description, own, title=skill.entry_title or skill.name)
        for value, own, skill in _marked_skills(project_root, home, plugin, lambda s: s.entry_marked)
    ]


def config_entries(project_root: str | None, home: str | None = None, plugin: tuple[str, str] | None = None) -> dict:
    """`tw config --entries` が出す形。"""
    return {
        "entries": [
            {"value": c.value, "label": c.label, "title": c.title, "description": c.description}
            for c in entry_candidates(project_root, home, plugin)
        ]
    }


def default_steps(candidates: list[StepCandidate]) -> list[str]:
    """既定の並び。`tw` と同じ plugin の候補を `workflow-step-order` の小さい順に、位置の無いものはその後ろに走査順で並べる。"""
    own = [c for c in candidates if c.own]
    return [c.value for c in sorted(own, key=lambda c: (c.order is None, c.order or 0))]


def config_schema(project_root: str | None, home: str | None = None, plugin: tuple[str, str] | None = None) -> dict:
    """`tw config --schema` が出す欄の形（schema 2）。既定の並びは `default_steps`。"""
    candidates = step_candidates(project_root, home, plugin)
    return {
        "schema": 2,
        "keys": [
            {
                "key": STEPS_KEY,
                "label": "並び",
                "description": "タスクを進めるスキルの並び（上から順に呼ぶ）",
                "kind": "steps",
                "default": default_steps(candidates),
                "values": [
                    {"value": c.value, "label": c.label, "title": c.title, "description": c.description}
                    for c in candidates
                ],
            },
            {
                "key": SHELL_WRITE_GUARD_KEY,
                "label": "シェル書き込みの判定",
                "description": "着手中の作業ツリーのシェルでの書き換えを、tw の hook が止めるか、プロジェクトの hook に任せるか",
                "kind": "choice",
                "default": SHELL_WRITE_GUARD_TW,
                "values": [
                    {"value": SHELL_WRITE_GUARD_TW, "label": "tw が止める", "description": "tw commit-guard が SHELL_WRITE で拒む"},
                    {
                        "value": SHELL_WRITE_GUARD_PROJECT,
                        "label": "プロジェクトが止める",
                        "description": "プロジェクト自前の hook に任せ、tw は SHELL_WRITE を見ない",
                    },
                ],
            },
        ],
    }


_VALUE_LINE_PATTERN = re.compile(
    r"""([ \t]*([A-Za-z0-9_-]+)[ \t]*=[ \t]*)("(?:[^"\\]|\\.)*"|'[^']*'|\[(?:"(?:[^"\\]|\\.)*"|[ \t,])*\])(.*)""",
    re.DOTALL,
)


def steps_problem(items: object) -> str | None:
    """並びとして書けない理由（形・空・重複）。書けるなら `None`。候補との突き合わせは呼ぶ側が行う。"""
    if not isinstance(items, list) or not all(isinstance(i, str) and i != "" and i.strip() == i for i in items):
        return "steps は空でない名前の文字列の配列でなければならない"
    if not items:
        return "steps は1つ以上要る（空にできない）"
    dup = sorted({i for i in items if items.count(i) > 1})
    if dup:
        return f"steps に同じ名前を2度並べられない: {', '.join(dup)}"
    return None


def steps_text(items: list[str]) -> str:
    """`tw config` の値の列・`set` の出力に使う1行の JSON の配列。"""
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def config_text_with(text: str, key: str, value: str, label: str = CONFIG_PATH) -> str:
    """設定の全文 `text` の `key` を `value` にした全文。ほかの行・並び・コメントは変えない。

    キーの行があれば値の文字列だけを差し替え、無ければ最後のキーの行の次へ足す。書けないキー・値は
    `ConfigValueError`、書いた結果が読めなければ `ConfigError`。
    """
    if key not in SETTABLE_KEYS:
        raise ConfigValueError(f"書けるキーは {' / '.join(SETTABLE_KEYS)} だけ: {key!r}")
    if key == COMMAND_SETTABLE_KEY:
        if not value.strip() or not value.isprintable():
            raise ConfigValueError(f"{key} の値は空でない1行（タブ・改行などの制御文字なし）のコマンドか {NO_COMMAND}: {value!r}")
        quoted = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    elif key == SHELL_WRITE_GUARD_KEY:
        if value not in SHELL_WRITE_GUARD_VALUES:
            raise ConfigValueError(f"{key} の値は {' / '.join(SHELL_WRITE_GUARD_VALUES)} のどちらか: {value!r}")
        quoted = json.dumps(value)
    else:
        try:
            items = json.loads(value)
        except ValueError:
            raise ConfigValueError(f"steps の値が JSON の配列でない: {value!r}")
        problem = steps_problem(items)
        if problem is not None:
            raise ConfigValueError(problem)
        quoted = "[" + ", ".join(json.dumps(i, ensure_ascii=False) for i in items) + "]"
    name = key
    lines = text.splitlines(keepends=True)
    insert_at: int | None = None  # 足す位置（最後のキーの行の次）
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or _TABLE_PATTERN.fullmatch(stripped):
            continue
        m = _VALUE_LINE_PATTERN.fullmatch(raw)
        if m is None:
            continue
        if m.group(2) == name:
            lines[i] = m.group(1) + quoted + m.group(4)
            return _checked("".join(lines), label)
        insert_at = i + 1
    new_line = f"{name} = {quoted}\n"
    if insert_at is None:
        insert_at = 0
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
    lines.insert(insert_at, new_line)
    return _checked("".join(lines), label)


def _checked(text: str, label: str) -> str:
    _config_from_values(label, parse_config_text(text, label))
    return text
