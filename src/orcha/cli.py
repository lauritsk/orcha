"""Command line interface for Orcha."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from orcha.config import load_config
from orcha.errors import OrchaAbort
from orcha.workflow import run_orcha

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
    """Run Orcha."""
    raw_args = [*(args or []), *ctx.args]
    try:
        loaded_config = load_config(config)
    except OrchaAbort as error:
        raise typer.Exit(error.code) from error
    raise typer.Exit(run_orcha(raw_args, config=loaded_config))
