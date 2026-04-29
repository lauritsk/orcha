from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pid.engine import WorkflowEngine
from pid.errors import PIDAbort
from pid.extensions import ExtensionError, ExtensionRegistry, StepResult, WorkflowStep
from pid.run_state import RunStore


class EngineContext:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []
        self.checkpoints: list[str] = []

    def emit(
        self,
        name: str,
        *,
        step: str = "",
        level: str = "info",
        message: str = "",
        fields: dict[str, Any] | None = None,
    ) -> None:
        del level, fields
        self.events.append((name, step, message))


def run_state(tmp_path: Path) -> tuple[RunStore, str]:
    store = RunStore(tmp_path / "runs")
    state = store.create_run(branch="feature/engine", prompt="prompt", argv=[])
    return store, str(state["run_id"])


def test_engine_persists_start_success_skip_retry_stop_and_failure(
    tmp_path: Path,
) -> None:
    store, run_id = run_state(tmp_path)
    engine = WorkflowEngine(store, run_id)
    registry = ExtensionRegistry()
    ctx = EngineContext()

    engine.execute_step(ctx, WorkflowStep("success", lambda _ctx: None), registry)
    registry.add_hook("before.skipped", lambda _ctx: StepResult.skip("not needed"))
    engine.execute_step(ctx, WorkflowStep("skipped", lambda _ctx: None), registry)

    retries = {"count": 0}

    def retry_once(_ctx: EngineContext) -> StepResult | None:
        retries["count"] += 1
        return StepResult.retry("again") if retries["count"] == 1 else None

    engine.execute_step(ctx, WorkflowStep("retry_once", retry_once), registry)

    with pytest.raises(PIDAbort):
        engine.execute_step(
            ctx,
            WorkflowStep("stopped", lambda _ctx: StepResult.stop(7, "done")),
            registry,
        )

    def fail(_ctx: EngineContext) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        engine.execute_step(ctx, WorkflowStep("failed", fail), registry)

    state = store.read_state(run_id)
    steps = state["workflow"]["steps"]
    assert steps["success"]["status"] == "succeeded"
    assert steps["skipped"]["status"] == "skipped"
    assert steps["retry_once"]["status"] == "succeeded"
    assert any(
        item["step_id"] == "retry_once" and item["status"] == "retrying"
        for item in state["workflow"]["history"]
    )
    assert steps["stopped"]["status"] == "failed"
    assert steps["stopped"]["error"]["type"] == "PIDAbort"
    assert steps["failed"]["status"] == "failed"
    assert steps["failed"]["error"]["message"] == "boom"


def test_engine_runs_checkpoints_hooks_error_hooks_replacements_and_disables() -> None:
    registry = ExtensionRegistry()
    engine = WorkflowEngine()
    ctx = EngineContext()
    seen: list[str] = []

    def checkpoint(context: EngineContext, step: str) -> None:
        context.checkpoints.append(step)

    registry.disable_step("disabled")
    engine.execute_step(
        ctx,
        WorkflowStep("disabled", lambda _ctx: seen.append("disabled")),
        registry,
        checkpoint=checkpoint,
    )
    assert seen == []
    assert ctx.checkpoints == []

    registry.replace_step(
        "original",
        WorkflowStep("ignored_name", lambda _ctx: seen.append("replacement")),
    )
    registry.add_hook("before.original", lambda _ctx: seen.append("before"))
    registry.add_hook("after.original", lambda _ctx: seen.append("after"))
    engine.execute_step(
        ctx,
        WorkflowStep("original", lambda _ctx: seen.append("original")),
        registry,
        checkpoint=checkpoint,
    )
    assert seen == ["before", "replacement", "after"]
    assert ctx.checkpoints == ["original"]
    assert ("step.completed", "original", "") in ctx.events

    registry.add_hook("error.broken", lambda _ctx: seen.append("error"))
    with pytest.raises(ValueError):
        engine.execute_step(
            ctx,
            WorkflowStep("broken", lambda _ctx: (_ for _ in ()).throw(ValueError())),
            registry,
        )
    assert seen[-1] == "error"

    registry.add_hook("error.stop_on_error", lambda _ctx: StepResult.stop(9))
    with pytest.raises(PIDAbort) as error_hook_stop:
        engine.execute_step(
            ctx,
            WorkflowStep(
                "stop_on_error", lambda _ctx: (_ for _ in ()).throw(ValueError())
            ),
            registry,
        )
    assert error_hook_stop.value.code == 9

    with pytest.raises(ExtensionError):
        engine.handle_step_result(StepResult("bogus"))
