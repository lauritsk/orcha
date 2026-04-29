"""Persistent orchestrator run state."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pid.commands import CommandRunner
from pid.context import WorkflowContext
from pid.events import EventSink, WorkflowEvent
from pid.failures import WorkflowFailure

_RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}[0-9]{3}Z-[0-9a-f]{6}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(token|secret|password|api[_-]?key)=([^\s]+)"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)([^\s]+)"),
)


def utc_now() -> str:
    """Return compact UTC timestamp for run state."""

    return datetime.now(UTC).isoformat(timespec="milliseconds")


def generate_run_id() -> str:
    """Return monotonic sortable run ID."""

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")[:18] + "Z"
    return f"{stamp}-{secrets.token_hex(3)}"


def redact(value: Any) -> Any:
    """Redact likely secrets from persisted values."""

    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(
                lambda match: match.group(1) + "[REDACTED]", redacted
            )
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class RunPaths:
    """Filesystem paths for one orchestrator run."""

    directory: Path
    state: Path
    events: Path
    diagnostics: Path


class RunStore:
    """Durable JSON/JSONL store for orchestrator runs."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def discover(
        cls, runner: CommandRunner | None = None, *, configured_dir: str = ""
    ) -> "RunStore":
        """Create store from config or current repo common git dir."""

        if configured_dir:
            return cls(Path(configured_dir).expanduser().resolve())
        runner = runner or CommandRunner()
        result = runner.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
        common_git_dir = result.stdout.strip()
        if result.returncode != 0 or not common_git_dir:
            raise RuntimeError("could not determine git common dir for pid runs")
        return cls(Path(common_git_dir) / "pid" / "runs")

    def paths(self, run_id: str) -> RunPaths:
        """Return paths for run ID."""

        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"invalid run id: {run_id}")
        directory = self.root / run_id
        return RunPaths(
            directory=directory,
            state=directory / "state.json",
            events=directory / "events.jsonl",
            diagnostics=directory / "diagnostics",
        )

    def create_run(
        self, *, branch: str, prompt: str, argv: list[str]
    ) -> dict[str, Any]:
        """Create and persist initial run state."""

        run_id = generate_run_id()
        paths = self.paths(run_id)
        paths.diagnostics.mkdir(parents=True, exist_ok=False)
        now = utc_now()
        state: dict[str, Any] = {
            "run_id": run_id,
            "status": "running",
            "branch": branch,
            "prompt_summary": prompt_summary(prompt),
            "argv": argv,
            "attempts": 0,
            "thinking": "",
            "current_step": "",
            "pr_url": "",
            "worktree_path": "",
            "started_at": now,
            "updated_at": now,
            "final_result": "",
            "last_failure": None,
            "pending_recovery_action": None,
            "event_count": 0,
        }
        self.write_state(run_id, state)
        return state

    def read_state(self, run_id: str) -> dict[str, Any]:
        """Read state for run ID."""

        path = self.paths(run_id).state
        return json.loads(path.read_text(encoding="utf-8"))

    def write_state(self, run_id: str, state: dict[str, Any]) -> None:
        """Atomically write state JSON."""

        paths = self.paths(run_id)
        paths.directory.mkdir(parents=True, exist_ok=True)
        state = redact(state)
        state["updated_at"] = utc_now()
        temp = paths.state.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temp, paths.state)

    def append_event(self, run_id: str, event: WorkflowEvent) -> dict[str, Any]:
        """Append wrapped workflow event and project it into state."""

        state = self.read_state(run_id)
        sequence = int(state.get("event_count", 0)) + 1
        wrapped = {
            "run_id": run_id,
            "sequence": sequence,
            "event": redact(event.to_dict()),
        }
        paths = self.paths(run_id)
        paths.directory.mkdir(parents=True, exist_ok=True)
        with paths.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(wrapped, sort_keys=True, default=str) + "\n")
        state["event_count"] = sequence
        project_event(state, event)
        self.write_state(run_id, state)
        return state

    def update_from_context(
        self, run_id: str, ctx: WorkflowContext | None
    ) -> dict[str, Any]:
        """Persist best-known workflow context fields."""

        state = self.read_state(run_id)
        if ctx is not None:
            if ctx.branch:
                state["branch"] = ctx.branch
            if ctx.pr_url:
                state["pr_url"] = ctx.pr_url
            if ctx.worktree_path:
                state["worktree_path"] = ctx.worktree_path
            if ctx.followup_thinking_level:
                state["thinking"] = ctx.followup_thinking_level
            if ctx.attempt:
                state["attempts"] = ctx.attempt
            if ctx.commit_title:
                state["commit_title"] = ctx.commit_title
        self.write_state(run_id, state)
        return state

    def mark_succeeded(
        self, run_id: str, ctx: WorkflowContext | None
    ) -> dict[str, Any]:
        """Mark run successful."""

        state = self.update_from_context(run_id, ctx)
        state["status"] = "succeeded"
        state["final_result"] = "completed"
        state["last_failure"] = None
        state["pending_recovery_action"] = None
        self.write_state(run_id, state)
        return state

    def mark_failed(
        self,
        run_id: str,
        failure: WorkflowFailure,
        *,
        pending_recovery_action: dict[str, Any] | None = None,
        status: str = "failed",
    ) -> dict[str, Any]:
        """Mark run failed or stopped with typed failure."""

        state = self.read_state(run_id)
        state["status"] = status
        state["final_result"] = failure.kind.value
        state["last_failure"] = failure.to_dict()
        state["pending_recovery_action"] = pending_recovery_action
        self.write_state(run_id, state)
        return state

    def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """List most recent run states."""

        if not self.root.exists():
            return []
        states: list[dict[str, Any]] = []
        for state_path in sorted(self.root.glob("*/state.json"), reverse=True):
            try:
                states.append(json.loads(state_path.read_text(encoding="utf-8")))
            except OSError, json.JSONDecodeError:
                continue
            if len(states) >= limit:
                break
        return states


def project_event(state: dict[str, Any], event: WorkflowEvent) -> None:
    """Update user-facing state from one workflow event."""

    if event.name == "step.started":
        state["current_step"] = event.step
        state["status"] = "running"
    elif event.name == "step.completed" and state.get("current_step") == event.step:
        state["current_step"] = ""
    elif event.name == "workflow.completed":
        state["status"] = "succeeded"
        state["final_result"] = "completed"
        state["current_step"] = ""
    elif event.name == "workflow.failed":
        state["status"] = "failed"
    state["updated_at"] = utc_now()


def prompt_summary(prompt: str, *, limit: int = 80) -> str:
    """Return compact single-line prompt summary."""

    collapsed = " ".join(prompt.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


class RunEventSink:
    """Event sink that persists run-wrapped events and optionally forwards."""

    def __init__(
        self, store: RunStore, run_id: str, downstream: EventSink | None = None
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.downstream = downstream

    def emit(self, event: WorkflowEvent) -> None:
        self.store.append_event(self.run_id, event)
        if self.downstream is not None:
            self.downstream.emit(event)
