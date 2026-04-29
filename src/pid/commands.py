"""Command execution helpers for pid."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from subprocess import STDOUT

from plumbum import local
from plumbum.commands.processes import CommandNotFound

from pid.errors import abort
from pid.models import CommandResult, OutputMode
from pid.output import echo_err, write_command_output
from pid.session_logging import CommandLogHandle, SessionLogger


class CommandRunner:
    """Small plumbum wrapper preserving command output behavior."""

    def __init__(
        self,
        logger: SessionLogger | None = None,
        output_mode: OutputMode = OutputMode.NORMAL,
    ) -> None:
        self.logger = logger
        self.output_mode = output_mode

    def set_logger(self, logger: SessionLogger | None) -> None:
        """Attach the active session logger."""

        self.logger = logger

    def set_output_mode(self, output_mode: OutputMode) -> None:
        """Set console output detail level."""

        self.output_mode = output_mode

    def writes_success_output(self) -> bool:
        """Return true when successful command output is echoed immediately."""

        return self.output_mode == OutputMode.ALL

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        combine_output: bool = False,
    ) -> CommandResult:
        command_log = self._start_command_log(
            args,
            cwd=cwd,
            combine_output=combine_output,
        )

        try:
            local.env.update(os.environ)
            command = local[args[0]]
            if combine_output:
                returncode, stdout, _stderr = command.run(
                    args[1:],
                    cwd=cwd,
                    stderr=STDOUT,
                    retcode=None,
                )
                result = CommandResult(returncode, stdout or "")
            else:
                returncode, stdout, stderr = command.run(
                    args[1:],
                    cwd=cwd,
                    retcode=None,
                )
                result = CommandResult(returncode, stdout or "", stderr or "")
        except CommandNotFound, FileNotFoundError:
            result = CommandResult(127, "", f"pid: command not found: {args[0]}\n")
        except Exception as error:
            self._log_command_exception(command_log, error)
            raise

        self._finish_command_log(command_log, result)
        if self.writes_success_output() and result.returncode == 0:
            write_command_output(result)
        return result

    def run_interactive(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
    ) -> CommandResult:
        """Run a command attached to the current terminal."""

        command_log = self._start_command_log(
            args,
            cwd=cwd,
            combine_output=False,
        )

        try:
            completed = subprocess.run(args, cwd=cwd, check=False)
            result = CommandResult(completed.returncode, "", "")
        except FileNotFoundError:
            result = CommandResult(127, "", f"pid: command not found: {args[0]}\n")
        except Exception as error:
            self._log_command_exception(command_log, error)
            raise

        self._finish_command_log(command_log, result)
        return result

    def _start_command_log(
        self,
        args: list[str],
        *,
        cwd: str | Path | None,
        combine_output: bool,
    ) -> CommandLogHandle | None:
        if self.logger is None:
            return None
        return self.logger.command_start(args, cwd=cwd, combine_output=combine_output)

    def _finish_command_log(
        self, command_log: CommandLogHandle | None, result: CommandResult
    ) -> None:
        if command_log is not None and self.logger is not None:
            self.logger.command_result(command_log, result)

    def _log_command_exception(
        self, command_log: CommandLogHandle | None, error: BaseException
    ) -> None:
        if command_log is not None and self.logger is not None:
            self.logger.command_exception(command_log, error)

    def require(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        quiet: bool = False,
    ) -> None:
        """Run a command and abort when it fails."""

        result = self.run(args, cwd=cwd)
        if result.returncode == 0:
            return
        if not quiet:
            write_command_output(result)
        abort(result.returncode)

    def output(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        combine_output: bool = False,
        quiet: bool = False,
    ) -> str:
        """Run a command, returning stdout or aborting on failure."""

        result = self.run(args, cwd=cwd, combine_output=combine_output)
        if result.returncode == 0:
            return result.stdout
        if not quiet:
            write_command_output(result)
        abort(result.returncode)


def require_command(command: str, message: str) -> None:
    """Abort when a required executable is missing from PATH."""

    if shutil.which(command) is None:
        echo_err(message)
        abort(1)
