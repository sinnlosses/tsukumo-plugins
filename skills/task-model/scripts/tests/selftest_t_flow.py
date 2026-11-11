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


READONLY_KEYS = ("store", "tracker", "github_project", "backup", "base_branch", "loop_max_ships")


def _entry_skill(root: str, name: str, mark: str, title: str | None = None) -> str:
    meta = f'  workflow-entry: "{mark}"\n'
    if title is not None:
        meta += f'  workflow-entry-title: "{title}"\n'
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


def test_config_flow_is_script_only() -> None:
    say("自己テスト: 設定の欄 flow の既定と値は script だけで、skills は script として読み、set は拒む。steps の候補は常にワークフロースクリプトの段")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "flow")
        flow = _schema_key(repo, "flow")
        check("flow の既定は script で、選択肢は script だけ",
              flow["default"] == "script" and [v["value"] for v in flow["values"]] == ["script"], repr(flow))
        steps = _schema_key(repo, "steps")
        check("steps の候補と既定は implement・accept・retrospect",
              [v["value"] for v in steps["values"]] == ["implement", "accept", "retrospect"]
              and steps["default"] == ["implement", "accept", "retrospect"], repr(steps))
        check("tw config の flow の行は script・default", _config_rows(repo)["flow"][1:] == ["script", "default"])
        check("tw config の steps の行は既定の3段", json.loads(_config_rows(repo)["steps"][1]) == ["implement", "accept", "retrospect"])
        done = run_task(repo, "config", "set", "flow", "script")
        check("flow を script にできる", done.returncode == 0 and done.stdout.strip() == "SET\tflow\tscript",
              done.stdout + done.stderr)
        set_steps = run_task(repo, "config", "set", "steps", '["implement","accept"]')
        check("steps にワークフロースクリプトの段の名前を書ける", set_steps.returncode == 0, set_steps.stdout + set_steps.stderr)
        check("tw config の steps の行はワークフロースクリプトの段の名前の配列", _config_rows(repo)["steps"][1] == '["implement","accept"]')
        for value in ("skills", "workflow"):
            bad = run_task(repo, "config", "set", "flow", value)
            check(f"flow を {value} にする set は終了コード2", bad.returncode == 2, bad.stdout + bad.stderr)
        config_path = os.path.join(repo, ".tw", "config.toml")
        with open(config_path, encoding="utf-8") as f:
            text = f.read()
        write(config_path, text.replace('flow = "script"', 'flow = "skills"'))
        legacy = layout.read_config(repo)
        check("設定に flow = \"skills\" が残っていても script として読む", legacy.flow == "script", repr(legacy.flow))
        check("そのときの tw config も落ちず、flow の行は script",
              _config_rows(repo)["flow"][1] == "script", repr(_config_rows(repo).get("flow")))
        old_name = lambda step: "tsukumo-workflow:" + step + "-step"
        check("旧いスキルの全名で書いた steps もワークフロースクリプトの段の名前に読み替えられ、送り出しは落ちる",
              layout.script_steps([old_name("implement"), old_name("ship"), "retrospect"]) == ["implement", "retrospect"])


