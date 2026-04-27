"""Diagnostic and inspection commands for pid."""

from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from pid.config import PIDConfig, default_config_path
from pid.session_logging import session_log_dir

_SESSION_PID_RE = re.compile(r"^pid: (?P<pid>\d+)$", re.MULTILINE)
_SESSION_STARTED_RE = re.compile(r"^started: (?P<started>.+)$", re.MULTILINE)
_SESSION_CWD_RE = re.compile(r"^cwd: (?P<cwd>.+)$", re.MULTILINE)
_SESSION_ARGV_RE = re.compile(r"^argv: (?P<argv>.+)$", re.MULTILINE)
_STEP_START_RE = re.compile(r"^STEP START: (?P<step>.+)$", re.MULTILINE)
_STEP_END_RE = re.compile(
    r"^STEP (?:PASS|FAIL status=\d+|END): (?P<step>.+)$", re.MULTILINE
)


def config_to_toml(config: PIDConfig) -> str:
    """Render pid config as TOML."""

    lines: list[str] = []
    for section in fields(config):
        value = getattr(config, section.name)
        lines.append(f"[{section.name}]")
        for item in fields(value):
            item_value = getattr(value, item.name)
            lines.append(f"{item.name} = {_toml_value(item_value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def print_config_metadata(*, config_path: Path | None = None) -> str:
    """Return common pid path metadata."""

    return (
        f"config_path = {_toml_value(str(config_path or default_config_path()))}\n"
        f"log_dir = {_toml_value(str(session_log_dir()))}\n"
    )


def active_sessions_table(*, include_all: bool = False) -> str:
    """Return a human-readable table of pid sessions from session logs."""

    sessions = list_sessions(include_all=include_all)
    if not sessions:
        return "no active pid sessions\n" if not include_all else "no pid sessions\n"

    headers = ["pid", "state", "stage", "started", "cwd", "log"]
    rows = [[session[key] for key in headers] for session in sessions]
    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    ]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append(
            "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        )
    return "\n".join(lines) + "\n"


def list_sessions(*, include_all: bool = False) -> list[dict[str, str]]:
    """List active pid sessions, optionally including completed/stale logs."""

    directory = session_log_dir()
    if not directory.exists():
        return []

    sessions: list[dict[str, str]] = []
    for path in sorted(directory.glob("pid-session-*.log"), reverse=True):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        session = _session_from_log(path, text)
        if include_all or session["state"] == "active":
            sessions.append(session)
    return sessions


def _session_from_log(path: Path, text: str) -> dict[str, str]:
    pid = _match(_SESSION_PID_RE, text, "pid") or "?"
    ended = "SESSION END" in text
    live = pid.isdigit() and _pid_is_live(int(pid))
    if ended:
        state = "complete"
    elif live:
        state = "active"
    else:
        state = "stale"

    return {
        "pid": pid,
        "state": state,
        "stage": _current_stage(text, ended=ended),
        "started": _match(_SESSION_STARTED_RE, text, "started") or "?",
        "cwd": _match(_SESSION_CWD_RE, text, "cwd") or "?",
        "argv": _match(_SESSION_ARGV_RE, text, "argv") or "?",
        "log": str(path),
    }


def _current_stage(text: str, *, ended: bool) -> str:
    if ended:
        return "ended"
    open_step: str | None = None
    events: list[tuple[int, str, str]] = []
    events.extend(
        (match.start(), "start", match.group("step"))
        for match in _STEP_START_RE.finditer(text)
    )
    events.extend(
        (match.start(), "end", match.group("step"))
        for match in _STEP_END_RE.finditer(text)
    )
    for _position, kind, step in sorted(events):
        if kind == "start":
            open_step = step
        elif open_step == step:
            open_step = None
    return open_step or "running"


def _match(pattern: re.Pattern[str], text: str, group: str) -> str | None:
    match = pattern.search(text)
    return match.group(group) if match else None


def _pid_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, tuple | list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if is_dataclass(value) and not isinstance(value, type):
        return _toml_value(dataclasses.asdict(value))
    raise TypeError(f"unsupported TOML value: {value!r}")
