#!/usr/bin/env python3
"""`self_review.py` の自己テスト。

使い方: python3 selftest_self_review.py

標準ライブラリだけで動く。落ちたら非0で終わる。
comment-audit のスクリプトは、ホームの下に置いた代役で確かめる。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "self_review.py")

STUB = """\
import subprocess
out = subprocess.run(["git", "diff", "HEAD", "-U0"], capture_output=True, text=True).stdout
for line in out.splitlines():
    if line.startswith("+") and not line.startswith("+++") and line[1:].lstrip().startswith("#"):
        print("x:1\\t" + line[1:])
"""

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}{(': ' + detail) if detail else ''}")
        failures.append(label)


def git(repo: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def write(repo: str, path: str, body: str) -> None:
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(body)


def run(repo: str, home: str, skill_dir: str = "", sub: str = "") -> list[list[str]]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_SKILL_DIR"}
    env["HOME"] = home
    if skill_dir:
        env["CLAUDE_SKILL_DIR"] = skill_dir
    out = subprocess.run(
        [sys.executable, SCRIPT], cwd=os.path.join(repo, sub), capture_output=True, text=True, env=env
    )
    check("非0で終わらない", out.returncode == 0, out.stderr)
    return [line.split("\t") for line in out.stdout.splitlines()]


def kinds(rows: list[list[str]]) -> list[str]:
    return [r[1] for r in rows if r[0] == "SELF"]


def init_repo(d: str) -> None:
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "a@example.com")
    git(d, "config", "user.name", "a")
    write(d, "README.md", "a\n")
    write(d, "a.py", "x = 1\n")
    write(d, "selftest_a.py", "y = 1\n")
    git(d, "add", "README.md", "a.py", "selftest_a.py")
    git(d, "commit", "-q", "-m", "init")


def with_repo(with_comment_audit: bool, body) -> None:
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
        repo = os.path.join(d, "repo")
        os.makedirs(repo)
        init_repo(repo)
        if with_comment_audit:
            write(home, ".claude/skills/comment-audit/scripts/diff_added_comment_lines.py", STUB)
        body(repo, home)


def test_nothing() -> None:
    print("差分が無ければ NOTHING")
    with_repo(True, lambda r, h: check("NOTHING", run(r, h) == [["NOTHING"]]))


def test_comment() -> None:
    print("comment: コメント行を足すと出る。足さなければ出ない")

    def hit(r: str, h: str) -> None:
        write(r, "a.py", "x = 1\n# 言い直し\n")
        check("出る", kinds(run(r, h)) == ["comment"], repr(run(r, h)))

    def miss(r: str, h: str) -> None:
        write(r, "a.py", "x = 1\nz = 2\n")
        check("出ない", "comment" not in kinds(run(r, h)))

    def untracked(r: str, h: str) -> None:
        write(r, "b.py", "z = 2\n")
        check("未追跡のコードがあれば出る", "comment" in kinds(run(r, h)))

    with_repo(True, hit)
    with_repo(True, miss)
    with_repo(True, untracked)


def test_comment_audit_missing() -> None:
    print("comment: comment-audit が見つからなければ SKIPPED を出す")

    def body(r: str, h: str) -> None:
        write(r, "a.py", "x = 1\n# 言い直し\n")
        rows = run(r, h)
        check("SKIPPED comment が先頭", rows[0][:2] == ["SKIPPED", "comment"], repr(rows))
        check("comment は SELF に出ない", "comment" not in kinds(rows))

    def docs_only(r: str, h: str) -> None:
        write(r, "README.md", "b\n")
        check("文書だけの変更では出ない", run(r, h) == [["NOTHING"]])

    with_repo(False, body)
    with_repo(False, docs_only)


def test_subdirectory() -> None:
    print("サブディレクトリから打っても、追跡済みと未追跡のパスが根からでそろう")

    def body(r: str, h: str) -> None:
        write(r, "src/c.ts", "export const X = 1\n")
        git(r, "add", "src/c.ts")
        git(r, "commit", "-q", "-m", "ts")
        write(r, "src/c.ts", "export const X = 2\nexport const Y = 1\n")
        write(r, "src/d.ts", "export const Z = 1\n")
        rows = [x for x in run(r, h, sub="src") if x[0] == "SELF" and x[1] == "type-shape"]
        check("type-shape が1行", len(rows) == 1, repr(rows))
        check("根からのパスで両方並ぶ", rows and "src/c.ts" in rows[0][2] and "src/d.ts" in rows[0][2])

    with_repo(False, body)


def test_comment_audit_by_skill_dir() -> None:
    print("comment: CLAUDE_SKILL_DIR の兄弟にあれば、ホームに無くても見つかる")

    def body(r: str, h: str) -> None:
        sibling = os.path.join(os.path.dirname(r), "skills")
        write(sibling, "comment-audit/scripts/diff_added_comment_lines.py", STUB)
        write(r, "a.py", "x = 1\n# 言い直し\n")
        rows = run(r, h, os.path.join(sibling, "next-task"))
        check("出る", kinds(rows) == ["comment"], repr(rows))

    with_repo(False, body)


def test_type_shape() -> None:
    print("type-shape: 型・定数の形の行を足す・消すと出る")

    def hit_ts(r: str, h: str) -> None:
        write(r, "c.ts", "export const X = ['a'] as const\n")
        check(".ts の as const", "type-shape" in kinds(run(r, h)))

    def hit_py(r: str, h: str) -> None:
        write(r, "a.py", "x = 1\nLIMIT = 3\n")
        check(".py の大文字定数", "type-shape" in kinds(run(r, h)))

    def hit_removed(r: str, h: str) -> None:
        write(r, "a.py", "X_MAX = 1\n")
        git(r, "add", "a.py")
        git(r, "commit", "-q", "-m", "const")
        write(r, "a.py", "y = 1\n")
        check("消した定数も出る", "type-shape" in kinds(run(r, h)))

    def miss_local_const(r: str, h: str) -> None:
        write(r, "c.ts", "function f() {\n  const x = 1\n  return x\n}\n")
        check("関数の中の const は外れる", "type-shape" not in kinds(run(r, h)))

    def miss_ext(r: str, h: str) -> None:
        write(r, "c.sh", "export const X=1\n")
        check("表に無い拡張子は出ない", "type-shape" not in kinds(run(r, h)))

    def miss_body(r: str, h: str) -> None:
        write(r, "a.py", "x = 2\n")
        check("関数の中身の変更は出ない", "type-shape" not in kinds(run(r, h)))

    for body in (hit_ts, hit_py, hit_removed, miss_local_const, miss_ext, miss_body):
        with_repo(False, body)


def test_repeat_shape() -> None:
    print("repeat-shape: 関数定義かループの見出しを足すと出る")

    def hit_def(r: str, h: str) -> None:
        write(r, "a.py", "x = 1\ndef f():\n    pass\n")
        check(".py の def", "repeat-shape" in kinds(run(r, h)))

    def hit_for(r: str, h: str) -> None:
        write(r, "c.ts", "for (const a of b) {}\nfor (let i = 0; i < 1; i++) {}\n")
        check(".ts の for", "repeat-shape" in kinds(run(r, h)))

    def miss(r: str, h: str) -> None:
        write(r, "a.py", "x = 2\n")
        check("既存の行の中身だけ変えると出ない", "repeat-shape" not in kinds(run(r, h)))

    def miss_removed(r: str, h: str) -> None:
        write(r, "a.py", "def f():\n    pass\n")
        git(r, "add", "a.py")
        git(r, "commit", "-q", "-m", "def")
        write(r, "a.py", "x = 1\n")
        check("見出しを消しただけでは出ない", "repeat-shape" not in kinds(run(r, h)))

    for body in (hit_def, hit_for, miss, miss_removed):
        with_repo(False, body)


def test_doc_vs_check() -> None:
    print("doc-vs-check: 文書と検査が両方変わると出る")

    def both(r: str, h: str) -> None:
        write(r, "README.md", "b\n")
        write(r, "selftest_a.py", "y = 2\n")
        check("両方で出る", "doc-vs-check" in kinds(run(r, h)))

    def doc_only(r: str, h: str) -> None:
        write(r, "README.md", "b\n")
        check("文書だけでは出ない", "doc-vs-check" not in kinds(run(r, h)))

    def check_only(r: str, h: str) -> None:
        write(r, "selftest_a.py", "y = 2\n")
        check("検査だけでは出ない", "doc-vs-check" not in kinds(run(r, h)))

    for body in (both, doc_only, check_only):
        with_repo(False, body)


def main() -> int:
    test_nothing()
    test_comment()
    test_comment_audit_missing()
    test_comment_audit_by_skill_dir()
    test_subdirectory()
    test_type_shape()
    test_repeat_shape()
    test_doc_vs_check()
    if failures:
        print(f"\n{len(failures)} 件落ちた: {', '.join(failures)}")
        return 1
    print("\n全部通った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
