#!/usr/bin/env python3
"""リポジトリ全体の整合を見る（スキルの中身の良し悪しではなく、**ズレ**を見る）。

使い方: python3 scripts/check_repo.py

見るもの:
- 各 SKILL.md の frontmatter が読めて、`name` がディレクトリ名と一致すること
- 各 `agents/*.md`（`install.sh` が `~/.claude/agents/` へ張るエージェント定義）の frontmatter が
  読めて、`name` がファイル名と一致し、`description` があること。`no-delegate` の frontmatter が
  コミットを拒む hook（`tw commit-guard`）と返却を拒む hook（`tw handback-guard`）を持つこと。
  `acceptor` が `model: opus` で、`Agent`・`Edit`・`Write`・`NotebookEdit` を持たず hooks も持たないこと
- README の「由来」一覧が `skills/` と過不足なく一致すること（README が索引なので）
- スキル同士の相互参照が、実在するスキルか `OPTIONAL_SKILLS`（あれば使う外のスキル）を指していること
- スクリプトのパスが `${CLAUDE_SKILL_DIR}` 形で書かれ、実在するファイルを指していること
  （`OPTIONAL_SKILLS` の中を指すものは除く）
- 同梱スクリプトが構文として読めること
- `.claude-plugin/plugin.json` の `name` が `tsukumo-workflow` であること、`bin/tw` が実行でき `task.py` を
  呼ぶこと、`hooks/hooks.json` が `--agent-scoped` 付きで `${CLAUDE_PLUGIN_ROOT}/bin/tw` を呼ぶ hook 3つを持つこと
- `tw` の指す `task.py` が実行でき、スキルの Markdown が `task.py` を `python3` で呼ぶ形や `` `task …` `` の略記で書いていないこと
- 兄弟スキルの `scripts/` を `sys.path` に足して `import` しているなら、`REQUIRES` にその
  兄弟スキル名があること
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_NAME = "tsukumo-workflow"
SKILLS = os.path.join(ROOT, "skills")
AGENTS = os.path.join(ROOT, "agents")

# このリポジトリに無く、スキルの本文が「一覧にあれば使う」とだけ書いてよい外のスキル。
OPTIONAL_SKILLS = (
    "code-review",
    "comment-audit",
    "resolving-merge-conflicts",
    "token-usage-diet",
    "verifying-before-completion",
    "writing-for-agents",
)

# `agents/no-delegate.md` の frontmatter の hooks に要る行。
NO_DELEGATE_HOOK_LINES = (
    "command: tw commit-guard 2>/dev/null || true",
    "command: tw handback-guard 2>/dev/null || true",
)

ACCEPTOR_DISALLOWED_TOOLS = ("Agent", "Edit", "Write", "NotebookEdit")

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


def skill_names() -> list[str]:
    return sorted(
        n for n in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, n))
    )


def agent_names() -> list[str]:
    if not os.path.isdir(AGENTS):
        return []
    return sorted(f[:-3] for f in os.listdir(AGENTS) if f.endswith(".md"))


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def frontmatter(text: str) -> dict:
    """`key: value` だけの浅い frontmatter を読む（YAML パーサに依存しない）。"""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[4:end].splitlines():
        if line.startswith(" ") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip('"')
    return out


def check_frontmatter(names: list[str]) -> None:
    for n in names:
        path = os.path.join(SKILLS, n, "SKILL.md")
        if not os.path.exists(path):
            fail(f"{n}: SKILL.md が無い")
            continue
        fm = frontmatter(read(path))
        if not fm:
            fail(f"{n}: frontmatter を読めない")
            continue
        if fm.get("name") != n:
            fail(f"{n}: frontmatter の name が {fm.get('name')!r} でディレクトリ名と違う")
        if not fm.get("description"):
            fail(f"{n}: description が無い")


def check_agent_frontmatter(names: list[str]) -> None:
    for n in names:
        path = os.path.join(AGENTS, f"{n}.md")
        fm = frontmatter(read(path))
        if not fm:
            fail(f"agents/{n}.md: frontmatter を読めない")
            continue
        if fm.get("name") != n:
            fail(f"agents/{n}.md: frontmatter の name が {fm.get('name')!r} でファイル名と違う")
        if not fm.get("description"):
            fail(f"agents/{n}.md: description が無い")
    if "no-delegate" in names:
        text = read(os.path.join(AGENTS, "no-delegate.md"))
        head = text[: text.find("\n---\n", 3)]
        lines = [line.strip() for line in head.splitlines()]
        for hook_line in NO_DELEGATE_HOOK_LINES:
            if hook_line not in lines:
                fail(f"agents/no-delegate.md: frontmatter に hook の行 {hook_line!r} が無い")
    if "acceptor" in names:
        text = read(os.path.join(AGENTS, "acceptor.md"))
        head = text[: text.find("\n---\n", 3)]
        fm = frontmatter(text) or {}
        if str(fm.get("model", "")).strip() != "opus":
            fail("agents/acceptor.md: frontmatter の model が opus でない")
        denied = {t.strip() for t in str(fm.get("disallowedTools", "")).split(",")}
        for tool in ACCEPTOR_DISALLOWED_TOOLS:
            if tool not in denied:
                fail(f"agents/acceptor.md: disallowedTools に {tool} が無い")
        if "hooks:" in head:
            fail("agents/acceptor.md: frontmatter に hooks を持たせない")


def check_readme_index(names: list[str]) -> None:
    text = read(os.path.join(ROOT, "README.md"))
    try:
        section = text.split("## 由来")[1].split("\n## ")[0]
    except IndexError:
        fail("README に「## 由来」節が無い")
        return
    listed = set(re.findall(r"`([a-z][a-z0-9-]+)`", section))
    listed &= set(names) | (listed - set(names))
    missing = sorted(set(names) - listed)
    extra = sorted(n for n in listed if n not in names and "-" in n)
    if missing:
        fail(f"README の由来一覧に載っていないスキル: {', '.join(missing)}")
    if extra:
        fail(f"README の由来一覧にあるが実在しない: {', '.join(extra)}")
    m = re.search(r"全(\d+)スキル", section)
    if not m:
        fail("README の由来節に「全Nスキル」の記載が無い")
    elif int(m.group(1)) != len(names):
        fail(f"README は全{m.group(1)}スキルと書いているが実際は{len(names)}件")


def markdown_files(name: str) -> list[str]:
    out = []
    for dirpath, _, files in os.walk(os.path.join(SKILLS, name)):
        out += [os.path.join(dirpath, f) for f in files if f.endswith(".md")]
    return out


def check_script_paths(names: list[str]) -> None:
    """`${CLAUDE_SKILL_DIR}/...` が実在するファイルを指しているか。"""
    pat = re.compile(r"\$\{CLAUDE_SKILL_DIR\}(/[\w./-]+)")
    for n in names:
        for md in markdown_files(n):
            for rel in pat.findall(read(md)):
                target = os.path.normpath(os.path.join(SKILLS, n, rel.lstrip("/")))
                if os.path.relpath(target, SKILLS).split(os.sep)[0] in OPTIONAL_SKILLS:
                    continue
                if not os.path.exists(target):
                    fail(f"{os.path.relpath(md, ROOT)}: ${{CLAUDE_SKILL_DIR}}{rel} が実在しない")
    # 絶対パス・曖昧なプレースホルダは書かない（リポジトリを移すと壊れる）。
    for n in names:
        for md in markdown_files(n):
            body = read(md)
            for bad in ("<このスキルのパス>", "<task-workflowスキルのディレクトリ>"):
                if bad in body:
                    fail(f"{os.path.relpath(md, ROOT)}: {bad} は ${{CLAUDE_SKILL_DIR}} で書く")
            if re.search(r"python3 [^\n`]*/Users/", body):
                fail(f"{os.path.relpath(md, ROOT)}: スクリプトの実行に絶対パスを埋めている")


TW_TARGET = os.path.join(SKILLS, "task-workflow", "scripts", "task.py")


def check_tw_entry(names: list[str]) -> None:
    if not os.path.exists(TW_TARGET):
        fail(f"{os.path.relpath(TW_TARGET, ROOT)} が無い（install.sh の tw の張り先）")
    else:
        if not os.access(TW_TARGET, os.X_OK):
            fail(f"{os.path.relpath(TW_TARGET, ROOT)} に実行ビットが無い（tw から起こせない）")
        if not read(TW_TARGET).startswith("#!/usr/bin/env python3\n"):
            fail(f"{os.path.relpath(TW_TARGET, ROOT)} の1行目が #!/usr/bin/env python3 でない")
    long_form = re.compile(r"python3 [^\n`]*task-workflow/scripts/task\.py")
    old_abbrev = re.compile(r"`task[ `]")
    for n in names:
        for md in markdown_files(n):
            body = read(md)
            rel = os.path.relpath(md, ROOT)
            for i, line in enumerate(body.splitlines(), 1):
                if long_form.search(line):
                    fail(f"{rel}:{i}: task.py を python3 で呼んでいる（tw で書く）")
                if old_abbrev.search(line):
                    fail(f"{rel}:{i}: `task …` の略記が残っている（tw で書く）")


def check_plugin() -> None:
    manifest = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    try:
        name = json.loads(read(manifest)).get("name")
    except (OSError, ValueError):
        fail(".claude-plugin/plugin.json を JSON として読めない")
    else:
        if name != PLUGIN_NAME:
            fail(f".claude-plugin/plugin.json の name が {name!r} で {PLUGIN_NAME!r} でない")

    tw = os.path.join(ROOT, "bin", "tw")
    if not os.access(tw, os.X_OK):
        fail("bin/tw が無いか実行ビットが無い")
    else:
        done = subprocess.run([tw, "--help"], capture_output=True, text=True, cwd=ROOT)
        if done.returncode != 0 or "commit-guard" not in done.stdout:
            fail(f"bin/tw --help が task.py に届かない（終了コード {done.returncode}）")

    try:
        hooks = json.loads(read(os.path.join(ROOT, "hooks", "hooks.json")))["hooks"]
        commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
    except (OSError, ValueError, KeyError, TypeError):
        fail("hooks/hooks.json を読めない（hooks.<イベント>[].hooks[].command の形）")
        return
    for subcommand in ("commit-guard", "handback-guard"):
        want = f'"${{CLAUDE_PLUGIN_ROOT}}/bin/tw" {subcommand} --agent-scoped 2>/dev/null || true'
        if want not in commands:
            fail(f"hooks/hooks.json に hook {want!r} が無い")


def check_cross_references(names: list[str]) -> None:
    """`` `skill-name` スキル`` の形の参照が実在するか。"""
    pat = re.compile(r"`([a-z][a-z0-9-]{2,})`\s*スキル")
    known = set(names) | set(OPTIONAL_SKILLS)
    for n in names:
        for md in markdown_files(n):
            for ref in set(pat.findall(read(md))):
                if ref not in known:
                    fail(f"{os.path.relpath(md, ROOT)}: `{ref}` スキルは実在しない")


def check_requires(names: list[str]) -> None:
    """各スキルの `REQUIRES`（依存する兄弟スキル名を1行ずつ）が実在するスキルを指しているか。"""
    for n in names:
        path = os.path.join(SKILLS, n, "REQUIRES")
        if not os.path.exists(path):
            continue
        for dep in read(path).splitlines():
            dep = dep.strip()
            if not dep:
                continue
            if dep not in names:
                fail(f"{n}/REQUIRES: 依存先 `{dep}` は実在しない")


def check_python_syntax(names: list[str]) -> None:
    for n in names:
        for dirpath, _, files in os.walk(os.path.join(SKILLS, n)):
            for f in files:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    ast.parse(read(p))
                except SyntaxError as e:
                    fail(f"{os.path.relpath(p, ROOT)}: 構文エラー（{e}）")


# T-018: 履歴の置き場・指示メモの見出し・IDの形は skills/task-workflow/scripts/layout.py に
# 1箇所だけ書き、読む側は直書きしない（正典は task-workflow の WORKFLOW.md「ファイル配置と
# 設定ファイル（AGENTS.md → CLAUDE.md の順）」）。ここでは layout.py 自身は対象から外し、
# 読む側のファイルだけを見る。
_TASK_WORKFLOW_SCRIPTS = os.path.join(SKILLS, "task-workflow", "scripts")
_RETROSPECT_SCRIPTS = os.path.join(SKILLS, "retrospect", "scripts")
LAYOUT_PATH = os.path.join(_TASK_WORKFLOW_SCRIPTS, "layout.py")
LAYOUT_CONSUMERS = (
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "task.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "tw_base.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "tw_claim.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "tw_edit.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "tw_maint.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "tw_new.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "tw_ship.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "tw_status.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "tw_verify.py"),
    os.path.join(_TASK_WORKFLOW_SCRIPTS, "init.py"),
    os.path.join(_RETROSPECT_SCRIPTS, "material.py"),
    os.path.join(_RETROSPECT_SCRIPTS, "transcript.py"),
)

# layout.py が持つ値そのもの（値は1文字も変えない。ここは「他のファイルに戻っていないか」の検査）。
_LAYOUT_PATHS = {
    "docs/history/tasks.md",
}
_LAYOUT_STRINGS = _LAYOUT_PATHS | {
    "## ユーザーから",
    "## エージェントのドラフト",
    "feature/",
    r"T-\d{3,}",
    r"^T-\d{3,}$",
    r"\bT-\d{3,}\b",
    r"feature/(T-\d{3,})",
    r"^## (T-\d{3,})\b",
}


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """モジュール／クラス／関数の docstring として使われている `Constant` ノードの `id()` の集合。

    人向けの説明文で `docs/history/tasks.md` のようにパスへ触れるのは直書きの問題ではないので、
    リテラルの検査から外す（対象は実際に使われる値だけ）。
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _is_os_path_join(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "path"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "os"
    )


