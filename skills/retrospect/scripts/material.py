#!/usr/bin/env python3
"""1件のタスクについて、振り返りの材料をまとめて出す。

使い方: material.py <リポジトリの根> <T-XXX> [--diff] [--diff-bytes N] [--signals]
       material.py <リポジトリの根> <T-XXX> --gate --friction none|some|missing
           --human N [--reverify N] [--fixes N] [--review skip|clean|found]

出すのは6つ（`--signals` のときは「手数」だけ。流れの中の1件ごとの振り返りが、
材料を観点に当てるときに、当たりの目安を数だけで見るのに使う。本文と diff はメインが受け入れで読み終えている）。

`--gate` のときは判定だけを出す。5つの答えと手数の当たりの目安のどれにも当たらなければ `QUIET` の1行、
当たれば当たった材料ごとに `SIGNAL\t<材料>\t<値>` を並べ、続けて「手数」と「段の所要時間」と「ドラフトの行方」を出す。段の所要時間の `SIGNAL` は、
1つの段が着手から最後の出来事までの50%以上・終わりの無い段がある・段のあいだの空きが20%以上のとき。5つの答え:

- `--friction`（必須）: 委譲先の friction log が全部「なし」（`none`）／行がある（`some`）／書いていない返却がある（`missing`）
- `--human`（必須）: 人の差し戻しの回数
- `--reverify`: 検証を打ち直した回数。台帳の `verify` の `result` が `FORMAT_FAILED`・`VERIFY_NOT_PASSED` の件数
- `--fixes`: 受け入れの差し戻しの回数。台帳の `lap review start` の続く組のうち、間に `verify` が挟まる組の数
  （差し戻しのたびに必ず打たれるのは `lap review start` で、`lap delegate start` は打たれないことがあるので見ない）
- `--review`: 台帳の `lap review start` が無ければ `skip`、`--fixes` が 1 以上なら `found`、無ければ `clean`

台帳の3つは、そのタスクの最後の `claim` 以降だけを数える。台帳が読めない・`claim` が無いときだけ引数の値を使い、
引数も無ければ `0`・`0`・`skip` として標準エラーに `UNCOUNTED` を出す。台帳から数えられたときに引数が違えば、
台帳の値を使い標準エラーに `MISMATCH` を出す。縮み: 作業先が別のリポジトリのタスクは `verify` が台帳に残らず
`--reverify` が 0 になる。受け入れ役が捨てた指摘は直されないので `found` にならない。
最後のレビューで出た指摘の直し（再レビューしない）は、続く組が無いので数えない。レビューを飛ばした回は差し戻しが起きない。

- **タスク**: Beads の課題（`tw show` と同じ形。front matter と本文、`## 結果`）。
  Beads に無い古いタスクは `docs/history/tasks.md` のアーカイブから本文
- **登録から完了までの差分**: `bd history` の最初の版と最後の版の本文の差
  （着手時に書き足した `## やること`・`## 注意` の量が出る）。Beads に無い古いタスクは代わりに
  `docs/history/progress.md` の該当の小節
- **コミット**: 件名とファイルごとの増減（`--diff` を付けたときだけ中身も）。`tw ship` が課題の
  metadata `task_shipped` に書いた送った範囲があればそのコミット、無ければ件名にタスクIDを含むコミット
- **手数**: サブエージェントのトランスクリプトから取った**数だけ**（どのタスクのものかは台帳の `agent` の出来事で決め、無い記録だけ先頭のタスクIDで探す）
- **段の所要時間**: `flow/` から取った、そのタスクの段ごとの秒と割合・終わりの無い段・段のあいだの空き（`metrics.py`）
- **ドラフトの行方**: 観点ごとに積んだドラフトの採用・却下・未決の数と絞り中（`draft_fate.py`）

**会話の中身は1文字も出さない。** トランスクリプトから読むのはツール名・ファイルパス・
コマンドの先頭2語・件数・時刻に限る（CLAUDE.md「会話内容の扱い」）。本文を出したい誘惑が
出たら、それは振り返りに要る材料ではなく引用したいだけなので、諦める。

**データの不備で traceback を出さない。** 見つからないものは `-` と欠席の理由を出して先へ進む。
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

_TASK_MODEL_SCRIPTS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "task-model", "scripts", "core")
)
if _TASK_MODEL_SCRIPTS not in sys.path:
    sys.path.insert(0, _TASK_MODEL_SCRIPTS)

import beads  # noqa: E402
import draft_fate  # noqa: E402
import gitcmd  # noqa: E402
import layout  # noqa: E402
import gitinfo  # noqa: E402
import ledger  # noqa: E402
import metrics  # noqa: E402
import transcript  # noqa: E402

DEFAULT_DIFF_BYTES = 40000


def main() -> None:
    root, task_id, want_diff, diff_bytes, signals_only, gate = parse_args(sys.argv[1:])
    if not layout.ANY_ID_PATTERN.fullmatch(task_id):
        print(f"INVALID\t{task_id}\tタスクIDは T- + 3桁以上・GH- + 番号・Jira のキー（PROJ-123）")
        return

    if gate is not None:
        print_gate(root, task_id, gate)
        return

    if signals_only:
        section("手数（トランスクリプトから取った数だけ）")
        print_signals(root, task_id)
        return

    section("タスク")
    if print_task(root, task_id):
        section("登録から完了までの差分（Beads の版）")
        print_task_history(root, task_id)
    else:
        section("progress の小節（Beads に無い古いタスク）")
        print_progress(root, task_id)

    section("コミット")
    revs = print_commits(root, task_id, want_diff, diff_bytes)

    section("手数（トランスクリプトから取った数だけ）")
    print_signals(root, task_id)

    print_stages(read_stages(read_ledger(root), task_id))

    draft_fate.print_section(root, datetime.now(timezone.utc))

    if not revs:
        print()
        print(f"注意\t{task_id} のコミットが見つからない（送った範囲の記録が無く、件名にも無い）")


# ---- タスク本文 ---------------------------------------------------------------


def print_task(root: str, task_id: str) -> bool:
    """タスクを出す。Beads に課題があれば True。

    Beads に無い古いタスクは `docs/history/tasks.md` のアーカイブから本文を出す。
    """
    if print_beads_task(root, task_id):
        return True
    archive = os.path.join(root, layout.HISTORY_TASKS_PATH)
    body = find_archived_task(archive, task_id)
    if body is None:
        print(f"-\t{task_id} が Beads にもアーカイブにも無い")
        return False
    print(f"出典\t{archive}")
    print()
    print(body)
    return False


def find_archived_task(path: str, task_id: str) -> str | None:
    """`## T-XXX ...` から次の `## T-XXX ...` までを切り出す。

    本文の中にも `## 目的・背景` のような見出しが入っているので、区切りは
    **タスクIDを持つ見出しだけ**で見る。
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    start = None
    for i, ln in enumerate(lines):
        m = layout.HISTORY_HEADING_PATTERN.match(ln)
        if not m:
            continue
        if m.group(1) == task_id:
            start = i
        elif start is not None:
            return "\n".join(lines[start:i]).rstrip()
    return "\n".join(lines[start:]).rstrip() if start is not None else None


