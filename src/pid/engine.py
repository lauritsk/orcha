"""Durable workflow step engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pid.errors import abort
from pid.extensions import (
    ExtensionError,
    ExtensionRegistry,
    StepResult,
    WorkflowStep,
    normalize_step_result,
)
from pid.output import echo_err
from pid.utils import workflow_step_label


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


class WorkflowContextLike(Protocol):
    """Context protocol used by durable step execution."""

    def emit(
        self,
        name: str,
        *,
        step: str = "",
        level: str = "info",
        message: str = "",
        fields: dict[str, Any] | None = None,
    ) -> None: ...


StepCheckpoint = Callable[[Any, str], None]
CurrentStepCallback = Callable[[str], None]


@dataclass(frozen=True)
class WorkflowStepState:
    """Durable state for one workflow step."""

    step_id: str
    status: str
    outcome: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class WorkflowEngine:
    """Execute workflow steps and persist durable step boundaries."""

    def __init__(
        self,
        store: WorkflowStateStore | None = None,
        run_id: str = "",
        *,
        retry_limit: int = 3,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.retry_limit = retry_limit
        self.current_step = ""

    @property
    def durable(self) -> bool:
        """Return true when step state should be persisted."""

        return self.store is not None and bool(self.run_id)

    def execute_step(
        self,
        ctx: WorkflowContextLike,
        step: WorkflowStep,
        registry: ExtensionRegistry,
        *,
        checkpoint: StepCheckpoint | None = None,
        current_step_callback: CurrentStepCallback | None = None,
    ) -> None:
        """Run one step with hooks, events, retry, and durable state."""

        if step.name in registry.disabled_steps:
            return
        step = registry.replaced_steps.get(step.name, step)
        retries = 0
        while True:
            self._set_current_step(step.name, current_step_callback)
            self.start_step(step.name)
            try:
                if checkpoint is not None:
                    checkpoint(ctx, step.name)
                ctx.emit("step.started", step=step.name)
                before_result = registry.run_hooks(f"before.{step.name}", ctx)
                if before_result.action == "skip":
                    self.complete_step(
                        step.name,
                        status="skipped",
                        outcome=self.step_result_outcome(before_result),
                    )
                    ctx.emit(
                        "step.skipped", step=step.name, message=before_result.reason
                    )
                    self._clear_current_step(current_step_callback)
                    return
                self.handle_step_result(before_result)
            except Exception as error:
                self.fail_step(step.name, error)
                raise

            try:
                result = normalize_step_result(step.run(ctx))
            except Exception as error:
                ctx.emit(
                    "step.failed",
                    step=step.name,
                    level="error",
                    fields={"error": f"{type(error).__name__}: {error}"},
                )
                self.fail_step(step.name, error)
                error_result = registry.run_hooks(f"error.{step.name}", ctx)
                if error_result.action != "continue":
                    self.handle_step_result(error_result)
                raise

            try:
                after_result = registry.run_hooks(f"after.{step.name}", ctx)
                if after_result.action != "continue":
                    result = after_result
                if result.action == "retry":
                    retries += 1
                    self.complete_step(
                        step.name,
                        status="retrying",
                        outcome=self.step_result_outcome(result),
                    )
                    if retries > self.retry_limit:
                        echo_err(
                            "pid: step retry limit reached: "
                            f"{workflow_step_label(step.name)}"
                        )
                        abort(1)
                    ctx.emit("step.retrying", step=step.name, message=result.reason)
                    continue
                self.handle_step_result(result)
                self.complete_step(
                    step.name,
                    outcome=self.step_result_outcome(result),
                )
                ctx.emit("step.completed", step=step.name)
                self._clear_current_step(current_step_callback)
                return
            except Exception as error:
                self.fail_step(step.name, error)
                raise

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

    def _set_current_step(
        self, step_id: str, callback: CurrentStepCallback | None
    ) -> None:
        self.current_step = step_id
        if callback is not None:
            callback(step_id)

    def _clear_current_step(self, callback: CurrentStepCallback | None) -> None:
        self.current_step = ""
        if callback is not None:
            callback("")

    @staticmethod
    def step_result_outcome(result: StepResult) -> dict[str, object]:
        """Return persisted, stable step result fields."""

        return {
            "action": result.action,
            "code": result.code,
            "reason": result.reason,
        }

    @staticmethod
    def handle_step_result(result: StepResult) -> None:
        """Apply a step or hook result."""

        if result.action in {"continue", "skip"}:
            return
        if result.action == "stop":
            abort(result.code)
        if result.action == "retry":
            return
        raise ExtensionError(f"unknown step result action: {result.action}")
