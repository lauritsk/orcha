"""Command line interface for pid."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Annotated, cast

import typer

from pid import __version__
from pid.commands import CommandRunner
from pid.config import PIDConfig, init_config, load_config
from pid.diagnostics import active_sessions_table, config_to_toml, print_config_metadata
from pid.errors import PIDAbort
from pid.extensions import (
    ExtensionCommandContext,
    ExtensionError,
    ExtensionRegistry,
    load_enabled_extensions,
)
from pid.interactive import resolve_interactive_args
from pid.models import OutputMode
from pid.orchestrator import AgentStartOptions, OrchestratorAgent, OrchestratorDisabled
from pid.output import echo_err, echo_out
from pid.run_state import RunStore
from pid.workflow import run_pid

APP_CONTEXT = {
    "allow_extra_args": True,
    "help_option_names": ["-h", "--help"],
    "ignore_unknown_options": True,
}

CONFIG_USAGE = "usage: pid config show|default|path"
SESSIONS_USAGE = "usage: pid sessions [--all|-a]"
VERSION_USAGE = "usage: pid version"
X_USAGE = "usage: pid x <extension-command> [ARGS...]"
AGENT_USAGE = "usage: pid agent start|resume|status|runs"

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
    output: Annotated[
        OutputMode,
        typer.Option(
            "--output",
            help="Console detail: normal, agent, or all.",
            case_sensitive=False,
        ),
    ] = OutputMode.NORMAL,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-v",
            help="Show pid version and exit.",
        ),
    ] = False,
) -> None:
    """Run pid. Use `pid init` to create the default config.

    Info commands:

    - pid sessions [--all|-a]
    - pid config show|default|path
    - pid x <extension-command> [ARGS...]
    - pid version
    """
    raw_args = [*(args or []), *ctx.args]
    if version or raw_args == ["version"]:
        echo_out(f"pid {__version__}")
        raise typer.Exit(0)

    command_exit = _run_info_command(raw_args, config_path=config)
    if command_exit is not None:
        raise typer.Exit(command_exit)

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

    if raw_args and raw_args[0] == "agent":
        raise typer.Exit(
            _run_agent_command(raw_args[1:], config=loaded_config, output_mode=output)
        )
    if raw_args and raw_args[0] == "run":
        raw_args = raw_args[1:]

    try:
        resolved_args = (
            resolve_interactive_args(raw_args, loaded_config)
            if sys.stdin.isatty()
            else raw_args
        )
    except PIDAbort as error:
        raise typer.Exit(error.code) from error

    raise typer.Exit(run_pid(resolved_args, config=loaded_config, output_mode=output))


def _run_info_command(raw_args: list[str], *, config_path: Path | None) -> int | None:
    if not raw_args:
        return None

    if raw_args == ["config", "path"]:
        typer.echo(print_config_metadata(config_path=config_path), nl=False)
        return 0
    if raw_args == ["config", "default"]:
        typer.echo(config_to_toml(PIDConfig()), nl=False)
        return 0
    if raw_args == ["config", "show"]:
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

    if raw_args[0] == "x":
        return _run_extension_command(raw_args[1:], config_path=config_path)

    if raw_args == ["sessions"]:
        typer.echo(active_sessions_table(), nl=False)
        return 0
    if raw_args in (["sessions", "--all"], ["sessions", "-a"]):
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


def _run_agent_command(
    raw_args: list[str], *, config: PIDConfig, output_mode: OutputMode
) -> int:
    if not raw_args or raw_args[0] in {"--help", "-h"}:
        echo_out(AGENT_USAGE)
        return 0
    if not config.orchestrator.enabled:
        echo_err("pid: orchestrator agent is disabled in config")
        return 2

    try:
        store = RunStore.discover(configured_dir=config.orchestrator.store_dir)
    except RuntimeError as error:
        echo_err(f"pid: {error}")
        return 1

    command = raw_args[0]
    if command == "runs":
        if len(raw_args) != 1:
            echo_err("pid: agent runs does not accept arguments")
            echo_err(AGENT_USAGE)
            return 2
        typer.echo(_runs_table(store.list_runs()), nl=False)
        return 0
    if command == "status":
        if len(raw_args) != 2:
            echo_err("usage: pid agent status RUN_ID")
            return 2
        try:
            typer.echo(_run_status(store.read_state(raw_args[1])), nl=False)
        except (OSError, ValueError) as error:
            echo_err(f"pid: could not read run {raw_args[1]}: {error}")
            return 1
        return 0
    if command == "resume":
        if len(raw_args) != 2:
            echo_err("usage: pid agent resume RUN_ID")
            return 2
        try:
            state = store.read_state(raw_args[1])
        except (OSError, ValueError) as error:
            echo_err(f"pid: could not read run {raw_args[1]}: {error}")
            return 1
        typer.echo(_run_status(state), nl=False)
        echo_err("pid: agent resume cannot reconstruct workflow context yet")
        return 2
    if command != "start":
        echo_err(f"pid: unknown agent command: {command}")
        echo_err(AGENT_USAGE)
        return 2

    try:
        options = _parse_agent_start(raw_args[1:])
    except ValueError as error:
        echo_err(f"pid: {error}")
        echo_err(
            "usage: pid agent start --branch BRANCH --prompt TEXT [--attempts N] [--thinking LEVEL]"
        )
        return 2

    try:
        agent = OrchestratorAgent(
            config=config,
            store=store,
            output_mode=output_mode,
        )
        result = agent.start(options)
    except OrchestratorDisabled as error:  # pragma: no cover - prechecked above
        echo_err(f"pid: {error}")
        return 2
    except ValueError as error:
        echo_err(f"pid: {error}")
        return 2

    echo_out(f"pid: agent run {result.run_id}: {result.state['status']}")
    if result.state.get("pr_url"):
        echo_out(f"pid: PR: {result.state['pr_url']}")
    return result.exit_code


def _parse_agent_start(raw_args: list[str]) -> AgentStartOptions:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--thinking", default="")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--advisor", choices=("policy", "pi"), default="policy")
    parser.add_argument("--confirm-merge", action="store_true")
    try:
        namespace, extras = parser.parse_known_args(raw_args)
    except SystemExit as error:
        raise ValueError("invalid agent start options") from error
    if extras:
        raise ValueError(f"unexpected agent start arguments: {' '.join(extras)}")
    if namespace.attempts < 1:
        raise ValueError("--attempts must be a positive integer")
    if not namespace.branch:
        raise ValueError("--branch must be non-empty")
    if not namespace.prompt:
        raise ValueError("--prompt must be non-empty")
    return AgentStartOptions(
        branch=namespace.branch,
        prompt=namespace.prompt,
        attempts=namespace.attempts,
        thinking=namespace.thinking,
        non_interactive=namespace.non_interactive,
        yes=namespace.yes,
        advisor=namespace.advisor,
        confirm_merge=namespace.confirm_merge,
    )


def _runs_table(runs: list[dict[str, object]]) -> str:
    if not runs:
        return "no pid agent runs\n"
    headers = ["run_id", "status", "branch", "step", "pr_url"]
    rows = [
        [
            str(run.get("run_id", "")),
            str(run.get("status", "")),
            str(run.get("branch", "")),
            str(run.get("current_step", "")),
            str(run.get("pr_url", "")),
        ]
        for run in runs
    ]
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    ]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )
    return "\n".join(lines) + "\n"


def _run_status(state: dict[str, object]) -> str:
    lines = [
        f"run_id: {state.get('run_id', '')}",
        f"status: {state.get('status', '')}",
        f"branch: {state.get('branch', '')}",
        f"current_step: {state.get('current_step', '')}",
        f"pr_url: {state.get('pr_url', '')}",
    ]
    failure = state.get("last_failure")
    if isinstance(failure, dict):
        failure_data = cast("dict[str, object]", failure)
        lines.append(
            f"failure: {failure_data.get('kind', '')} at {failure_data.get('step', '')}"
        )
        lines.append(f"message: {failure_data.get('message', '')}")
    action = state.get("pending_recovery_action")
    if isinstance(action, dict):
        action_data = cast("dict[str, object]", action)
        lines.append(f"pending_recovery_action: {action_data.get('kind', '')}")
    return "\n".join(lines) + "\n"


def _run_extension_command(raw_args: list[str], *, config_path: Path | None) -> int:
    if not raw_args:
        echo_err(X_USAGE)
        return 2

    try:
        loaded_config = load_config(config_path)
    except PIDAbort as error:
        return error.code

    registry = ExtensionRegistry()
    repo_root = _optional_repo_root()
    try:
        load_enabled_extensions(
            loaded_config.extensions,
            registry,
            repo_root=repo_root,
            include_entry_points=True,
            include_local=True,
            fail_missing=True,
        )
    except ExtensionError as error:
        echo_err(f"pid: {error}")
        return 2

    if raw_args in (["extensions", "list"], ["extensions"]):
        typer.echo(_extensions_table(registry), nl=False)
        return 0

    command_name = raw_args[0]
    callback = registry.cli_commands.get(command_name)
    if callback is None:
        echo_err(f"pid: unknown extension command: {command_name}")
        echo_err(X_USAGE)
        return 2

    context = ExtensionCommandContext(
        argv=raw_args[1:],
        config=loaded_config,
        registry=registry,
        repo_root=repo_root,
    )
    try:
        return callback(context) or 0
    except PIDAbort as error:
        return error.code
    except ExtensionError as error:
        echo_err(f"pid: {error}")
        return 2
    except Exception as error:  # noqa: BLE001 - extension command boundary
        echo_err(
            f"pid: extension command {command_name} failed: "
            f"{type(error).__name__}: {error}"
        )
        return 1


def _extensions_table(registry: ExtensionRegistry) -> str:
    if not registry.extension_infos:
        return "no enabled pid extensions\n"
    lines = ["name  api  source"]
    lines.append("----  ---  ------")
    for info in registry.extension_infos:
        lines.append(f"{info.name}  {info.api_version}    {info.source}")
    return "\n".join(lines) + "\n"


def _optional_repo_root() -> Path | None:
    result = CommandRunner().run(["git", "rev-parse", "--show-toplevel"])
    output = result.stdout.strip()
    if result.returncode == 0 and output:
        return Path(output)
    return None
