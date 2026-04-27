"""General-purpose Orcha helpers."""

from __future__ import annotations

import os
from pathlib import Path


def env_int(name: str, default: int) -> int:
    """Read an integer from the environment, falling back on invalid values."""

    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def has_output(value: str) -> bool:
    """Return true when captured command output contains non-whitespace text."""

    return bool(value.strip())


def worktree_path_for(repo_root: str, branch: str) -> str:
    """Build the sibling worktree path for a branch."""

    repo_path = Path(repo_root)
    safe_branch = branch.replace("/", "-")
    return str(repo_path.parent / f"{repo_path.name}-{safe_branch}")


def review_target_for(base_rev: str, commit_count: int, dirty: bool) -> str:
    """Describe what the review pi pass should inspect."""

    if commit_count > 0:
        return f"Review the commits in {base_rev}..HEAD."
    if dirty:
        return "Review the uncommitted changes in this worktree."
    return (
        "No commits or uncommitted changes exist yet; verify whether the requested "
        "task was already satisfied or make the needed changes."
    )