# ---- Beads の課題 -------------------------------------------------------------


def print_beads_task(root: str, task_id: str) -> bool:
    """Beads の課題を `tw show` と同じ形で出す。課題が無い・`bd` を読めなければ理由だけ出して False。"""
    bd_id = beads.to_bd_id(task_id)
    try:
        issue = beads.show(root, bd_id)
        result = beads.last_result(beads.comments(root, bd_id)) if issue is not None else None
    except beads.BeadsError as e:
        print(f"-\t{e}")
        return False
    if issue is None:
        print(f"-\t{task_id} が Beads に無い")
        return False
    task, err = beads.to_task(issue)
    if task is None:
        print(f"-\t{task_id} を読めない（{err or '振り分け前'}）")
        return True
    print(f"出典\tBeads {bd_id}")
    print()
    print(beads.render_task(task, issue, result).rstrip())
    return True


def print_task_history(root: str, task_id: str) -> None:
    """登録した版（`bd history` の最初）と最後の版の本文の差。"""
    try:
        snaps = beads.history(root, beads.to_bd_id(task_id))
    except beads.BeadsError as e:
        print(f"-\t{e}")
        return
    if not snaps:
        print(f"-\t{task_id} の版が無い")
        return

    def text_of(snap: dict) -> str:
        return beads.compose_body(
            str(snap.get("description") or ""),
            str(snap.get("acceptance_criteria") or ""),
            str(snap.get("notes") or ""),
            None,
        )

    first, last = text_of(snaps[0]), text_of(snaps[-1])
    print(f"版\t{len(snaps)}")
    for label, text in (("登録時の節", first), ("いまの節", last)):
        heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
        print(f"{label}\t" + (", ".join(h[3:] for h in heads) or "-"))
    diff = difflib.unified_diff(first.splitlines(), last.splitlines(), "登録時", "いま", lineterm="")
    print()
    print("\n".join(diff) or "（差分なし）")


