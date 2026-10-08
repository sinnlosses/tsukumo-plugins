"""置き場と ID の形と、設定（`.tw/config.toml`）を読む口。

正典は WORKFLOW.md「ファイル配置と設定ファイル」。

別のスキルのスクリプトは、`sys.path` にこのファイルのディレクトリを足してから `import layout` する。
`task-workflow` が入っていない環境では
`ImportError` で止まる（データの不備ではなく環境の不備として扱う。他のスクリプトの docstring の
「環境の故障」の扱いと同じ）。
"""

from __future__ import annotations

import dataclasses
import os
import posixpath
import re
from dataclasses import dataclass

# 設定・作業ツリーごとの控えの置き場（`root` によらない）
TW_DIR = ".tw"
LOCAL_DIR = ".tw/local"
TW_GITIGNORE_PATH = ".tw/.gitignore"
TW_GITIGNORE = "local/\n"
# `.tw/` 直下の控えを git の外に置いていたころの `.tw/.gitignore`
OLD_TW_GITIGNORES = ("*", "*\n", "*\n!config.toml", "*\n!config.toml\n")

# 旧配置（`.tw/config.toml` が無いプロジェクト）の根
LEGACY_ROOT = "develop"
LEGACY_DIRECTION_PATH = "develop/direction.md"

# <根>/direction.md の節（正典「指示メモ」）
SECTION_USER = "## ユーザーから"
# ドラフトを direction.md に積んでいたころの節。移し忘れを数えるためだけに残す。
LEGACY_SECTION_DRAFT = "## エージェントのドラフト"

# docs/history/tasks.md（旧形式の履歴・採番の下限。正典5.3）
HISTORY_TASKS_PATH = "docs/history/tasks.md"

# 横断の振り返りの記録（1回ごとに `## YYYY-MM-DD（開始日〜終了日）` の見出しを1つ。retrospect の SKILL.md「週ごとに振り返る」）
RETROSPECT_RECORD_PATH = "docs/history/retrospect.md"
RETROSPECT_RECORD_HEADING_PATTERN = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)

# タスクID: "T-" + 3桁以上の数字（正典3.1）
ID_FRAGMENT = r"T-\d{3,}"
ID_PATTERN = re.compile(rf"^{ID_FRAGMENT}$")  # 全体一致（front matter の id・--deps の各要素）
# Beads 方式でトラッカーが github なら、タスクID は Issue 番号の `GH-<n>`（ゼロ埋めしない。正典「Beads 方式」）。
GH_ID_FRAGMENT = r"GH-\d+"
# Jira のキー（`PROJ-123`。プロジェクトキーは2文字以上・英大文字始まり）。
JIRA_ID_FRAGMENT = r"[A-Z][A-Z0-9_]+-\d+"
ANY_ID_FRAGMENT = rf"(?:{ID_FRAGMENT}|{GH_ID_FRAGMENT}|{JIRA_ID_FRAGMENT})"
ANY_ID_PATTERN = re.compile(rf"^{ANY_ID_FRAGMENT}$")  # Beads 方式の --deps・retrospect の引数
ID_SEARCH_PATTERN = re.compile(rf"\b{ANY_ID_FRAGMENT}\b")  # 文中から拾う（コミット件名・トランスクリプト）
HISTORY_HEADING_PATTERN = re.compile(rf"^## ({ID_FRAGMENT})\b", re.MULTILINE)  # docs/history/tasks.md の見出し

# 作業ブランチの接頭辞（`claim` が切る `feature/T-xxx`。正典「ファイル配置と設定ファイル」6.1）
FEATURE_BRANCH_PREFIX = "feature/"
FEATURE_BRANCH_PATTERN = re.compile(rf"{FEATURE_BRANCH_PREFIX}({ANY_ID_FRAGMENT})")

# --- 設定（`.tw/config.toml`。正典「ファイル配置と設定ファイル」） ---------------

CONFIG_PATH = ".tw/config.toml"

STORE_FILES = "files"
STORE_BEADS = "beads"
STORE_VALUES = (STORE_FILES, STORE_BEADS)
TRACKER_VALUES = ("なし", "github", "jira")
BRANCH_VALUES = ("既定", "作業ブランチを切る", "切らない")
DEFAULT_BRANCH = "既定"
NO_COMMAND = "なし"
DEFAULT_ROOT = ".tw"

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
    "root",
)
GITHUB_PROJECT_PATTERN = re.compile(r"([^/\s]+)/(\d+)")


class ConfigError(RuntimeError):
    """設定が読めない（呼ぶ側が `INVALID`・終了コード3にする）。"""


@dataclass(frozen=True)
class Config:
    """解けた設定。`source` は読んだファイル（`CONFIG_PATH`・`AGENTS.md`・`CLAUDE.md`。どれも無ければ `None`）。

    `written` は設定に書いてあったキー。
    """

    source: str | None
    verify: str | None = None
    verify_before_ship: str | None = None
    format: str | None = None
    branch: str = DEFAULT_BRANCH
    base_branch: str | None = None
    store: str = STORE_FILES
    tracker: str = "なし"
    github_project: str | None = None
    backup: str | None = None
    hook_tally: str | None = None
    root: str = DEFAULT_ROOT
    written: frozenset = frozenset()

    @property
    def legacy(self) -> bool:
        return self.source in CONFIG_FILENAMES

    @property
    def project_owner_number(self) -> tuple[str, str] | None:
        m = GITHUB_PROJECT_PATTERN.fullmatch(self.github_project or "")
        return (m.group(1), m.group(2)) if m else None


