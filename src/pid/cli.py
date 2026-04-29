"""Command line interface for pid."""

from __future__ import annotations

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
from pid.interactive import (
    resolve_agent_start_args,
    resolve_interactive_args,
    resolve_orchestrator_start_args,
)
from pid.models import OutputMode
from pid.orchestrator import (
    AgentStartOptions,
    OrchestratorAgent,
    OrchestratorFollowUpOptions,
    OrchestratorStartOptions,
    OrchestratorSupervisor,
)
from pid.output import echo_err, echo_out
from pid.run_state import RunStore
from pid.typer_parsing import TYPER_PARSER_CONTEXT, parse_typer_args
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
AGENT_USAGE = (
    "usage: pid agent [start] --branch BRANCH --prompt TEXT [--attempts N] "
    "[--thinking LEVEL]\n"
    "       pid agent follow-up|status|runs ..."
)
ORCHESTRATOR_USAGE = (
    "usage: pid orchestrator [start] --goal TEXT [--plan-file plan.json] "
    "[--branch-prefix PREFIX] [--concurrency N]\n"
    "       pid orchestrator follow-up|status|runs ..."
)

app = typer.Typer(add_completion=False, context_settings=APP_CONTEXT)

_AGENT_START_PARSER = typer.Typer(
    add_completion=False, context_settings=TYPER_PARSER_CONTEXT
)
_AGENT_FOLLOW_UP_PARSER = typer.Typer(
    add_completion=False, context_settings=TYPER_PARSER_CONTEXT
)
_ORCHESTRATOR_START_PARSER = typer.Typer(
    add_completion=False, context_settings=TYPER_PARSER_CONTEXT
)
_ORCHESTRATOR_FOLLOW_UP_PARSER = typer.Typer(
    add_completion=False, context_settings=TYPER_PARSER_CONTEXT
)


@_AGENT_START_PARSER.command(context_settings=TYPER_PARSER_CONTEXT)
def _agent_start_options(
    ctx: typer.Context,
    branch: Annotated[str, typer.Option("--branch", help="Branch to create/run.")],
    prompt: Annotated[str, typer.Option("--prompt", help="Agent prompt text.")],
    attempts: Annotated[
        int, typer.Option("--attempts", help="Maximum PR loop attempts.")
    ] = 3,
    thinking: Annotated[
        str, typer.Option("--thinking", help="Agent thinking level override.")
    ] = "",
    non_interactive: Annotated[
        bool, typer.Option("--non-interactive", help="Disable intake prompts.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Accept prompted values.")
    ] = False,
    run_id: Annotated[
        str, typer.Option("--run-id", help="Reuse an existing run id.")
    ] = "",
    parent_run_id: Annotated[
        str, typer.Option("--parent-run-id", help="Parent orchestrator run id.")
    ] = "",
    plan_item_id: Annotated[
        str, typer.Option("--plan-item-id", help="Parent plan item id.")
    ] = "",
) -> AgentStartOptions:
    """Parse `pid agent start` options with Typer."""

    del non_interactive, yes
    if ctx.args:
        raise ValueError(f"unexpected agent start arguments: {' '.join(ctx.args)}")
    branch = branch.strip()
    prompt = prompt.strip()
    thinking = thinking.strip()
    if attempts < 1:
        raise ValueError("--attempts must be a positive integer")
    if not branch:
        raise ValueError("--branch must be non-empty")
    if not prompt:
        raise ValueError("--prompt must be non-empty")
    return AgentStartOptions(
        branch=branch,
        prompt=prompt,
        attempts=attempts,
        thinking=thinking,
        run_id=run_id.strip(),
        parent_run_id=parent_run_id.strip(),
        plan_item_id=plan_item_id.strip(),
    )


