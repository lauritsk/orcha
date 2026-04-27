"""Command execution helpers for Orcha."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from orcha.errors import abort
from orcha.models import CommandResult
from orcha.output import echo_err, write_command_output


class CommandRunner:
    """Small subprocess wrapper preserving command output behavior."""

    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        combine_output: bool = False,
    ) -> CommandResult:
        try:
            if combine_output:
                process = subprocess.run(
                    args,
                    cwd=cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                return CommandResult(process.returncode, process.stdout or "")

            process = subprocess.run(
                args,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            return CommandResult(
                process.returncode,
                process.stdout or "",
                process.stderr or "",
            )
        except FileNotFoundError:
            return CommandResult(127, "", f"orcha: command not found: {args[0]}\n")

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
