"""High-level orchestrator agent supervisor."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pid.config import PIDConfig
from pid.events import EventSink
from pid.failures import FailureKind, WorkflowFailure
from pid.models import OutputMode
from pid.policy import DeterministicRecoveryPolicy, RecoveryActionKind
from pid.run_state import FOLLOWUP_KINDS, RunEventSink, RunStore, generate_run_id
from pid.workflow import PIDFlow

INTAKE_QUESTIONS = [
    "What exact outcome should exist when this is done?",
    "What are explicit non-goals or areas the work must not touch?",
    "Which repo areas, packages, commands, docs, or configs may change?",
    "What UX, CLI, API, compatibility, migration, or data constraints apply?",
    "What security, privacy, performance, accessibility, or reliability risks exist?",
    "What test strategy and quality gates must each child session run?",
    "What documentation, release notes, examples, or handoff notes are required?",
    "What branch prefix and PR granularity should child sessions use?",
    "May child sessions merge independently, or should they stop at PRs?",
    "Which subtasks conflict, depend on each other, or must run serially?",
]


@dataclass(frozen=True)
class AgentStartOptions:
    """Options for starting an orchestrated run."""

    branch: str
    prompt: str
    attempts: int = 3
    thinking: str = ""
    run_id: str = ""
    parent_run_id: str = ""
    plan_item_id: str = ""


@dataclass(frozen=True)
class AgentRunResult:
    """Final orchestrator result."""

    run_id: str
    state: dict[str, Any]
    exit_code: int


@dataclass(frozen=True)
class OrchestratorStartOptions:
    """Options for starting a multi-child orchestrator run."""

    goal: str
    plan_file: Path | None = None
    branch_prefix: str = ""
    concurrency: int = 0
    dry_run: bool = False
    non_interactive: bool = False
    config_path: Path | None = None
    yes: bool = False


@dataclass(frozen=True)
class OrchestratorFollowUpOptions:
    """Options for routing an orchestrator follow-up."""

    run_id: str
    message: str
    kind: str = "clarify"
    target: str = ""
    all_children: bool = False


@dataclass(frozen=True)
class OrchestratorRunResult:
    """Final multi-child orchestrator command result."""

    run_id: str
    state: dict[str, Any]
    exit_code: int = 0


class OrchestratorDisabled(RuntimeError):
    """Raised when orchestrator CLI is disabled by config."""


class OrchestratorAgent:
    """Supervise one PIDFlow and persist run state."""

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

        if (
            options.thinking
            and options.thinking not in self.config.agent.thinking_levels
        ):
            levels = ", ".join(self.config.agent.thinking_levels)
            raise ValueError(f"--thinking must be one of: {levels}")
        argv = workflow_argv(
            options, default_thinking=self.config.agent.default_thinking
        )
        state = self.store.create_run(
            branch=options.branch,
            prompt=options.prompt,
            argv=argv,
            run_id=options.run_id,
            parent_run_id=options.parent_run_id,
            plan_item_id=options.plan_item_id,
            status="running",
            extra={"thinking": options.thinking or self.config.agent.default_thinking},
        )
        run_id = str(state["run_id"])
        flow = PIDFlow(
            config=self.config,
            output_mode=self.output_mode,
            events=RunEventSink(self.store, run_id, self.events),
            run_store=self.store,
            run_id=run_id,
        )
        try:
            ctx = flow.run_supervised(argv)
        except WorkflowFailure as failure:
            self.store.update_from_context(run_id, flow.context)
            action = self.policy.decide(failure)
            if action.kind == RecoveryActionKind.MARK_DONE:
                state = self.store.mark_failed(
                    run_id,
                    failure,
                    pending_recovery_action=action.to_dict(),
                    status="no_changes",
                )
                return AgentRunResult(run_id, state, failure.code)
            if failure.kind == FailureKind.FOLLOWUP_PAUSED:
                state = self.store.mark_failed(
                    run_id,
                    failure,
                    pending_recovery_action=action.to_dict(),
                    status="paused",
                )
                return AgentRunResult(run_id, state, failure.code)
            if failure.kind == FailureKind.FOLLOWUP_ABORTED:
                state = self.store.mark_failed(
                    run_id,
                    failure,
                    pending_recovery_action=action.to_dict(),
                    status="aborted",
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


class OrchestratorSupervisor:
    """Coordinate a larger effort across many child pid agent runs."""

    def __init__(
        self,
        *,
        config: PIDConfig,
        store: RunStore,
        output_mode: OutputMode = OutputMode.NORMAL,
    ) -> None:
        if not config.orchestrator.enabled:
            raise OrchestratorDisabled("orchestrator agent is disabled in config")
        self.config = config
        self.store = store
        self.output_mode = output_mode

    def start(self, options: OrchestratorStartOptions) -> OrchestratorRunResult:
        """Create an orchestrator run, ask intake, and launch ready children."""

        questions = list(INTAKE_QUESTIONS)
        branch_prefix = options.branch_prefix or slug(options.goal) or "work"
        concurrency = (
            options.concurrency or self.config.orchestrator.max_parallel_agents
        )
        validation_commands = list(self.config.orchestrator.validation_commands)
        if options.plan_file is None:
            state = self.store.create_orchestrator_run(
                goal=options.goal,
                questions=questions,
                branch_prefix=branch_prefix,
                concurrency=concurrency,
                validation_commands=validation_commands,
                status="needs_answers" if options.non_interactive else "awaiting_plan",
            )
            return OrchestratorRunResult(
                str(state["run_id"]), state, 2 if options.non_interactive else 0
            )

        raw_plan = load_plan(options.plan_file)
        state = self.store.create_orchestrator_run(
            goal=options.goal,
            questions=questions,
            plan=raw_plan,
            branch_prefix=branch_prefix,
            concurrency=concurrency,
            validation_commands=validation_commands,
            status="planned",
        )
        run_id = str(state["run_id"])
        children = build_child_records(
            raw_plan,
            goal=options.goal,
            parent_run_id=run_id,
            branch_prefix=branch_prefix,
            config=self.config,
        )
        for child in children:
            self.store.create_run(
                branch=str(child["branch"]),
                prompt=str(child["prompt"]),
                argv=workflow_argv(
                    AgentStartOptions(
                        branch=str(child["branch"]),
                        prompt=str(child["prompt"]),
                        thinking=str(child["thinking"]),
                        run_id=str(child["child_run_id"]),
                        parent_run_id=run_id,
                        plan_item_id=str(child["item_id"]),
                    ),
                    default_thinking=self.config.agent.default_thinking,
                ),
                run_id=str(child["child_run_id"]),
                parent_run_id=run_id,
                plan_item_id=str(child["item_id"]),
                status="planned",
                extra={"thinking": child["thinking"]},
            )

        state["children"] = children
        state["status"] = "planned" if options.dry_run else "running"
        self.store.write_state(run_id, state)
        if options.dry_run:
            return OrchestratorRunResult(run_id, self.store.read_state(run_id))

        launched = launch_ready_children(
            children,
            parent_run_id=run_id,
            config_path=options.config_path,
            default_thinking=self.config.agent.default_thinking,
            concurrency=concurrency,
        )
        state = self.store.read_state(run_id)
        state["children"] = launched
        if not any(child.get("status") == "launched" for child in launched):
            state["status"] = "blocked"
        self.store.write_state(run_id, state)
        return OrchestratorRunResult(run_id, self.store.read_state(run_id))

    def follow_up(self, options: OrchestratorFollowUpOptions) -> dict[str, Any]:
        """Record and optionally route a follow-up to child run inboxes."""

        if options.kind not in FOLLOWUP_KINDS:
            valid = ", ".join(FOLLOWUP_KINDS)
            raise ValueError(f"follow-up type must be one of: {valid}")
        state = self.store.read_state(options.run_id)
        if state.get("run_type") != "orchestrator":
            raise ValueError(f"run is not an orchestrator run: {options.run_id}")

        record = self.store.append_followup(
            options.run_id,
            message=options.message,
            kind=options.kind,
            source="user",
        )
        targets = select_followup_targets(
            state.get("children", []),
            target=options.target,
            all_children=options.all_children,
        )
        delivered: list[str] = []
        for child in targets:
            child_run_id = str(child["child_run_id"])
            child_record = self.store.append_followup(
                child_run_id,
                message=options.message,
                kind=options.kind,
                source="orchestrator",
                sender_run_id=options.run_id,
            )
            child["last_follow_up_id"] = child_record["id"]
            delivered.append(child_run_id)

        state = self.store.read_state(options.run_id)
        state["children"] = state.get("children", [])
        for child in state["children"]:
            for updated in targets:
                if child.get("child_run_id") == updated.get("child_run_id"):
                    child["last_follow_up_id"] = updated.get("last_follow_up_id", "")
        state.setdefault("followups", []).append(
            {
                "id": record["id"],
                "kind": options.kind,
                "message": options.message,
                "target": options.target or ("all" if options.all_children else ""),
                "routed_to": delivered,
                "status": "queued" if delivered else "recorded",
            }
        )
        self.store.write_state(options.run_id, state)
        return {"record": record, "routed_to": delivered}


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


def load_plan(path: Path) -> dict[str, Any]:
    """Load structured orchestrator plan JSON."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"could not read plan file: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"plan file must be valid JSON: {error}") from error
    if isinstance(value, list):
        return {"items": value, "constraints": []}
    if not isinstance(value, dict):
        raise ValueError("plan file must contain a JSON object or item array")
    if not isinstance(value.get("items"), list):
        raise ValueError("plan file must contain an items array")
    return value


