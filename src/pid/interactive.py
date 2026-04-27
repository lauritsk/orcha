"""Interactive argument collection for pid."""

from __future__ import annotations

import re

import click

from pid.config import PIDConfig


def resolve_interactive_args(argv: list[str], config: PIDConfig) -> list[str]:
    """Prompt for missing pid arguments, then return fish-compatible argv."""

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

    def render(self, values: dict[str, str], *, error: str | None = None) -> None:
        self._clear_previous()
        lines = _value_lines(values)
        if error and self._can_update:
            lines.append(error)
        click.echo("\n".join(lines))
        if error and not self._can_update:
            click.echo(error, err=True)
        self._line_count = (
            len(lines) + 1
        )  # include next click.prompt/click.confirm line

    def _clear_previous(self) -> None:
        if not self._can_update or self._line_count <= 0:
            return
        click.echo(f"\033[{self._line_count}A", nl=False, color=True)
        for index in range(self._line_count):
            click.echo("\033[2K\r", nl=False, color=True)
            if index < self._line_count - 1:
                click.echo("\033[1B", nl=False, color=True)
        click.echo(f"\033[{self._line_count - 1}A", nl=False, color=True)


def _value_lines(values: dict[str, str]) -> list[str]:
    lines = ["pid values:"]
    lines.extend(f"  {key}: {value}" for key, value in values.items())
    lines.append("")
    return lines


def _prompt_attempts(
    default: str, values: dict[str, str], *, display: _InteractiveDisplay
) -> str:
    error: str | None = None
    while True:
        display.render(values, error=error)
        value = click.prompt(
            "Attempts (positive integer)", default=default, show_default=True
        )
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
        value = click.prompt(
            f"Thinking level ({levels})", default=default, show_default=True
        )
        if value in thinking_levels:
            return value
        error = f"Choose one of: {levels}."


def _prompt_required(
    label: str, *, example: str, values: dict[str, str], display: _InteractiveDisplay
) -> str:
    error: str | None = None
    while True:
        display.render(values, error=error)
        value = click.prompt(
            f"{label} (example: {example})", default="", show_default=False
        )
        if value.strip():
            return value.strip()
        error = f"{label} is required. Example: {example}"
