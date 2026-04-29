"""Typed runtime context for pid workflows and extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pid.commands import CommandRunner
from pid.config import PIDConfig
from pid.events import EventSink, NullEventSink, WorkflowEvent
from pid.extensions import ExtensionRegistry
from pid.forge import Forge
from pid.keepawake import KeepAwake
from pid.models import CommandResult, CommitMessage, OutputMode, ParsedArgs
from pid.repository import Repository
from pid.session_logging import SessionLogger


@dataclass
class PRLoopState:
    """Mutable state for fine-grained PR-loop extension points."""

    need_force_push: bool = False
    message_state_hash: str = ""
    refresh_stage: str = ""
    refresh_result: str = ""
    refreshed_before_message: bool = False
    checks_timeout_seconds: int = 0
    checks_poll_interval_seconds: int = 0
    merge_retry_limit: int = 0
    merge_result: CommandResult | None = None
    pr_head_oid: str = ""
    merge_confirmed: bool = False
    next_iteration: bool = False
    completed: bool = False


@dataclass
class WorkflowContext:
    """Explicit mutable state for one pid workflow run."""

    argv: list[str]
    config: PIDConfig
    runner: CommandRunner
    repository: Repository
    forge: Forge
    registry: ExtensionRegistry
    output_mode: OutputMode = OutputMode.NORMAL
    events: EventSink = field(default_factory=NullEventSink)
    parsed: ParsedArgs | None = None
    repo_root: str = ""
    main_worktree: str = ""
    worktree_path: str = ""
    default_branch: str = ""
    base_rev: str = ""
    followup_thinking_level: str = ""
    review_rejected_first_pass: bool = False
    initial_commit_count: int = 0
    initial_dirty: str = ""
    pre_review_state_hash: str = ""
    post_review_commit_count: int = 0
    post_review_dirty: str = ""
    commit_message: CommitMessage | None = None
    commit_title: str = ""
    rewritten_head: str = ""
    pr_url: str = ""
    pr_title: str = ""
    checks_status: int = 0
    checks_output: str = ""
    attempt: int = 0
    base_refresh_count: int = 0
    base_refresh_stage_counts: dict[str, int] = field(default_factory=dict)
    merge_retries: int = 0
    session_logger: SessionLogger | None = None
    keep_awake: KeepAwake | None = None
    services: dict[str, Any] = field(default_factory=dict)
    scratch: dict[str, Any] = field(default_factory=dict)
    step_retries: dict[str, int] = field(default_factory=dict)
    pr_loop: PRLoopState = field(default_factory=PRLoopState)

    @property
    def extension_config(self) -> dict[str, dict[str, Any]]:
        """Return raw per-extension config tables."""

        return self.config.extensions.config

    @property
    def branch(self) -> str:
        """Return parsed branch, or an empty string before argument parsing."""

        return self.parsed.branch if self.parsed is not None else ""

    def require_parsed(self) -> ParsedArgs:
        """Return parsed args or raise when called too early."""

        if self.parsed is None:
            raise RuntimeError("workflow arguments are not parsed yet")
        return self.parsed

    def require_worktree(self) -> str:
        """Return worktree path or raise when no worktree exists yet."""

        if not self.worktree_path:
            raise RuntimeError("workflow worktree is not available yet")
        return self.worktree_path

    def repo_path(self) -> Path:
        """Return the repository root as a path."""

        if not self.repo_root:
            raise RuntimeError("repository root is not available yet")
        return Path(self.repo_root)

    def set_commit_message(self, message: CommitMessage) -> None:
        """Set generated commit/PR metadata."""

        self.commit_message = message
        self.pr_title = message.title

    def emit(
        self,
        name: str,
        *,
        step: str = "",
        level: str = "info",
        message: str = "",
        fields: dict[str, Any] | None = None,
    ) -> None:
        """Emit a structured workflow event."""

        self.events.emit(
            WorkflowEvent(
                name=name,
                step=step,
                level=level,
                message=message,
                fields=fields or {},
            )
        )