def build_child_records(
    plan: dict[str, Any],
    *,
    goal: str,
    parent_run_id: str,
    branch_prefix: str,
    config: PIDConfig,
) -> list[dict[str, Any]]:
    """Return child launch records with branch, thinking, and prompt selected."""

    constraints = string_list(plan.get("constraints", []))
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_item in enumerate(_plan_items(plan), start=1):
        item = _plan_item(raw_item)
        title, item_id = _child_item_identity(item, index)
        if item_id in seen_ids:
            raise ValueError(f"duplicate plan item id: {item_id}")
        seen_ids.add(item_id)
        records.append(
            _build_child_record(
                item,
                item_id=item_id,
                title=title,
                goal=goal,
                constraints=constraints,
                parent_run_id=parent_run_id,
                branch_prefix=branch_prefix,
                config=config,
            )
        )
    return records


def _plan_items(plan: dict[str, Any]) -> list[object]:
    items = plan.get("items", [])
    if not isinstance(items, list) or not items:
        raise ValueError("plan must contain at least one item")
    return items


def _plan_item(raw_item: object) -> dict[str, object]:
    if not isinstance(raw_item, dict):
        raise ValueError("each plan item must be an object")
    return cast("dict[str, object]", raw_item)


def _child_item_identity(item: dict[str, object], index: int) -> tuple[str, str]:
    title = str(item.get("title") or f"Item {index}").strip()
    item_id = slug(str(item.get("id") or title or f"item-{index}"))
    if not item_id:
        item_id = f"item-{index}"
    return title, item_id


