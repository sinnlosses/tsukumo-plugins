from __future__ import annotations

import glob
import importlib
import json
import os
import shutil
import tempfile
import types
from datetime import datetime, timedelta, timezone

from selftest_support import RESULT_FULL, check, copy_beads, git, refuse_direct_run, say, weight, write  # noqa: E402
import beads  # noqa: E402
import layout  # noqa: E402
import ledger  # noqa: E402
import metrics  # noqa: E402
import taskfile  # noqa: E402
import tw_base  # noqa: E402
import tw_config  # noqa: E402
from selftest_fixtures import BODY, PLANNED_BODY, commit_task, flow_rows, make_repo, run_task  # noqa: E402


def _make_config_doctor_repo(tmp: str, name: str, with_config: bool = True) -> str:
    """`task config-doctor`・`task config` のフィクスチャ用の最小リポジトリ（`.beads` 付き）。
    `with_config` が偽なら `.tw/config.toml` を置かず、CLAUDE.md の旧い節だけを置く。"""
    repo = os.path.join(tmp, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "beads.role", "maintainer")
    copy_beads(repo, beads.PREFIX_LOCAL)
    write(os.path.join(repo, "docs", "history", "tasks.md"), "# 完了タスクのアーカイブ\n")
    if with_config:
        write(os.path.join(repo, ".tw", "config.toml"), 'verify = "なし"\nbranch = "既定"\n')
    else:
        write(
            os.path.join(repo, "CLAUDE.md"),
            "# x\n\n## タスク運用\n\n- 検証コマンド: なし\n- 整形コマンド: なし\n- ブランチ: 既定\n",
        )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def _compact_json_array(items: list[str]) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _skill(root: str, name: str, mark: str | None, description: str = "説明", order: str | None = None,
           title: str | None = None) -> str:
    """`root/<name>/SKILL.md` を書く。`mark` は `metadata` に置く `workflow-step` の値（None なら metadata を置かない）。
    `order` は `workflow-step-order`、`title` は `workflow-step-title` の値（None なら置かない）。"""
    meta = f'metadata:\n  workflow-step: "{mark}"\n' if mark is not None else ""
    if mark is not None and order is not None:
        meta += f'  workflow-step-order: "{order}"\n'
    if mark is not None and title is not None:
        meta += f'  workflow-step-title: "{title}"\n'
    return write(
        os.path.join(root, name, "SKILL.md"),
        f"---\nname: {name}\ndescription: >\n  {description}\n  続き\n{meta}---\n本文\n",
    )


READONLY_KEYS = ("store", "tracker", "github_project", "backup", "base_branch", "loop_max_ships")


def _entry_skill(root: str, name: str, mark: str, title: str | None = None, step: bool = False) -> str:
    meta = f'  workflow-entry: "{mark}"\n'
    if title is not None:
        meta += f'  workflow-entry-title: "{title}"\n'
    if step:
        meta += '  workflow-step: "true"\n'
    return write(
        os.path.join(root, name, "SKILL.md"),
        f"---\nname: {name}\ndescription: 入口の説明\nmetadata:\n{meta}---\n本文\n",
    )


def unregistered_tests(module: types.ModuleType) -> list[str]:
    registered = set(getattr(module, "TESTS", ()))
    return [n for n, f in vars(module).items() if n.startswith("test_") and callable(f) and f not in registered]


def test_unregistered_tests_named() -> None:
    say("自己テスト: TESTS に載っていない test_ 関数は関数名で返り、載っていれば・TESTS が無くても返り方が決まっている")
    fake = types.ModuleType("fake_selftest")
    fake.test_listed = lambda: None
    fake.test_unlisted = lambda: None
    fake.helper = lambda: None
    fake.TESTS = (fake.test_listed,)
    check("載っていない test_ 関数だけを名前で返す（test_ で始まらない名前は返さない）",
          unregistered_tests(fake) == ["test_unlisted"], repr(unregistered_tests(fake)))
    fake.TESTS = (fake.test_listed, fake.test_unlisted)
    check("全部載っていれば空", unregistered_tests(fake) == [], repr(unregistered_tests(fake)))
    bare = types.ModuleType("fake_bare")
    bare.test_orphan = lambda: None
    check("TESTS が無いモジュールは test_ 関数がすべて未登録として返る",
          unregistered_tests(bare) == ["test_orphan"], repr(unregistered_tests(bare)))


