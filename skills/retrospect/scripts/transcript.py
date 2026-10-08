"""サブエージェントのトランスクリプトを見つけ、そこから**数だけ**を取り出す。

Claude Code は作業ディレクトリのパスの英数字以外を `-` に潰した名前で
`<設定ディレクトリ>/projects/<slug>/<セッション>/subagents/<エージェント>.jsonl` に置く
（設定ディレクトリは `CLAUDE_CONFIG_DIR` が設定されていればそこ、無ければ `~/.claude`）。

**会話の中身をここから外へ出さない。** 返すのはツール名・ファイルパス・コマンドの先頭2語・
件数・時刻だけ（CLAUDE.md「会話内容の扱い」）。本文を返す関数をここに足さない。
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

_TASK_WORKFLOW_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "task-workflow", "scripts")
)
if _TASK_WORKFLOW_SCRIPTS not in sys.path:
    sys.path.insert(0, _TASK_WORKFLOW_SCRIPTS)

import layout  # noqa: E402

TASK_ID = layout.ID_SEARCH_PATTERN


def subagent_dirs(root: str) -> list[str]:
    """このリポジトリの `subagents/` ディレクトリ。無ければ空（材料が1つ減るだけ）。"""
    base = project_dir(root)
    if base is None:
        return []
    found = []
    for entry in sorted(os.listdir(base)):
        sub = os.path.join(base, entry, "subagents")
        if os.path.isdir(sub):
            found.append(sub)
    return found


def project_dir(root: str) -> str | None:
    """`<設定ディレクトリ>/projects/` の下のこのリポジトリのディレクトリ。

    設定ディレクトリは `CLAUDE_CONFIG_DIR` → `~/.claude` の順に見て、`projects/` が
    実在する最初の1つだけを使う（どちらにも無ければ None）。その中では、潰し方は版によって
    変わりうるので、**計算した名前で当ててから、外れたら総当たりで照合する**（見つからなければ
    None を返して先へ進む）。
    """
    projects = _projects_dir()
    if projects is None:
        return None
    want = _slug(os.path.abspath(root))
    direct = os.path.join(projects, want)
    if os.path.isdir(direct):
        return direct
    for entry in os.listdir(projects):
        if _slug(entry) == want and os.path.isdir(os.path.join(projects, entry)):
            return os.path.join(projects, entry)
    return None


def _projects_dir() -> str | None:
    """`CLAUDE_CONFIG_DIR` → `~/.claude` の順で `projects/` が実在する先を1つ返す。"""
    for config_dir in _config_dir_candidates():
        projects = os.path.join(config_dir, "projects")
        if os.path.isdir(projects):
            return projects
    return None


def _config_dir_candidates() -> list[str]:
    candidates = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        candidates.append(os.path.expanduser(env))
    candidates.append(os.path.join(os.path.expanduser("~"), ".claude"))
    return candidates


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def find_transcripts(root: str, task_id: str) -> list[str]:
    """先頭の数行で**最初に出てくるタスクID**が `task_id` の jsonl だけを拾う（mtime では当てにいかない）。

    委譲の指示は自分のタスクIDから書き始めるので、最初のIDがそのタスク。後ろで依存や後続として
    名前を挙げただけの別のタスクのトランスクリプトは拾わない。
    """
    hits = []
    for sub in subagent_dirs(root):
        for name in sorted(os.listdir(sub)):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(sub, name)
            first = TASK_ID.search(_head_text(path))
            if first is not None and first.group(0) == task_id:
                hits.append(path)
    return hits


def _head_text(path: str, lines: int = 3) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(next(f, "") for _ in range(lines))
    except OSError:
        return ""


def _repo_key(fp: str, root: str) -> str:
    """`fp` を `root` から見た相対パスにする（別ディレクトリの同名ファイルを合算しないため）。

    `root` の外（`/private/tmp/...` など）や別ドライブで relpath が組めない場合は、
    落とさずに `fp` をそのまま返す。
    """
    try:
        rel = os.path.relpath(fp, root)
    except ValueError:
        return fp
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return fp
    return rel


def _seconds_between(start: str, end: str) -> float | None:
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return (b - a).total_seconds()
    except (ValueError, TypeError):
        return None


def read_signals(path: str, root: str) -> dict | None:
    """ツール名・ファイルパス・コマンドの先頭2語・件数・時刻だけを数える。

    `files` の集計キーは `root` から見たリポジトリ相対パス（`_repo_key` 参照）。
    `slow_calls` は呼び出しから結果までの秒数の長い3件（道具名・Bash の description の先頭40字・秒数）。
    """
    pending: dict[str, tuple[str, str, str]] = {}
    waits: list[tuple[str, str, float]] = []
    tools: Counter[str] = Counter()
    files: Counter[str] = Counter()
    commands: Counter[str] = Counter()
    calls = errors = out_tokens = 0
    first = last = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                ts = o.get("timestamp") or ""
                if ts:
                    first = first or ts
                    last = ts
                msg = o.get("message") or {}
                content = msg.get("content")
                blocks = content if isinstance(content, list) else []
                if o.get("type") == "assistant":
                    out_tokens += int((msg.get("usage") or {}).get("output_tokens") or 0)
                    for b in blocks:
                        if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                            continue
                        calls += 1
                        name = str(b.get("name") or "?")
                        tools[name] += 1
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        if b.get("id") and ts:
                            desc = str(inp.get("description") or "")[:40] if name == "Bash" else ""
                            pending[str(b["id"])] = (name, desc, ts)
                        if name in ("Edit", "Write", "NotebookEdit"):
                            fp = str(inp.get("file_path") or "")
                            if fp:
                                files[_repo_key(fp, root)] += 1
                        elif name == "Bash":
                            head = " ".join(str(inp.get("command") or "").split()[:2])
                            if head:
                                commands[head] += 1
                elif o.get("type") == "user":
                    for b in blocks:
                        if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                            continue
                        if b.get("is_error"):
                            errors += 1
                        started = pending.pop(str(b.get("tool_use_id")), None)
                        if started and ts:
                            secs = _seconds_between(started[2], ts)
                            if secs is not None and secs >= 0:
                                waits.append((started[0], started[1], secs))
    except OSError:
        return None
    return {
        "elapsed": f"{first[:19]} → {last[:19]}" if first else "不明",
        "tool_calls": calls,
        "errors": errors,
        "tools": tools.most_common(8),
        "rewrites": [(k, v) for k, v in files.most_common(5) if v >= 2],
        "commands": [(k, v) for k, v in commands.most_common(6) if v >= 2],
        "command_counts": dict(commands),
        "output_tokens": out_tokens,
        "slow_calls": sorted(waits, key=lambda w: -w[2])[:3],
    }
