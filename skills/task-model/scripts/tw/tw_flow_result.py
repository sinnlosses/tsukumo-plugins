"""`tw flow-result`（ワークフロースクリプトの戻り値の JSON から、申し送りを書き、止まっていれば release までを行う）。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys

import accept_hook
import beads
import flow_journal
import tw_base
import tw_claim
import tw_relay


def _text(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, list):
        return ",".join(str(v) for v in value) or "-"
    return str(value)


def _lines(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v) != ""]
    return [str(value)]


def _accept_body(judgment: dict) -> str:
    lines = ["判定: 受け入れ", f"- 材料: friction={_text(judgment.get('friction'))} human={_text(judgment.get('human'))}"]
    for finding in _lines(judgment.get("findings")):
        lines.append(finding if finding.startswith("- ") else f"- 指摘: {finding}")
    for visual in _lines(judgment.get("visual")):
        lines.append(visual if visual.startswith("- 目視:") else f"- 目視: {visual}")
    lines.extend(_lines(judgment.get("notes")))
    return "\n".join(lines)


def _flat(value: object) -> str:
    return re.sub(r"[\t\r\n]+", " ", _text(value))


def _fail_usage(message: str) -> None:
    print(f"usage: {message}", file=sys.stderr)
    raise SystemExit(2)


def _record_stages(toplevel: str, task_id: str, transcript_dir: str) -> None:
    for stage in flow_journal.read_stages(transcript_dir):
        verdict = {} if stage.verdict is None else {"verdict": stage.verdict}
        tw_base.record(
            toplevel,
            "flow_stage",
            task_id,
            stage=stage.stage,
            label=stage.label,
            start=stage.start,
            end=stage.end,
            seconds=stage.seconds,
            source="journal",
            **verdict,
        )


def cmd_flow_result(toplevel: str, task_id: str, json_path: str, patch: str | None, transcript_dir: str | None) -> None:
    bd_id = tw_base.bd_task_id(task_id)
    shown = beads.to_task_id(bd_id)
    try:
        result = json.loads(tw_base.read_body(json_path))
    except ValueError as exc:
        _fail_usage(f"戻り値の JSON が壊れている（{exc}）")
    if not isinstance(result, dict) or not isinstance(result.get("stopped"), bool):
        _fail_usage("戻り値がオブジェクトでないか、stopped が真偽値でない")
    if transcript_dir is not None and not os.path.isdir(transcript_dir):
        _fail_usage(f"--transcript-dir のディレクトリが無い（{transcript_dir}）")
    if not tw_base.owns(toplevel, shown):
        print(f"NOT_OWNER\t{shown}")
        raise SystemExit(4)
    accept_hook.close_round(toplevel, shown)
    if transcript_dir is not None:
        _record_stages(toplevel, task_id, transcript_dir)
    if not result["stopped"]:
        accept = result.get("accept")
        judgment = accept.get("judgment") if isinstance(accept, dict) else None
        if isinstance(judgment, dict):
            tw_relay.write_relay(toplevel, bd_id, shown, "受け入れ", _accept_body(judgment))
        retrospect = result.get("retrospect")
        line = retrospect.get("line") if isinstance(retrospect, dict) else None
        if isinstance(line, str) and line.strip() != "":
            tw_relay.write_relay(toplevel, bd_id, shown, "振り返り", line)
        print(f"FINISH_READY\t{shown}")
        return
    phase = _flat(result.get("phase"))
    reason = _flat(result.get("reason"))
    body = phase if patch is None else f"{phase}\n- patch: {patch}"
    tw_relay.write_relay(toplevel, bd_id, shown, "再開", body)
    if transcript_dir is not None:
        label = flow_journal.unfinished_label(transcript_dir)
        tw_base.record(toplevel, "flow_stopped", task_id, phase=phase, label=label if label is not None else "-")
    out = io.StringIO()
    code: object = None
    with contextlib.redirect_stdout(out):
        try:
            tw_claim.cmd_release(toplevel, task_id, False)
        except SystemExit as exc:
            code = exc.code
    released = f"RELEASED\t{shown}"
    rows = out.getvalue().splitlines()
    for row in rows:
        if row != released:
            print(row)
    if code not in (None, 0) or released not in rows:
        raise SystemExit(code if code not in (None, 0) else 4)
    print(f"RELEASED\t{shown}\t{phase}\t{reason}")
