"""Helpers for Typer-backed internal argument parsers."""

from __future__ import annotations

from typing import TypeVar, cast

import click
import typer
from typer.main import get_command

T = TypeVar("T")

TYPER_PARSER_CONTEXT = {
    "allow_extra_args": True,
    "help_option_names": [],
    "ignore_unknown_options": True,
}


def parse_typer_args(
    parser: typer.Typer,
    argv: list[str],
    *,
    prog_name: str,
    error_message: str,
) -> T:
    """Parse argv through Typer and return the parser command result."""

    try:
        result = get_command(parser).main(
            args=argv,
            prog_name=prog_name,
            standalone_mode=False,
        )
    except click.ClickException as error:
        raise ValueError(error_message) from error
    return cast(T, result)
