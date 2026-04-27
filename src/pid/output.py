"""Console output helpers for pid."""

from __future__ import annotations

import sys
from typing import TextIO

from pid.session_logging import SessionLogger

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pid.models import CommandResult, CommitMessage

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
    writer = stream.write
    writer(value)
    if not value.endswith("\n"):
        writer("\n")


def write_command_output(result: CommandResult) -> None:
    """Write both streams from a command result to their matching stdio streams."""

    write_collected(result.stdout, stream=sys.stdout)
    write_collected(result.stderr, stream=sys.stderr)


def echo_out(message: str) -> None:
    """Print a normal status message."""

    if _CURRENT_LOGGER is not None:
        _CURRENT_LOGGER.output("stdout", message)
    print(message)


def echo_err(message: str) -> None:
    """Print an error status message."""

    if _CURRENT_LOGGER is not None:
        _CURRENT_LOGGER.output("stderr", message)
    print(message, file=sys.stderr)


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
