"""`tw config --schema`（欄の形を出す）・`tw config set`（検査してから `.tw/config.toml` に書く）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import gitinfo
import layout


def script_steps(config: layout.Config) -> list[str]:
    """設定の `steps`（無ければ既定の並び）を、ワークフロースクリプトの段の名前にしたもの。"""
    return layout.script_steps(config.steps) if config.steps is not None else layout.default_steps()


def _toplevel_or_none() -> str | None:
    try:
        return gitinfo.git_toplevel()
    except gitinfo.GitCommandError:
        return None


def cmd_config_schema() -> None:
    """欄の形（schema 2。流れ・並び・シェル書き込みの判定・コマンド型6欄・ブランチの扱い）を1行の JSON で出す（読むだけ。設定にはよらない）。並びの候補はワークフロースクリプトの段。"""
    print(json.dumps(layout.config_schema(), ensure_ascii=False))


def cmd_config_entries() -> None:
    """入口にできるスキルを `{"entries": [...]}` の1行の JSON で出す（読むだけ。設定にはよらない）。"""
    entries = layout.config_entries(_toplevel_or_none(), os.path.expanduser("~"), layout.own_plugin_root())
    print(json.dumps(entries, ensure_ascii=False))


def cmd_config_set(toplevel: str, key: str, value: str) -> None:
    """`key` を `value` にして書く。書けないキー・値は stderr に理由を出して終了コード2（ファイルは変えない）。"""
    config = layout.read_config(toplevel)
    if config.source is None:
        print(f"MISSING\t{layout.CONFIG_PATH}")
        raise SystemExit(6)
    path = os.path.join(toplevel, layout.CONFIG_PATH)
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    try:
        new_text = layout.config_text_with(text, key, value)
        if key == layout.STEPS_KEY:
            items = json.loads(value)
            allowed = {c.value for c in layout.script_step_candidates()} | set(config.steps or ())
            unknown = [i for i in items if i not in allowed]
            if unknown:
                raise layout.ConfigValueError(f"steps の候補にも今の値にも無い名前: {', '.join(unknown)}")
            value = layout.steps_text(items)
    except layout.ConfigValueError as e:
        print(f"tw config set: {e}", file=sys.stderr)
        raise SystemExit(2)
    if new_text != text:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config.toml.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(new_text)
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    print(f"SET\t{key}\t{value}")
