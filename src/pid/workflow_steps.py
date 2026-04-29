"""Stable built-in workflow step identifiers."""

from __future__ import annotations

BOOTSTRAP_STEP_IDS: tuple[str, ...] = (
    "parse_args",
    "start_session_logging",
    "start_keep_awake",
    "render_run_summary",
    "validate_branch",
    "resolve_repo_root",
)

SETUP_STEP_IDS: tuple[str, ...] = (
    "require_commands",
    "resolve_main_worktree",
    "validate_clean_main_worktree",
    "resolve_default_branch",
    "update_default_branch",
    "capture_base_rev",
    "create_worktree",
    "run_setup_command",
)

AGENT_REVIEW_STEP_IDS: tuple[str, ...] = (
    "run_initial_agent",
    "inspect_initial_changes",
    "run_review_agent",
    "inspect_review_changes",
    "stop_if_no_changes",
)

COMMIT_MESSAGE_STEP_IDS: tuple[str, ...] = (
    "generate_message",
    "verify_commit_title",
    "commit_changes",
)

PR_ENTRY_STEP_IDS: tuple[str, ...] = ("run_pr_loop",)

DEFAULT_STEP_IDS: tuple[str, ...] = (
    *SETUP_STEP_IDS,
    *AGENT_REVIEW_STEP_IDS,
    *COMMIT_MESSAGE_STEP_IDS,
    *PR_ENTRY_STEP_IDS,
)

PR_LOOP_STEP_IDS: tuple[str, ...] = (
    "pr_prepare_attempt",
    "pr_refresh_base_before_message",
    "pr_regenerate_message",
    "pr_refresh_base_before_pr",
    "pr_push_branch",
    "pr_ensure_pr",
    "pr_wait_for_checks",
    "pr_handle_checks",
    "pr_refresh_base_after_checks",
    "pr_squash_merge",
    "pr_recover_merge",
    "pr_confirm_merge",
    "pr_cleanup",
)
