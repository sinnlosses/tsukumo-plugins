"""`tw config --schema`（欄の形を出す）・`tw config set`（検査してから `.tw/config.toml` に書く）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import layout


def cmd_config_schema() -> None:
    """欄の形を1行の JSON で出す（読むだけ。設定にも git にもよらない）。"""
    print(json.dumps(layout.config_schema(), ensure_ascii=False))


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
