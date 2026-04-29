"""Durable workflow step engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class WorkflowStateStore(Protocol):
    """Store protocol needed by the workflow engine."""

    def record_step_started(self, run_id: str, step_id: str) -> dict[str, Any]: ...

    def record_step_completed(
        self,
        run_id: str,
        step_id: str,
        *,
        status: str = "succeeded",
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def record_step_failed(
        self, run_id: str, step_id: str, *, error: dict[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class WorkflowStepState:
    """Durable state for one workflow step."""

    step_id: str
    status: str
    outcome: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class WorkflowEngine:
    """Persist workflow step boundaries through ``RunStore`` when available."""

    def __init__(
        self, store: WorkflowStateStore | None = None, run_id: str = ""
    ) -> None:
        self.store = store
        self.run_id = run_id

    @property
    def durable(self) -> bool:
        """Return true when step state should be persisted."""

        return self.store is not None and bool(self.run_id)

    def start_step(self, step_id: str) -> WorkflowStepState:
        """Record that a workflow step started."""

        state = WorkflowStepState(step_id=step_id, status="running")
        if self.store is not None and self.run_id:
            self.store.record_step_started(self.run_id, step_id)
        return state

    def complete_step(
        self,
        step_id: str,
        *,
        status: str = "succeeded",
        outcome: dict[str, Any] | None = None,
    ) -> WorkflowStepState:
        """Record that a workflow step reached an outcome."""

        state = WorkflowStepState(step_id=step_id, status=status, outcome=outcome or {})
        if self.store is not None and self.run_id:
            self.store.record_step_completed(
                self.run_id,
                step_id,
                status=status,
                outcome=outcome or {},
            )
        return state

    def fail_step(self, step_id: str, error: BaseException) -> WorkflowStepState:
        """Record that a workflow step failed."""

        error_data = {
            "type": type(error).__name__,
            "message": str(error),
        }
        state = WorkflowStepState(step_id=step_id, status="failed", error=error_data)
        if self.store is not None and self.run_id:
            self.store.record_step_failed(
                self.run_id,
                step_id,
                error=error_data,
            )
        return state