@_AGENT_FOLLOW_UP_PARSER.command(context_settings=TYPER_PARSER_CONTEXT)
def _agent_follow_up_options(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run id to receive the follow-up.")],
    message: Annotated[
        str, typer.Option("--message", "-m", help="Follow-up message text.")
    ] = "",
    kind: Annotated[
        str, typer.Option("--type", help="Follow-up type/kind.")
    ] = "clarify",
    read_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read follow-up message from stdin.")
    ] = False,
) -> tuple[str, str, str]:
    """Parse `pid agent follow-up` options with Typer."""

    if ctx.args:
        raise ValueError(f"unexpected follow-up arguments: {' '.join(ctx.args)}")
    body = sys.stdin.read() if read_stdin else message
    return run_id.strip(), kind.strip(), body.strip()


@_ORCHESTRATOR_START_PARSER.command(context_settings=TYPER_PARSER_CONTEXT)
def _orchestrator_start_options(
    ctx: typer.Context,
    goal: Annotated[str, typer.Option("--goal", help="Overall orchestration goal.")],
    plan_file: Annotated[
        Path | None,
        typer.Option("--plan-file", help="Structured plan JSON to launch."),
    ] = None,
    branch_prefix: Annotated[
        str, typer.Option("--branch-prefix", help="Prefix for child branches.")
    ] = "work",
    concurrency: Annotated[
        int, typer.Option("--concurrency", help="Maximum concurrent child runs.")
    ] = 4,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Plan child runs without launching them.")
    ] = False,
    non_interactive: Annotated[
        bool, typer.Option("--non-interactive", help="Disable intake prompts.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Accept prompted values.")
    ] = False,
) -> OrchestratorStartOptions:
    """Parse `pid orchestrator start` options with Typer."""

    del yes
    if ctx.args:
        raise ValueError(f"unexpected orchestrator arguments: {' '.join(ctx.args)}")
    goal = goal.strip()
    branch_prefix = branch_prefix.strip().strip("/")
    if not goal:
        raise ValueError("--goal must be non-empty")
    if not branch_prefix:
        raise ValueError("--branch-prefix must be non-empty")
    if concurrency < 1:
        raise ValueError("--concurrency must be a positive integer")
    return OrchestratorStartOptions(
        goal=goal,
        plan_file=plan_file,
        branch_prefix=branch_prefix,
        concurrency=concurrency,
        dry_run=dry_run,
        non_interactive=non_interactive,
    )


