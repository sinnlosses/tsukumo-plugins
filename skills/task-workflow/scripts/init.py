#!/usr/bin/env python3
"""タスク運用に要るファイルをプロジェクトに用意する（既にあるものは触らない）。

使い方: init.py   （プロジェクトの根で打つ）

作るのは `<根>/direction.md` の骨組み（見出しと `## ユーザーから` の節）と、`.tw/local/` を外す
`.tw/.gitignore`（中身 `local/`。旧い形のものは `local/` に書き換える）だけ（正典は task-workflow の WORKFLOW.md「ファイル配置と設定ファイル」）。
根は設定の `root`（既定 `.tw`。`.tw/config.toml` が無く `develop/direction.md` があれば `develop`）。
`<根>/task/` は最初の `task new` が、`<根>/draft/` は最初のドラフトが作る。骨組みは決まりきっているので
モデルに書かせない（`direction.md` に見出し以外の行が混ざると `/plan-tasks` が「未対応の指示がある」と誤判定する）。

**旧形式（`develop/tasks.json` がある）なら何も作らず `LEGACY` で止まる**（終了コード5。
`task.py` と同じ）。移すのは `task migrate` で、ここでは骨組みを混ぜない。

**既存ファイルは上書きしない。** 中身の点検結果だけを出し、直すかどうかは呼び出し側が決める。
設定（`.tw/config.toml`）は**点検するだけで書かない**（値は検証コマンドの選定そのもので、
判断が要る。書くのは `/setup-tasks`）。
"""

from __future__ import annotations

import os
import sys

import beads
import layout

# direction.md の節（正典「指示メモ」）。前方一致で探す。値は layout.py の正典を読む。
SECTION_USER = layout.SECTION_USER
LEGACY_SECTION_DRAFT = layout.LEGACY_SECTION_DRAFT
# 見出しの行は数えないので、ドラフトの置き場は見出しの括弧に書く。
def direction_skeleton(draft_dir: str) -> str:
    return f"# 未対応の指示メモ（エージェントのドラフトは {draft_dir}/ に1件1ファイル）\n\n{SECTION_USER}\n"


def main() -> None:
    if sys.argv[1:]:
        print("usage: init.py   （引数は取らない。プロジェクトの根で打つ）", file=sys.stderr)
        raise SystemExit(2)

    tasks_json = os.path.join(layout.LEGACY_ROOT, "tasks.json")
    if os.path.exists(tasks_json):
        print(f"LEGACY\t{tasks_json}\ttw migrate --dry-run")
        raise SystemExit(5)

    direction, draft = places(".")
    os.makedirs(os.path.dirname(direction), exist_ok=True)
    create(direction, direction_skeleton(draft), lambda path: check_direction(path, draft))
    os.makedirs(layout.TW_DIR, exist_ok=True)
    prepare_gitignore(layout.TW_GITIGNORE_PATH)
    print(f"config\t{check_config()[0]}")
    code = prepare_beads(".")
    if code:
        raise SystemExit(code)


def places(toplevel: str) -> tuple[str, str]:
    """`(direction.md のパス, draft/ のパス)`。設定が読めなければ、互換のプロジェクトは `develop`、ほかは既定の `.tw` の下
    （設定の不備は `check_config` が報告する）。"""
    try:
        return layout.direction_path(toplevel), layout.draft_dir(toplevel)
    except layout.ConfigError:
        legacy = not os.path.exists(os.path.join(toplevel, layout.CONFIG_PATH)) and os.path.exists(
            os.path.join(toplevel, layout.LEGACY_DIRECTION_PATH)
        )
        root = layout.LEGACY_ROOT if legacy else layout.DEFAULT_ROOT
        return os.path.join(root, "direction.md"), os.path.join(root, "draft")