_config_cache: dict[str, tuple[tuple, Config]] = {}


def read_config(toplevel: str) -> Config:
    """`toplevel` の設定。`.tw/config.toml` が無く `develop/direction.md` があれば旧い「## タスク運用」節を
    写して読み、根は `develop`。どちらも無ければ既定の `Config(source=None)`。

    根ごとに覚え、設定のファイルが変わっていれば読み直す。読めなければ `ConfigError`。
    """
    root = os.path.realpath(toplevel)
    stamp = tuple(_mtime(os.path.join(root, p)) for p in (CONFIG_PATH, LEGACY_DIRECTION_PATH, *CONFIG_FILENAMES))
    cached = _config_cache.get(root)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    path = os.path.join(root, CONFIG_PATH)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            config = _config_from_values(CONFIG_PATH, parse_config_text(f.read(), CONFIG_PATH))
    elif os.path.exists(os.path.join(root, LEGACY_DIRECTION_PATH)):
        config = dataclasses.replace(_config_from_legacy_section(root), root=LEGACY_ROOT)
    else:
        config = Config(source=None)
    _config_cache[root] = (stamp, config)
    return config


def task_dir(toplevel: str) -> str:
    """タスクファイルの置き場（`toplevel` からの相対）。"""
    return posixpath.join(read_config(toplevel).root, "task")


def direction_path(toplevel: str) -> str:
    return posixpath.join(read_config(toplevel).root, "direction.md")


def draft_dir(toplevel: str) -> str:
    return posixpath.join(read_config(toplevel).root, "draft")


def stranded_legacy_places(toplevel: str, config: Config) -> list[tuple[str, str]]:
    """`.tw/config.toml` があり根が `develop` でないのに `develop/` に残っている置き場の `(元, 先)`（先が既にあっても返す）。"""
    if config.source != CONFIG_PATH or config.root == LEGACY_ROOT:
        return []
    names = ["direction.md", "draft"] + (["task"] if config.store == STORE_FILES else [])
    return [
        (posixpath.join(LEGACY_ROOT, n), posixpath.join(config.root, n))
        for n in names
        if os.path.lexists(os.path.join(toplevel, LEGACY_ROOT, n))
    ]


def _mtime(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


_LINE_PATTERN = re.compile(r"([A-Za-z0-9_-]+)[ \t]*=[ \t]*(.*)")


def parse_config_text(text: str, label: str) -> list[tuple[int, str, str]]:
    """TOML の部分集合（空行・`# コメント`・`key = "値"`／`key = '値'`）を `(行番号, キー, 値)` にする。"""
    entries: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_PATTERN.fullmatch(line)
        if m is None:
            raise ConfigError(f"{label}:{lineno}: `key = \"値\"` の形でない: {line}")
        key, rest = m.group(1), m.group(2)
        if key not in CONFIG_KEYS:
            raise ConfigError(f"{label}:{lineno}: 知らないキー {key!r}")
        if key in seen:
            raise ConfigError(f"{label}:{lineno}: キー {key!r} が {seen[key]} 行目にもある")
        seen[key] = lineno
        entries.append((lineno, key, _parse_string(rest, f"{label}:{lineno}")))
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
    if "root" in values:
        values["root"] = _check_root(values["root"] or "", f"{source}:{lines['root']}")
    return _build_config(source, values)


def _check_root(value: str, where: str) -> str:
    if value.startswith("/") or os.path.isabs(value):
        raise ConfigError(f"{where}: root はリポジトリの根からの相対パス: {value}")
    if ".." in value.split("/"):
        raise ConfigError(f"{where}: root に .. を含められない: {value}")
    root = posixpath.normpath(value)
    if root == ".":
        raise ConfigError(f"{where}: root にリポジトリの根そのものは使えない: {value}")
    for reserved in (".git", LOCAL_DIR):
        if root == reserved or root.startswith(reserved + "/"):
            raise ConfigError(f"{where}: root に {reserved} とその下は使えない: {value}")
    return root


def _build_config(source: str | None, values: dict[str, str | None]) -> Config:
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
        root=values.get("root") or defaults.root,
        written=frozenset(values),
    )
    if config.github_project is not None and config.project_owner_number is None:
        raise ConfigError(f"{source}: github_project の値 {config.github_project!r} が \"<owner>/<番号>\" の形でない")
    if config.tracker == "github" and config.github_project is None:
        raise ConfigError(f"{source}: tracker = \"github\" には github_project = \"<owner>/<番号>\" が要る")
    return config


# --- 旧い「## タスク運用」節（`.tw/config.toml` が無いプロジェクトの互換） -----------

CONFIG_FILENAMES = ("AGENTS.md", "CLAUDE.md")
TASK_SECTION_HEADING = "## タスク運用"
_LEGACY_STORE_FILES = "develop/task"