def test_all_tests_registered() -> None:
    say("自己テスト: selftest_t_*・selftest_b_* の test_ 関数は全部 TESTS に載っている（載っていなければ関数名を出して落ちる）")
    here = os.path.dirname(os.path.abspath(__file__))
    missing: list[str] = []
    for path in sorted(glob.glob(os.path.join(here, "selftest_[tb]_*.py"))):
        module = importlib.import_module(os.path.basename(path)[: -len(".py")])
        missing += [f"{module.__name__}.{n}" for n in unregistered_tests(module)]
    check("並びに載っていない test_ 関数が無い", not missing, "並びに載っていない: " + ", ".join(missing))


def _schema_key(repo: str, key: str) -> dict:
    out = run_task(repo, "config", "--schema")
    return next(k for k in json.loads(out.stdout)["keys"] if k["key"] == key)


def _config_rows(repo: str) -> dict[str, list[str]]:
    return {r.split("\t")[0]: r.split("\t") for r in run_task(repo, "config").stdout.splitlines() if "\t" in r}


def test_config_flow_switches_step_candidates() -> None:
    say("自己テスト: 設定の欄 flow は既定 skills で、script にすると config --schema の steps の候補がワークフロースクリプトの段になる")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "flow")
        flow = _schema_key(repo, "flow")
        check("flow の既定は skills で、選択肢は skills と script",
              flow["default"] == "skills" and [v["value"] for v in flow["values"]] == ["skills", "script"], repr(flow))
        check("flow が skills の steps の候補はワークフロースクリプトの段の名前でない",
              "implement" not in [v["value"] for v in _schema_key(repo, "steps")["values"]])
        check("tw config の flow の行は skills・default", _config_rows(repo)["flow"][1:] == ["skills", "default"])
        done = run_task(repo, "config", "set", "flow", "script")
        check("flow を script にできる", done.returncode == 0 and done.stdout.strip() == "SET\tflow\tscript",
              done.stdout + done.stderr)
        steps = _schema_key(repo, "steps")
        check("flow が script の steps の候補と既定は implement・accept・retrospect",
              [v["value"] for v in steps["values"]] == ["implement", "accept", "retrospect"]
              and steps["default"] == ["implement", "accept", "retrospect"], repr(steps))
        set_steps = run_task(repo, "config", "set", "steps", '["implement","accept"]')
        check("flow が script なら steps にワークフロースクリプトの段の名前を書ける", set_steps.returncode == 0, set_steps.stdout + set_steps.stderr)
        check("tw config の steps の行はワークフロースクリプトの段の名前の配列", _config_rows(repo)["steps"][1] == '["implement","accept"]')
        bad = run_task(repo, "config", "set", "flow", "workflow")
        check("flow の値が skills・script の外なら終了コード2", bad.returncode == 2, bad.stdout + bad.stderr)
        mapped = tw_config.skill_steps(repo, layout.read_config(repo))
        check("スキルの全名で読む側には、ワークフロースクリプトの段をスキルの全名に読み替え、送り出しを末尾に足して渡す",
              [layout.step_short_name(s) for s in mapped] == ["implement", "accept", "ship"], repr(mapped))
        run_task(repo, "config", "set", "flow", "skills")
        back = tw_config.skill_steps(repo, layout.read_config(repo))
        check("script で書いた短い名前の steps は、flow を skills に戻してもスキルの全名に読み替え、送り出しを足す",
              [layout.step_short_name(s) for s in back] == ["implement", "accept", "ship"] and all("-step" in s for s in back),
              repr(back))
        row = json.loads(_config_rows(repo)["steps"][1])
        check("切り戻したあとの tw config の steps の行も、スキルの全名で送り出しを足した並びで出る",
              [layout.step_short_name(s) for s in row] == ["implement", "accept", "ship"] and all("-step" in s for s in row),
              repr(row))
        check("スキルの全名で書いた steps もワークフロースクリプトの段の名前に読み替えられ、送り出しは落ちる",
              layout.script_steps(["tsukumo-workflow:implement-step", "tsukumo-workflow:ship-step", "retrospect"])
              == ["implement", "retrospect"])