def prepare_gitignore(path: str) -> None:
    """無ければ `local/` で作り、旧い形（`layout.OLD_TW_GITIGNORES`）なら `local/` に書き換え、ほかの中身は触らない。"""
    if not os.path.exists(path):
        create(path, layout.TW_GITIGNORE, lambda _path: "")
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
    """設定が Beads 方式（`store = "beads"`）なら `.beads` を用意する。終了コードを返す。

    `bd init --stealth` は `.git/info/exclude` で `.beads` を外し、コミットも `AGENTS.md`・
    `CLAUDE.md` への書き足しもしない（`--stealth` なしでは両方をして自動でコミットする）。
    `.beads` は主ブランチを出している作業ツリーの根に置くので、別の作業ツリーからは作らない。
    ファイル方式なら何もしない。
    """
    try:
        config = layout.read_config(root)
    except layout.ConfigError:
        return 0
    if config.store != layout.STORE_BEADS:
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


def create(path: str, body: str, check) -> None:
    """無ければ骨組みで作り、在れば中身を点検して報告する。"""
    if os.path.exists(path):
        print(f"KEPT\t{path}\t{check(path)}")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"CREATED\t{path}")


def check_config(root: str = ".") -> tuple[str, layout.Config | None]:
    """設定が読めるかの1行 `<OK|MISSING|INVALID>\t<ファイル>\t<詳細>` と、読めた `Config`（読めなければ `None`）。

    旧い節の `- ブランチ:` が語彙の外なら行は `INVALID` で、`Config` は返す。書き換えはしない。
    """
    try:
        config = layout.read_config(root)
    except layout.ConfigError as e:
        where = layout.CONFIG_PATH if os.path.exists(os.path.join(root, layout.CONFIG_PATH)) else "/".join(layout.CONFIG_FILENAMES)
        return f"INVALID\t{where}\t{e}", None
    if config.source is None:
        return f"MISSING\t{layout.CONFIG_PATH}\t（verify を書いて作る）", config
    if config.branch not in layout.BRANCH_VALUES:
        values = " / ".join(layout.BRANCH_VALUES)
        return f"INVALID\t{config.source}\t- ブランチ: の値 {config.branch!r} が {values} のどれでもない", config
    if "verify" not in config.written:
        return f"MISSING\t{config.source}\t（検証コマンドが無い）", config
    return f"OK\t{config.source}\tverify={config.verify or layout.NO_COMMAND}", config


def check_direction(path: str, draft_dir: str) -> str:
    """`## ユーザーから` の本文行数と、`draft_dir` のドラフトの件数を数える（正典「指示メモ」）。

    **節見出しが1つも無い（古い）ファイルは、全体を `## ユーザーから` とみなす**（後方互換）。
    ドラフトを積んでいた旧い節に行が残っていれば、それも数えて移すよう促す。
    """
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    draft_n = _count_drafts(draft_dir)

    known = (SECTION_USER, LEGACY_SECTION_DRAFT)
    if not any(l.startswith(k) for l in lines for k in known):
        body = [l for l in lines if l.strip() and not l.startswith("#")]
        return _direction_result(len(body), draft_n, 0, draft_dir)

    counts = {"user": 0, "legacy": 0}
    current: str | None = None
    for l in lines:
        if l.startswith(SECTION_USER):
            current = "user"
        elif l.startswith(LEGACY_SECTION_DRAFT):
            current = "legacy"
        elif l.startswith("## "):
            current = None
        elif l.strip() and current is not None:
            counts[current] += 1
    return _direction_result(counts["user"], draft_n, counts["legacy"], draft_dir)


def _count_drafts(draft_dir: str) -> int:
    if not os.path.isdir(draft_dir):
        return 0
    return sum(1 for n in os.listdir(draft_dir) if n.endswith(".md"))


def _direction_result(user_n: int, draft_n: int, legacy_n: int, draft_dir: str) -> str:
    if not (user_n or draft_n or legacy_n):
        return "OK: 未対応の指示は無い"
    legacy = f"、旧ドラフト節{legacy_n}行（{draft_dir}/ へ移す）" if legacy_n else ""
    return f"PENDING: ユーザーから{user_n}行、エージェントのドラフト{draft_n}件{legacy}（/plan-tasks が先）"


if __name__ == "__main__":
    main()
