"""毎回読まれるスキルの文書のバイト数の上限の表と、上限までの残り。"""

from __future__ import annotations

import os

import gitinfo

# 毎回読まれる文書の上限（skills/ からの相対パス: バイト）。今の値を100B 単位で切り上げて凍らせたもの
LIMITS: dict[str, int] = {
    "accept-step/SKILL.md": 4000,
    "comment-audit/SKILL.md": 9200,
    "implement-step/SKILL.md": 4500,
    "list-tasks/SKILL.md": 5100,
    "next-task/SKILL.md": 7100,
    "plan-tasks/SKILL.md": 20500,
    "retro/SKILL.md": 6700,
    "retrospect-step/SKILL.md": 3900,
    "retrospect/SKILL.md": 2700,
    "setup-tasks/SKILL.md": 6400,
    "ship-step/SKILL.md": 2900,
    "task-model/SKILL.md": 2000,
    "task-workflow/SKILL.md": 18600,
    "task-workflow/completion-report.md": 5800,
    "task-workflow/review-criteria.md": 1800,
    "task-workflow/parts/implementer/delegate.md": 11500,
    "task-workflow/parts/implementer/handback-read.md": 4500,
    "task-workflow/parts/implementer/implementer-brief.md": 21800,
    "task-workflow/parts/implementer/implementer-request.md": 9300,
    "task-workflow/parts/acceptor/accept.md": 11500,
    "task-workflow/parts/acceptor/acceptor-return.md": 6000,
    "task-workflow/parts/acceptor/acceptor-brief.md": 5500,
    "task-workflow/parts/acceptor/acceptor-request.md": 3200,
    "task-workflow/parts/acceptor/verify-check.md": 1500,
    "task-workflow/parts/retrospect/retrospect.md": 3900,
    "task-workflow/parts/ship/ship.md": 7400,
    "task-model/WORKFLOW.md": 70500,
    "task-model/tw-reference.md": 45300,
    "task-model/preflight.md": 800,
}

SKILLS_PREFIX = "skills/"


def limit_key(path: str) -> str | None:
    """リポジトリの根からの相対パスを表のキー（`skills/` を外したもの）にする。`skills/` の外は `None`。"""
    if not path.startswith(SKILLS_PREFIX):
        return None
    return path[len(SKILLS_PREFIX):]


def same_repo(root: str) -> bool:
    """`root` がこのファイルの置き場と同じリポジトリ（作業ツリー違いは同じとみなす）か。git でなければ偽。"""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        return os.path.realpath(gitinfo.git_common_dir(root)) == os.path.realpath(gitinfo.git_common_dir(here))
    except (gitinfo.GitCommandError, OSError):
        return False


def remaining(root: str, path: str) -> tuple[int, int] | None:
    """`(上限 - 実バイト数, 上限)`。超過なら残りは負。表に無い・読めないとき `None`。"""
    key = limit_key(path)
    if key is None or key not in LIMITS:
        return None
    try:
        with open(os.path.join(root, path), "rb") as f:
            size = len(f.read())
    except OSError:
        return None
    return LIMITS[key] - size, LIMITS[key]
