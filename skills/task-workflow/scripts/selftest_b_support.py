"""`selftest_beads.py` の部品が共有する足場（定数・ヘルパ・起動時の設定）。"""

from __future__ import annotations

import functools
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import beads  # noqa: E402
import ledger  # noqa: E402
from selftest_body import task_body  # noqa: E402
from selftest_fixtures import make_repo as make_fixture_repo, run_task  # noqa: E402
from selftest_support import git, local, write  # noqa: E402

# 利用者の値のままだと、一時リポジトリの台帳がその置き場に積もる。
os.environ.pop(ledger.STATE_DIR_ENV, None)
# このプロセスは `task.py` の子プロセスが書いたあとに読み直すので、読みを使い回さない。
beads.READ_REUSE_SECONDS = 0

BODY = task_body(acceptance="- 通る", caution="z")
# 登録の既定の本文。`make_repo` が主ブランチに置く `shared.txt` を名指す。
PLANNED_BODY = task_body([("書く", "x")], ["shared.txt"], acceptance="- 通る", caution="z")


def env() -> dict[str, str]:
    """テストは CPU 数の半分まで並行に走らせる。出力と環境変数はテストごとに持つ。"""
    return getattr(local, "env", None) or dict(os.environ)


def bd(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bd", *args], cwd=cwd, capture_output=True, text=True, env=env())


def rows(out: str) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for line in out.split("---")[0].splitlines():
        cols = line.split("\t")
        if len(cols) == 8:
            table[cols[0]] = cols
    return table


def claim_snapshots(cwd: str) -> dict[str, beads.Issue]:
    return beads.claim_snapshots(beads.read_kv(cwd))


def tail_line(out: str, key: str) -> str:
    return next((l for l in out.splitlines() if l.startswith(key + "\t")), "")


# `init.py` を打つ足場。作業ツリーの枝の名前（課題の持ち主の名前になる）は `wt1`・`wt2`。
make_repo = functools.partial(make_fixture_repo, branch="切らない", init=True, worktree_branches=("wt1", "wt2"))


def new(cwd: str, summary: str, *extra: str, body: str = PLANNED_BODY) -> str:
    r = run_task(cwd, "new", "--summary", summary, "--difficulty", "sonnet", "--loopable", "Y", *extra,
                 "--body-file", "-", stdin=body)
    if r.returncode != 0:
        raise RuntimeError(f"task new 失敗: {r.stdout}{r.stderr}")
    return r.stdout.split("\t")[1]


def new_unplanned(cwd: str, summary: str) -> str:
    """`## やること` の空な todo（`--hold` で登録して戻す）。"""
    task_id = new(cwd, summary, "--hold", body=BODY)
    r = run_task(cwd, "edit", task_id, "--status", "todo")
    if r.returncode != 0:
        raise RuntimeError(f"task edit --status todo 失敗: {r.stdout}{r.stderr}")
    return task_id


def work_and_done(wt: str, task_id: str, *, dropped: bool = False) -> None:
    write(os.path.join(wt, f"{task_id}.txt"), f"{task_id}\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", f"{task_id}: 作業")
    extra = ("--dropped",) if dropped else ()
    r = run_task(wt, "done", task_id, *extra, "--result-file", "-", stdin="- 検証: なし\n- 振り返り: 兆候なし\n")
    if r.returncode != 0:
        raise RuntimeError(f"task done 失敗: {r.stdout}{r.stderr}")


def num(task_id: str) -> int:
    return int(beads.to_bd_id(task_id).split("-")[1])
