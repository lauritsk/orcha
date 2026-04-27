"""Console output helpers for Orcha."""

from __future__ import annotations

import sys
from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from orcha.models import CommandResult

OUT_CONSOLE = Console(highlight=False)


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

    print(message)


def echo_err(message: str) -> None:
    """Print an error status message."""

    print(message, file=sys.stderr)


def print_commit_message(title: str) -> None:
    """Print the commit message preview panel."""

    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("commit", title)
    OUT_CONSOLE.print(
        Panel.fit(
            table,
            title=Text("orcha commit message", style="bold cyan"),
            border_style="cyan",
        )
    )


def print_merge_success(pr_title: str, pr_url: str) -> None:
    """Print the successful merge summary panel."""

    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("commit", pr_title)
    table.add_row("PR", pr_url)
    OUT_CONSOLE.print(
        Panel.fit(
            table,
            title=Text("orcha github squash merged", style="bold green"),
            border_style="green",
        )
    )
