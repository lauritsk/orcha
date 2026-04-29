"""Interactive argument collection for pid."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pid.config import PIDConfig
from pid.parsing import is_positive_integer, is_unsigned_integer
from pid.typer_parsing import TYPER_PARSER_CONTEXT, parse_typer_args


_AGENT_START_USAGE = (
    "usage: pid agent [start] --branch BRANCH --prompt TEXT "
    "[--attempts N] [--thinking LEVEL]"
)
_ORCHESTRATOR_START_USAGE = (
    "usage: pid orchestrator [start] --goal TEXT "
    "[--plan-file plan.json] [--branch-prefix PREFIX] [--concurrency N]"
)


@dataclass(frozen=True)
class _AgentStartPartial:
    branch: str | None = None
    prompt: str | None = None
    attempts: str | None = None
    thinking: str | None = None
    non_interactive: bool = False
    yes: bool = False
    run_id: str = ""
    parent_run_id: str = ""
    plan_item_id: str = ""


@dataclass(frozen=True)
class _OrchestratorStartPartial:
    goal: str | None = None
    plan_file: Path | None = None
    branch_prefix: str | None = None
    concurrency: str | None = None
    dry_run: bool = False
    non_interactive: bool = False
    yes: bool = False


_AGENT_START_PARTIAL_PARSER = typer.Typer(
    add_completion=False, context_settings=TYPER_PARSER_CONTEXT
)
_ORCHESTRATOR_START_PARTIAL_PARSER = typer.Typer(
    add_completion=False, context_settings=TYPER_PARSER_CONTEXT
)


@_AGENT_START_PARTIAL_PARSER.command(context_settings=TYPER_PARSER_CONTEXT)
def _agent_start_partial_options(
    ctx: typer.Context,
    branch: Annotated[
        str | None, typer.Option("--branch", help="Branch to create/run.")
    ] = None,
    prompt: Annotated[
        str | None, typer.Option("--prompt", help="Agent prompt text.")
    ] = None,
    attempts: Annotated[
        str | None, typer.Option("--attempts", help="Maximum PR loop attempts.")
    ] = None,
    thinking: Annotated[
        str | None, typer.Option("--thinking", help="Agent thinking level override.")
    ] = None,
    non_interactive: Annotated[
        bool, typer.Option("--non-interactive", help="Disable intake prompts.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Accept prompted values.")
    ] = False,
    run_id: Annotated[
        str, typer.Option("--run-id", help="Reuse an existing run id.")
    ] = "",
    parent_run_id: Annotated[
        str, typer.Option("--parent-run-id", help="Parent orchestrator run id.")
    ] = "",
    plan_item_id: Annotated[
        str, typer.Option("--plan-item-id", help="Parent plan item id.")
    ] = "",
) -> tuple[_AgentStartPartial, list[str]]:
    return (
        _AgentStartPartial(
            branch=branch,
            prompt=prompt,
            attempts=attempts,
            thinking=thinking,
            non_interactive=non_interactive,
            yes=yes,
            run_id=run_id,
            parent_run_id=parent_run_id,
            plan_item_id=plan_item_id,
        ),
        list(ctx.args),
    )


@_ORCHESTRATOR_START_PARTIAL_PARSER.command(context_settings=TYPER_PARSER_CONTEXT)
def _orchestrator_start_partial_options(
    ctx: typer.Context,
    goal: Annotated[
        str | None, typer.Option("--goal", help="Overall orchestration goal.")
    ] = None,
    plan_file: Annotated[
        Path | None,
        typer.Option("--plan-file", help="Structured plan JSON to launch."),
    ] = None,
    branch_prefix: Annotated[
        str | None, typer.Option("--branch-prefix", help="Prefix for child branches.")
    ] = None,
    concurrency: Annotated[
        str | None, typer.Option("--concurrency", help="Maximum concurrent child runs.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Plan child runs without launching them.")
    ] = False,
    non_interactive: Annotated[
        bool, typer.Option("--non-interactive", help="Disable intake prompts.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Accept prompted values.")
    ] = False,
) -> tuple[_OrchestratorStartPartial, list[str]]:
    return (
        _OrchestratorStartPartial(
            goal=goal,
            plan_file=plan_file,
            branch_prefix=branch_prefix,
            concurrency=concurrency,
            dry_run=dry_run,
            non_interactive=non_interactive,
            yes=yes,
        ),
        list(ctx.args),
    )


def resolve_interactive_args(argv: list[str], config: PIDConfig) -> list[str]:
    """Prompt for missing pid arguments, then return argv."""

    if argv and argv[0] in {"--help", "-h"}:
        return argv

    original = list(argv)
    args = list(argv)
    resolved: list[str] = []
    prompted = False
    display = _InteractiveDisplay()
    prompt_all_defaults = not args
    interactive = False

    if args and args[0] == "session":
        interactive = True
        resolved.append(args.pop(0))
        if args and args[0] in {"--help", "-h"}:
            return original

    attempts = "3"
    thinking = config.agent.default_thinking
    if args and is_unsigned_integer(args[0]):
        if not is_positive_integer(args[0]):
            return original
        attempts = args.pop(0)
        resolved.append(attempts)
    elif prompt_all_defaults:
        attempts = _prompt_positive_int(
            "Attempts",
            default=attempts,
            example="3",
            values=_values(attempts, thinking, "", ""),
            display=display,
        )
        resolved.append(attempts)
        prompted = True

    if args and args[0] in config.agent.thinking_levels:
        thinking = args.pop(0)
        resolved.append(thinking)
    elif prompt_all_defaults:
        thinking = _prompt_thinking(
            thinking,
            config.agent.thinking_levels,
            _values(attempts, thinking, "", ""),
            display=display,
        )
        resolved.append(thinking)
        prompted = True

    branch = ""
    if args:
        branch = args.pop(0)
        resolved.append(branch)
    else:
        branch = _prompt_required(
            "Branch",
            example="feature/short-description",
            values=_values(attempts, thinking, branch, ""),
            display=display,
        )
        resolved.append(branch)
        prompted = True

    prompt = " ".join(args)
    if prompt:
        resolved.extend(args)
    elif not interactive:
        prompt = _prompt_required(
            "Prompt",
            example="Add OAuth login and tests",
            values=_values(attempts, thinking, branch, prompt),
            display=display,
        )
        resolved.extend(prompt.split())
        prompted = True

    if prompted:
        display.render(_values(attempts, thinking, branch, prompt))
        if not typer.confirm("Continue with these values?", default=True):
            raise typer.Abort()

    return resolved


def resolve_agent_start_args(argv: list[str], config: PIDConfig) -> list[str]:
    """Prompt for missing `pid agent start` options, then return normalized argv."""

    if argv and argv[0] in {"--help", "-h"}:
        return argv

    original = list(argv)
    try:
        namespace, extras = _parse_agent_start_partial(argv)
    except ValueError:
        return original
    if extras or namespace.non_interactive:
        return original

    attempts = str(namespace.attempts or "3").strip()
    thinking = str(namespace.thinking or config.agent.default_thinking).strip()
    branch = str(namespace.branch or "").strip()
    prompt = str(namespace.prompt or "").strip()
    prompt_all_defaults = not argv
    prompted = False
    display = _InteractiveDisplay(title="pid agent start")

    if namespace.attempts is None and prompt_all_defaults:
        attempts = _prompt_positive_int(
            "Attempts",
            default=attempts,
            example="3",
            values=_values(attempts, thinking, branch, prompt),
            display=display,
        )
        prompted = True
    elif not is_positive_integer(attempts):
        return original

    if namespace.thinking is None and prompt_all_defaults:
        thinking = _prompt_thinking(
            thinking,
            config.agent.thinking_levels,
            _values(attempts, thinking, branch, prompt),
            display=display,
        )
        prompted = True
    elif thinking not in config.agent.thinking_levels:
        return original

    if not branch:
        branch = _prompt_required(
            "Branch",
            example="feature/short-description",
            values=_values(attempts, thinking, branch, prompt),
            display=display,
        )
        prompted = True
    if not prompt:
        prompt = _prompt_required(
            "Prompt",
            example="Add OAuth login and tests",
            values=_values(attempts, thinking, branch, prompt),
            display=display,
        )
        prompted = True

    if not prompted:
        return original
    values = _values(attempts, thinking, branch, prompt)
    display.render(values)
    if not namespace.yes and not typer.confirm(
        "Start supervised agent run?", default=True
    ):
        raise typer.Abort()

    return _normalized_agent_start_args(namespace, attempts, thinking, branch, prompt)


def resolve_orchestrator_start_args(argv: list[str], config: PIDConfig) -> list[str]:
    """Prompt for missing `pid orchestrator start` options, then return argv."""

    if argv and argv[0] in {"--help", "-h"}:
        return argv

    original = list(argv)
    try:
        namespace, extras = _parse_orchestrator_start_partial(argv)
    except ValueError:
        return original
    if extras or namespace.non_interactive:
        return original

    goal = str(namespace.goal or "").strip()
    plan_file = str(namespace.plan_file or "").strip()
    branch_prefix = str(namespace.branch_prefix or "").strip().strip("/")
    concurrency = str(
        namespace.concurrency or config.orchestrator.max_parallel_agents
    ).strip()
    prompt_all_defaults = not argv
    prompted = False
    display = _InteractiveDisplay(title="pid orchestrator start")

    if not goal:
        goal = _prompt_required(
            "Goal",
            example="Ship the larger change safely",
            values=_orchestrator_values(goal, plan_file, branch_prefix, concurrency),
            display=display,
        )
        prompted = True
    if namespace.plan_file is None and prompt_all_defaults:
        plan_file = _prompt_optional(
            "Plan file",
            example="plan.json (blank for intake questions first)",
            values=_orchestrator_values(goal, plan_file, branch_prefix, concurrency),
            display=display,
        )
        prompted = True
    if not branch_prefix:
        branch_prefix = default_branch_prefix(goal)
    if namespace.branch_prefix is None and prompt_all_defaults:
        branch_prefix = _prompt_required(
            "Branch prefix",
            example=branch_prefix,
            values=_orchestrator_values(goal, plan_file, branch_prefix, concurrency),
            display=display,
            default=branch_prefix,
        )
        branch_prefix = branch_prefix.strip("/")
        prompted = True
    if namespace.concurrency is None and prompt_all_defaults:
        concurrency = _prompt_positive_int(
            "Concurrency",
            default=concurrency,
            example="4",
            values=_orchestrator_values(goal, plan_file, branch_prefix, concurrency),
            display=display,
        )
        prompted = True
    elif not is_positive_integer(concurrency):
        return original

    if not branch_prefix:
        return original
    if not prompted:
        return original
    values = _orchestrator_values(goal, plan_file, branch_prefix, concurrency)
    display.render(values)
    if not namespace.yes and not typer.confirm("Start orchestrator run?", default=True):
        raise typer.Abort()

    return _normalized_orchestrator_start_args(
        namespace,
        goal=goal,
        plan_file=plan_file,
        branch_prefix=branch_prefix,
        concurrency=concurrency,
    )


def default_branch_prefix(goal: str) -> str:
    """Return branch-prefix default generated from the orchestrator goal."""

    lowered = goal.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug[:48].strip("-") or "work"


def _parse_agent_start_partial(
    argv: list[str],
) -> tuple[_AgentStartPartial, list[str]]:
    parsed: tuple[_AgentStartPartial, list[str]] = parse_typer_args(
        _AGENT_START_PARTIAL_PARSER,
        argv,
        prog_name=_AGENT_START_USAGE,
        error_message="invalid agent start options",
    )
    return parsed


def _parse_orchestrator_start_partial(
    argv: list[str],
) -> tuple[_OrchestratorStartPartial, list[str]]:
    parsed: tuple[_OrchestratorStartPartial, list[str]] = parse_typer_args(
        _ORCHESTRATOR_START_PARTIAL_PARSER,
        argv,
        prog_name=_ORCHESTRATOR_START_USAGE,
        error_message="invalid orchestrator start options",
    )
    return parsed


def _normalized_agent_start_args(
    namespace: _AgentStartPartial,
    attempts: str,
    thinking: str,
    branch: str,
    prompt: str,
) -> list[str]:
    args = ["--branch", branch, "--prompt", prompt, "--attempts", attempts]
    if thinking:
        args.extend(["--thinking", thinking])
    for option, value in (
        ("--run-id", namespace.run_id),
        ("--parent-run-id", namespace.parent_run_id),
        ("--plan-item-id", namespace.plan_item_id),
    ):
        if value:
            args.extend([option, str(value).strip()])
    return args


def _normalized_orchestrator_start_args(
    namespace: _OrchestratorStartPartial,
    *,
    goal: str,
    plan_file: str,
    branch_prefix: str,
    concurrency: str,
) -> list[str]:
    args = [
        "--goal",
        goal,
        "--branch-prefix",
        branch_prefix,
        "--concurrency",
        concurrency,
    ]
    if plan_file:
        args.extend(["--plan-file", plan_file])
    if namespace.dry_run:
        args.append("--dry-run")
    return args


def _values(attempts: str, thinking: str, branch: str, prompt: str) -> dict[str, str]:
    return {
        "attempts": attempts or "(default 3)",
        "thinking": thinking or "(default)",
        "branch": branch or "(unset)",
        "prompt": prompt or "(unset)",
    }


def _orchestrator_values(
    goal: str, plan_file: str, branch_prefix: str, concurrency: str
) -> dict[str, str]:
    return {
        "goal": goal or "(unset)",
        "plan file": plan_file or "(intake first)",
        "branch prefix": branch_prefix or "(unset)",
        "concurrency": concurrency or "(default 4)",
    }


class _InteractiveDisplay:
    """Render prompt summary in-place when stdout is interactive."""

    def __init__(
        self,
        *,
        title: str = "pid prompt",
        subtitle: str = "enter to accept defaults",
    ) -> None:
        self.title = title
        self.subtitle = subtitle
        self._line_count = 0
        self._can_update = typer.get_text_stream("stdout").isatty()
        terminal_width = shutil.get_terminal_size((88, 24)).columns
        width = min(terminal_width, 88) if self._can_update else 88
        self._console = Console(
            color_system="auto" if self._can_update else None,
            force_terminal=self._can_update,
            highlight=False,
            width=width,
        )

    def render(self, values: dict[str, str], *, error: str | None = None) -> None:
        self._clear_previous()
        rendered = self._render(values, error=error if self._can_update else None)
        typer.echo(rendered, nl=False, color=True)
        if error and not self._can_update:
            typer.echo(error, err=True)
        self._line_count = len(rendered.splitlines())

    def record_prompt_result(
        self,
        message: str,
        value: str,
        *,
        default: str | None = None,
        show_default: bool = False,
    ) -> None:
        """Account for the Typer prompt line before the next in-place render."""

        if not self._can_update:
            return
        prompt_line = message
        if show_default and default is not None:
            prompt_line = f"{prompt_line} [{default}]"
        prompt_line = f"{prompt_line}: {value}"
        width = max(1, self._console.width)
        self._line_count += max(1, (len(prompt_line) - 1) // width + 1)

    def _render(self, values: dict[str, str], *, error: str | None) -> str:
        with self._console.capture() as capture:
            self._console.print(
                _prompt_summary(
                    values,
                    error=error,
                    width=self._console.width,
                    title=self.title,
                    subtitle=self.subtitle,
                )
            )
        return capture.get()

    def _clear_previous(self) -> None:
        if not self._can_update or self._line_count <= 0:
            return
        typer.echo(f"\033[{self._line_count}A", nl=False, color=True)
        for index in range(self._line_count):
            typer.echo("\033[2K\r", nl=False, color=True)
            if index < self._line_count - 1:
                typer.echo("\033[1B", nl=False, color=True)
        typer.echo(f"\033[{self._line_count - 1}A", nl=False, color=True)


def _prompt_summary(
    values: dict[str, str],
    *,
    error: str | None,
    width: int | None = None,
    title: str = "pid prompt",
    subtitle: str = "enter to accept defaults",
) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold magenta", no_wrap=True)
    table.add_column()
    for label, value in values.items():
        table.add_row(label, _summary_value(value))

    renderable = table
    if error:
        renderable = Group(table, Text(f"✗ {error}", style="bold red"))

    return Panel(
        renderable,
        title=Text(title, style="bold magenta"),
        subtitle=Text(subtitle, style="dim"),
        border_style="magenta",
        width=width,
    )


def _summary_value(value: str) -> Text:
    if value == "(unset)" or value.startswith("(default"):
        return Text(value, style="dim")
    return Text(value)


def _prompt_thinking(
    default: str,
    thinking_levels: tuple[str, ...],
    values: dict[str, str],
    *,
    display: _InteractiveDisplay,
) -> str:
    levels = ", ".join(thinking_levels)
    error: str | None = None
    while True:
        display.render(values, error=error)
        message = f"Thinking level ({levels})"
        value = typer.prompt(message, default=default, show_default=True)
        display.record_prompt_result(message, value, default=default, show_default=True)
        if value in thinking_levels:
            return value
        error = f"Choose one of: {levels}."


def _prompt_required(
    label: str,
    *,
    example: str,
    values: dict[str, str],
    display: _InteractiveDisplay,
    default: str = "",
) -> str:
    error: str | None = None
    while True:
        display.render(values, error=error)
        message = f"{label} (example: {example})"
        value = typer.prompt(message, default=default, show_default=bool(default))
        display.record_prompt_result(
            message, value, default=default or None, show_default=bool(default)
        )
        if value.strip():
            return value.strip()
        error = f"{label} is required. Example: {example}"


def _prompt_optional(
    label: str, *, example: str, values: dict[str, str], display: _InteractiveDisplay
) -> str:
    display.render(values)
    message = f"{label} (optional, example: {example})"
    value = typer.prompt(message, default="", show_default=False)
    display.record_prompt_result(message, value)
    return value.strip()


def _prompt_positive_int(
    label: str,
    *,
    default: str,
    example: str,
    values: dict[str, str],
    display: _InteractiveDisplay,
) -> str:
    error: str | None = None
    while True:
        display.render(values, error=error)
        message = f"{label} (positive integer)"
        value = typer.prompt(message, default=default, show_default=True)
        display.record_prompt_result(message, value, default=default, show_default=True)
        if is_positive_integer(value):
            return value
        error = f"Enter a positive integer, e.g. {example}."