def _relay_out(wt: str, task_id: str, key: str) -> str:
    return run_task(wt, "relay", task_id, key).stdout


def _flow_passed(accept: bool = True) -> dict:
    result: dict = {"stopped": False, "conventionLines": ["- 規約: x"], "implement": {"ok": True}}
    if accept:
        result["accept"] = {
            "rounds": 1,
            "kind": "ok",
            "closed": True,
            "judgment": {
                "verdict": "ok",
                "findings": ["画面が狭い", "- 指摘: 余白が足りない"],
                "visual": "撮った画は崩れていない",
                "friction": "なし",
                "human": "不要",
                "notes": "- 補足: 一行目\n- 補足: 二行目",
            },
        }
        result["retrospect"] = {"signal": "none", "line": "- 振り返り: 兆候なし", "draftIds": []}
    return result


def test_flow_result_writes_relays_and_releases() -> None:
    say("tw flow-result: 通れば受け入れ・振り返りを書いて FINISH_READY、止まれば 再開 を書いて release し RELEASED に段と理由を足す")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, wt2 = make_repo(tmp)
        for n in range(100, 107):
            commit_task(main_path, taskfile.Task(f"T-{n}", f"既存タスク{n}", "todo", "sonnet", "Y", (), BODY))

        def flow(wt: str, task_id: str, payload: object, *extra: str):
            text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            return run_task(wt, "flow-result", task_id, "--json-file", "-", *extra, stdin=text)

        run_task(wt1, "claim", "T-100")
        r = flow(wt1, "T-100", _flow_passed())
        check("通った戻り値は終了コード0で最後の行が FINISH_READY",
              r.returncode == 0 and r.stdout.splitlines()[-1] == "FINISH_READY\tT-100", r.stdout + r.stderr)
        accepted = _relay_out(wt1, "T-100", "受け入れ")
        check("受け入れに判定・材料・指摘2行・目視・notes が書かれる",
              accepted.startswith("RELAY\tT-100\t受け入れ\n判定: 受け入れ\n- 材料: friction=なし human=不要\n")
              and "- 指摘: 画面が狭い\n- 指摘: 余白が足りない\n" in accepted
              and "- 目視: 撮った画は崩れていない\n" in accepted
              and accepted.rstrip().endswith("- 補足: 一行目\n- 補足: 二行目"), accepted)
        check("振り返りに retrospect.line が書かれる",
              _relay_out(wt1, "T-100", "振り返り") == "RELAY\tT-100\t振り返り\n- 振り返り: 兆候なし\n",
              _relay_out(wt1, "T-100", "振り返り"))
        check("実装・再開は書かれない",
              _relay_out(wt1, "T-100", "実装").startswith("NONE") and _relay_out(wt1, "T-100", "再開").startswith("NONE"))
        again = run_task(wt1, "relay", "T-100", "受け入れ", "--body-file", "-", stdin="x")
        check("通ったあとも着手の印は残る", again.stdout.startswith("RELAYED\tT-100"), again.stdout + again.stderr)
        run_task(wt1, "release", "T-100")

        run_task(wt1, "claim", "T-101")
        r = flow(wt1, "T-101", _flow_passed(accept=False))
        check("accept・retrospect が無い戻り値も FINISH_READY",
              r.returncode == 0 and r.stdout.splitlines()[-1] == "FINISH_READY\tT-101", r.stdout)
        check("走らなかった段の鍵は書かない",
              _relay_out(wt1, "T-101", "受け入れ").startswith("NONE") and _relay_out(wt1, "T-101", "振り返り").startswith("NONE"))
        run_task(wt1, "release", "T-101")

        run_task(wt1, "claim", "T-102")
        patch = os.path.join(tmp, "stopped.patch")
        r = flow(wt1, "T-102", {"stopped": True, "phase": "accept", "reason": "差し戻し\t3回"}, "--patch", patch)
        check("受け入れで止まると最後の行が段と理由つきの RELEASED",
              r.returncode == 0 and r.stdout.splitlines()[-1] == "RELEASED\tT-102\taccept\t差し戻し 3回", r.stdout + r.stderr)
        check("再開は1行目が段、2行目が patch のパス",
              _relay_out(wt1, "T-102", "再開") == f"RELAY\tT-102\t再開\naccept\n- patch: {patch}\n",
              _relay_out(wt1, "T-102", "再開"))
        check("止まった戻り値は受け入れを書かない", _relay_out(wt1, "T-102", "受け入れ").startswith("NONE"))
        released = run_task(wt1, "release", "T-102")
        check("止まったあとは release 済みで、もう一度の release は NOT_CLAIMED",
              released.stdout.startswith("NOT_CLAIMED\tT-102"), released.stdout)
        releases = {r["task"]: r for r in flow_rows(wt1) if r.get("event") == "release"}
        check("止まった release の台帳の行には stuck が真で入る", releases.get("T-102", {}).get("stuck") is True, str(releases.get("T-102")))
        check("手で外した release の行には stuck が無い", "T-100" in releases and "stuck" not in releases["T-100"], str(releases.get("T-100")))

        run_task(wt1, "claim", "T-103")
        r = flow(wt1, "T-103", {"stopped": True, "phase": "implement", "reason": "判断が要る"})
        check("実装で止まると RELEASED に implement と理由",
              r.stdout.splitlines()[-1] == "RELEASED\tT-103\timplement\t判断が要る", r.stdout + r.stderr)
        check("patch が無ければ再開は段の1行だけ",
              _relay_out(wt1, "T-103", "再開") == "RELAY\tT-103\t再開\nimplement\n", _relay_out(wt1, "T-103", "再開"))

        run_task(wt1, "claim", "T-104")
        write(os.path.join(wt1, "leftover.txt"), "leftover\n")
        r = flow(wt1, "T-104", {"stopped": True, "phase": "retrospect", "reason": "r"})
        rows = r.stdout.splitlines()
        check("未コミットの変更があれば RELEASED の前に LEFTOVER が出て、最後の行は RELEASED",
              len(rows) >= 2 and rows[-2] == "LEFTOVER\tT-104\t1" and rows[-1] == "RELEASED\tT-104\tretrospect\tr", r.stdout)
        os.remove(os.path.join(wt1, "leftover.txt"))

        run_task(wt1, "claim", "T-105")
        r = flow(wt1, "T-105", "{ここは JSON でない")
        check("壊れた JSON は終了コード2で何も書かない",
              r.returncode == 2 and _relay_out(wt1, "T-105", "再開").startswith("NONE")
              and _relay_out(wt1, "T-105", "受け入れ").startswith("NONE"), r.stdout + r.stderr)
        check("壊れた JSON のあとも着手の印は残る",
              run_task(wt1, "relay", "T-105", "受け入れ", "--body-file", "-", stdin="x").stdout.startswith("RELAYED"))
        r = flow(wt2, "T-105", _flow_passed())
        check("着手の印を持たない作業ツリーは NOT_OWNER・終了コード4で何も書かない",
              r.returncode == 4 and r.stdout.strip() == "NOT_OWNER\tT-105"
              and _relay_out(wt1, "T-105", "振り返り").startswith("NONE"), r.stdout + r.stderr)