def _build_child_record(
    item: dict[str, object],
    *,
    item_id: str,
    title: str,
    goal: str,
    constraints: list[str],
    parent_run_id: str,
    branch_prefix: str,
    config: PIDConfig,
) -> dict[str, Any]:
    scope = _child_scope(item)
    acceptance = string_list(
        item.get("acceptance", item.get("acceptance_criteria", []))
    )
    validation = _child_validation(item, config)
    dependencies = [slug(value) for value in string_list(item.get("dependencies", []))]
    return {
        "item_id": item_id,
        "title": title,
        "scope": scope,
        "acceptance": acceptance,
        "validation": validation,
        "dependencies": dependencies,
        "branch": _child_branch(
            item, branch_prefix=branch_prefix, item_id=item_id, title=title
        ),
        "thinking": _child_thinking(
            item,
            item_id=item_id,
            title=title,
            scope=scope,
            acceptance=acceptance,
            validation=validation,
            config=config,
        ),
        "prompt": _child_prompt(
            item,
            goal=goal,
            constraints=constraints,
            item_id=item_id,
            title=title,
            scope=scope,
            acceptance=acceptance,
            validation=validation,
            dependencies=dependencies,
        ),
        "child_run_id": generate_run_id(),
        "parent_run_id": parent_run_id,
        "status": "blocked" if dependencies else "planned",
        "pid": None,
        "launch_command": [],
        "last_follow_up_id": "",
    }


def _child_scope(item: dict[str, object]) -> str:
    return str(item.get("scope") or item.get("description") or "").strip()


def _child_validation(item: dict[str, object], config: PIDConfig) -> list[str]:
    return string_list(
        item.get(
            "validation",
            item.get(
                "validation_commands", list(config.orchestrator.validation_commands)
            ),
        )
    )


def _child_branch(
    item: dict[str, object], *, branch_prefix: str, item_id: str, title: str
) -> str:
    branch = str(item.get("branch") or "").strip()
    if branch:
        return branch
    return f"{branch_prefix}/{item_id}-{slug(title)}".rstrip("-")


def _child_thinking(
    item: dict[str, object],
    *,
    item_id: str,
    title: str,
    scope: str,
    acceptance: list[str],
    validation: list[str],
    config: PIDConfig,
) -> str:
    thinking = str(item.get("thinking") or "").strip()
    if not thinking:
        thinking = select_thinking(
            title=title,
            scope=scope,
            acceptance=acceptance,
            validation=validation,
            config=config,
        )
    if thinking not in config.agent.thinking_levels:
        levels = ", ".join(config.agent.thinking_levels)
        raise ValueError(f"thinking for {item_id} must be one of: {levels}")
    return thinking


def _child_prompt(
    item: dict[str, object],
    *,
    goal: str,
    constraints: list[str],
    item_id: str,
    title: str,
    scope: str,
    acceptance: list[str],
    validation: list[str],
    dependencies: list[str],
) -> str:
    prompt = str(item.get("prompt") or "").strip()
    if prompt:
        return prompt
    return build_child_prompt(
        goal=goal,
        constraints=constraints,
        item_id=item_id,
        title=title,
        scope=scope,
        acceptance=acceptance,
        validation=validation,
        dependencies=dependencies,
    )


