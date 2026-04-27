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
            attempts, _values(attempts, config.agent.default_thinking, "", "")
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
        )
        resolved.extend(prompt.split())
        prompted = True

    if prompted:
        _show_values(_values(attempts, thinking, branch, prompt))
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


def _show_values(values: dict[str, str]) -> None:
    click.echo("\npid values:")
    for key, value in values.items():
        click.echo(f"  {key}: {value}")
    click.echo("")


def _prompt_attempts(default: str, values: dict[str, str]) -> str:
    while True:
        _show_values(values)
        value = click.prompt(
            "Attempts (positive integer)", default=default, show_default=True
        )
        if re.fullmatch(r"[1-9][0-9]*", value):
            return value
        click.echo("Enter a positive integer, e.g. 3.", err=True)


def _prompt_thinking(
    default: str, thinking_levels: tuple[str, ...], values: dict[str, str]
) -> str:
    levels = ", ".join(thinking_levels)
    while True:
        _show_values(values)
        value = click.prompt(
            f"Thinking level ({levels})", default=default, show_default=True
        )
        if value in thinking_levels:
            return value
        click.echo(f"Choose one of: {levels}.", err=True)


def _prompt_required(label: str, *, example: str, values: dict[str, str]) -> str:
    while True:
        _show_values(values)
        value = click.prompt(
            f"{label} (example: {example})", default="", show_default=False
        )
        if value.strip():
            return value.strip()
        click.echo(f"{label} is required. Example: {example}", err=True)
