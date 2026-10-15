"""`tw config --schema`（欄の形を出す）・`tw config set`（検査してから `.tw/config.toml` に書く）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import gitinfo
import layout


def _step_candidates(project_root: str | None) -> list[layout.StepCandidate]:
    return layout.step_candidates(project_root, os.path.expanduser("~"), layout.own_plugin_root())


def steps_default(toplevel: str) -> list[str]:
    """`steps` を書いていないときの並び（schema 2 の `default`）。"""
    return [c.value for c in _step_candidates(toplevel) if c.own]


def cmd_config_schema(version: str | None = None) -> None:
    """欄の形を1行の JSON で出す（読むだけ。設定にはよらない）。

    `version` を省くと schema 1、`2` なら schema 2（並びの欄。候補は作業ツリー・`~`・`tw` と同じ plugin から走査する）。
    知らない版は stderr に理由を出して終了コード2。
    """
    if version is None or version == "1":
        schema = layout.config_schema()
    elif version == "2":
        try:
            project_root = gitinfo.git_toplevel()
        except gitinfo.GitCommandError:
            project_root = None
        schema = layout.config_schema_v2(project_root, os.path.expanduser("~"), layout.own_plugin_root())
    else:
        known = " / ".join(str(v) for v in layout.SCHEMA_VERSIONS)
        print(f"tw config --schema: 知らない版 {version!r}（{known} のどれか）", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(schema, ensure_ascii=False))


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
            allowed = {c.value for c in _step_candidates(toplevel)} | set(config.steps or ())
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