@_ORCHESTRATOR_FOLLOW_UP_PARSER.command(context_settings=TYPER_PARSER_CONTEXT)
def _orchestrator_follow_up_options(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Orchestrator run id.")],
    message: Annotated[
        str, typer.Option("--message", "-m", help="Follow-up message text.")
    ] = "",
    kind: Annotated[
        str, typer.Option("--type", help="Follow-up type/kind.")
    ] = "clarify",
    target: Annotated[
        str, typer.Option("--target", help="Plan item id or child run id target.")
    ] = "",
    all_children: Annotated[
        bool, typer.Option("--all", help="Route follow-up to all child runs.")
    ] = False,
    read_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read follow-up message from stdin.")
    ] = False,
) -> OrchestratorFollowUpOptions:
    """Parse `pid orchestrator follow-up` options with Typer."""

    if ctx.args:
        raise ValueError(
            f"unexpected orchestrator follow-up arguments: {' '.join(ctx.args)}"
        )
    body = sys.stdin.read() if read_stdin else message
    return OrchestratorFollowUpOptions(
        run_id=run_id.strip(),
        message=body.strip(),
        kind=kind.strip(),
        target=target.strip(),
        all_children=all_children,
    )


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

    - pid agent [start]|follow-up|status|runs
    - pid orchestrator [start]|follow-up|status|runs
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
    if raw_args and raw_args[0] == "orchestrator":
        raise typer.Exit(
            _run_orchestrator_command(
                raw_args[1:],
                config=loaded_config,
                output_mode=output,
                config_path=config,
            )
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
    if not raw_args:
        if sys.stdin.isatty():
            raw_args = ["start"]
        else:
            echo_out(AGENT_USAGE)
            return 0
    if raw_args[0] in {"--help", "-h"}:
        echo_out(AGENT_USAGE)
        return 0
    if raw_args[0].startswith("-"):
        raw_args = ["start", *raw_args]
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
    if command == "follow-up":
        try:
            run_id, kind, message = _parse_agent_follow_up(raw_args[1:])
            record = store.append_followup(run_id, message=message, kind=kind)
        except (OSError, ValueError) as error:
            echo_err(f"pid: could not queue follow-up: {error}")
            return 2
        echo_out(f"pid: queued follow-up {record['id']} for run {run_id}")
        return 0
    if command != "start":
        echo_err(f"pid: unknown agent command: {command}")
        echo_err(AGENT_USAGE)
        return 2

    start_args = raw_args[1:]
    if start_args and start_args[0] in {"--help", "-h"}:
        echo_out(AGENT_USAGE)
        return 0
    if sys.stdin.isatty():
        start_args = resolve_agent_start_args(start_args, config)
    try:
        options = _parse_agent_start(start_args)
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
    except ValueError as error:
        echo_err(f"pid: {error}")
        return 2

    echo_out(f"pid: agent run {result.run_id}: {result.state['status']}")
    if result.state.get("pr_url"):
        echo_out(f"pid: PR: {result.state['pr_url']}")
    return result.exit_code


def _parse_agent_start(raw_args: list[str]) -> AgentStartOptions:
    options: AgentStartOptions = parse_typer_args(
        _AGENT_START_PARSER,
        raw_args,
        prog_name="pid agent start",
        error_message="invalid agent start options",
    )
    return options


def _parse_agent_follow_up(raw_args: list[str]) -> tuple[str, str, str]:
    follow_up: tuple[str, str, str] = parse_typer_args(
        _AGENT_FOLLOW_UP_PARSER,
        raw_args,
        prog_name="pid agent follow-up",
        error_message="invalid follow-up options",
    )
    return follow_up


def _run_orchestrator_command(
    raw_args: list[str],
    *,
    config: PIDConfig,
    output_mode: OutputMode,
    config_path: Path | None,
) -> int:
    if not raw_args:
        if sys.stdin.isatty():
            raw_args = ["start"]
        else:
            echo_out(ORCHESTRATOR_USAGE)
            return 0
    if raw_args[0] in {"--help", "-h"}:
        echo_out(ORCHESTRATOR_USAGE)
        return 0
    if raw_args[0].startswith("-"):
        raw_args = ["start", *raw_args]
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
            echo_err("pid: orchestrator runs does not accept arguments")
            echo_err(ORCHESTRATOR_USAGE)
            return 2
        runs = [
            run for run in store.list_runs() if run.get("run_type") == "orchestrator"
        ]
        typer.echo(_runs_table(runs), nl=False)
        return 0
    if command == "status":
        if len(raw_args) != 2:
            echo_err("usage: pid orchestrator status RUN_ID")
            return 2
        try:
            state = store.read_state(raw_args[1])
        except (OSError, ValueError) as error:
            echo_err(f"pid: could not read run {raw_args[1]}: {error}")
            return 1
        typer.echo(_orchestrator_status(state, store), nl=False)
        return 0
    if command == "follow-up":
        try:
            options = _parse_orchestrator_follow_up(raw_args[1:])
            supervisor = OrchestratorSupervisor(
                config=config, store=store, output_mode=output_mode
            )
            result = supervisor.follow_up(options)
        except (OSError, ValueError) as error:
            echo_err(f"pid: could not route orchestrator follow-up: {error}")
            return 2
        routed_to = result["routed_to"]
        if routed_to:
            echo_out(
                f"pid: routed follow-up {result['record']['id']} to "
                f"{len(routed_to)} child run(s)"
            )
        else:
            echo_out(f"pid: recorded orchestrator follow-up {result['record']['id']}")
        return 0
    if command != "start":
        echo_err(f"pid: unknown orchestrator command: {command}")
        echo_err(ORCHESTRATOR_USAGE)
        return 2

    try:
        start_args = raw_args[1:]
        if start_args and start_args[0] in {"--help", "-h"}:
            echo_out(ORCHESTRATOR_USAGE)
            return 0
        if sys.stdin.isatty():
            start_args = resolve_orchestrator_start_args(start_args, config)
        options = _parse_orchestrator_start(start_args, config_path=config_path)
        supervisor = OrchestratorSupervisor(
            config=config, store=store, output_mode=output_mode
        )
        result = supervisor.start(options)
    except (OSError, ValueError) as error:
        echo_err(f"pid: {error}")
        echo_err(
            "usage: pid orchestrator start --goal TEXT "
            "[--plan-file plan.json] [--branch-prefix PREFIX] [--concurrency N]"
        )
        return 2
    echo_out(f"pid: orchestrator run {result.run_id}: {result.state['status']}")
    if result.state.get("intake_questions") and not result.state.get("approved_plan"):
        echo_out("pid: answer these intake questions before child launch:")
        for index, question in enumerate(result.state["intake_questions"], start=1):
            echo_out(f"{index}. {question}")
    children = result.state.get("children")
    if isinstance(children, list) and children:
        launched = sum(
            1
            for child in children
            if isinstance(child, dict) and child.get("status") == "launched"
        )
        echo_out(f"pid: child runs planned={len(children)} launched={launched}")
    return result.exit_code


def _parse_orchestrator_start(
    raw_args: list[str], *, config_path: Path | None
) -> OrchestratorStartOptions:
    options: OrchestratorStartOptions = parse_typer_args(
        _ORCHESTRATOR_START_PARSER,
        raw_args,
        prog_name="pid orchestrator start",
        error_message="invalid orchestrator start options",
    )
    return OrchestratorStartOptions(
        goal=options.goal,
        plan_file=options.plan_file,
        branch_prefix=options.branch_prefix,
        concurrency=options.concurrency,
        dry_run=options.dry_run,
        non_interactive=options.non_interactive,
        config_path=config_path,
    )


def _parse_orchestrator_follow_up(raw_args: list[str]) -> OrchestratorFollowUpOptions:
    options: OrchestratorFollowUpOptions = parse_typer_args(
        _ORCHESTRATOR_FOLLOW_UP_PARSER,
        raw_args,
        prog_name="pid orchestrator follow-up",
        error_message="invalid orchestrator follow-up options",
    )
    return options


def _orchestrator_status(state: dict[str, object], store: RunStore) -> str:
    lines = [
        f"run_id: {state.get('run_id', '')}",
        f"status: {state.get('status', '')}",
        f"goal: {state.get('goal', '')}",
    ]
    questions = state.get("intake_questions")
    if isinstance(questions, list) and questions and not state.get("approved_plan"):
        lines.append("intake_questions:")
        lines.extend(f"- {question}" for question in questions)
    children = state.get("children")
    if isinstance(children, list) and children:
        lines.append("children:")
        for child in children:
            if not isinstance(child, dict):
                continue
            child_data = cast("dict[str, object]", child)
            child_run_id = str(child_data.get("child_run_id", ""))
            child_state = _safe_child_state(store, child_run_id)
            status = child_state.get("status") or child_data.get("status", "")
            lines.append(
                f"- {child_data.get('item_id', '')} {status} "
                f"{child_data.get('branch', '')} {child_run_id}"
            )
    return "\n".join(lines) + "\n"


def _safe_child_state(store: RunStore, run_id: str) -> dict[str, object]:
    try:
        return store.read_state(run_id)
    except OSError, ValueError:
        return {}


def _runs_table(runs: list[dict[str, object]]) -> str:
    if not runs:
        return "no pid agent runs\n"
    headers = ["run_id", "type", "status", "branch", "step", "pr_url"]
    rows = [
        [
            str(run.get("run_id", "")),
            str(run.get("run_type", "agent")),
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
        f"follow_ups: {state.get('applied_follow_up_count', 0)}/{state.get('follow_up_count', 0)}",
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
