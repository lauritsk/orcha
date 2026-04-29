"""General-purpose pid helpers."""

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


def pluralize(value: int, singular: str, plural: str | None = None) -> str:
    """Return a count with a singular or plural noun."""

    noun = singular if value == 1 else (plural or f"{singular}s")
    return f"{value} {noun}"


def review_display_target_for(commit_count: int, dirty: bool) -> str:
    """Describe review scope for user-facing UI."""

    commit_text = pluralize(commit_count, "commit")
    if commit_count > 0 and dirty:
        return f"review {commit_text} and uncommitted changes"
    if commit_count > 0:
        return f"review {commit_text}"
    if dirty:
        return "review uncommitted changes"
    return "verify requested work is complete"


BASE_REFRESH_STAGE_LABELS = {
    "before_message": "before commit message",
    "before_pr": "before PR push",
    "after_checks": "after checks",
}

BASE_REFRESH_RESULT_LABELS = {
    "limit_reached": "refresh limit reached",
    "conflict_unresolved": "rebase conflict needs manual cleanup",
    "rebased_cleanly": "rebased cleanly",
    "rebased_with_agent_fix": "rebased after agent conflict fix",
    "unchanged": "already up to date",
}


def base_refresh_stage_label(stage: str) -> str:
    """Return a concise display label for a base-refresh stage."""

    return BASE_REFRESH_STAGE_LABELS.get(stage, stage.replace("_", " "))


def base_refresh_result_label(result: str) -> str:
    """Return a concise display label for a base-refresh result."""

    return BASE_REFRESH_RESULT_LABELS.get(result, result.replace("_", " "))


def workflow_step_label(name: str) -> str:
    """Return a readable label for workflow step names."""

    return name.replace("_", " ")


def review_target_for(base_rev: str, commit_count: int, dirty: bool) -> str:
    """Describe what the review agent pass should inspect."""

    if commit_count > 0 and dirty:
        return (
            f"Review the commits in {base_rev}..HEAD and the uncommitted changes "
            "in this worktree."
        )
    if commit_count > 0:
        return f"Review the commits in {base_rev}..HEAD."
    if dirty:
        return "Review the uncommitted changes in this worktree."
    return (
        "No commits or uncommitted changes exist yet; verify whether the requested "
        "task was already satisfied or make the needed changes."
    )
