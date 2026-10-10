#!/usr/bin/env python3
"""最後の段の担当が `tw verify` の前に自己点検する性質を、作業ツリーの差分から決める。

使い方:
  python3 self_review.py

出力は当たった性質ごとに1行（タブ区切り）。何も当たらなければ `NOTHING` の1行:
  SELF<TAB><性質><TAB><コマンド><TAB><問い>

性質:
  comment       差分でコメント行が足された。comment-audit のスクリプトが見つからず、
                文書以外のファイルが変わっているときは、判定せず先頭に
                `SKIPPED<TAB>comment<TAB><理由>` の1行を出す
  type-shape    型・定数の形の行が足された・消えた（拡張子の表に無い言語では出さない）
  repeat-shape  関数定義かループの見出しが足された（同上）
  doc-vs-check  文書と検査のパスが両方変わった
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys

DOC_EXTENSIONS = frozenset({".md", ".mdx", ".markdown", ".txt", ".rst", ".adoc"})

TS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")

TYPE_SHAPE = {
    **dict.fromkeys(
        TS,
        r"^(export\s+)?(type|interface|enum|const)\b|\bas const\b|\breadonly\b",
    ),
    ".py": r"^[A-Z][A-Z0-9_]*\s*[:=]|\b(Final|frozenset|TypedDict|NamedTuple|dataclass|Literal)\b",
    ".rs": r"^\s*(pub\s+)?(const|static|struct|enum|type)\b",
    ".go": r"^\s*(const|type|var)\b",
}

REPEAT_SHAPE = {
    **dict.fromkeys(TS, r"^\s*((export\s+)?(async\s+)?function\b|for\s*\(|while\s*\()"),
    ".py": r"^\s*((async\s+)?def\b|for\b.*:\s*$|while\b.*:\s*$)",
    ".rs": r"^\s*((pub\s+)?(async\s+)?fn\b|for\b|while\b|loop\b)",
    ".go": r"^\s*(func\b|for\b)",
}

CHECK_PATH = re.compile(
    r"(^|/)(selftest[^/]*|test_[^/]*|[^/]*_test\.[^/]*|[^/]*\.test\.[^/]*|[^/]*\.spec\.[^/]*|check[^/]*)$"
    r"|(^|/)tests?/"
)

COMMENT_AUDIT_RELATIVE = os.path.join("comment-audit", "scripts", "diff_added_comment_lines.py")

QUESTIONS = {
    "comment": "足されたコメント行のそれぞれに comment-audit の SKILL.md の「判定の1問」を当てて、消す・縮める・正典へ移すものが無いか",
    "type-shape": "変えた型・定数の推論された型が前より緩んでいないか（readonly・リテラル・タプルが広がっていないか）",
    "repeat-shape": "足した関数・ループと同じ形の処理が、出力に並んだ同じファイルの中に既にないか（あれば寄せられないか）",
    "doc-vs-check": "変えた文書が言う範囲と、変えた検査が拾う範囲が合っているか",
}


def main() -> int:
    os.chdir(git_lines("rev-parse", "--show-toplevel")[0])
    untracked = set(git_lines("ls-files", "--others", "--exclude-standard"))
    paths = sorted(set(git_lines("diff", "--name-only", "HEAD")) | untracked)
    comment_script = find_comment_script()
    if comment_script is None and any(ext(p) not in DOC_EXTENSIONS for p in paths):
        print("SKIPPED\tcomment\tcomment-audit のスクリプトが見つからないので、コメントの性質は判定していない")
    lines = decide(paths, untracked, comment_script)
    print("\n".join(lines) if lines else "NOTHING")
    return 0


def find_comment_script() -> str | None:
    """メインが brief を実体のパスで渡すと兄弟が見えないので、スキルの置き場の候補を順に探す。"""
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [
        os.path.join(os.environ.get("CLAUDE_SKILL_DIR", ""), ".."),
        os.path.join(here, "..", ".."),
        os.path.join(os.path.expanduser("~"), ".claude", "skills"),
    ]
    for root in roots:
        candidate = os.path.normpath(os.path.join(root, COMMENT_AUDIT_RELATIVE))
        if os.path.isfile(candidate):
            return candidate
    return None


def decide(paths: list[str], untracked: set[str], comment_script: str | None) -> list[str]:
    out: list[str] = []
    code = [p for p in paths if ext(p) not in DOC_EXTENSIONS]

    if comment_script is not None and comment_hit(code, untracked, comment_script):
        cmd = f"python3 {shlex.quote(comment_script)} HEAD"
        out.append(row("comment", cmd))

    lines = {p: diff_lines(p, untracked) for p in paths}
    shaped = [p for p in paths if hits(TYPE_SHAPE, p, lines[p], both=True)]
    if shaped:
        out.append(row("type-shape", show_command(shaped, untracked)))

    repeated = [p for p in paths if hits(REPEAT_SHAPE, p, lines[p], both=False)]
    if repeated:
        by_ext = sorted({ext(p) for p in repeated})
        pattern = "|".join(REPEAT_SHAPE[e] for e in by_ext)
        cmd = f"grep -nE {shlex.quote(pattern)} " + quote_all(repeated)
        out.append(row("repeat-shape", cmd))

    docs = [p for p in paths if ext(p) in DOC_EXTENSIONS]
    checks = [p for p in paths if CHECK_PATH.search(p)]
    if docs and checks:
        out.append(row("doc-vs-check", "git diff HEAD -- " + quote_all(docs + checks)))

    return out


def show_command(paths: list[str], untracked: set[str]) -> str:
    new = [p for p in paths if p in untracked]
    old = [p for p in paths if p not in new]
    parts = []
    if old:
        parts.append("git diff HEAD -U0 -- " + quote_all(old))
    if new:
        parts.append("cat " + quote_all(new))
    return " ; ".join(parts)


def row(kind: str, command: str) -> str:
    return f"SELF\t{kind}\t{command}\t{QUESTIONS[kind]}"


def quote_all(paths: list[str]) -> str:
    return " ".join(shlex.quote(p) for p in paths)


def ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def hits(table: dict[str, str], path: str, lines: tuple[list[str], list[str]], both: bool) -> bool:
    pattern = table.get(ext(path))
    if pattern is None:
        return False
    added, removed = lines
    target = added + removed if both else added
    return any(re.search(pattern, line) for line in target)


def comment_hit(code: list[str], untracked: set[str], script: str) -> bool:
    if any(p in untracked for p in code):
        return True
    out = subprocess.run(["python3", script, "HEAD"], capture_output=True, text=True)
    return out.returncode == 0 and bool(out.stdout.strip())


def diff_lines(path: str, untracked: set[str]) -> tuple[list[str], list[str]]:
    if path in untracked:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().splitlines(), []
        except (OSError, UnicodeDecodeError):
            return [], []
    added: list[str] = []
    removed: list[str] = []
    for line in git_lines("diff", "HEAD", "-U0", "--", path):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def git_lines(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(out.stderr.strip() or f"git {args[0]} が失敗した")
    return [line for line in out.stdout.splitlines() if line]


if __name__ == "__main__":
    sys.exit(main())
