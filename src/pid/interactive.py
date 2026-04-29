"""Interactive argument collection for pid."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import click
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pid.config import PIDConfig


_AGENT_START_USAGE = (
    "usage: pid agent [start] --branch BRANCH --prompt TEXT "
    "[--attempts N] [--thinking LEVEL]"
)
_ORCHESTRATOR_START_USAGE = (
    "usage: pid orchestrator [start] --goal TEXT "
    "[--plan-file plan.json] [--branch-prefix PREFIX] [--concurrency N]"
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
    if args and re.fullmatch(r"[0-9]+", args[0]):
        if re.fullmatch(r"[1-9][0-9]*", args[0]) is None:
            return original
        attempts = args.pop(0)
        resolved.append(attempts)
    elif prompt_all_defaults:
        attempts = _prompt_attempts(
            attempts,
            _values(attempts, config.agent.default_thinking, "", ""),
            display=display,
        )
        resolved.append(attempts)
        prompted = True

    thinking = config.agent.default_thinking
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
        if not click.confirm("Continue with these values?", default=True):
            raise click.Abort()

    return resolved


def resolve_agent_start_args(argv: list[str], config: PIDConfig) -> list[str]:
    """Prompt for missing `pid agent start` options, then return normalized argv."""

    if argv and argv[0] in {"--help", "-h"}:
        return argv

    original = list(argv)
    try:
        namespace, extras = _parse_agent_start_partial(argv)
    except SystemExit:
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
        attempts = _prompt_attempts(
            attempts,
            _values(attempts, thinking, branch, prompt),
            display=display,
        )
        prompted = True
    elif re.fullmatch(r"[1-9][0-9]*", attempts) is None:
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
    if not namespace.yes and not click.confirm(
        "Start supervised agent run?", default=True
    ):
        raise click.Abort()

    return _normalized_agent_start_args(namespace, attempts, thinking, branch, prompt)


def resolve_orchestrator_start_args(argv: list[str], config: PIDConfig) -> list[str]:
    """Prompt for missing `pid orchestrator start` options, then return argv."""

    del config
    if argv and argv[0] in {"--help", "-h"}:
        return argv

    original = list(argv)
    try:
        namespace, extras = _parse_orchestrator_start_partial(argv)
    except SystemExit:
        return original
    if extras or namespace.non_interactive:
        return original

    goal = str(namespace.goal or "").strip()
    plan_file = str(namespace.plan_file or "").strip()
    branch_prefix = str(namespace.branch_prefix or "work").strip().strip("/")
    concurrency = str(namespace.concurrency or "4").strip()
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
    if namespace.branch_prefix is None and prompt_all_defaults:
        branch_prefix = _prompt_required(
            "Branch prefix",
            example="work",
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
            values=_orchestrator_values(goal, plan_file, branch_prefix, concurrency),
            display=display,
        )
        prompted = True
    elif re.fullmatch(r"[1-9][0-9]*", concurrency) is None:
        return original

    if not branch_prefix:
        return original
    if not prompted:
        return original
    values = _orchestrator_values(goal, plan_file, branch_prefix, concurrency)
    display.render(values)
    if not namespace.yes and not click.confirm("Start orchestrator run?", default=True):
        raise click.Abort()

    return _normalized_orchestrator_start_args(
        namespace,
        goal=goal,
        plan_file=plan_file,
        branch_prefix=branch_prefix,
        concurrency=concurrency,
    )


def _parse_agent_start_partial(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False, usage=_AGENT_START_USAGE)
    parser.add_argument("--branch")
    parser.add_argument("--prompt")
    parser.add_argument("--attempts")
    parser.add_argument("--thinking")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--advisor", choices=("policy", "pi"), default="policy")
    parser.add_argument("--confirm-merge", action="store_true")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--parent-run-id", default="")
    parser.add_argument("--plan-item-id", default="")
    return parser.parse_known_args(argv)


def _parse_orchestrator_start_partial(
    argv: list[str],
) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False, usage=_ORCHESTRATOR_START_USAGE)
    parser.add_argument("--goal")
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--branch-prefix")
    parser.add_argument("--concurrency")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser.parse_known_args(argv)


def _normalized_agent_start_args(
    namespace: argparse.Namespace,
    attempts: str,
    thinking: str,
    branch: str,
    prompt: str,
) -> list[str]:
    args = ["--branch", branch, "--prompt", prompt, "--attempts", attempts]
    if thinking:
        args.extend(["--thinking", thinking])
    if namespace.non_interactive:
        args.append("--non-interactive")
    if namespace.yes:
        args.append("--yes")
    if namespace.advisor != "policy":
        args.extend(["--advisor", namespace.advisor])
    if namespace.confirm_merge:
        args.append("--confirm-merge")
    for option, value in (
        ("--run-id", namespace.run_id),
        ("--parent-run-id", namespace.parent_run_id),
        ("--plan-item-id", namespace.plan_item_id),
    ):
        if value:
            args.extend([option, str(value).strip()])
    return args


def _normalized_orchestrator_start_args(
    namespace: argparse.Namespace,
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
    if namespace.non_interactive:
        args.append("--non-interactive")
    if namespace.yes:
        args.append("--yes")
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
        self._can_update = click.get_text_stream("stdout").isatty()
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
        click.echo(rendered, nl=False, color=True)
        if error and not self._can_update:
            click.echo(error, err=True)
        self._line_count = len(rendered.splitlines())

    def record_prompt_result(
        self,
        message: str,
        value: str,
        *,
        default: str | None = None,
        show_default: bool = False,
    ) -> None:
        """Account for the click prompt line before the next in-place render."""

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
        click.echo(f"\033[{self._line_count}A", nl=False, color=True)
        for index in range(self._line_count):
            click.echo("\033[2K\r", nl=False, color=True)
            if index < self._line_count - 1:
                click.echo("\033[1B", nl=False, color=True)
        click.echo(f"\033[{self._line_count - 1}A", nl=False, color=True)


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


def _prompt_attempts(
    default: str, values: dict[str, str], *, display: _InteractiveDisplay
) -> str:
    error: str | None = None
    while True:
        display.render(values, error=error)
        message = "Attempts (positive integer)"
        value = click.prompt(message, default=default, show_default=True)
        display.record_prompt_result(message, value, default=default, show_default=True)
        if re.fullmatch(r"[1-9][0-9]*", value):
            return value
        error = "Enter a positive integer, e.g. 3."


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
        value = click.prompt(message, default=default, show_default=True)
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
        value = click.prompt(message, default=default, show_default=bool(default))
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
    value = click.prompt(message, default="", show_default=False)
    display.record_prompt_result(message, value)
    return value.strip()


def _prompt_positive_int(
    label: str,
    *,
    default: str,
    values: dict[str, str],
    display: _InteractiveDisplay,
) -> str:
    error: str | None = None
    while True:
        display.render(values, error=error)
        message = f"{label} (positive integer)"
        value = click.prompt(message, default=default, show_default=True)
        display.record_prompt_result(message, value, default=default, show_default=True)
        if re.fullmatch(r"[1-9][0-9]*", value):
            return value
        error = "Enter a positive integer, e.g. 4."