def _trailing_literal_segments(args: list[ast.expr]) -> list[str]:
    """`os.path.join(x, "develop", "task")` の末尾から連続する文字列リテラルだけを集める。"""
    segments: list[str] = []
    for arg in reversed(args):
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            segments.insert(0, arg.value)
        else:
            break
    return segments


def check_task_workflow_layout() -> None:
    """`layout.py` の値が読む側のファイルに直書きで戻っていないか（T-018）。"""
    if not os.path.exists(LAYOUT_PATH):
        fail("skills/task-workflow/scripts/layout.py が無い")
        return
    for path in LAYOUT_CONSUMERS:
        rel = os.path.relpath(path, ROOT)
        if not os.path.exists(path):
            fail(f"{rel} が無い")
            continue
        text = read(path)
        if not re.search(r"^import layout\b", text, flags=re.MULTILINE):
            fail(f"{rel}: `import layout` が無い（layout.py の値を読んでいない）")
            continue
        tree = ast.parse(text)
        docstring_ids = _docstring_constant_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstring_ids
                and node.value in _LAYOUT_STRINGS
            ):
                fail(f"{rel}:{node.lineno}: layout.py にある値 {node.value!r} を直書きしている")
            if _is_os_path_join(node):
                segments = _trailing_literal_segments(node.args)
                joined = ["/".join(segments[i:]) for i in range(len(segments))]
                hit = next((j for j in joined if j in _LAYOUT_PATHS), None)
                if hit is not None:
                    fail(f"{rel}:{node.lineno}: os.path.join が {hit!r} を直書きしている")


