"""Configuration loading for Orcha."""

from __future__ import annotations

import os
import shlex
import string
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orcha.errors import abort
from orcha.output import echo_err

DEFAULT_THINKING_LEVELS = ("low", "medium", "high", "xhigh")
TEMPLATE_FIELDS = frozenset({"prompt", "thinking"})


@dataclass(frozen=True)
class AgentConfig:
    """Configured coding-agent launch templates."""

    command: tuple[str, ...] = ("pi",)
    non_interactive_args: tuple[str, ...] = (
        "--thinking",
        "{thinking}",
        "-p",
        "{prompt}",
    )
    interactive_args: tuple[str, ...] = ("--thinking", "{thinking}")
    default_thinking: str = "medium"
    review_thinking: str = "high"
    thinking_levels: tuple[str, ...] = DEFAULT_THINKING_LEVELS
    label: str = "agent"

    @property
    def executable(self) -> str:
        return self.command[0]

    def interactive_command(
        self, *, prompt: str | None = None, thinking: str = ""
    ) -> list[str]:
        rendered_args = [
            arg.format(prompt=prompt or "", thinking=thinking)
            for arg in self.interactive_args
        ]
        if prompt and not any("{prompt" in arg for arg in self.interactive_args):
            rendered_args.append(prompt)
        return [*self.command, *rendered_args]

    def non_interactive_command(self, *, prompt: str, thinking: str) -> list[str]:
        return [
            *self.command,
            *(
                arg.format(prompt=prompt, thinking=thinking)
                for arg in self.non_interactive_args
            ),
        ]


@dataclass(frozen=True)
class OrchaConfig:
    """Top-level Orcha config."""

    agent: AgentConfig = field(default_factory=AgentConfig)


def default_config_path() -> Path:
    """Return XDG/macOS-aware default config path."""

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        xdg_path = Path(xdg_config_home)
        if xdg_path.is_absolute():
            return xdg_path / "orcha" / "config.toml"

    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "orcha" / "config.toml"

    return home / ".config" / "orcha" / "config.toml"


def load_config(path: Path | None = None) -> OrchaConfig:
    """Load config.toml, returning defaults when the default file is absent."""

    explicit_path = path is not None
    config_path = path or default_config_path()
    if not config_path.exists():
        if explicit_path:
            echo_err(f"orcha: config file not found: {config_path}")
            abort(2)
        return OrchaConfig()

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        echo_err(f"orcha: could not read config at {config_path}: {error}")
        abort(2)
    except UnicodeDecodeError as error:
        echo_err(f"orcha: config is not valid UTF-8 at {config_path}: {error}")
        abort(2)
    except tomllib.TOMLDecodeError as error:
        echo_err(f"orcha: invalid config TOML at {config_path}: {error}")
        abort(2)

    return parse_config(data, config_path)


