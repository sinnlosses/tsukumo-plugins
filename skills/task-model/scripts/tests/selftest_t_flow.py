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
    say("自己テスト: 設定の欄 flow は既定 skills で、script にすると config --schema の steps の候補が台本の段になる")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_config_doctor_repo(tmp, "flow")
        flow = _schema_key(repo, "flow")
        check("flow の既定は skills で、選択肢は skills と script",
              flow["default"] == "skills" and [v["value"] for v in flow["values"]] == ["skills", "script"], repr(flow))
        check("flow が skills の steps の候補は台本の段の名前でない",
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
        check("flow が script なら steps に台本の段の名前を書ける", set_steps.returncode == 0, set_steps.stdout + set_steps.stderr)
        check("tw config の steps の行は台本の段の名前の配列", _config_rows(repo)["steps"][1] == '["implement","accept"]')
        bad = run_task(repo, "config", "set", "flow", "workflow")
        check("flow の値が skills・script の外なら終了コード2", bad.returncode == 2, bad.stdout + bad.stderr)
        mapped = tw_config.skill_steps(repo, layout.read_config(repo))
        check("スキルの全名で読む側には、台本の段をスキルの全名に読み替え、送り出しを末尾に足して渡す",
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
        check("スキルの全名で書いた steps も台本の段の名前に読み替えられ、送り出しは落ちる",
              layout.script_steps(["tsukumo-workflow:implement-step", "tsukumo-workflow:ship-step", "retrospect"])
              == ["implement", "retrospect"])


TESTS = (
    test_unregistered_tests_named,
    test_all_tests_registered,
    test_config_flow_switches_step_candidates,
)


if __name__ == "__main__":
    refuse_direct_run()