def check_selftest_body_literal() -> None:
    """自己テストが `### 名指すファイル` を直書きして本文を組んでいないか（`selftest_body.task_body` で組む）。"""
    for fname in sorted(os.listdir(_TASK_WORKFLOW_SCRIPTS)):
        if not (fname.startswith("selftest") and fname.endswith(".py")) or fname == "selftest_body.py":
            continue
        path = os.path.join(_TASK_WORKFLOW_SCRIPTS, fname)
        tree = ast.parse(read(path))
        docstring_ids = _docstring_constant_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstring_ids
                and "### 名指すファイル" in node.value
            ):
                fail(f"{os.path.relpath(path, ROOT)}:{node.lineno}: `### 名指すファイル` を直書きしている（selftest_body.task_body で組む）")


def _sys_path_call(node: ast.AST) -> ast.expr | None:
    """`sys.path.insert(i, x)` / `sys.path.append(x)` なら、足す先のパスの引数ノードを返す。"""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "path"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "sys"
    ):
        return None
    if node.func.attr == "insert" and len(node.args) >= 2:
        return node.args[1]
    if node.func.attr == "append" and len(node.args) >= 1:
        return node.args[0]
    return None


def _unwrap_normpath(node: ast.expr) -> ast.expr:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "normpath":
        return node.args[0] if node.args else node
    return node