def parse_config(data: dict[str, Any], path: Path) -> OrchaConfig:
    unknown_top = set(data) - {"agent"}
    if unknown_top:
        fail_config(path, f"unknown top-level key: {sorted(unknown_top)[0]}")

    agent_data = data.get("agent", {})
    if not isinstance(agent_data, dict):
        fail_config(path, "[agent] must be a table")

    allowed_agent = {
        "command",
        "non_interactive_args",
        "interactive_args",
        "default_thinking",
        "review_thinking",
        "thinking_levels",
        "label",
    }
    unknown_agent = set(agent_data) - allowed_agent
    if unknown_agent:
        fail_config(path, f"unknown [agent] key: {sorted(unknown_agent)[0]}")

    default = AgentConfig()
    command = string_tuple(
        agent_data.get("command", default.command),
        path,
        "agent.command",
        split_string=True,
    )
    non_interactive_args = string_tuple(
        agent_data.get("non_interactive_args", default.non_interactive_args),
        path,
        "agent.non_interactive_args",
    )
    interactive_args = string_tuple(
        agent_data.get("interactive_args", default.interactive_args),
        path,
        "agent.interactive_args",
    )
    thinking_levels = string_tuple(
        agent_data.get("thinking_levels", default.thinking_levels),
        path,
        "agent.thinking_levels",
    )
    default_thinking = string_value(
        agent_data.get("default_thinking", default.default_thinking),
        path,
        "agent.default_thinking",
    )
    review_thinking = string_value(
        agent_data.get("review_thinking", default.review_thinking),
        path,
        "agent.review_thinking",
    )
    label = string_value(agent_data.get("label", default.label), path, "agent.label")

    if not command:
        fail_config(path, "agent.command must not be empty")
    if not command[0]:
        fail_config(path, "agent.command executable must not be empty")
    if not non_interactive_args:
        fail_config(path, "agent.non_interactive_args must not be empty")
    non_interactive_fields = validate_template(
        non_interactive_args, path, "agent.non_interactive_args"
    )
    validate_template(interactive_args, path, "agent.interactive_args")
    if "prompt" not in non_interactive_fields:
        fail_config(path, "agent.non_interactive_args must include {prompt}")
    if not thinking_levels:
        fail_config(path, "agent.thinking_levels must not be empty")
    if any(not level for level in thinking_levels):
        fail_config(path, "agent.thinking_levels must not contain empty strings")
    if len(set(thinking_levels)) != len(thinking_levels):
        fail_config(path, "agent.thinking_levels must not contain duplicates")
    if not label:
        fail_config(path, "agent.label must not be empty")
    if default_thinking not in thinking_levels:
        fail_config(path, "agent.default_thinking must be in agent.thinking_levels")
    if review_thinking and review_thinking not in thinking_levels:
        fail_config(path, "agent.review_thinking must be in agent.thinking_levels")

    return OrchaConfig(
        agent=AgentConfig(
            command=command,
            non_interactive_args=non_interactive_args,
            interactive_args=interactive_args,
            default_thinking=default_thinking,
            review_thinking=review_thinking,
            thinking_levels=thinking_levels,
            label=label,
        )
    )


def string_value(value: Any, path: Path, key: str) -> str:
    if not isinstance(value, str):
        fail_config(path, f"{key} must be a string")
    return value


def string_tuple(
    value: Any, path: Path, key: str, *, split_string: bool = False
) -> tuple[str, ...]:
    if isinstance(value, str):
        if split_string:
            try:
                return tuple(shlex.split(value))
            except ValueError as error:
                fail_config(path, f"{key} must be a valid shell-style string: {error}")
        fail_config(path, f"{key} must be an array of strings")
    if not isinstance(value, list | tuple):
        fail_config(path, f"{key} must be an array of strings")
    if not all(isinstance(item, str) for item in value):
        fail_config(path, f"{key} must contain only strings")
    return tuple(value)


def validate_template(args: tuple[str, ...], path: Path, key: str) -> set[str]:
    """Validate agent argument templates and return referenced field names."""

    fields: set[str] = set()
    formatter = string.Formatter()
    for arg in args:
        try:
            parsed_fields = list(formatter.parse(arg))
        except ValueError as error:
            fail_config(path, f"{key} has invalid placeholder syntax: {error}")
        for _literal_text, field_name, _format_spec, _conversion in parsed_fields:
            if field_name is None:
                continue
            if field_name not in TEMPLATE_FIELDS:
                fail_config(
                    path,
                    f"{key} uses unsupported placeholder {{{field_name}}}; "
                    "supported placeholders are {prompt} and {thinking}",
                )
            fields.add(field_name)
        try:
            arg.format(prompt="", thinking="")
        except (AttributeError, IndexError, KeyError, ValueError) as error:
            fail_config(path, f"{key} has invalid placeholder syntax: {error}")
    return fields


def fail_config(path: Path, message: str) -> None:
    echo_err(f"orcha: invalid config at {path}: {message}")
    abort(2)
