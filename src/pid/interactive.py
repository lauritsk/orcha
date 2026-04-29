"""Interactive argument collection for pid."""

from __future__ import annotations

import re
import shutil

import click
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pid.config import PIDConfig


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


def _values(attempts: str, thinking: str, branch: str, prompt: str) -> dict[str, str]:
    return {
        "attempts": attempts or "(default 3)",
        "thinking": thinking or "(default)",
        "branch": branch or "(unset)",
        "prompt": prompt or "(unset)",
    }


class _InteractiveDisplay:
    """Render prompt summary in-place when stdout is interactive."""

    def __init__(self) -> None:
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
                _prompt_summary(values, error=error, width=self._console.width)
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
    values: dict[str, str], *, error: str | None, width: int | None = None
) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold magenta", no_wrap=True)
    table.add_column()
    table.add_row("attempts", _summary_value(values["attempts"]))
    table.add_row("thinking", _summary_value(values["thinking"]))
    table.add_row("branch", _summary_value(values["branch"]))
    table.add_row("prompt", _summary_value(values["prompt"]))

    renderable = table
    if error:
        renderable = Group(table, Text(f"✗ {error}", style="bold red"))

    return Panel(
        renderable,
        title=Text("pid prompt", style="bold magenta"),
        subtitle=Text("enter to accept defaults", style="dim"),
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
    label: str, *, example: str, values: dict[str, str], display: _InteractiveDisplay
) -> str:
    error: str | None = None
    while True:
        display.render(values, error=error)
        message = f"{label} (example: {example})"
        value = click.prompt(message, default="", show_default=False)
        display.record_prompt_result(message, value)
        if value.strip():
            return value.strip()
        error = f"{label} is required. Example: {example}"
