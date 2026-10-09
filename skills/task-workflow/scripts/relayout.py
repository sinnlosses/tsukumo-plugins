"""`tw migrate-layout`: 旧配置（`develop/` と「## タスク運用」節、`.tw/` 直下の控え）を `.tw/` の形へ移す。

正典は task-workflow の WORKFLOW.md「旧形式からの移行」。`git add` まででコミットしない。
`.beads` と共有の台帳には触らない。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

import beads
import layout
import ledger

CONFIG_HEADER = "# タスク運用の設定（tw が読む）\n"
# `.tw/` 直下にあってよいもの（コミットするものと控えの置き場）
_LEGACY_STORE_FILES = "develop/task"
_TW_KNOWN = (".gitignore", "config.toml", "local", "direction.md", "draft", "task")


@dataclass
class Outcome:
    kind: str  # DIRTY / BUSY / NOTHING / PLAN / MIGRATED / INVALID
    lines: list[str] = field(default_factory=list)


def migrate_layout(toplevel: str, dry_run: bool) -> Outcome:
    if not ledger.is_clean(cwd=toplevel):
        return Outcome("DIRTY")
    has_config = os.path.exists(os.path.join(toplevel, layout.CONFIG_PATH))
    config = layout.read_config(toplevel) if has_config else config_from_legacy_section(toplevel)
    busy = _busy(toplevel, config.store)
    if busy:
        return Outcome("BUSY", busy)

    if has_config:
        candidates = layout.stranded_legacy_places(toplevel, config)
    else:
        names = ["direction.md", "draft"] + (["task"] if config.store == layout.STORE_FILES else [])
        candidates = [(f"{layout.LEGACY_ROOT}/{n}", f"{layout.TW_DIR}/{n}") for n in names]
    moves: list[tuple[str, str]] = []
    for src, dst in candidates:
        if not _tracked(toplevel, src):
            continue
        if os.path.lexists(os.path.join(toplevel, dst)):
            return Outcome("INVALID", [f"INVALID\t{dst} が既にある（{src} を移せない）"])
        moves.append((src, dst))

    lines: list[str] = []
    state = ledger.legacy_state_entries(os.path.join(toplevel, layout.TW_DIR))
    lines += [f"MOVE\t{layout.TW_DIR}/{n}\t{layout.LOCAL_DIR}/{n}" for n in state]
    ignore_now = _read(os.path.join(toplevel, layout.TW_GITIGNORE_PATH))
    write_ignore = ignore_now is None or ignore_now in layout.OLD_TW_GITIGNORES
    if write_ignore:
        lines.append(f"WRITE\t{layout.TW_GITIGNORE_PATH}")
    elif ignore_now != layout.TW_GITIGNORE:
        lines.append(f"KEPT\t{layout.TW_GITIGNORE_PATH}")
    lines += [f"MOVE\t{src}\t{dst}" for src, dst in moves]

    config_text = section_path = section_text = None
    if not has_config:
        found = layout.find_legacy_section(toplevel)
        config_text = config_toml(config, found)
        lines.append(f"WRITE\t{layout.CONFIG_PATH}")
        if found is not None:
            section_path = os.path.relpath(found[0], toplevel)
            section_text = strip_section(found[1])
            lines.append(f"KEPT_PROSE\t{section_path}")
    if moves or not has_config:
        lines += [f"LEFTOVER\t{p}" for p in _develop_leftovers(toplevel, {src for src, _ in moves})]
    lines += [f"LEFTOVER\t{layout.TW_DIR}/{n}" for n in _tw_leftovers(toplevel, state)]

    if not any(l.split("\t", 1)[0] in ("MOVE", "WRITE") for l in lines):
        return Outcome("NOTHING", ["NOTHING\t.tw/ は移し終えている"])
    if dry_run:
        return Outcome("PLAN", lines)

    _move_state(toplevel, state)
    if write_ignore:
        _write(os.path.join(toplevel, layout.TW_GITIGNORE_PATH), layout.TW_GITIGNORE)
    for src, dst in moves:
        os.makedirs(os.path.join(toplevel, os.path.dirname(dst)), exist_ok=True)
        _git(toplevel, ["mv", src, dst])
    staged = [layout.TW_GITIGNORE_PATH] if write_ignore or ignore_now == layout.TW_GITIGNORE else []
    if config_text is not None:
        _write(os.path.join(toplevel, layout.CONFIG_PATH), config_text)
        staged.append(layout.CONFIG_PATH)
    if section_path is not None and section_text is not None:
        _write(os.path.join(toplevel, section_path), section_text)
        staged.append(section_path)
    if staged:
        _git(toplevel, ["add", "--", *staged])
    return Outcome("MIGRATED", lines)


def _legacy_lines(text: str) -> dict[str, str]:
    """節の中の `- <ラベル>: <値>` を、ラベルごとに最初の1行だけ拾う。"""
    found: dict[str, str] = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.startswith(layout.TASK_SECTION_HEADING)
            continue
        m = re.match(r"- ([^:]+):(.*)$", line) if in_section else None
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2).strip()
    return found


def _legacy_word(value: str) -> str:
    quoted = re.search(r"`([^`]+)`", value)
    word = quoted.group(1).strip() if quoted else (value.split() or [""])[0]
    return re.split(r"[（(、。]", word)[0].strip("`").strip()


def _legacy_command(value: str) -> str | None:
    if value.startswith(layout.NO_COMMAND):
        return None
    m = re.search(r"`([^`]+)`", value)
    return m.group(1) if m else None


def config_from_legacy_section(root: str) -> layout.Config:
    found = layout.find_legacy_section(root)
    if found is None:
        return layout.Config(source=None)
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
        values["branch"] = next((w for w in layout.BRANCH_VALUES if value.startswith(w)), None) or (value.split() or [""])[0]
    for label, key in (("主ブランチ", "base_branch"), ("バックアップ", "backup"), ("GitHub Project", "github_project")):
        if label in lines:
            values[key] = _legacy_word(lines[label]) or None
    if "タスクの置き場" in lines:
        word = _legacy_word(lines["タスクの置き場"])
        store = {_LEGACY_STORE_FILES: layout.STORE_FILES, layout.STORE_BEADS: layout.STORE_BEADS}.get(word)
        if store is None:
            raise layout.ConfigError(f"- タスクの置き場: の値 {word!r} を機械が読めない（{_LEGACY_STORE_FILES} / {layout.STORE_BEADS}）")
        values["store"] = store
    if lines.get("トラッカー"):
        word = _legacy_word(lines["トラッカー"])
        if word not in layout.TRACKER_VALUES:
            raise layout.ConfigError(f"- トラッカー: の値 {word!r} を機械が読めない（{' / '.join(layout.TRACKER_VALUES)}）")
        values["tracker"] = word
    return layout.build_config(os.path.basename(path), values)


def _busy(toplevel: str, store: str) -> list[str]:
    here = os.path.realpath(toplevel)
    found: list[str] = []
    root = ledger.ledger_root(cwd=toplevel)
    for task_id in ledger.list_claims(root):
        worktree = (ledger.read_owner(ledger.claim_dir(root, task_id)) or {}).get("worktree", "?")
        if os.path.realpath(worktree) != here:
            found.append(f"BUSY\t{task_id}\t{worktree}")
    if store == layout.STORE_BEADS and beads.is_initialized(toplevel):
        me = os.path.basename(toplevel)
        for issue in beads.list_issues(toplevel):
            if issue.status == "in_progress" and issue.assignee != me:
                found.append(f"BUSY\t{beads.to_task_id(issue.bd_id)}\t{issue.assignee or '?'}")
    return found


def config_toml(config: layout.Config, found: tuple[str, str] | None) -> str:
    """旧い節（`found` は `layout.find_legacy_section` の結果）の値から `.tw/config.toml` の中身を組む（`root` は書かない）。"""
    notes = {l.key: _notes(l) for l in layout.legacy_section_lines(found[1])} if found else {}
    out = [CONFIG_HEADER]
    for key in layout.CONFIG_KEYS:
        if key == "root":
            continue
        value = getattr(config, key)
        if key == "verify":
            value = value or layout.NO_COMMAND
        elif key not in config.written or value is None:
            continue
        out += [f"# {n}\n" for n in notes.get(key, ())]
        out.append(f"{key} = {_toml_string(value)}\n")
    return "".join(out)


def _notes(line: layout.LegacyLine) -> list[str]:
    value = line.value
    quoted = re.search(r"`[^`]*`", value)
    if quoted:
        rest = (value[: quoted.start()] + value[quoted.end() :]).strip()
    else:
        word = re.split(r"[\s（(、。]", value, maxsplit=1)[0]
        rest = value[len(word) :].strip()
    return [n for n in (rest, *line.continuation) if n]


def strip_section(text: str) -> str:
    """節から知っているラベルの行と続きの字下げ行だけを消す（見出しと文章は残す）。"""
    drop: set[int] = set()
    for l in layout.legacy_section_lines(text):
        drop.update(range(l.start, l.end))
    lines = text.splitlines(keepends=True)
    return "".join(line for i, line in enumerate(lines) if i not in drop)


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _develop_leftovers(toplevel: str, moved: set[str]) -> list[str]:
    develop = os.path.join(toplevel, layout.LEGACY_ROOT)
    left: list[str] = []
    for dirpath, dirnames, filenames in os.walk(develop):
        rel_dir = os.path.relpath(dirpath, toplevel).replace(os.sep, "/")
        dirnames[:] = [d for d in dirnames if f"{rel_dir}/{d}" not in moved]
        left += [f"{rel_dir}/{f}" for f in filenames if f"{rel_dir}/{f}" not in moved]
    return sorted(left)


def _tw_leftovers(toplevel: str, state: list[str]) -> list[str]:
    tw_dir = os.path.join(toplevel, layout.TW_DIR)
    if not os.path.isdir(tw_dir):
        return []
    return sorted(
        n for n in os.listdir(tw_dir)
        if n not in _TW_KNOWN and n not in state and not _tracked(toplevel, f"{layout.TW_DIR}/{n}")
    )


def _move_state(toplevel: str, state: list[str]) -> None:
    if not state:
        return
    local = os.path.join(toplevel, layout.LOCAL_DIR)
    if not os.path.isdir(local):
        ledger.worktree_state_dir(toplevel)
        return
    tw_dir = os.path.join(toplevel, layout.TW_DIR)
    for name in state:
        if not os.path.lexists(os.path.join(local, name)):
            shutil.move(os.path.join(tw_dir, name), os.path.join(local, name))


def _tracked(toplevel: str, path: str) -> bool:
    r = subprocess.run(["git", "ls-files", "--", path], cwd=toplevel, capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() != ""


def _git(toplevel: str, args: list[str]) -> None:
    r = subprocess.run(["git", *args], cwd=toplevel, capture_output=True, text=True)
    if r.returncode != 0:
        raise ledger.GitCommandError(f"git {' '.join(args)} が失敗（{r.returncode}）: {r.stderr.strip()}")


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
