"""Deterministic recovery policy for orchestrator runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pid.failures import FailureKind, WorkflowFailure


class RecoveryActionKind(StrEnum):
    """Allowed bounded recovery actions."""

    ASK_USER = "ask_user"
    ABORT = "abort"
    MARK_DONE = "mark_done"
    CLEANUP_RETRY = "cleanup_retry"


@dataclass(frozen=True)
class RecoveryAction:
    """Chosen bounded recovery action."""

    kind: RecoveryActionKind
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "reason": self.reason}


class DeterministicRecoveryPolicy:
    """Conservative policy. No arbitrary commands, no unsafe retries."""

    def decide(self, failure: WorkflowFailure) -> RecoveryAction:
        """Choose a recovery action for a terminal failure."""

        if failure.kind == FailureKind.NO_CHANGES:
            return RecoveryAction(
                RecoveryActionKind.MARK_DONE,
                "agent produced no changes; run can be marked done without PR",
            )
        if failure.kind == FailureKind.CLEANUP_FAILED:
            return RecoveryAction(
                RecoveryActionKind.CLEANUP_RETRY,
                "merge may be complete but cleanup failed; retry cleanup manually",
            )
        if failure.kind in {
            FailureKind.FOLLOWUP_PAUSED,
            FailureKind.FOLLOWUP_ABORTED,
        }:
            return RecoveryAction(
                RecoveryActionKind.ASK_USER,
                "run stopped by a queued follow-up control message",
            )
        if failure.kind in {
            FailureKind.INVALID_ARGS,
            FailureKind.MISSING_COMMAND,
            FailureKind.DIRTY_MAIN_WORKTREE,
            FailureKind.WORKTREE_EXISTS,
            FailureKind.SETUP_COMMAND_FAILED,
        }:
            return RecoveryAction(
                RecoveryActionKind.ASK_USER,
                "requires user/environment change before retry",
            )
        return RecoveryAction(
            RecoveryActionKind.ABORT,
            "no safe deterministic recovery for this terminal failure",
        )
