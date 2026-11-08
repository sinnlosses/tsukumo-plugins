"""ワークフローの run の記録（`journal.jsonl` と `agent-<agentId>.jsonl`）から、段の区間を読む。

読むのは journal の `type`・`label`・`agentId`・`result.verdict` と、transcript の先頭行・末尾行の `timestamp` だけ。
agent の戻り値の本文と会話の中身は読まない・返さない。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime

JOURNAL_NAME = "journal.jsonl"
VERDICTS = ("accept", "reject")
_TAIL_BYTES = 65536


@dataclass(frozen=True)
class Stage:
    stage: str
    label: str
    start: str
    end: str
    seconds: int
    verdict: str | None


def stage_of(label: str) -> str | None:
    if label == "implement" or re.fullmatch(r"fix-\d+", label):
        return "delegate"
    if re.fullmatch(r"acceptor-\d+", label):
        return "review"
    if label == "retrospect":
        return "retro"
    return None


def _journal_rows(transcript_dir: str) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(os.path.join(transcript_dir, JOURNAL_NAME), encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("agentId"), str):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _timestamp(line: bytes) -> str | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    value = row.get("timestamp") if isinstance(row, dict) else None
    return value if isinstance(value, str) else None


def _span(transcript_dir: str, agent_id: str) -> tuple[str, str] | None:
    """transcript の先頭行と末尾行の `timestamp`。末尾は最後の `_TAIL_BYTES` だけを読む。"""
    try:
        with open(os.path.join(transcript_dir, f"agent-{agent_id}.jsonl"), "rb") as f:
            first = _timestamp(f.readline())
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            tail = [line for line in f.read().splitlines() if line.strip()]
    except OSError:
        return None
    last = _timestamp(tail[-1]) if tail else None
    if first is None or last is None:
        return None
    return first, last


def parse_time(value: object) -> datetime | None:
    """ISO の時刻（末尾 `Z` も可）。読めないか時差が無ければ `None`。"""
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def _verdict(result: object) -> str:
    value = result.get("verdict") if isinstance(result, dict) else None
    return value if value in VERDICTS else "other"


def read_stages(transcript_dir: str) -> list[Stage]:
    """`result` まで届いた agent のうち、段になる label のものを journal の順で返す。読めない行・agent は飛ばす。"""
    rows = _journal_rows(transcript_dir)
    results = {row["agentId"]: row.get("result") for row in rows if row.get("type") == "result"}
    stages: list[Stage] = []
    for row in rows:
        label = row.get("label")
        if row.get("type") != "started" or not isinstance(label, str) or row["agentId"] not in results:
            continue
        stage = stage_of(label)
        if stage is None:
            continue
        span = _span(transcript_dir, row["agentId"])
        if span is None:
            continue
        begin, end = parse_time(span[0]), parse_time(span[1])
        if begin is None or end is None:
            continue
        verdict = _verdict(results[row["agentId"]]) if stage == "review" else None
        seconds = max(0, int((end - begin).total_seconds()))
        stages.append(Stage(stage, label, span[0], span[1], seconds, verdict))
    return stages


def unfinished_label(transcript_dir: str) -> str | None:
    """`result` の無い `started`（`failed` を含む）のうち最後のものの label。無ければ `None`。"""
    rows = _journal_rows(transcript_dir)
    done = {row["agentId"] for row in rows if row.get("type") == "result"}
    label: str | None = None
    for row in rows:
        if row.get("type") == "started" and row["agentId"] not in done and isinstance(row.get("label"), str):
            label = row["label"]
    return label
