"""Keep-screen-awake helpers for pid."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any

from pid.errors import abort
from pid.output import echo_err, echo_out
from pid.session_logging import SessionLogger


class KeepAwake:
    """Manage an optional OS-level keep-awake helper."""

    def __init__(self, *, enabled: bool, logger: SessionLogger | None = None) -> None:
        self.enabled = enabled
        self.logger = logger
        self.process: subprocess.Popen[Any] | None = None

    def start(self) -> None:
        """Start the platform keep-awake process when configured."""

        if not self.enabled:
            return

        if sys.platform != "darwin":
            echo_err(
                "pid: runtime.keep_screen_awake is only implemented on macOS for now"
            )
            abort(2)

        if shutil.which("caffeinate") is None:
            echo_err("pid: runtime.keep_screen_awake requires caffeinate on PATH")
            abort(2)

        args = ["caffeinate", "-d", "-i"]
        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            echo_err(f"pid: could not start caffeinate: {error}")
            abort(2)
        if self.logger is not None:
            self.logger.event("keep-screen-awake started: caffeinate -d -i")
        echo_out("pid: keeping screen awake with caffeinate")

    def stop(self) -> None:
        """Stop any active keep-awake process."""

        if self.process is None:
            return

        process = self.process
        self.process = None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if self.logger is not None:
            self.logger.event("keep-screen-awake stopped")
