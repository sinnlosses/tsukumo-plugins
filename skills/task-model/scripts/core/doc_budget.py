"""毎回読まれるスキルの文書のバイト数の上限の表と、上限までの残り。"""

from __future__ import annotations

import os

import gitinfo

# 毎回読まれる文書の上限（リポジトリの根からの相対パス: バイト）。今の値を100B 単位で切り上げて凍らせたもの
LIMITS: dict[str, int] = {
    "skills/comment-audit/SKILL.md": 9200,
    "skills/list-tasks/SKILL.md": 5100,
    "skills/next-task/SKILL.md": 7100,
    "skills/plan-tasks/SKILL.md": 20500,
    "skills/retro/SKILL.md": 6700,
    "skills/retrospect/SKILL.md": 2700,
    "skills/setup-tasks/SKILL.md": 6400,
    "skills/task-model/SKILL.md": 2000,
    "workflow-briefs/review-criteria.md": 1800,
    "workflow-briefs/implementer-brief.md": 21800,
    "workflow-briefs/implementer-request.md": 9300,
    "workflow-briefs/acceptor-return.md": 6000,
    "workflow-briefs/acceptor-brief.md": 5500,
    "workflow-briefs/acceptor-request.md": 3200,
    "skills/task-model/WORKFLOW.md": 70500,
    "skills/task-model/tw-reference.md": 45300,
    "skills/task-model/preflight.md": 800,
}


def same_repo(root: str) -> bool:
    """`root` がこのファイルの置き場と同じリポジトリ（作業ツリー違いは同じとみなす）か。git でなければ偽。"""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        return os.path.realpath(gitinfo.git_common_dir(root)) == os.path.realpath(gitinfo.git_common_dir(here))
    except (gitinfo.GitCommandError, OSError):
        return False


def remaining(root: str, path: str) -> tuple[int, int] | None:
    """`(上限 - 実バイト数, 上限)`。超過なら残りは負。表に無い・読めないとき `None`。"""
    if path not in LIMITS:
        return None
    try:
        with open(os.path.join(root, path), "rb") as f:
            size = len(f.read())
    except OSError:
        return None
    return LIMITS[path] - size, LIMITS[path]
