"""Typed workflow failure model for supervised pid runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pid.context import WorkflowContext


class FailureKind(StrEnum):
    """High-level terminal workflow failure categories."""

    INVALID_ARGS = "invalid_args"
    MISSING_COMMAND = "missing_command"
    DIRTY_MAIN_WORKTREE = "dirty_main_worktree"
    BRANCH_EXISTS = "branch_exists"
    WORKTREE_EXISTS = "worktree_exists"
    MISE_TRUST_FAILED = "mise_trust_failed"
    INITIAL_AGENT_FAILED = "initial_agent_failed"
    REVIEW_AGENT_FAILED = "review_agent_failed"
    NO_CHANGES = "no_changes"
    MESSAGE_FAILED = "message_failed"
    COMMIT_FAILED = "commit_failed"
    PUSH_FAILED = "push_failed"
    PR_FAILED = "pr_failed"
    CHECKS_FAILED = "checks_failed"
    MERGE_FAILED = "merge_failed"
    REBASE_IN_PROGRESS = "rebase_in_progress"
    CLEANUP_FAILED = "cleanup_failed"
    EXTENSION_FAILED = "extension_failed"
    FOLLOWUP_PAUSED = "followup_paused"
    FOLLOWUP_ABORTED = "followup_aborted"


@dataclass(frozen=True)
class WorkflowFailure(Exception):
    """Typed terminal failure raised by supervised workflows."""

    kind: FailureKind
    step: str
    code: int
    message: str
    recoverable: bool
    diagnostics: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable failure metadata."""

        data: dict[str, Any] = {
            "kind": self.kind.value,
            "step": self.step,
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.diagnostics:
            data["diagnostics"] = self.diagnostics
        if self.context:
            data["context"] = self.context
        return data


_STEP_FAILURES: dict[str, tuple[FailureKind, str, bool]] = {
    "parse_args": (FailureKind.INVALID_ARGS, "invalid workflow arguments", False),
    "require_commands": (
        FailureKind.MISSING_COMMAND,
        "missing required command",
        False,
    ),
    "validate_clean_main_worktree": (
        FailureKind.DIRTY_MAIN_WORKTREE,
        "main worktree is dirty",
        False,
    ),
    "create_worktree": (
        FailureKind.WORKTREE_EXISTS,
        "worktree or branch exists",
        False,
    ),
    "trust_mise": (FailureKind.MISE_TRUST_FAILED, "mise trust failed", False),
    "run_initial_agent": (
        FailureKind.INITIAL_AGENT_FAILED,
        "initial agent failed",
        True,
    ),
    "run_review_agent": (FailureKind.REVIEW_AGENT_FAILED, "review agent failed", True),
    "stop_if_no_changes": (FailureKind.NO_CHANGES, "no changes after agent", True),
    "generate_message": (
        FailureKind.MESSAGE_FAILED,
        "commit message generation failed",
        True,
    ),
    "verify_commit_title": (
        FailureKind.MESSAGE_FAILED,
        "commit title verification failed",
        True,
    ),
    "commit_changes": (FailureKind.COMMIT_FAILED, "commit failed", True),
    "pr_push_branch": (FailureKind.PUSH_FAILED, "push failed", True),
    "pr_ensure_pr": (FailureKind.PR_FAILED, "pull request operation failed", True),
    "pr_wait_for_checks": (FailureKind.CHECKS_FAILED, "checks failed", True),
    "pr_handle_checks": (FailureKind.CHECKS_FAILED, "checks failed", True),
    "pr_squash_merge": (FailureKind.MERGE_FAILED, "merge failed", True),
    "pr_recover_merge": (FailureKind.MERGE_FAILED, "merge recovery failed", True),
    "pr_confirm_merge": (FailureKind.MERGE_FAILED, "merge confirmation failed", True),
    "pr_cleanup": (FailureKind.CLEANUP_FAILED, "cleanup failed", True),
}


def failure_from_abort(
    *, code: int, step: str, context: WorkflowContext | None
) -> WorkflowFailure:
    """Classify a PIDAbort from the current supervised step."""

    kind, message, recoverable = _STEP_FAILURES.get(
        step,
        (
            FailureKind.INVALID_ARGS if code == 2 else FailureKind.PR_FAILED,
            "workflow failed",
            False,
        ),
    )
    if step == "create_worktree" and context is not None:
        branch = context.branch
        worktree = context.worktree_path
        if branch:
            message = f"could not create worktree for {branch}"
        if worktree:
            failure_context = _context_snapshot(context)
            failure_context["worktree_path"] = worktree
            return WorkflowFailure(
                kind, step, code, message, recoverable, context=failure_context
            )
    if code == 0 and step == "stop_if_no_changes":
        kind = FailureKind.NO_CHANGES
        recoverable = True
    return WorkflowFailure(
        kind=kind,
        step=step,
        code=code,
        message=message,
        recoverable=recoverable,
        context=_context_snapshot(context),
    )


def _context_snapshot(context: WorkflowContext | None) -> dict[str, Any]:
    if context is None:
        return {}
    snapshot: dict[str, Any] = {
        "branch": context.branch,
        "repo_root": context.repo_root,
        "main_worktree": context.main_worktree,
        "worktree_path": context.worktree_path,
        "default_branch": context.default_branch,
        "pr_url": context.pr_url,
        "pr_title": context.pr_title,
        "attempt": context.attempt,
        "base_refresh_count": context.base_refresh_count,
        "merge_retries": context.merge_retries,
    }
    return {key: value for key, value in snapshot.items() if value not in ("", 0)}