def test_config_commit_task_id() -> None:
    say("自己テスト: 設定の欄 commit_task_id の既定は 付ける で、set は 付ける・付けない だけを書ける")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "commit-task-id")
        field = _schema_key(repo, "commit_task_id")
        check("commit_task_id の既定は 付ける で、選択肢は 付ける・付けない",
              field["default"] == "付ける" and [v["value"] for v in field["values"]] == ["付ける", "付けない"], repr(field))
        check("tw config の commit_task_id の行は 付ける・default", _config_rows(repo)["commit_task_id"][1:] == ["付ける", "default"])
        done = run_task(repo, "config", "set", "commit_task_id", "付けない")
        check("commit_task_id を 付けない にできる", done.returncode == 0 and done.stdout.strip() == "SET\tcommit_task_id\t付けない",
              done.stdout + done.stderr)
        check("tw config の commit_task_id の行は 付けない・config", _config_rows(repo)["commit_task_id"][1:] == ["付けない", "config"])
        bad = run_task(repo, "config", "set", "commit_task_id", "off")
        check("語彙の外の値を書く set は終了コード2", bad.returncode == 2, bad.stdout + bad.stderr)


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
    ("a3", "acceptor-1", {"verdict": "reject", "notes": "秘密の本文XYZ", "findings": ["秘密の指摘P", "秘密の指摘Q"],
                          "findingKinds": ["正しさ", "規約"]}, (150, 210)),
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
        check("flow_stage は journal の順に delegate・review・delegate・review・retro",
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
        check("差し戻しの行だけ findings（件数）と kinds（種類ごとの件数）を持ち、accept などは持たない",
              [(row.get("findings"), row.get("kinds")) for row in rows]
              == [(None, None), (2, {"正しさ": "1", "規約": "1"}), (None, None), (None, None), (None, None)], repr(rows))
        ledger_text = json.dumps(flow_rows(wt1), ensure_ascii=False)
        check("台帳に agent の戻り値の本文も指摘の文も transcript の本文も出ない",
              all(secret not in ledger_text for secret in ("秘密の本文XYZ", "秘密の指摘P", "秘密の指摘Q", "秘密の会話ABC")),
              ledger_text)
        run_task(wt1, "release", "T-110")

        run_task(wt1, "claim", "T-111")
        stopped = _write_run(os.path.join(tmp, "run-stopped"), _PASSED_RUN[:3] + [
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

        run_task(wt1, "claim", "T-114")
        odd = _write_run(os.path.join(tmp, "run-odd-kinds"), [
            ("b1", "acceptor-1", {"verdict": "reject", "findings": ["x", "y", "z"],
                                  "findingKinds": ["正しさ", "謎の種類"]}, (0, 10)),
            ("b2", "acceptor-2", {"verdict": "reject", "findings": ["x", "y"], "findingKinds": ["規約", "簡素化", "完了条件"]},
             (20, 30)),
            ("b3", "acceptor-3", {"verdict": "reject", "findings": ["x", "y"]}, (40, 50)),
            ("b4", "acceptor-4", {"verdict": "reject"}, (60, 70)),
        ])
        flow("T-114", {"stopped": False}, "--transcript-dir", odd)
        odd_rows = stages_of("T-114")
        check("語彙外・足りない種類は不明に入り、超えた種類は数えず、kinds の合計は findings に等しい",
              [(row["findings"], row["kinds"]) for row in odd_rows[:2]]
              == [(3, {"正しさ": "1", "不明": "2"}), (2, {"規約": "1", "簡素化": "1"})], repr(odd_rows))
        check("findingKinds の無い差し戻しは件数がすべて不明、findings も無ければ 0 件と空の kinds",
              [(row["findings"], row["kinds"]) for row in odd_rows[2:]] == [(2, {"不明": "2"}), (0, {})], repr(odd_rows))
        run_task(wt1, "release", "T-114")

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


def test_metrics_counts_finding_kinds() -> None:
    say("finding_kind_lines: 期間内の受け入れ役の flow_stage から差し戻しの着手の数と種類ごとの件数を出し、旧い行・期間外は種類に数えない")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start, end = now - timedelta(days=28), now + timedelta(seconds=1)

    def _review(days_ago: float, task: str, verdict: str, **fields: object) -> metrics.Event:
        return metrics.Event(now - timedelta(days=days_ago), "flow_stage", task, {"stage": "review", "verdict": verdict, **fields})

    events = [
        _review(1, "T-1", "reject", findings=3, kinds={"正しさ": "2", "規約": "1"}),
        _review(1, "T-1", "reject", findings=1, kinds={"不明": "1"}),
        _review(1, "T-1", "accept"),
        _review(2, "T-2", "accept"),
        _review(3, "T-3", "reject"),
        _review(40, "T-4", "reject", findings=5, kinds={"簡素化": "5"}),
        metrics.Event(now - timedelta(days=1), "flow_stage", "T-5", {"stage": "delegate", "kinds": {"規約": "9"}}),
    ]
    check("差し戻しの着手の数と届いた着手の数、種類は4種類と不明の順で0の行も出す",
          metrics.finding_kind_lines(events, start, end) == [
              "REJECTED_RUNS\t2\t3", "FINDING_KIND\t完了条件\t0", "FINDING_KIND\t正しさ\t2", "FINDING_KIND\t規約\t1",
              "FINDING_KIND\t簡素化\t0", "FINDING_KIND\t不明\t1"], repr(metrics.finding_kind_lines(events, start, end)))
    check("flow_stage が無い・期間に受け入れ役の行が無ければ空の一覧",
          metrics.finding_kind_lines([], start, end) == [] and metrics.finding_kind_lines(events[5:], start, end) == [])


def test_metrics_counts_reverts() -> None:
    say("tw metrics: revert_within_7d は送り出しから7日以内に revert されたタスクを1本ずつ数え、reverts が無ければ - を返す")
    tmp = tempfile.mkdtemp()
    try:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "test")
        for name, subject in (("a", "T-1: a"), ("b", "no task id here"), ("c", "T-3, c")):
            write(os.path.join(repo, name), name)
            git(repo, "add", name)
            git(repo, "commit", "-q", "-m", subject)
        shas = git(repo, "log", "--format=%H", "--reverse").stdout.split()
        for sha in shas:
            git(repo, "revert", "--no-edit", sha)
        reverts = metrics.read_reverts(repo, "main")
        check("revert の元の件名の先頭の課題の ID を引く（引けない件名は読み飛ばす）",
              sorted(r.task for r in reverts) == ["T-1", "T-3"] and all(len(r.sha) == 40 for r in reverts),
              repr(reverts))
    finally:
        shutil.rmtree(tmp)

    base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=30)

    def _ship(task: str, days: float) -> metrics.Event:
        return metrics.Event(base + timedelta(days=days), "ship", task, {"result": "SHIPPED"})

    def _revert(task: str, days: float) -> metrics.Revert:
        return metrics.Revert(task, "0" * 40, base + timedelta(days=days))

    shipped = [_ship("T-1", 0), _ship("T-2", 0), _ship("T-3", 1)]
    got = metrics.revert_tasks(
        shipped, [_revert("T-1", 2), _revert("T-1", 3), _revert("T-2", 8), _revert("T-9", 1)]
    )
    check("7日以内の revert だけ数え、何度 revert されても1本で、送っていないタスクは数えない", got == {"T-1"}, repr(got))
    start, end = base - timedelta(days=1), base + timedelta(days=5)
    check("column_between は reverts が None なら revert_within_7d を - にする",
          metrics.column_between(list(shipped), start, end)["revert_within_7d"] == "-")
    check("column_between は reverts があれば revert_within_7d に本数を出す",
          metrics.column_between(list(shipped), start, end, [_revert("T-1", 2)])["revert_within_7d"] == "1")


def test_metrics_escapes_lists_candidates() -> None:
    say("tw metrics --escapes: 元の送り出しから7日以内の bug で同じファイルを変えたもの・revert を候補に出し、結び済みなどは出さない")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    base = now - timedelta(days=20)

    def _ship(task: str, days: float) -> metrics.Event:
        return metrics.Event(base + timedelta(days=days), "ship", task, {"result": "SHIPPED"})

    files = {
        "T-1": {"a.py", "b.py"},
        "T-2": {"b.py", "z.py"},
        "T-3": {"b.py"},
        "T-4": {"y.py"},
        "T-5": {"a.py"},
        "T-6": {"a.py"},
    }
    kinds = {"T-2": "bug", "T-3": "feature", "T-4": "bug", "T-5": "bug", "T-6": "bug"}
    events = [_ship("T-1", 0), _ship("T-2", 1), _ship("T-3", 2), _ship("T-4", 3), _ship("T-5", 4), _ship("T-6", 14)]

    def run(linked: set[tuple[str, str]], reverts: list[metrics.Revert] | None = None, days: int = 28) -> list[str]:
        return metrics.escape_candidates(
            events, lambda t: files.get(t, set()), lambda t: kinds.get(t), linked, days, reverts, now
        )

    got = run({("T-5", "T-1")})
    check("同じファイルを変えた7日以内の bug だけ出す（7日超・bug でない・ファイルが重ならない・結び済みは出さない）",
          got == ["ESCAPE\tbug\tT-1\tT-2\tb.py"], repr(got))
    check("結ばれていなければ別の bug の候補も出る", "ESCAPE\tbug\tT-1\tT-5\ta.py" in run(set()), repr(run(set())))
    revert = metrics.Revert("T-1", "f" * 40, base + timedelta(days=2))
    check("7日以内の revert を ESCAPE revert で出す",
          f"ESCAPE\trevert\tT-1\t{'f' * 40}" in run({("T-5", "T-1")}, [revert]), repr(run(set(), [revert])))
    check("何も無ければ空（呼ぶ側が NOTHING を出す）", metrics.escape_candidates([], lambda t: set(), lambda t: None, set(), 28, [], now) == [])
    check("期間の外の元は候補にしない", run(set(), days=1) == [], repr(run(set(), days=1)))


TESTS = (
    test_unregistered_tests_named,
    test_all_tests_registered,
    test_config_flow_is_script_only,
    test_config_commit_task_id,
    test_flow_result_writes_relays_and_releases,
    test_flow_result_writes_journal_stages,
    test_metrics_counts_flow_stage_once,
    test_metrics_counts_finding_kinds,
    test_metrics_counts_reverts,
    test_metrics_escapes_lists_candidates,
    test_claim_holds_task_stopped_twice,
)


if __name__ == "__main__":
    refuse_direct_run()