def test_claim_holds_task_stopped_twice() -> None:
    say("tw claim: 再開 つきで2回止まったタスクは掴まず hold にして HELD_STUCK・終了コード4。手で外すだけの release は数えない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        for n in (107, 108):
            commit_task(main_path, taskfile.Task(f"T-{n}", f"既存タスク{n}", "todo", "sonnet", "Y", (), BODY))

        def stop(task_id: str, phase: str) -> None:
            text = json.dumps({"stopped": True, "phase": phase, "reason": "r"}, ensure_ascii=False)
            run_task(wt1, "flow-result", task_id, "--json-file", "-", stdin=text)

        r = run_task(wt1, "claim", "T-107")
        stop("T-107", "implement")
        r = run_task(wt1, "claim", "T-107")
        check("1回止まったタスクは CLAIMED で掴める", r.stdout.startswith("CLAIMED\tT-107"), r.stdout + r.stderr)
        stop("T-107", "accept")
        r = run_task(wt1, "claim", "T-107")
        check("2回止まったあとは HELD_STUCK\\tT-107\\t2・終了コード4",
              r.returncode == 4 and r.stdout.startswith("HELD_STUCK\tT-107\t2\n"), r.stdout + r.stderr)
        shown = run_task(wt1, "show", "T-107").stdout
        check("status が hold で、解除の条件と最後の止まりの 再開 が書かれる",
              "status: hold" in shown and "止まった理由を人が読み、計画を直すか分けたら `todo` に戻す" in shown
              and "accept" in shown.split("## 注意")[-1], shown)
        check("着手の印は立たない", run_task(wt1, "release", "T-107").stdout.startswith("NOT_CLAIMED\tT-107"))
        run_task(wt1, "edit", "T-107", "--status", "todo")
        r = run_task(wt1, "claim", "T-107")
        check("todo に戻したあとは数え直して CLAIMED", r.stdout.startswith("CLAIMED\tT-107"), r.stdout + r.stderr)

        for _ in range(3):
            r = run_task(wt1, "claim", "T-108")
            run_task(wt1, "release", "T-108")
        check("止まらず release だけを繰り返したタスクは3回目も CLAIMED", r.stdout.startswith("CLAIMED\tT-108"), r.stdout + r.stderr)


_RUN_START = datetime(2026, 11, 8, 1, 0, 0, tzinfo=timezone.utc)


def _iso(seconds: float, base: datetime = _RUN_START) -> str:
    return (base + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_run(root: str, agents: list[tuple[str, str, object, tuple[float, float] | None]], noise: bool = False) -> str:
    """作り物の run の記録。`agents` は `(agentId, label, result, (始めの秒, 終わりの秒))`。
    `result` が `None` なら `started` だけ、時刻が `None` なら transcript を置かない。"""
    os.makedirs(root)
    lines = [json.dumps({"type": "launched"})]
    for agent_id, label, result, span in agents:
        phase = "accept" if label.startswith(("accept", "fix")) else label
        lines.append(json.dumps({"type": "started", "key": agent_id, "agentId": agent_id, "label": label, "phase": phase}))
        if noise:
            lines.append("{壊れた行")
        if result is not None:
            lines.append(json.dumps({"type": "result", "key": agent_id, "agentId": agent_id, "result": result,
                                     "toolsUsed": []}, ensure_ascii=False))
        if span is not None:
            write(os.path.join(root, f"agent-{agent_id}.jsonl"),
                  "".join(json.dumps({"timestamp": _iso(t), "message": "秘密の会話ABC"}, ensure_ascii=False) + "\n"
                          for t in span))
    write(os.path.join(root, "journal.jsonl"), "\n".join(lines) + "\n")
    return root


_PASSED_RUN = [
    ("a1", "implement", {"stopped": False}, (0, 120.5)),
    ("a2", "accept-prepare-1", {"kind": "code"}, (130, 140)),
    ("a3", "acceptor-1", {"verdict": "reject", "notes": "秘密の本文XYZ"}, (150, 210)),
    ("a4", "fix-1", {"stopped": False}, (220, 250)),
    ("a5", "acceptor-2", {"verdict": "accept"}, (260, 300)),
    ("a6", "retrospect", {"line": "- 振り返り: x"}, (310, 325)),
]


def test_flow_result_writes_journal_stages() -> None:
    say("tw flow-result --transcript-dir: run の journal から段の区間・差し戻し・止まった段を台帳に書き、本文は写さない")
    with tempfile.TemporaryDirectory() as tmp:
        main_path, wt1, _wt2 = make_repo(tmp)
        for n in range(110, 115):
            commit_task(main_path, taskfile.Task(f"T-{n}", f"既存タスク{n}", "todo", "sonnet", "Y", (), BODY))

        def flow(task_id: str, payload: dict, *extra: str):
            return run_task(wt1, "flow-result", task_id, "--json-file", "-", *extra, stdin=json.dumps(payload))

        def stages_of(task_id: str) -> list[dict]:
            return [r for r in flow_rows(wt1) if r.get("event") == "flow_stage" and r.get("task") == task_id]

        run_task(wt1, "claim", "T-110")
        passed = _write_run(os.path.join(tmp, "run-passed"), _PASSED_RUN)
        r = flow("T-110", {"stopped": False}, "--transcript-dir", passed)
        check("通った戻り値は FINISH_READY", r.returncode == 0 and r.stdout.splitlines()[-1] == "FINISH_READY\tT-110",
              r.stdout + r.stderr)
        rows = stages_of("T-110")
        check("flow_stage は journal の順に delegate・review・delegate・review・retro（受け入れの準備は書かない）",
              [(row["stage"], row["label"]) for row in rows]
              == [("delegate", "implement"), ("review", "acceptor-1"), ("delegate", "fix-1"), ("review", "acceptor-2"),
                  ("retro", "retrospect")], repr(rows))
        check("seconds は transcript の先頭行と末尾行の時刻の差（秒未満は切り捨て）",
              [row["seconds"] for row in rows] == [120, 60, 30, 40, 15], repr(rows))
        check("start・end は transcript の時刻、source は journal",
              rows[0]["start"] == _iso(0) and rows[0]["end"] == _iso(120.5) and all(row["source"] == "journal" for row in rows),
              repr(rows[0]))
        check("verdict は受け入れ役の行だけで reject・accept（差し戻し1回）",
              [row.get("verdict") for row in rows] == [None, "reject", None, "accept", None], repr(rows))
        ledger_text = json.dumps(flow_rows(wt1), ensure_ascii=False)
        check("台帳に agent の戻り値の本文も transcript の本文も出ない",
              "秘密の本文XYZ" not in ledger_text and "秘密の会話ABC" not in ledger_text, ledger_text)
        run_task(wt1, "release", "T-110")

        run_task(wt1, "claim", "T-111")
        stopped = _write_run(os.path.join(tmp, "run-stopped"), _PASSED_RUN[:4] + [
            ("a5", "acceptor-2", {"verdict": "reject"}, (260, 300)),
            ("a6", "fix-2", None, (310, 320)),
        ])
        r = flow("T-111", {"stopped": True, "phase": "accept", "reason": "差し戻し"}, "--transcript-dir", stopped)
        check("止まった戻り値は RELEASED", r.stdout.splitlines()[-1] == "RELEASED\tT-111\taccept\t差し戻し", r.stdout + r.stderr)
        stops = [row for row in flow_rows(wt1) if row.get("event") == "flow_stopped" and row.get("task") == "T-111"]
        check("flow_stopped に phase と result の無い最後の agent の label",
              [(row["phase"], row["label"]) for row in stops] == [("accept", "fix-2")], repr(stops))
        check("止まっても届いた段は書き、result の無い fix-2 は区間にしない",
              [row["label"] for row in stages_of("T-111")] == ["implement", "acceptor-1", "fix-1", "acceptor-2"],
              repr(stages_of("T-111")))

        run_task(wt1, "claim", "T-112")
        before = len(flow_rows(wt1))
        r = flow("T-112", {"stopped": False}, "--transcript-dir", os.path.join(tmp, "無いディレクトリ"))
        check("--transcript-dir のディレクトリが無ければ終了コード2で台帳に何も書かない",
              r.returncode == 2 and len(flow_rows(wt1)) == before, r.stdout + r.stderr)
        r = flow("T-112", {"stopped": False})
        check("--transcript-dir の無い旧い呼び方は flow_stage を書かない",
              r.returncode == 0 and stages_of("T-112") == [], r.stdout + r.stderr)
        run_task(wt1, "release", "T-112")

        run_task(wt1, "claim", "T-113")
        noisy = _write_run(os.path.join(tmp, "run-noisy"), [
            ("a1", "implement", {"stopped": False}, (0, 10)),
            ("a2", "acceptor-1", {"verdict": "accept"}, None),
            ("a3", "acceptor-2", "文字列の戻り値", (20, 30)),
            ("a4", "retrospect", {}, (40, 45)),
        ], noise=True)
        write(os.path.join(noisy, "agent-a4.jsonl"), "{壊れた行\n")
        r = flow("T-113", {"stopped": False}, "--transcript-dir", noisy)
        check("壊れた行・transcript の無い agent・時刻の読めない transcript を混ぜても落ちず、読めた段だけ書く",
              r.returncode == 0 and [(row["label"], row.get("verdict")) for row in stages_of("T-113")]
              == [("implement", None), ("acceptor-2", "other")], r.stdout + r.stderr + repr(stages_of("T-113")))


def test_metrics_counts_flow_stage_once() -> None:
    say("tw metrics --stages: flow_stage のある着手は委譲・レビューを flow_stage だけから1回ずつ数え、旧い着手は lap から数える")
    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)

    def _event(offset: float, kind: str, task: str, **fields: object) -> metrics.Event:
        return metrics.Event(base + timedelta(seconds=offset), kind, task, {"difficulty": "opus", **fields})

    new_run = [
        _event(0, "claim", "T-1"),
        _event(10, "lap", "T-1", stage="delegate", edge="start"),
        _event(100, "lap", "T-1", stage="delegate", edge="end"),
        _event(110, "lap", "T-1", stage="review", edge="start"),
        _event(300, "flow_stage", "T-1", stage="delegate", start=_iso(10, base), end=_iso(95.5, base), seconds=85),
        _event(301, "flow_stage", "T-1", stage="review", start=_iso(120, base), end=_iso(180, base), seconds=60,
               verdict="accept"),
        _event(400, "ship", "T-1", result="SHIPPED"),
    ]
    old_run = [
        _event(0, "claim", "T-2"),
        _event(10, "lap", "T-2", stage="delegate", edge="start"),
        _event(70, "lap", "T-2", stage="delegate", edge="end"),
        _event(80, "lap", "T-2", stage="review", edge="start"),
        _event(200, "ship", "T-2", result="SHIPPED"),
    ]
    durations, opens = metrics.stage_durations(new_run, 7)
    check("新しい着手の委譲は flow_stage の1回だけ（lap と足して2回にならない）",
          durations.get(("委譲", "opus", "normal")) == [85.0], repr(durations))
    check("新しい着手のレビューは flow_stage の1回だけ", durations.get(("レビュー", "opus", "normal")) == [60.0], repr(durations))
    check("新しい着手の計画は claim から最初の lap までのまま", durations.get(("計画", "opus", "normal")) == [10.0], repr(durations))
    check("新しい着手では lap の閉じ残り（review の start）を OPEN に数えない", opens == {}, repr(opens))
    durations, opens = metrics.stage_durations(old_run, 7)
    check("flow_stage の無い旧い着手は lap の対から委譲を数える", durations.get(("委譲", "opus", "normal")) == [60.0], repr(durations))
    check("旧い着手は lap の閉じ残りを OPEN に数える", opens == {"レビュー": 1}, repr(opens))
    spans = metrics.task_stage_spans(new_run, "T-1")
    check("task_stage_spans も委譲の区間を flow_stage の1つだけ返す",
          spans is not None and [p for p in spans[0] if p[0] == "委譲"] == [
              ("委譲", base + timedelta(seconds=10), base + timedelta(seconds=95))], repr(spans))


TESTS = (
    test_unregistered_tests_named,
    test_all_tests_registered,
    test_config_flow_switches_step_candidates,
    test_flow_result_writes_relays_and_releases,
    test_flow_result_writes_journal_stages,
    test_metrics_counts_flow_stage_once,
    test_claim_holds_task_stopped_twice,
)


if __name__ == "__main__":
    refuse_direct_run()

