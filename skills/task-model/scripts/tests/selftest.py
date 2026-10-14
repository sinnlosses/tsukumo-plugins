#!/usr/bin/env python3
"""`init.py` の自己テスト。

使い方: python3 selftest.py

標準ライブラリと `bd` で動く（`init.py` は設定ファイルがあれば `bd init` を打つ）。`bd` は一時の HOME で打つ。
落ちたら非0で終わる。`task.py` 一式は `selftest_task.py` が見る。

**ここで守っているのは「モデルが誤読しない出力を返すこと」**。
`INVALID` と traceback の区別、既存ファイルを上書きしないこと
といった、間違えると*静かに*データを失う／原因を取り違える経路を重点的に見る。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

failures: list[str] = []

# 一時の HOME に置く `bd` の設定（使用状況の送信を止める）。
_BD_CONFIG = "metrics:\n    disabled: true\n    notice_shown: true\nno-git-ops: true\n"


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def write(path: str, body: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def test_init() -> None:
    print("init.py")
    if shutil.which("bd") is None:
        check("bd が PATH にある（init.py は設定ファイルがあれば bd init を打つ）", False)
        return
    real_config = os.path.join(os.path.expanduser("~"), ".config", "bd", "config.yaml")
    before = _stat(real_config)
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as d:
        write(os.path.join(home, ".config", "bd", "config.yaml"), _BD_CONFIG)
        env = {
            **os.environ,
            "HOME": home,
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
        }
        env.pop("BEADS_ACTOR", None)
        env.pop("GITHUB_TOKEN", None)

        def run(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "..", "core", "init.py"), *args],
                capture_output=True,
                text=True,
                env=env,
            )

        cwd = os.getcwd()
        os.chdir(d)
        try:
            subprocess.run(["git", "init", "-q", "-b", "main"], check=True, capture_output=True)
            subprocess.run(["git", "config", "beads.role", "maintainer"], check=True, capture_output=True)
            r = run()
            check(".tw/.gitignore だけを作る（direction.md は作らない）",
                  r.stdout.splitlines()[:1] == ["CREATED\t.tw/.gitignore"]
                  and r.stdout.count("CREATED") == 1 and not os.path.exists(".tw/direction.md"), r.stdout)
            check("develop/ は作らない", not os.path.exists("develop"))
            check(".tw/.gitignore は local/ だけを外す", open(".tw/.gitignore", encoding="utf-8").read() == "local/\n")
            check("設定が無ければ config MISSING", "config\tMISSING\t.tw/config.toml" in r.stdout, r.stdout)
            check("設定が無ければ .beads を作らない", not os.path.exists(".beads"), r.stdout)

            check("draft/ は作らない", not os.path.exists(".tw/draft"))

            write(".tw/.gitignore", "local/\nx\n")
            r = run()
            check("2回目は上書きしない", "KEPT" in r.stdout and "CREATED" not in r.stdout
                  and open(".tw/.gitignore", encoding="utf-8").read() == "local/\nx\n", r.stdout)

            for old in ("*\n", "*\n!config.toml\n"):
                write(".tw/.gitignore", old)
                r = run()
                check(f"旧い .tw/.gitignore（{old!r}）は local/ に書き換えて UPDATED",
                      r.returncode == 0 and "UPDATED\t.tw/.gitignore\t" in r.stdout
                      and open(".tw/.gitignore", encoding="utf-8").read() == "local/\n", r.stdout)

            r = run("develop-dir")
            check("引数を渡すと終了コード2で何も作らない", r.returncode == 2 and not os.path.exists("develop-dir"), r.stdout + r.stderr)
            shutil.rmtree(".tw")

            write("develop/direction.md", "# 未対応の指示メモ\n\nこれをやって\n")
            write("develop/tasks.json", "[]\n")
            r = run()
            check("develop/direction.md・develop/tasks.json があっても止まらずに .tw/ を作る",
                  r.returncode == 0 and "CREATED\t.tw/.gitignore" in r.stdout
                  and "OLD_LAYOUT" not in r.stdout and "LEGACY" not in r.stdout, r.stdout)
            check("develop/ には触らない", open("develop/direction.md", encoding="utf-8").read() == "# 未対応の指示メモ\n\nこれをやって\n")
            shutil.rmtree(".tw")
            shutil.rmtree("develop")

            old_direction = "# 未対応の指示メモ\n\n## ユーザーから\nこれをやって\n"
            write(".tw/direction.md", old_direction)
            write(".tw/draft/2026-09-27-fix-a.md", "# a\n")
            r = run()
            check("旧い .tw/direction.md・.tw/draft/ が残っていても読まず・数えず・消さない",
                  r.returncode == 0 and "direction" not in r.stdout and "PENDING" not in r.stdout
                  and open(".tw/direction.md", encoding="utf-8").read() == old_direction
                  and os.path.exists(".tw/draft/2026-09-27-fix-a.md"), r.stdout)
            shutil.rmtree(".tw/draft")
            os.remove(".tw/direction.md")

            write(".tw/config.toml", 'format = "なし"\n')
            r = run()
            check("検証コマンドの行が無ければ config INVALID", "config\tINVALID\t.tw/config.toml" in r.stdout, r.stdout)

            write(".tw/config.toml", 'verify = "なし"\nbranch = "自分で切らない"\n')
            r = run()
            check("branch が語彙に無ければ config INVALID", "config\tINVALID\t.tw/config.toml" in r.stdout, r.stdout)

            write(".tw/config.toml", 'verify = "なし"\nstore = "files"\n')
            r = run()
            check('store = "files" は config INVALID で、.beads を作らない',
                  "config\tINVALID\t.tw/config.toml" in r.stdout and not os.path.exists(".beads"), r.stdout)

            write(".tw/config.toml", 'verify = "なし"\n')
            r = run()
            check(".tw/config.toml があればそれを読み、verify だけで OK", "config\tOK\t.tw/config.toml" in r.stdout, r.stdout)
            check("設定ファイルがあれば .beads を bd init で作る",
                  r.returncode == 0 and os.path.isdir(".beads") and "CREATED\t" in r.stdout and "-p t" in r.stdout, r.stdout + r.stderr)
            r = run()
            check("2回目は .beads を作り直さない", r.returncode == 0 and "KEPT\t" in r.stdout and ".beads" in r.stdout, r.stdout)
            os.remove(".tw/config.toml")

            r = run("--help")
            check("打ち間違いをディレクトリにしない", r.returncode == 2 and not os.path.exists("--help"))
        finally:
            os.chdir(cwd)
    check("利用者の ~/.config/bd/config.yaml に触れていない", _stat(real_config) == before, real_config)


def _stat(path: str) -> tuple[float, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime, st.st_size


def main() -> None:
    test_init()
    print()
    if failures:
        print(f"FAILED {len(failures)}件: " + ", ".join(failures))
        raise SystemExit(1)
    print("すべて通った")


if __name__ == "__main__":
    main()