def check_sibling_imports(names: list[str]) -> None:
    """兄弟スキルの `scripts/` を `sys.path` に足して `import` しているなら、そのスキルの
    `REQUIRES` に足し先のスキル名があるか（T-029）。

    見るのは `sys.path.insert(0, X)` / `sys.path.append(X)` の `X` だけ。`X` が変数なら
    同じファイル内の代入までたどる。`os.path.join(dirname(__file__), "..", "..", "<skill>",
    "scripts")`（`os.path.normpath` で包んでもよい）の形で、二階層上がって別スキルの
    `scripts` を指しているものだけを「兄弟スキルへの依存」として扱う。依存先の並びの取り方は
    「`..`,`..` の次の要素を見る」で、この1形しか今のリポジトリに無いのでそれで十分。

    `sys.path.insert(0, HERE)`（`HERE = os.path.dirname(...)`）のように `..` を挟まず
    自分の `scripts/` を指すものは同一スキル内の兄弟モジュール読み込みで、依存ではないので
    無視する（`selftest.py`・`selftest_task.py` 等）。

    それ以外の形（文字列の連結や f-string で組んだパスなど）は、依存の有無をこの検査では
    読み取れない。黙って通すと「検査を素通りする書き方」が増えても気づけなくなるので、
    「読めない」として指摘し、人に読める形へ直すか `REQUIRES` を確認するかを判断させる。
    """
    for n in names:
        scripts_dir = os.path.join(SKILLS, n, "scripts")
        if not os.path.isdir(scripts_dir):
            continue
        req_path = os.path.join(SKILLS, n, "REQUIRES")
        required = set()
        if os.path.exists(req_path):
            required = {line.strip() for line in read(req_path).splitlines() if line.strip()}
        for fname in sorted(os.listdir(scripts_dir)):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(scripts_dir, fname)
            rel = os.path.relpath(path, ROOT)
            tree = ast.parse(read(path))
            assigns = {
                node.targets[0].id: node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            }
            for node in ast.walk(tree):
                target = _sys_path_call(node)
                if target is None:
                    continue
                expr = target
                if isinstance(expr, ast.Name) and expr.id in assigns:
                    expr = assigns[expr.id]
                expr = _unwrap_normpath(expr)
                if _is_os_path_join(expr):
                    segments = _trailing_literal_segments(expr.args)
                    if (
                        len(segments) == 4
                        and segments[0] == ".."
                        and segments[1] == ".."
                        and segments[3] == "scripts"
                    ):
                        dep = segments[2]
                        if dep == n:
                            continue
                        if dep not in names:
                            fail(f"{rel}:{node.lineno}: sys.path の足し先 `{dep}` はスキルとして実在しない")
                        elif dep not in required:
                            fail(
                                f"{rel}:{node.lineno}: `{dep}` の scripts/ を import しているが "
                                f"{n}/REQUIRES に `{dep}` が無い"
                            )
                        continue
                    fail(f"{rel}:{node.lineno}: sys.path の組み立てを読めない（兄弟スキル依存を検査できない）")
                    continue
                if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "dirname":
                    continue  # HERE 相当。自分の scripts/ を指すだけで依存ではない
                fail(f"{rel}:{node.lineno}: sys.path の組み立てを読めない（兄弟スキル依存を検査できない）")


def main() -> None:
    names = skill_names()
    check_frontmatter(names)
    check_readme_index(names)
    check_script_paths(names)
    check_tw_entry(names)
    check_plugin()
    check_cross_references(names)
    check_requires(names)
    check_sibling_imports(names)
    check_python_syntax(names)
    check_task_workflow_layout()
    check_selftest_body_literal()
    check_agent_frontmatter(agent_names())

    if problems:
        for p in problems:
            print(f"  FAIL {p}")
        print(f"\n{len(problems)}件の不整合")
        raise SystemExit(1)
    print(f"  ok   {len(names)}スキル、不整合なし")


if __name__ == "__main__":
    main()
