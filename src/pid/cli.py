"""Command line interface for pid."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from pid.config import load_config
from pid.errors import PIDAbort
from pid.interactive import resolve_interactive_args
from pid.workflow import run_pid

APP_CONTEXT = {
    "allow_extra_args": True,
    "help_option_names": ["-h", "--help"],
    "ignore_unknown_options": True,
}

app = typer.Typer(add_completion=False, context_settings=APP_CONTEXT)


@app.command(context_settings=APP_CONTEXT)
def main(
    ctx: typer.Context,
    args: Annotated[
        list[str] | None,
        typer.Argument(
            help="Optional session mode, attempts, thinking level, branch, then prompt words.",
            metavar="[session] [ATTEMPTS] [THINKING] BRANCH [PROMPT...]",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to config.toml (default: XDG/macOS config location).",
            exists=False,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Run pid."""
    raw_args = [*(args or []), *ctx.args]
    try:
        loaded_config = load_config(config)
        resolved_args = (
            resolve_interactive_args(raw_args, loaded_config)
            if sys.stdin.isatty()
            else raw_args
        )
    except PIDAbort as error:
        raise typer.Exit(error.code) from error
    raise typer.Exit(run_pid(resolved_args, config=loaded_config))