def build_child_prompt(
    *,
    goal: str,
    constraints: list[str],
    item_id: str,
    title: str,
    scope: str,
    acceptance: list[str],
    validation: list[str],
    dependencies: list[str],
) -> str:
    """Build a bounded child-session prompt."""

    lines = [
        "You are a child pid session launched by an orchestrator.",
        f"Global goal: {goal}",
        f"Plan item: {item_id} - {title}",
        f"Scope: {scope or title}",
    ]
    if constraints:
        lines.append("Global constraints:")
        lines.extend(f"- {constraint}" for constraint in constraints)
    if dependencies:
        lines.append("Dependencies to respect:")
        lines.extend(f"- {dependency}" for dependency in dependencies)
    if acceptance:
        lines.append("Acceptance criteria:")
        lines.extend(f"- {criterion}" for criterion in acceptance)
    if validation:
        lines.append("Validation commands to run when relevant:")
        lines.extend(f"- {command}" for command in validation)
    lines.extend(
        [
            "Stay inside this item scope.",
            "Do not broaden work without a follow-up from the orchestrator/user.",
            "Record blockers clearly if dependencies or conflicts prevent completion.",
        ]
    )
    return "\n".join(lines)


def select_thinking(
    *,
    title: str,
    scope: str,
    acceptance: list[str],
    validation: list[str],
    config: PIDConfig,
) -> str:
    """Select a thinking level from item risk and complexity."""

    levels = config.agent.thinking_levels
    text = " ".join([title, scope, *acceptance, *validation]).lower()
    low = levels[0]
    medium = (
        config.agent.default_thinking
        if config.agent.default_thinking in levels
        else levels[min(1, len(levels) - 1)]
    )
    high = levels[-2] if len(levels) > 2 else levels[-1]
    highest = levels[-1]
    if any(
        word in text
        for word in (
            "security",
            "migration",
            "data",
            "privacy",
            "auth",
            "performance",
            "concurrency",
        )
    ):
        return highest
    if any(
        word in text
        for word in (
            "api",
            "schema",
            "integration",
            "merge",
            "workflow",
            "orchestrator",
        )
    ):
        return high
    if any(word in text for word in ("docs", "readme", "typo", "test", "lint")):
        return low
    return medium


def launch_ready_children(
    children: list[dict[str, Any]],
    *,
    parent_run_id: str,
    config_path: Path | None,
    default_thinking: str,
    concurrency: int,
) -> list[dict[str, Any]]:
    """Launch dependency-free child runs as parallel pid subprocesses."""

    launched = 0
    for child in children:
        if child.get("dependencies"):
            child["status"] = "blocked"
            continue
        if launched >= concurrency:
            child["status"] = "queued"
            continue
        command = child_agent_command(
            child,
            parent_run_id=parent_run_id,
            config_path=config_path,
            default_thinking=default_thinking,
        )
        process = subprocess.Popen(command, cwd=Path.cwd(), env=os.environ.copy())  # noqa: S603 - command is constructed from pid allow-listed arguments
        child["pid"] = process.pid
        child["status"] = "launched"
        child["launch_command"] = command
        launched += 1
    return children


def child_agent_command(
    child: dict[str, Any],
    *,
    parent_run_id: str,
    config_path: Path | None,
    default_thinking: str,
) -> list[str]:
    """Return command line for one child pid agent process."""

    command = [sys.executable, "-m", "pid"]
    if config_path is not None:
        command.extend(["--config", str(config_path)])
    command.extend(
        [
            "agent",
            "start",
            "--run-id",
            str(child["child_run_id"]),
            "--parent-run-id",
            parent_run_id,
            "--plan-item-id",
            str(child["item_id"]),
            "--branch",
            str(child["branch"]),
            "--prompt",
            str(child["prompt"]),
        ]
    )
    thinking = str(child.get("thinking") or "")
    if thinking and thinking != default_thinking:
        command.extend(["--thinking", thinking])
    return command


def select_followup_targets(
    children: object, *, target: str, all_children: bool
) -> list[dict[str, Any]]:
    """Select child records that should receive an orchestrator follow-up."""

    if not isinstance(children, list):
        return []
    typed_children = [
        cast("dict[str, Any]", child) for child in children if isinstance(child, dict)
    ]
    if all_children:
        return typed_children
    if not target:
        return []
    selected = [
        child
        for child in typed_children
        if target in {str(child.get("item_id", "")), str(child.get("child_run_id", ""))}
    ]
    if not selected:
        raise ValueError(f"no child matches follow-up target: {target}")
    return selected


def string_list(value: object) -> list[str]:
    """Normalize a string-or-list field to a string list."""

    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def slug(value: str) -> str:
    """Return a branch-safe slug component."""

    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-")