def has_task_section(text: str) -> bool:
    """本文に `## タスク運用` の見出し行があるか（行頭一致）。"""
    return any(line.startswith(TASK_SECTION_HEADING) for line in text.splitlines())


def find_legacy_section(root: str) -> tuple[str, str] | None:
    """`## タスク運用` 節を持つファイルを `AGENTS.md` → `CLAUDE.md` の順で探し、`(パス, 中身)` を返す。

    両方に節があれば `ConfigError`。どちらにも無ければ `None`。
    """
    hits: list[tuple[str, str]] = []
    for name in CONFIG_FILENAMES:
        path = os.path.normpath(os.path.join(root, name))
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if has_task_section(text):
            hits.append((path, text))
    if len(hits) > 1:
        raise ConfigError("AGENTS.md と CLAUDE.md の両方に「## タスク運用」節がある")
    return hits[0] if hits else None


def _legacy_lines(text: str) -> dict[str, str]:
    """節の中の `- <ラベル>: <値>` を、ラベルごとに最初の1行だけ拾う。"""
    found: dict[str, str] = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.startswith(TASK_SECTION_HEADING)
            continue
        m = re.match(r"- ([^:]+):(.*)$", line) if in_section else None
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2).strip()
    return found


LEGACY_LABELS = {
    "検証コマンド": "verify",
    "送る前の検証コマンド": "verify_before_ship",
    "整形コマンド": "format",
    "規則の発火の集計": "hook_tally",
    "ブランチ": "branch",
    "主ブランチ": "base_branch",
    "バックアップ": "backup",
    "GitHub Project": "github_project",
    "タスクの置き場": "store",
    "トラッカー": "tracker",
}


@dataclass(frozen=True)
class LegacyLine:
    """節の中の知っているラベルの `- <ラベル>: <値>` 行。`start`〜`end`（含まない）が続きの字下げ行までの行番号（0始まり）。"""

    key: str
    value: str
    continuation: tuple[str, ...]
    start: int
    end: int


def legacy_section_lines(text: str) -> list[LegacyLine]:
    """「## タスク運用」節の、知っているラベルの行（ラベルごとに最初の1行）。"""
    lines = text.splitlines()
    found: list[LegacyLine] = []
    seen: set[str] = set()
    in_section = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            in_section = line.startswith(TASK_SECTION_HEADING)
            i += 1
            continue
        m = re.match(r"- ([^:]+):(.*)$", line) if in_section else None
        if m is None or m.group(1) not in LEGACY_LABELS or m.group(1) in seen:
            i += 1
            continue
        seen.add(m.group(1))
        end = i + 1
        while end < len(lines) and lines[end][:1] in (" ", "\t") and lines[end].strip():
            end += 1
        found.append(LegacyLine(LEGACY_LABELS[m.group(1)], m.group(2).strip(),
                                tuple(l.strip() for l in lines[i + 1 : end]), i, end))
        i = end
    return found


def _legacy_word(value: str) -> str:
    quoted = re.search(r"`([^`]+)`", value)
    word = quoted.group(1).strip() if quoted else (value.split() or [""])[0]
    return re.split(r"[（(、。]", word)[0].strip("`").strip()


def _legacy_command(value: str) -> str | None:
    if value.startswith(NO_COMMAND):
        return None
    m = re.search(r"`([^`]+)`", value)
    return m.group(1) if m else None


def _config_from_legacy_section(root: str) -> Config:
    found = find_legacy_section(root)
    if found is None:
        return Config(source=None)
    path, text = found
    lines = _legacy_lines(text)
    values: dict[str, str | None] = {}
    for label, key in (
        ("検証コマンド", "verify"),
        ("送る前の検証コマンド", "verify_before_ship"),
        ("整形コマンド", "format"),
        ("規則の発火の集計", "hook_tally"),
    ):
        if label in lines:
            values[key] = _legacy_command(lines[label])
    if "ブランチ" in lines:
        value = lines["ブランチ"]
        values["branch"] = next((w for w in BRANCH_VALUES if value.startswith(w)), None) or (value.split() or [""])[0]
    for label, key in (("主ブランチ", "base_branch"), ("バックアップ", "backup"), ("GitHub Project", "github_project")):
        if label in lines:
            values[key] = _legacy_word(lines[label]) or None
    if "タスクの置き場" in lines:
        word = _legacy_word(lines["タスクの置き場"])
        store = {_LEGACY_STORE_FILES: STORE_FILES, STORE_BEADS: STORE_BEADS}.get(word)
        if store is None:
            raise ConfigError(f"- タスクの置き場: の値 {word!r} を機械が読めない（{_LEGACY_STORE_FILES} / {STORE_BEADS}）")
        values["store"] = store
    if lines.get("トラッカー"):
        word = _legacy_word(lines["トラッカー"])
        if word not in TRACKER_VALUES:
            raise ConfigError(f"- トラッカー: の値 {word!r} を機械が読めない（{' / '.join(TRACKER_VALUES)}）")
        values["tracker"] = word
    return _build_config(os.path.basename(path), values)