# ---- progress.md --------------------------------------------------------------


def print_progress(root: str, task_id: str) -> None:
    path = os.path.join(root, "docs", "history", "progress.md")
    block = find_progress_section(path, task_id)
    if not block:
        print(f"-\t{task_id} を見出しに含む小節が progress.md に無い")
        return
    print(f"出典\t{path}")
    print(block)


def find_progress_section(path: str, task_id: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    out: list[str] = []
    keep = False
    for ln in lines:
        if ln.startswith("### "):
            keep = task_id in ln
        elif ln.startswith("## "):
            keep = False
        if keep:
            out.append(ln)
    return "\n".join(out).strip()


# ---- コミット -----------------------------------------------------------------


def shipped_commits(root: str, task_id: str, log_args: tuple[str, ...]) -> str | None:
    """`tw ship` が metadata `task_shipped` に書いた範囲のコミット（`log_args` の書式）。

    記録が使えない（`beads.shipped_range_on_base` が `None`）・課題を読めない・範囲が引けない・空のときは
    `None`（呼ぶ側が件名で探す）。
    """
    try:
        issue = beads.show(root, beads.to_bd_id(task_id))
    except beads.BeadsError:
        return None
    if issue is None or beads.shipped_range(issue) is None:
        return None
    try:
        shipped = beads.shipped_range_on_base(issue, gitinfo.base_branch(root), root)
    except gitinfo.NoBaseBranch:
        return None
    if shipped is None:
        return None
    out, _ = git_out(root, *log_args, f"{shipped[0]}..{shipped[1]}")
    return out if out and out.strip() else None


def print_commits(root: str, task_id: str, want_diff: bool, diff_bytes: int) -> list[str]:
    log_args = ("log", "--reverse", "--no-merges", "--pretty=format:%h\t%ad\t%s", "--date=short")
    recorded = shipped_commits(root, task_id, log_args)
    if recorded:
        out, err = recorded, ""
    else:
        # 記録が無いときは**件名だけ**で照合する（`--grep` は本文にも当たるので、タスクIDに言及しただけの
        # 別のコミットを拾ってしまう）。件名にIDを置く運用は task-model の
        # 「コミットメッセージ」が決めている。
        out, err = git_out(root, *log_args)
    if out is None:
        print(f"-\tgit log が読めない: {err}")
        return []
    revs = []
    for ln in out.splitlines():
        if not ln.strip():
            continue
        h, date, subject = ln.split("\t", 2)
        if not recorded and not re.search(rf"\b{task_id}\b", subject):
            continue
        revs.append(h)
        print(f"{h}\t{date}\t{subject}")
        stat, _ = git_out(root, "show", "--numstat", "--format=", h)
        for s in (stat or "").splitlines():
            if s.strip():
                print("\t" + s)
    if not revs:
        print("-\t該当するコミットが無い")
        return revs
    if want_diff:
        print()
        print(f"--- diff（上限 {diff_bytes} バイト） ---")
        body, _ = git_out(root, "show", "--format=", "-p", *revs)
        body = body or ""
        print(body[:diff_bytes])
        if len(body) > diff_bytes:
            print(f"\n（ここで切った。残り {len(body) - diff_bytes} バイトは "
                  f"`git show <hash> -- <path>` でファイルを絞って読む）")
    return revs


# ---- トランスクリプトから取る数 ------------------------------------------------


def read_all_signals(root: str, task_id: str) -> list[tuple[str, dict | None]]:
    return [(p, transcript.read_signals(p, root)) for p in transcript.find_transcripts(root, task_id)]


def print_signals(root: str, task_id: str, read: list[tuple[str, dict | None]] | None = None) -> None:
    if read is None:
        read = read_all_signals(root, task_id)
    if not read:
        print("-\tトランスクリプトが見つからない（材料を1つ諦めて先へ進む）")
        return
    for p, stats in read:
        if stats is None:
            print(f"-\t読めない: {os.path.basename(p)}")
            continue
        print(f"出典\t{os.path.basename(p)}")
        print(f"経過\t{stats['elapsed']}")
        print(f"ツール呼び出し\t{stats['tool_calls']}件\tエラー\t{stats['errors']}件")
        print("内訳\t" + ", ".join(f"{k}={v}" for k, v in stats["tools"]))
        if stats["rewrites"]:
            print("同じファイルを2回以上直した\t"
                  + ", ".join(f"{k}×{v}" for k, v in stats["rewrites"]))
        if stats["commands"]:
            print("よく打ったコマンド\t"
                  + ", ".join(f"{k}×{v}" for k, v in stats["commands"]))
        if stats["slow_calls"]:
            print("長く待った呼び出し\t"
                  + ", ".join(f"{(n + ' ' + d).strip()} {s:.0f}秒" for n, d, s in stats["slow_calls"]))
        print(f"出力トークン\t{stats['output_tokens']}")


# ---- 判定の口 -----------------------------------------------------------------

GATE_CHOICES = {"--friction": ("none", "some", "missing"), "--review": ("skip", "clean", "found")}
GATE_COUNTS = ("--reverify", "--fixes", "--human")
GATE_REQUIRED = ("--friction", "--human")
SLOW_SECONDS = 180


def read_ledger(root: str) -> list[metrics.Event] | str:
    """台帳の出来事を時刻順に。読めなければ理由の文字列。"""
    try:
        events, _ = metrics.read_events(ledger.ledger_root(cwd=root))
    except (gitinfo.GitCommandError, OSError):
        return "台帳が読めない（git のリポジトリでない）"
    return events


def count_from_ledger(events: list[metrics.Event] | str, task_id: str) -> dict[str, str] | None:
    """台帳から `--reverify`・`--fixes`・`--review` を数える。読めない・そのタスクの `claim` が無ければ `None`。

    差し戻しは、受け入れ役が指摘を返す → 委譲先が直して `verify` を打つ → 次の `lap review start`、の順で現れる。
    `lap delegate start` は差し戻しのたびには打たれないので見ず、続く `lap review start` の組に
    `verify` が挟まるかで数える。
    """
    if isinstance(events, str):
        return None
    own = [e for e in events if e.task == task_id]
    claims = [i for i, e in enumerate(own) if e.kind == "claim"]
    if not claims:
        return None
    run = own[claims[-1] :]
    reviews = [i for i, e in enumerate(run) if e.kind == "lap" and e.fields.get("stage") == "review" and e.fields.get("edge") == "start"]
    failed = sum(1 for e in run if e.kind == "verify" and e.fields.get("result") in metrics.VERIFY_FAILED_RESULTS)
    fixes = sum(1 for a, b in zip(reviews, reviews[1:]) if any(e.kind == "verify" for e in run[a:b]))
    review = "skip" if not reviews else "found" if fixes else "clean"
    return {"--reverify": str(failed), "--fixes": str(fixes), "--review": review}


def decide_gate(events: list[metrics.Event] | str, task_id: str, gate: dict[str, str]) -> dict[str, str]:
    """台帳の値を優先して 5 つの答えを決める。食い違いと数えられなかったものは標準エラーに出す。"""
    counted = count_from_ledger(events, task_id)
    decided = dict(gate)
    for opt in ("--reverify", "--fixes", "--review"):
        given = gate.get(opt)
        if counted is not None:
            decided[opt] = counted[opt]
            if given is not None and given != counted[opt]:
                print(f"MISMATCH\t{opt}\t引数={given}\t台帳={counted[opt]}", file=sys.stderr)
        elif given is None:
            decided[opt] = "skip" if opt == "--review" else "0"
            print(f"UNCOUNTED\t{opt}", file=sys.stderr)
    return decided


def verify_heads(root: str) -> list[str]:
    heads = ["tw verify"]
    try:
        command = layout.read_config(root).verify
    except layout.ConfigError:
        command = None
    head = " ".join((command or "").split()[:2])
    if head and head not in heads:
        heads.append(head)
    return heads


STAGE_SHARE_PERCENT = 50
STAGE_GAP_PERCENT = 20


@dataclass(frozen=True)
class StageTimes:
    """`seconds` は段名 → 秒（`metrics.STAGE_ORDER` の順）。`gap` は区間に覆われない秒。"""

    total: int
    seconds: dict[str, int]
    opens: list[str]
    gap: int


def read_stages(events: list[metrics.Event] | str, task_id: str) -> StageTimes | str | None:
    """台帳から段ごとの秒を取る。読めなければ理由の文字列、そのタスクの `claim` が無ければ `None`。"""
    if isinstance(events, str):
        return events
    found = metrics.task_stage_spans(events, task_id)
    if found is None:
        return None
    spans, opens, began, last = found
    total = round((last - began).total_seconds())
    by_stage: dict[str, list[tuple[datetime, datetime]]] = {}
    for stage, start, end in spans:
        by_stage.setdefault(stage, []).append((start, end))
    return StageTimes(
        total=total,
        seconds={s: round(union_seconds(by_stage[s], began, last)) for s in metrics.STAGE_ORDER if s in by_stage},
        opens=list(dict.fromkeys(opens)),
        gap=max(0, total - round(union_seconds([(s, e) for _stage, s, e in spans], began, last))),
    )


def union_seconds(intervals: list[tuple[datetime, datetime]], lower: datetime, upper: datetime) -> float:
    """区間を `[lower, upper]` に切り詰めた和集合の秒（重なった時間を2回数えない）。"""
    covered = 0.0
    reach = lower
    for start, end in sorted((max(s, lower), min(e, upper)) for s, e in intervals):
        begin = max(start, reach)
        if end > begin:
            covered += (end - begin).total_seconds()
        reach = max(reach, end)
    return covered


def stage_percent(seconds: int, total: int) -> int:
    return round(100 * seconds / total)


def print_stages(stages: StageTimes | str | None) -> None:
    section("段の所要時間（flow/ から取った秒）")
    if stages is None:
        print("EMPTY")
        return
    if isinstance(stages, str):
        print(f"-\t{stages}")
        return
    print(f"TOTAL\t{stages.total}")
    if stages.total > 0:
        for stage, seconds in stages.seconds.items():
            print(f"STAGE\t{stage}\t{seconds}\t{stage_percent(seconds, stages.total)}%")
        if stages.gap > 0:
            print(f"GAP\t{stages.gap}\t{stage_percent(stages.gap, stages.total)}%")
    for stage in stages.opens:
        print(f"OPEN\t{stage}")


def stage_signals(stages: StageTimes | str | None) -> list[tuple[str, str]]:
    if not isinstance(stages, StageTimes):
        return []
    hits = [("段の所要時間", f"OPEN {stage}") for stage in stages.opens]
    if stages.total > 0:
        hits += [
            ("段の所要時間", f"{stage} {stage_percent(seconds, stages.total)}%")
            for stage, seconds in stages.seconds.items()
            if seconds * 100 >= STAGE_SHARE_PERCENT * stages.total
        ]
        if not stages.opens and stages.gap * 100 >= STAGE_GAP_PERCENT * stages.total:
            hits.append(("段の所要時間", f"段の空き {stage_percent(stages.gap, stages.total)}%"))
    return hits


def gate_signals(
    root: str, gate: dict[str, str], read: list[tuple[str, dict | None]], stages: StageTimes | str | None
) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if gate["--friction"] != "none":
        hits.append(("friction log", gate["--friction"]))
    if int(gate["--reverify"]) > 0:
        hits.append(("検証の打ち直し", f"{gate['--reverify']}回"))
    if int(gate["--fixes"]) > 0:
        hits.append(("受け入れでの直し", f"{gate['--fixes']}回"))
    if gate["--review"] == "found":
        hits.append(("受け入れでの直し", "レビューの指摘あり"))
    if int(gate["--human"]) > 0:
        hits.append(("人の差し戻し", f"{gate['--human']}回"))
    heads = verify_heads(root)
    for _p, stats in read:
        if stats is None:
            continue
        if stats["errors"] >= 3:
            hits.append(("ツールのエラー", f"{stats['errors']}件"))
        for path, n in stats["rewrites"]:
            if n >= 3:
                hits.append(("同じファイルの直し直し", f"{path}×{n}"))
        for head in heads:
            n = sum(v for k, v in stats["command_counts"].items() if k == head or k.startswith(head + " "))
            if n >= 3:
                hits.append(("検証の打ち直し", f"{head}×{n}"))
        for name, desc, secs in stats["slow_calls"]:
            if secs >= SLOW_SECONDS:
                hits.append(("時間の偏り", f"{(name + ' ' + desc).strip()} {secs:.0f}秒"))
    return hits + stage_signals(stages)


def print_gate(root: str, task_id: str, gate: dict[str, str]) -> None:
    read = read_all_signals(root, task_id)
    events = read_ledger(root)
    stages = read_stages(events, task_id)
    hits = gate_signals(root, decide_gate(events, task_id, gate), read, stages)
    if not hits:
        print("QUIET")
        return
    for name, value in hits:
        print(f"SIGNAL\t{name}\t{value}")
    section("手数（トランスクリプトから取った数だけ）")
    print_signals(root, task_id, read)
    print_stages(stages)
    draft_fate.print_section(root, datetime.now(timezone.utc))


# ---- 共通 ---------------------------------------------------------------------


def section(title: str) -> None:
    print()
    print(f"===== {title} =====")


USAGE = ("usage: material.py <リポジトリの根> <T-XXX> [--diff] [--diff-bytes N] [--signals]\n"
         "       material.py <リポジトリの根> <T-XXX> --gate --friction none|some|missing"
         " --human N [--reverify N] [--fixes N] [--review skip|clean|found]")


def parse_args(argv: list[str]) -> tuple[str, str, bool, int, bool, dict[str, str] | None]:
    positional: list[str] = []
    want_diff = False
    signals_only = False
    want_gate = False
    answers: dict[str, str] = {}
    diff_bytes = DEFAULT_DIFF_BYTES
    i = 0
    while i < len(argv):
        if argv[i] == "--diff":
            want_diff = True
        elif argv[i] == "--signals":
            signals_only = True
        elif argv[i] == "--gate":
            want_gate = True
        elif argv[i] == "--diff-bytes" and i + 1 < len(argv):
            diff_bytes = int(argv[i + 1])
            i += 1
        elif (argv[i] in GATE_CHOICES or argv[i] in GATE_COUNTS) and i + 1 < len(argv):
            answers[argv[i]] = argv[i + 1]
            i += 1
        else:
            positional.append(argv[i])
        i += 1
    if len(positional) != 2 or (want_gate and not gate_answers_valid(answers)):
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    return positional[0], positional[1], want_diff, diff_bytes, signals_only, answers if want_gate else None


def gate_answers_valid(answers: dict[str, str]) -> bool:
    if any(opt not in answers for opt in GATE_REQUIRED):
        return False
    for opt, choices in GATE_CHOICES.items():
        if opt in answers and answers[opt] not in choices:
            return False
    return all(answers[opt].isdigit() for opt in GATE_COUNTS if opt in answers)


def git_out(root: str, *args: str) -> tuple[str | None, str]:
    try:
        p = gitcmd.run(["-C", root, *args])
    except OSError as e:
        return None, str(e)
    if p.returncode != 0:
        return None, p.stderr.strip()
    return p.stdout, ""


if __name__ == "__main__":
    main()
