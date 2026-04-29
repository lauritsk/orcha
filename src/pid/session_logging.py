"""Session log file support for pid."""

from __future__ import annotations

import os
import platform
import shlex
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, TextIO

from pid.models import CommandResult

LOG_DIR_ENV = "PID_LOG_DIR"


def _absolute_env_path(value: str) -> Path | None:
    """Return an expanded absolute env path, or None for XDG-invalid relatives."""

    path = Path(value).expanduser()
    if not path.is_absolute():
        return None
    return path


def session_log_dir(
    environ: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the directory where pid session logs should be written.

    Precedence:
    1. PID_LOG_DIR explicit override.
    2. XDG_STATE_HOME/pid/logs when XDG_STATE_HOME is set, including macOS.
    3. ~/Library/Logs/pid on macOS.
    4. ~/.local/state/pid/logs elsewhere, per XDG base directory defaults.
    """

    env = os.environ if environ is None else environ
    if override := env.get(LOG_DIR_ENV):
        return Path(override).expanduser()

    if (xdg_state_home := env.get("XDG_STATE_HOME")) and (
        xdg_path := _absolute_env_path(xdg_state_home)
    ):
        return xdg_path / "pid" / "logs"

    base_home = Path.home() if home is None else home
    system = platform.system() if platform_name is None else platform_name
    if system == "Darwin":
        return base_home / "Library" / "Logs" / "pid"
    return base_home / ".local" / "state" / "pid" / "logs"


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _filename_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


@dataclass(frozen=True)
class CommandLogHandle:
    """Identifier for an in-flight command log entry."""

    command_id: int
    started_at: float


class SessionLogger:
    """Append-only human-readable log for one pid run."""

    def __init__(self, path: Path, stream: TextIO) -> None:
        self.path = path
        self._stream = stream
        self._command_count = 0
        self._current_step: str | None = None
        self._closed = False

    @classmethod
    def create(cls, argv: list[str]) -> "SessionLogger":
        """Create a new session log and write its header."""

        directory = session_log_dir()
        existed = directory.exists()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not existed:
            directory.chmod(0o700)
        path = directory / f"pid-session-{_filename_timestamp()}-p{os.getpid()}.log"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        file_descriptor = os.open(path, flags, 0o600)
        stream = os.fdopen(file_descriptor, "w", encoding="utf-8")
        logger = cls(path, stream)
        logger.separator("SESSION START")
        logger.line(f"started: {_utc_timestamp()}")
        logger.line(f"pid: {os.getpid()}")
        logger.line(f"cwd: {Path.cwd()}")
        logger.line(f"argv: {shlex.join(['pid', *argv])}")
        logger.line(f"python: {sys.executable}")
        logger.line(f"log: {path}")
        logger.blank()
        return logger

    def close(self) -> None:
        """Close the log stream."""

        if self._closed:
            return
        if self._current_step is not None:
            self.step_end(self._current_step, "END")
        self.separator("SESSION END")
        self.line(f"ended: {_utc_timestamp()}")
        self._stream.close()
        self._closed = True

    def blank(self) -> None:
        self._write("\n")

    def line(self, value: str) -> None:
        self._write(f"{value}\n")

    def separator(self, title: str) -> None:
        self.line("=" * 88)
        self.line(title)
        self.line("=" * 88)

    def event(self, message: str) -> None:
        self.line(f"[{_utc_timestamp()}] {message}")

    def output(self, stream_name: str, value: str) -> None:
        """Record an pid message written to stdout/stderr."""

        self.line(f"[{_utc_timestamp()}] PID {stream_name.upper()}: {value}")

    def step_start(self, title: str, *, cwd: str | Path | None = None) -> None:
        """Start a named step, ending any previous step with a clear boundary."""

        if self._current_step is not None:
            self.step_end(self._current_step, "END")
        self._current_step = title
        self.separator(f"STEP START: {title}")
        self.line(f"time: {_utc_timestamp()}")
        if cwd is not None:
            self.line(f"cwd: {cwd}")
        self.blank()

    def step_pass(self, title: str) -> None:
        self.step_end(title, "PASS")

    def step_fail(self, title: str, code: int) -> None:
        self.step_end(title, f"FAIL status={code}")

    def step_end(self, title: str, status: str) -> None:
        self.separator(f"STEP {status}: {title}")
        self.line(f"time: {_utc_timestamp()}")
        self.blank()
        if self._current_step == title:
            self._current_step = None

    def command_start(
        self,
        args: list[str],
        *,
        cwd: str | Path | None,
        combine_output: bool,
    ) -> CommandLogHandle:
        self._command_count += 1
        handle = CommandLogHandle(self._command_count, time.monotonic())
        self.line("-" * 88)
        self.line(f"COMMAND START #{handle.command_id} [{_utc_timestamp()}]")
        self.line(f"$ {shlex.join(args)}")
        self.line(f"cwd: {cwd if cwd is not None else Path.cwd()}")
        if combine_output:
            self.line("stderr: combined into stdout")
        self.blank()
        return handle

    def command_result(self, handle: CommandLogHandle, result: CommandResult) -> None:
        duration = time.monotonic() - handle.started_at
        if result.stdout:
            self.output_block(f"COMMAND STDOUT #{handle.command_id}", result.stdout)
        if result.stderr:
            self.output_block(f"COMMAND STDERR #{handle.command_id}", result.stderr)
        self.line(
            f"COMMAND END #{handle.command_id} status={result.returncode} "
            f"duration={duration:.3f}s [{_utc_timestamp()}]"
        )
        self.blank()

    def output_block(self, title: str, value: str) -> None:
        """Record captured process output while preserving its content."""

        self.line(title)
        self._write(value)
        if not value.endswith("\n"):
            self._write("\n")
        self.blank()

    def command_exception(self, handle: CommandLogHandle, error: BaseException) -> None:
        duration = time.monotonic() - handle.started_at
        self.line(
            f"COMMAND EXCEPTION #{handle.command_id} duration={duration:.3f}s "
            f"[{_utc_timestamp()}] {type(error).__name__}: {error}"
        )
        self.blank()

    def _write(self, value: str) -> None:
        self._stream.write(value)
        self._stream.flush()
