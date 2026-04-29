"""Console output helpers for pid."""

from __future__ import annotations

import sys
from typing import TextIO

import typer

from pid.session_logging import SessionLogger

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from pid.models import CommandResult, CommitMessage, OutputMode, ParsedArgs

OUT_CONSOLE = Console(highlight=False)
_CURRENT_LOGGER: SessionLogger | None = None


def set_session_logger(logger: SessionLogger | None) -> None:
    """Set the active session logger used by console helpers."""

    global _CURRENT_LOGGER
    _CURRENT_LOGGER = logger


def get_session_logger() -> SessionLogger | None:
    """Return the active session logger, if any."""

    return _CURRENT_LOGGER


def write_collected(value: str, *, stream: TextIO) -> None:
    """Write captured command output, preserving final newline behavior."""

    if not value:
        return
    typer.echo(value, file=stream, nl=False)
    if not value.endswith("\n"):
        typer.echo(file=stream)


def write_command_output(result: CommandResult) -> None:
    """Write both streams from a command result to their matching stdio streams."""

    write_collected(result.stdout, stream=sys.stdout)
    write_collected(result.stderr, stream=sys.stderr)


def echo_out(message: str) -> None:
    """Print a normal status message."""

    if _CURRENT_LOGGER is not None:
        _CURRENT_LOGGER.output("stdout", message)
    typer.echo(message)


def echo_err(message: str) -> None:
    """Print an error status message."""

    if _CURRENT_LOGGER is not None:
        _CURRENT_LOGGER.output("stderr", message)
    typer.echo(message, err=True)


def print_run_summary(
    parsed: ParsedArgs,
    *,
    agent_label: str,
    forge_label: str,
    output_mode: OutputMode,
) -> None:
    """Print a compact run summary before the workflow starts."""

    if _CURRENT_LOGGER is not None:
        _CURRENT_LOGGER.event(
            "run summary: "
            f"branch={parsed.branch} attempts={parsed.max_attempts} "
            f"thinking={parsed.thinking_level} mode={output_mode.value}"
        )
    flow = "interactive session" if parsed.interactive else "non-interactive agent"
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("branch", parsed.branch)
    table.add_row("attempts", str(parsed.max_attempts))
    table.add_row("thinking", parsed.thinking_level)
    table.add_row("flow", flow)
    table.add_row("agent", agent_label)
    table.add_row("forge", forge_label)
    table.add_row("output", output_mode.value)
    OUT_CONSOLE.print(
        Panel.fit(
            table,
            title=Text("pid run", style="bold magenta"),
            border_style="magenta",
        )
    )


def print_phase(title: str, detail: str = "") -> None:
    """Print a visual phase divider for long-running workflow sections."""

    event = f"phase: {title}"
    if detail:
        event = f"{event} - {detail}"
    if _CURRENT_LOGGER is not None:
        _CURRENT_LOGGER.event(event)
    label = Text(f" {title} ", style="bold cyan")
    if detail:
        label.append(f" {detail}", style="dim")
    OUT_CONSOLE.print(Rule(label, style="cyan"))


def print_attempt_header(attempt: int, max_attempts: int) -> None:
    """Print a visual PR attempt divider."""

    print_phase(f"PR attempt {attempt}/{max_attempts}")


def print_commit_message(message: CommitMessage) -> None:
    """Print the commit message preview panel."""

    if _CURRENT_LOGGER is not None:
        _CURRENT_LOGGER.event(f"commit message preview: {message.title}")
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("commit", message.title)
    table.add_row("body", message.body)
    OUT_CONSOLE.print(
        Panel.fit(
            table,
            title=Text("pid commit message", style="bold cyan"),
            border_style="cyan",
        )
    )


def print_merge_success(
    pr_title: str, pr_url: str, forge_label: str = "github"
) -> None:
    """Print the successful merge summary panel."""

    if _CURRENT_LOGGER is not None:
        _CURRENT_LOGGER.event(f"{forge_label} squash merged: {pr_title} {pr_url}")
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("commit", pr_title)
    table.add_row("PR", pr_url)
    OUT_CONSOLE.print(
        Panel.fit(
            table,
            title=Text(f"pid {forge_label} squash merged", style="bold green"),
            border_style="green",
        )
    )
