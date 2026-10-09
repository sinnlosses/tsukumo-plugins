#!/usr/bin/env python3
"""`legacy.py`（旧形式の読み取り）と `init.py` の自己テスト。

使い方: python3 selftest.py

標準ライブラリだけで動く（このリポジトリに依存パッケージを増やさないため）。
落ちたら非0で終わる。`task.py` 一式は `selftest_task.py` が見る。

**ここで守っているのは「モデルが誤読しない出力を返すこと」**。
`INVALID` と traceback の区別、旧形式で骨組みを混ぜないこと、既存ファイルを上書きしないこと
といった、間違えると*静かに*データを失う／原因を取り違える経路を重点的に見る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import layout  # noqa: E402
import legacy  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(HERE, script), *args],
        capture_output=True,
        text=True,
    )


def write(path: str, body: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


# --- legacy: tasks.json の読み取り ------------------------------------------


def test_load_tasks() -> None:
    print("legacy.load_tasks")
    with tempfile.TemporaryDirectory() as d:
        ok, err = legacy.load_tasks(write(os.path.join(d, "a.json"), json.dumps([{"id": "T-001"}])))
        check("正しいファイルは (中身, None)", err is None and len(ok) == 1, str(err))

        _, err = legacy.load_tasks(write(os.path.join(d, "b.json"), "[{"))
        check("壊れた JSON は理由を返す（例外を投げない）", err is not None and "JSON" in err, str(err))

        _, err = legacy.load_tasks(write(os.path.join(d, "c.json"), '{"a":1}'))
        check("配列でなければ理由を返す", err is not None and "配列ではない" in err, str(err))

        _, err = legacy.load_tasks(write(os.path.join(d, "d.json"), "[1,2]"))
        check("要素がオブジェクトでなければ理由を返す", err is not None and "オブジェクト" in err, str(err))

        _, err = legacy.load_tasks(os.path.join(d, "無い.json"))
        check("開けないファイルも理由を返す", err is not None, str(err))


# --- legacy: progress.md の節分け -------------------------------------------


def test_progress_sections() -> None:
    print("legacy: progress.md の節")
    body = (
        "# 進捗\n\n## 完了したこと\n\n"
        "### 2026-03-03 c（T-003）\nccc\n\n"
        "### 2026-02-02 b（T-002）\nbbb\n\n"
        "### 2026-01-01 a（T-001）\naaa\n\n"
        "## 未解決\n\n- なし\n"
    )
    head, sections, tail = legacy.split_done_section(body)
    check("小節を3つに割る", sections is not None and len(sections) == 3)
    check("小節の日付を読む", [s.date for s in sections or []] == ["2026-03-03", "2026-02-02", "2026-01-01"])
    check("「未解決」以降は後ろに残す", tail.startswith("## 未解決"))
    check("見出しは前に残す", head.rstrip().endswith("## 完了したこと"))

    _, none_sections, _ = legacy.split_done_section("# 進捗\n\n## 未解決\n")
    check("「完了したこと」節が無ければ None", none_sections is None)

    _, mid, _ = legacy.split_named_section(body, "## 未解決")
    check("名前つきの節を切り出す", mid.startswith("## 未解決") and "- なし" in mid, mid)
    _, mid, _ = legacy.split_named_section(body, "## 注意")
    check("無い節は空文字", mid == "")

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "docs", "history", "progress.md")
        legacy.prepend_to_history(p, legacy.PROGRESS_ARCHIVE_HEADER, "### 2026-01-01 古い\n")
        legacy.prepend_to_history(p, legacy.PROGRESS_ARCHIVE_HEADER, "### 2026-02-02 新しい\n")
        text = open(p, encoding="utf-8").read()
        check(
            "履歴には新しいものを見出しの直後に差し込む",
            text.startswith(legacy.PROGRESS_ARCHIVE_HEADER) and text.index("新しい") < text.index("古い"),
            text,
        )


# --- layout.find_legacy_section（AGENTS.md → CLAUDE.md の順） ----------


def test_find_legacy_section() -> None:
    print("layout.find_legacy_section")
    section = "## タスク運用\n\n- 検証コマンド: `なし`\n- 整形コマンド: `なし`\n- ブランチ: 既定\n"

    with tempfile.TemporaryDirectory() as d:
        check("どちらも無ければ None", layout.find_legacy_section(d) is None)

        write(os.path.join(d, "AGENTS.md"), f"# x\n\n{section}")
        found = layout.find_legacy_section(d)
        check(
            "AGENTS.md だけなら AGENTS.md を設定とする",
            found is not None and os.path.basename(found[0]) == "AGENTS.md" and "検証コマンド" in found[1],
            str(found),
        )

        check("develop/direction.md が無ければ read_config は旧い節を読まない", layout.read_config(d).source is None)
        write(os.path.join(d, "develop", "direction.md"), "# x\n")
        check("develop/direction.md があれば旧い節を写して読む", layout.read_config(d).source == "AGENTS.md")

        write(os.path.join(d, "CLAUDE.md"), f"# y\n\n{section}")
        raised = False
        try:
            layout.find_legacy_section(d)
        except layout.ConfigError:
            raised = True
        check("両方に節があれば ConfigError", raised)


def test_init() -> None:
    print("init.py")
    with tempfile.TemporaryDirectory() as d:
        cwd = os.getcwd()
        os.chdir(d)
        try:
            r = run("init.py")
            check(".tw/direction.md と .tw/.gitignore だけを作る",
                  r.stdout.splitlines()[:2] == ["CREATED\t.tw/direction.md", "CREATED\t.tw/.gitignore"]
                  and r.stdout.count("CREATED") == 2, r.stdout)
            check("develop/ は作らない", not os.path.exists("develop"))
            check(".tw/.gitignore は local/ だけを外す", open(".tw/.gitignore", encoding="utf-8").read() == "local/\n")
            check("設定が無ければ config MISSING", "config\tMISSING\t.tw/config.toml" in r.stdout, r.stdout)

            created = open(".tw/direction.md", encoding="utf-8").read()
            check(
                "作った direction.md は「ユーザーから」の節だけを持ち、ドラフトの置き場を見出しに書く",
                "## ユーザーから" in created and "## エージェントのドラフト" not in created and ".tw/draft/" in created,
                created,
            )
            check("task/・draft/ は作らない", not os.path.exists(".tw/draft") and not os.path.exists(".tw/task"))

            write(".tw/.gitignore", "local/\nx\n")
            r = run("init.py")
            check("2回目は上書きしない", "KEPT" in r.stdout and "CREATED" not in r.stdout
                  and open(".tw/.gitignore", encoding="utf-8").read() == "local/\nx\n", r.stdout)
            check("まっさらなら OK", "OK: 未対応の指示は無い" in r.stdout, r.stdout)

            for old in ("*\n", "*\n!config.toml\n"):
                write(".tw/.gitignore", old)
                r = run("init.py")
                check(f"旧い .tw/.gitignore（{old!r}）は local/ に書き換えて UPDATED",
                      r.returncode == 0 and "UPDATED\t.tw/.gitignore\t" in r.stdout
                      and open(".tw/.gitignore", encoding="utf-8").read() == "local/\n", r.stdout)

            r = run("init.py", "develop-dir")
            check("引数を渡すと終了コード2で何も作らない", r.returncode == 2 and not os.path.exists("develop-dir"), r.stdout + r.stderr)
            shutil.rmtree(".tw")

            write("develop/direction.md", "# 未対応の指示メモ\n\nこれをやって\n")
            r = run("init.py")
            check("develop/direction.md があり .tw/config.toml が無ければ OLD_LAYOUT（終了コード5）で止まり、何も作らない",
                  r.returncode == 5 and r.stdout == "OLD_LAYOUT\ttw migrate-layout --dry-run\n" and not os.path.exists(".tw"), r.stdout)
            write(".tw/config.toml", 'verify = "なし"\n')
            r = run("init.py")
            check(".tw/config.toml があれば develop/direction.md が残っていても止まらない",
                  r.returncode == 0 and "OLD_LAYOUT" not in r.stdout, r.stdout)
            shutil.rmtree(".tw")
            shutil.rmtree("develop")

            write(".tw/direction.md", "# 未対応の指示メモ\n\nこれをやって\n")
            r = run("init.py")
            check("既にある .tw/direction.md は点検するだけで作り直さない",
                  r.stdout.startswith("KEPT\t.tw/direction.md\t"), r.stdout)
            check(
                "節が無いファイルは全体を「ユーザーから」とみなして PENDING",
                "PENDING:" in r.stdout and "ユーザーから1行" in r.stdout,
                r.stdout,
            )

            write(".tw/direction.md", "# 未対応の指示メモ\n\n## ユーザーから\nこれをやって\n")
            write(".tw/draft/2026-09-27-fix-a.md", "# a\n\n- 根拠: x\n- 出し先: y\n")
            write(".tw/draft/2026-09-27-fix-b.md", "# b\n")
            r = run("init.py")
            check(
                "ユーザーからは行数、ドラフトは draft/ のファイルの件数で数える",
                "ユーザーから1行" in r.stdout and "エージェントのドラフト2件" in r.stdout and "旧ドラフト節" not in r.stdout,
                r.stdout,
            )

            os.remove(".tw/draft/2026-09-27-fix-a.md")
            os.remove(".tw/draft/2026-09-27-fix-b.md")
            write(
                ".tw/direction.md",
                "# 未対応の指示メモ\n\n## ユーザーから\n\n"
                "## エージェントのドラフト\n"
                "### 開発フローとスキルの汎用化\n"
                "開発フロー関連の改善についてのドラフト\n"
                "複数行のドラフトです\n",
            )
            r = run("init.py")
            check(
                "旧いドラフトの節に行が残っていれば、移すよう促して PENDING",
                "PENDING:" in r.stdout and "旧ドラフト節3行" in r.stdout and "エージェントのドラフト0件" in r.stdout,
                r.stdout,
            )

            write(".tw/config.toml", 'format = "なし"\n')
            r = run("init.py")
            check("検証コマンドの行が無ければ config INVALID", "config\tINVALID\t.tw/config.toml" in r.stdout, r.stdout)

            write(".tw/config.toml", 'verify = "なし"\nbranch = "自分で切らない"\n')
            r = run("init.py")
            check("branch が語彙に無ければ config INVALID", "config\tINVALID\t.tw/config.toml" in r.stdout, r.stdout)

            write(".tw/config.toml", 'verify = "なし"\n')
            r = run("init.py")
            check(".tw/config.toml があればそれを読み、verify だけで OK", "config\tOK\t.tw/config.toml" in r.stdout, r.stdout)
            os.remove(".tw/config.toml")

            r = run("init.py", "--help")
            check("打ち間違いをディレクトリにしない", r.returncode == 2 and not os.path.exists("--help"))
        finally:
            os.chdir(cwd)

    with tempfile.TemporaryDirectory() as d:
        cwd = os.getcwd()
        os.chdir(d)
        try:
            write("develop/tasks.json", "[]\n")
            r = run("init.py")
            check("旧形式なら LEGACY（終了コード5）", r.returncode == 5 and r.stdout.startswith("LEGACY\t"), r.stdout)
            check("旧形式には骨組みを混ぜない", not os.path.exists("develop/direction.md") and not os.path.exists(".tw"))
        finally:
            os.chdir(cwd)


def main() -> None:
    for t in (test_load_tasks, test_progress_sections, test_find_legacy_section, test_init):
        t()
    print()
    if failures:
        print(f"FAILED {len(failures)}件: " + ", ".join(failures))
        raise SystemExit(1)
    print("すべて通った")


if __name__ == "__main__":
    main()
