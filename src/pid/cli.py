"""Command line interface for pid."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from pid import __version__
from pid.config import PIDConfig, init_config, load_config
from pid.diagnostics import active_sessions_table, config_to_toml, print_config_metadata
from pid.errors import PIDAbort
from pid.interactive import resolve_interactive_args
from pid.output import echo_err, echo_out
from pid.workflow import run_pid

APP_CONTEXT = {
    "allow_extra_args": True,
    "help_option_names": ["-h", "--help"],
    "ignore_unknown_options": True,
}

CONFIG_USAGE = "usage: pid config show|default|path"
SESSIONS_USAGE = "usage: pid sessions [--all|-a]"
VERSION_USAGE = "usage: pid version"

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
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-v",
            help="Show pid version and exit.",
        ),
    ] = False,
    print_current_config: Annotated[
        bool,
        typer.Option(
            "--print-config",
            help="Print loaded config as TOML and exit.",
        ),
    ] = False,
    print_default_config: Annotated[
        bool,
        typer.Option(
            "--print-default-config",
            help="Print built-in default config as TOML and exit.",
        ),
    ] = False,
) -> None:
    """Run pid. Use `pid init` to create the default config.

    Info commands:

    - pid sessions [--all|-a]
    - pid config show|default|path
    - pid version
    """
    raw_args = [*(args or []), *ctx.args]
    if version or raw_args == ["version"]:
        echo_out(f"pid {__version__}")
        raise typer.Exit(0)

    command_exit = _run_info_command(raw_args, config_path=config)
    if command_exit is not None:
        raise typer.Exit(command_exit)

    if print_default_config:
        typer.echo(config_to_toml(PIDConfig()), nl=False)
        raise typer.Exit(0)

    try:
        if raw_args and raw_args[0] == "init":
            if config is not None:
                echo_err(
                    "pid: init does not accept --config; "
                    "it always writes the default config path"
                )
                raise typer.Exit(2)
            if len(raw_args) > 1:
                echo_err("pid: init does not accept arguments")
                raise typer.Exit(2)
            init_config()
            raise typer.Exit(0)
        loaded_config = load_config(config)
    except PIDAbort as error:
        raise typer.Exit(error.code) from error

    if print_current_config:
        typer.echo(config_to_toml(loaded_config), nl=False)
        raise typer.Exit(0)

    try:
        resolved_args = (
            resolve_interactive_args(raw_args, loaded_config)
            if sys.stdin.isatty()
            else raw_args
        )
    except PIDAbort as error:
        raise typer.Exit(error.code) from error

    raise typer.Exit(run_pid(resolved_args, config=loaded_config))


def _run_info_command(raw_args: list[str], *, config_path: Path | None) -> int | None:
    if not raw_args:
        return None

    if raw_args in (["config", "path"], ["config-path"]):
        typer.echo(print_config_metadata(config_path=config_path), nl=False)
        return 0
    if raw_args in (["config", "default"], ["default-config"]):
        typer.echo(config_to_toml(PIDConfig()), nl=False)
        return 0
    if raw_args in (["config", "show"], ["config", "current"]):
        try:
            loaded_config = load_config(config_path)
        except PIDAbort as error:
            return error.code
        typer.echo(config_to_toml(loaded_config), nl=False)
        return 0
    if raw_args[0] == "config":
        echo_err(f"pid: unknown config command: {' '.join(raw_args[1:]) or '(none)'}")
        echo_err(CONFIG_USAGE)
        return 2

    if raw_args in (["sessions"], ["sessions", "list"]):
        typer.echo(active_sessions_table(), nl=False)
        return 0
    if raw_args in (
        ["sessions", "--all"],
        ["sessions", "-a"],
        ["sessions", "list", "--all"],
        ["sessions", "list", "-a"],
    ):
        typer.echo(active_sessions_table(include_all=True), nl=False)
        return 0
    if raw_args[0] == "sessions":
        echo_err(f"pid: unknown sessions command: {' '.join(raw_args[1:]) or '(none)'}")
        echo_err(SESSIONS_USAGE)
        return 2

    if raw_args[0] == "version":
        echo_err(f"pid: unknown version command: {' '.join(raw_args[1:]) or '(none)'}")
        echo_err(VERSION_USAGE)
        return 2

    return None
