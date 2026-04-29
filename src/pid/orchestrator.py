"""High-level orchestrator agent supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pid.config import PIDConfig
from pid.events import EventSink
from pid.failures import FailureKind, WorkflowFailure
from pid.models import OutputMode
from pid.policy import DeterministicRecoveryPolicy, RecoveryActionKind
from pid.run_state import RunEventSink, RunStore
from pid.workflow import PIDFlow


@dataclass(frozen=True)
class AgentStartOptions:
    """Options for starting an orchestrated run."""

    branch: str
    prompt: str
    attempts: int = 3
    thinking: str = ""
    non_interactive: bool = False
    yes: bool = False
    advisor: str = "policy"
    confirm_merge: bool = False


@dataclass(frozen=True)
class AgentRunResult:
    """Final orchestrator result."""

    run_id: str
    state: dict[str, Any]
    exit_code: int


class OrchestratorDisabled(RuntimeError):
    """Raised when orchestrator CLI is disabled by config."""


class OrchestratorAgent:
    """Supervise PIDFlow and persist run state."""

    def __init__(
        self,
        *,
        config: PIDConfig,
        store: RunStore,
        output_mode: OutputMode = OutputMode.NORMAL,
        events: EventSink | None = None,
        policy: DeterministicRecoveryPolicy | None = None,
    ) -> None:
        if not config.orchestrator.enabled:
            raise OrchestratorDisabled("orchestrator agent is disabled in config")
        self.config = config
        self.store = store
        self.output_mode = output_mode
        self.events = events
        self.policy = policy or DeterministicRecoveryPolicy()

    def start(self, options: AgentStartOptions) -> AgentRunResult:
        """Start one supervised workflow run."""

        if options.advisor != "policy":
            raise ValueError("only deterministic policy advisor is supported")
        argv = workflow_argv(
            options, default_thinking=self.config.agent.default_thinking
        )
        state = self.store.create_run(
            branch=options.branch,
            prompt=options.prompt,
            argv=argv,
        )
        run_id = str(state["run_id"])
        flow = PIDFlow(
            config=self.config,
            output_mode=self.output_mode,
            events=RunEventSink(self.store, run_id, self.events),
        )
        try:
            ctx = flow.run_supervised(argv)
        except WorkflowFailure as failure:
            self.store.update_from_context(run_id, flow.context)
            state = self.store.read_state(run_id)
            action = self.policy.decide(failure, state=state)
            if action.kind == RecoveryActionKind.MARK_DONE:
                stopped = (
                    "no_changes"
                    if failure.kind == FailureKind.NO_CHANGES
                    else "stopped"
                )
                state = self.store.mark_failed(
                    run_id,
                    failure,
                    pending_recovery_action=action.to_dict(),
                    status=stopped,
                )
                return AgentRunResult(run_id, state, failure.code)
            state = self.store.mark_failed(
                run_id,
                failure,
                pending_recovery_action=action.to_dict(),
            )
            return AgentRunResult(run_id, state, failure.code)
        state = self.store.mark_succeeded(run_id, ctx)
        return AgentRunResult(run_id, state, 0)


def workflow_argv(options: AgentStartOptions, *, default_thinking: str) -> list[str]:
    """Translate orchestrator start options into existing workflow argv."""

    argv: list[str] = []
    if options.attempts != 3:
        argv.append(str(options.attempts))
    thinking = options.thinking or default_thinking
    if thinking != default_thinking:
        argv.append(thinking)
    argv.extend([options.branch, options.prompt])
    return argv
