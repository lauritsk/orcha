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
_FOLLOWUP_ID_RE = re.compile(r"^fu-(?P<sequence>[0-9]{6})$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(token|secret|password|api[_-]?key)=([^\s]+)"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)([^\s]+)"),
)
FOLLOWUP_KINDS = (
    "clarify",
    "scope_change",
    "pause",
    "resume",
    "abort",
    "rerun",
    "merge_policy",
    "status_request",
)


def utc_now() -> str:
    """Return compact UTC timestamp for run state."""

    return datetime.now(UTC).isoformat(timespec="milliseconds")


def generate_run_id() -> str:
    """Return monotonic sortable run ID."""

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")[:18] + "Z"
    return f"{stamp}-{secrets.token_hex(3)}"


def valid_run_id(run_id: str) -> bool:
    """Return whether value is a syntactically valid run ID."""

    return bool(_RUN_ID_RE.fullmatch(run_id))


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
    followups: Path
    diagnostics: Path


class RunStore:
    """Durable JSON/JSONL store for orchestrator and child runs."""

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

        if not valid_run_id(run_id):
            raise ValueError(f"invalid run id: {run_id}")
        directory = self.root / run_id
        return RunPaths(
            directory=directory,
            state=directory / "state.json",
            events=directory / "events.jsonl",
            followups=directory / "followups.jsonl",
            diagnostics=directory / "diagnostics",
        )

    def create_run(
        self,
        *,
        branch: str,
        prompt: str,
        argv: list[str],
        run_id: str = "",
        parent_run_id: str = "",
        plan_item_id: str = "",
        status: str = "running",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create and persist initial child run state.

        If a planned state already exists for ``run_id``, update launch metadata but
        preserve queued follow-ups so orchestrators can steer future waves before
        the child process starts.
        """

        run_id = run_id or generate_run_id()
        paths = self.paths(run_id)
        if paths.state.exists():
            state = self.read_state(run_id)
            state.update(
                {
                    "run_type": "agent",
                    "status": status,
                    "branch": branch,
                    "prompt_summary": prompt_summary(prompt),
                    "argv": argv,
                    "parent_run_id": parent_run_id or state.get("parent_run_id", ""),
                    "plan_item_id": plan_item_id or state.get("plan_item_id", ""),
                }
            )
            if extra:
                state.update(extra)
            self.write_state(run_id, state)
            return state

        _ensure_private_dir(paths.directory, exist_ok=False)
        _ensure_private_dir(paths.diagnostics)
        now = utc_now()
        state: dict[str, Any] = {
            "run_id": run_id,
            "run_type": "agent",
            "parent_run_id": parent_run_id,
            "plan_item_id": plan_item_id,
            "status": status,
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
            "follow_up_count": 0,
            "applied_follow_up_count": 0,
            "last_follow_up_id": "",
            "last_applied_follow_up_id": "",
        }
        if extra:
            state.update(extra)
        self.write_state(run_id, state)
        return state

    def create_orchestrator_run(
        self,
        *,
        goal: str,
        questions: list[str],
        plan: dict[str, Any] | None = None,
        children: list[dict[str, Any]] | None = None,
        status: str = "awaiting_plan",
        branch_prefix: str = "work",
        concurrency: int = 4,
    ) -> dict[str, Any]:
        """Create and persist an orchestrator run state."""

        run_id = generate_run_id()
        paths = self.paths(run_id)
        _ensure_private_dir(paths.directory, exist_ok=False)
        _ensure_private_dir(paths.diagnostics)
        now = utc_now()
        state: dict[str, Any] = {
            "run_id": run_id,
            "run_type": "orchestrator",
            "status": status,
            "goal": goal,
            "branch_prefix": branch_prefix,
            "concurrency": concurrency,
            "intake_questions": questions,
            "approved_plan": plan,
            "children": children or [],
            "followups": [],
            "started_at": now,
            "updated_at": now,
            "final_result": "",
            "event_count": 0,
            "follow_up_count": 0,
            "applied_follow_up_count": 0,
            "last_follow_up_id": "",
            "last_applied_follow_up_id": "",
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
        _ensure_private_dir(paths.directory)
        state = redact(state)
        state["updated_at"] = utc_now()
        temp = paths.state.with_suffix(".json.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        file_descriptor = os.open(temp, flags, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(temp, paths.state)
        _chmod_if_supported(paths.state, 0o600)

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
        _ensure_private_dir(paths.directory)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        file_descriptor = os.open(paths.events, flags, 0o600)
        with os.fdopen(file_descriptor, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(wrapped, sort_keys=True, default=str) + "\n")
        _chmod_if_supported(paths.events, 0o600)
        state["event_count"] = sequence
        project_event(state, event)
        self.write_state(run_id, state)
        return state

    def append_followup(
        self,
        run_id: str,
        *,
        message: str,
        kind: str = "clarify",
        source: str = "user",
        sender_run_id: str = "",
    ) -> dict[str, Any]:
        """Append a durable follow-up message to a run inbox."""

        if kind not in FOLLOWUP_KINDS:
            valid = ", ".join(FOLLOWUP_KINDS)
            raise ValueError(f"follow-up type must be one of: {valid}")
        message = message.strip()
        if not message:
            raise ValueError("follow-up message must be non-empty")

        state = self.read_state(run_id)
        sequence = int(state.get("follow_up_count", 0)) + 1
        followup_id = f"fu-{sequence:06d}"
        record = redact(
            {
                "record": "followup",
                "id": followup_id,
                "sequence": sequence,
                "run_id": run_id,
                "kind": kind,
                "source": source,
                "sender_run_id": sender_run_id,
                "message": message,
                "created_at": utc_now(),
            }
        )
        self._append_followup_record(run_id, record)
        state["follow_up_count"] = sequence
        state["last_follow_up_id"] = followup_id
        self.write_state(run_id, state)
        return record

    def pending_followups(self, run_id: str) -> list[dict[str, Any]]:
        """Return follow-ups not yet acknowledged by the child run."""

        state = self.read_state(run_id)
        applied = int(state.get("applied_follow_up_count", 0))
        records = self._read_followup_records(run_id)
        return [
            record
            for record in records
            if record.get("record") == "followup"
            and int(record.get("sequence", 0)) > applied
        ]

    def ack_followup(
        self,
        run_id: str,
        followup_id: str,
        *,
        step: str,
        status: str = "applied",
        note: str = "",
    ) -> dict[str, Any]:
        """Persist an acknowledgement for one follow-up."""

        sequence = followup_sequence(followup_id)
        state = self.read_state(run_id)
        current = int(state.get("applied_follow_up_count", 0))
        state["applied_follow_up_count"] = max(current, sequence)
        state["last_applied_follow_up_id"] = followup_id
        ack = redact(
            {
                "record": "ack",
                "id": followup_id,
                "sequence": sequence,
                "run_id": run_id,
                "status": status,
                "step": step,
                "note": note,
                "created_at": utc_now(),
            }
        )
        self._append_followup_record(run_id, ack)
        self.write_state(run_id, state)
        return ack

    def _append_followup_record(self, run_id: str, record: dict[str, Any]) -> None:
        paths = self.paths(run_id)
        _ensure_private_dir(paths.directory)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        file_descriptor = os.open(paths.followups, flags, 0o600)
        with os.fdopen(file_descriptor, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        _chmod_if_supported(paths.followups, 0o600)

    def _read_followup_records(self, run_id: str) -> list[dict[str, Any]]:
        path = self.paths(run_id).followups
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records

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


def followup_sequence(followup_id: str) -> int:
    """Return numeric sequence for a follow-up ID."""

    match = _FOLLOWUP_ID_RE.fullmatch(followup_id)
    if match is None:
        raise ValueError(f"invalid follow-up id: {followup_id}")
    return int(match.group("sequence"))


def _ensure_private_dir(path: Path, *, exist_ok: bool = True) -> None:
    """Create a directory readable only by the current user when possible."""

    path.mkdir(mode=0o700, parents=True, exist_ok=exist_ok)
    _chmod_if_supported(path, 0o700)


def _chmod_if_supported(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        return


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
