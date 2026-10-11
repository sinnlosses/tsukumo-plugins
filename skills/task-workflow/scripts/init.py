#!/usr/bin/env python3
"""タスク運用に要るファイルをプロジェクトに用意する（既にあるものは触らない）。

使い方: init.py   （プロジェクトの根で打つ）

作るのは `.tw/local/` を外す `.tw/.gitignore`（中身 `local/`。旧い形のものは `local/` に書き換える）だけ
（正典は task-workflow の WORKFLOW.md「ファイル配置と設定ファイル」）。

**既存ファイルは上書きしない。**
設定（`.tw/config.toml`）は**点検するだけで書かない**（値は検証コマンドの選定そのもので、
判断が要る。書くのは `/setup-tasks`）。
"""

from __future__ import annotations

import os
import sys

import beads
import layout


def main() -> None:
    if sys.argv[1:]:
        print("usage: init.py   （引数は取らない。プロジェクトの根で打つ）", file=sys.stderr)
        raise SystemExit(2)

    os.makedirs(layout.TW_DIR, exist_ok=True)
    prepare_gitignore(layout.TW_GITIGNORE_PATH)
    print(f"config\t{check_config()[0]}")
    code = prepare_beads(".")
    if code:
        raise SystemExit(code)


def prepare_gitignore(path: str) -> None:
    """無ければ `local/` で作り、旧い形（`layout.OLD_TW_GITIGNORES`）なら `local/` に書き換え、ほかの中身は触らない。"""
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(layout.TW_GITIGNORE)
        print(f"CREATED\t{path}")
        return
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text in layout.OLD_TW_GITIGNORES:
        with open(path, "w", encoding="utf-8") as f:
            f.write(layout.TW_GITIGNORE)
        print(f"UPDATED\t{path}\t旧い形を local/ に書き換えた")
    else:
        print(f"KEPT\t{path}\t中身は書き換えない")


def prepare_beads(root: str) -> int:
    """設定ファイルがあれば `.beads` を用意する。終了コードを返す。

    `bd init --stealth` は `.git/info/exclude` で `.beads` を外し、コミットも `AGENTS.md`・
    `CLAUDE.md` への書き足しもしない（`--stealth` なしでは両方をして自動でコミットする）。
    `.beads` は主ブランチを出している作業ツリーの根に置くので、別の作業ツリーからは作らない。
    設定ファイルが無い・読めないときは作らない（ID の接頭辞をトラッカーの行で決めるため）。
    """
    try:
        config = layout.read_config(root)
    except layout.ConfigError:
        return 0
    if config.source is None:
        return 0
    target = beads.beads_dir(root)
    if os.path.isdir(target):
        print(f"KEPT\t{target}")
    else:
        toplevel = os.path.realpath(os.path.abspath(root))
        if os.path.realpath(os.path.dirname(target)) != toplevel:
            print(f"NOT_MAIN_WORKTREE\t{os.path.dirname(target)}\t（.beads はそこで作る）")
            return 4
        # トラッカーが github なら ID は Issue 番号（`gh-<n>`）、それ以外は `task` の採番（`t-<n>`）。
        prefix = beads.PREFIX_GITHUB if config.tracker == "github" else beads.PREFIX_LOCAL
        r = beads.run(root, ["init", "--stealth", "-p", prefix, "--non-interactive", "--skip-hooks", "--quiet"])
        if r.returncode != 0:
            print(f"FAILED\tbd init\t{(r.stderr or r.stdout).strip()}")
            return 1
        print(f"CREATED\t{target}\t(bd init --stealth -p {prefix})")
    return 0


def check_config(root: str = ".") -> tuple[str, layout.Config | None]:
    """設定が読めるかの1行 `<OK|MISSING|INVALID>\t<ファイル>\t<詳細>` と、読めた `Config`（読めなければ `None`）。

    `branch` が語彙の外なら行は `INVALID` で、`Config` は返す。書き換えはしない。
    """
    try:
        config = layout.read_config(root)
    except layout.ConfigError as e:
        return f"INVALID\t{layout.CONFIG_PATH}\t{e}", None
    if config.source is None:
        return f"MISSING\t{layout.CONFIG_PATH}\t（verify を書いて作る）", config
    if config.branch not in layout.BRANCH_VALUES:
        values = " / ".join(layout.BRANCH_VALUES)
        return f"INVALID\t{config.source}\t- ブランチ: の値 {config.branch!r} が {values} のどれでもない", config
    if "verify" not in config.written:
        return f"MISSING\t{config.source}\t（検証コマンドが無い）", config
    return f"OK\t{config.source}\tverify={config.verify or layout.NO_COMMAND}", config


if __name__ == "__main__":
    main()
