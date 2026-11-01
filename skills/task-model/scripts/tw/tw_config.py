"""`tw config --schema`（欄の形を出す）・`tw config set`（検査してから `.tw/config.toml` に書く）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import gitinfo
import layout


def step_candidates(project_root: str | None, flow: str = layout.FLOW_SKILLS) -> list[layout.StepCandidate]:
    """並びの候補。`flow` が `script` なら台本の段、`skills` なら `workflow-step` の印のあるスキル。"""
    if flow == layout.FLOW_SCRIPT:
        return layout.script_step_candidates()
    return layout.step_candidates(project_root, os.path.expanduser("~"), layout.own_plugin_root())


def steps_default(toplevel: str, flow: str = layout.FLOW_SKILLS) -> list[str]:
    """`steps` を書いていないときの並び（schema 2 の `default`）。"""
    return layout.default_steps(step_candidates(toplevel, flow))


def skill_steps(toplevel: str, config: layout.Config) -> list[str]:
    """スキルの全名で読む側（`tw brief`・`tw plan-phases`）の並び。`flow` が `script` のとき、または `skills` でも台本の段の短い名前で書いてあるとき（`script` で書いたまま切り戻したとき）は、スキルの全名に読み替えて送り出しの段を末尾に足す。"""
    script_names = {name for name, _, _ in layout.SCRIPT_STEPS}
    script_written = config.steps is not None and any(s in script_names for s in config.steps)
    if config.flow != layout.FLOW_SCRIPT and not script_written:
        return list(config.steps) if config.steps is not None else steps_default(toplevel)
    wanted = layout.script_steps(config.steps) if config.steps is not None else steps_default(toplevel, layout.FLOW_SCRIPT)
    candidates = layout.step_candidates(toplevel, os.path.expanduser("~"), layout.own_plugin_root())
    by_name: dict[str, str] = {}
    for c in sorted(candidates, key=lambda c: not c.own):
        by_name.setdefault(layout.step_short_name(c.value), c.value)
    return [by_name[n] for n in [*wanted, "ship"] if n in by_name]


def _toplevel_or_none() -> str | None:
    try:
        return gitinfo.git_toplevel()
    except gitinfo.GitCommandError:
        return None


def _flow_or_default(toplevel: str | None) -> str:
    if toplevel is None:
        return layout.FLOW_SKILLS
    try:
        return layout.read_config(toplevel).flow
    except layout.ConfigError:
        return layout.FLOW_SKILLS


def cmd_config_schema() -> None:
    """欄の形（schema 2。流れ・並び・シェル書き込みの判定・コマンド型6欄・ブランチの扱い）を1行の JSON で出す（読むだけ）。候補は作業ツリー・`~`・`tw` と同じ plugin から走査し、設定の `flow` が `script` なら並びの候補は台本の段。"""
    toplevel = _toplevel_or_none()
    schema = layout.config_schema(toplevel, os.path.expanduser("~"), layout.own_plugin_root(), _flow_or_default(toplevel))
    print(json.dumps(schema, ensure_ascii=False))


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
            allowed = {c.value for c in step_candidates(toplevel, config.flow)} | set(config.steps or ())
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
