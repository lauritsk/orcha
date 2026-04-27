"""Command line interface for Orcha."""

from __future__ import annotations

from typing import Annotated

import typer

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
) -> None:
    """Run Orcha."""
    raw_args = [*(args or []), *ctx.args]
    raise typer.Exit(run_orcha(raw_args))
