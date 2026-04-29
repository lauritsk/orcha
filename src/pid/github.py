"""Configurable forge pull-request operations for pid."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

from pid.commands import CommandRunner
from pid.config import ForgeConfig
from pid.errors import abort
from pid.models import CommandResult, CommitMessage
from pid.output import echo_err, write_collected, write_command_output
from pid.utils import has_output


class Forge:
    """Forge CLI operations used by the pid workflow."""

    def __init__(self, runner: CommandRunner, config: ForgeConfig) -> None:
        self.runner = runner
        self.config = config

    def default_branch(self, worktree_path: str) -> str:
        """Return the forge-reported default branch, or an empty string."""

        if not self.config.default_branch_args:
            return ""
        result = self.runner.run(
            self.config.command_line(self.config.default_branch_args),
            cwd=worktree_path,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def ensure_pr(
        self, branch: str, message: CommitMessage, worktree_path: str
    ) -> None:
        """Create a pull request or update the title/body of an existing one."""

        values = self.values(branch=branch, message=message)
        view_result = self.runner.run(
            self.config.command_line(self.config.pr_view_args, **values),
            cwd=worktree_path,
        )
        if view_result.returncode != 0:
            create_result = self.runner.run(
                self.config.command_line(self.config.pr_create_args, **values),
                cwd=worktree_path,
            )
            if create_result.returncode != 0:
                write_command_output(create_result)
                abort(create_result.returncode)
            if (
                has_output(create_result.stdout)
                and not self.runner.writes_success_output()
            ):
                write_collected(create_result.stdout, stream=sys.stdout)
            return

        self.runner.require(
            self.config.command_line(self.config.pr_edit_args, **values),
            cwd=worktree_path,
        )

    def pr_url(self, branch: str, worktree_path: str) -> str:
        """Return the URL for a pull request branch."""

        return self.runner.output(
            self.config.command_line(self.config.pr_url_args, branch=branch),
            cwd=worktree_path,
        ).strip()

    def head_oid(self, branch: str, worktree_path: str) -> str:
        """Return the pull request head commit object ID if configured."""

        if not self.config.pr_head_oid_args:
            return ""
        return self.runner.output(
            self.config.command_line(self.config.pr_head_oid_args, branch=branch),
            cwd=worktree_path,
        ).strip()

    def wait_for_checks(
        self,
        branch: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
        worktree_path: str,
        on_poll: Callable[[], None] | None = None,
    ) -> tuple[int, str]:
        """Wait for PR checks to finish or time out."""

        if not self.config.pr_checks_args:
            return 0, ""

        deadline = int(time.time()) + timeout_seconds
        while True:
            if on_poll is not None:
                on_poll()
            checks_result = self.runner.run(
                self.config.command_line(self.config.pr_checks_args, branch=branch),
                cwd=worktree_path,
                combine_output=True,
            )
            if checks_result.returncode not in self.config.checks_pending_exit_codes:
                return checks_result.returncode, checks_result.stdout
            if int(time.time()) >= deadline:
                echo_err(
                    f"pid: CI checks still pending after {timeout_seconds} seconds"
                )
                return checks_result.returncode, checks_result.stdout
            if poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)

    def squash_merge(
        self,
        branch: str,
        head_oid: str,
        message: CommitMessage,
        pr_url: str,
        worktree_path: str,
    ) -> CommandResult:
        """Attempt a guarded squash merge for a pull request."""

        return self.runner.run(
            self.config.command_line(
                self.config.pr_merge_args,
                **self.values(
                    branch=branch,
                    message=message,
                    head_oid=head_oid,
                    pr_url=pr_url,
                ),
            ),
            cwd=worktree_path,
            combine_output=True,
        )

    def merged_at(self, pr_url: str, worktree_path: str | Path) -> CommandResult:
        """Return the raw configured merge-confirmation result."""

        if not self.config.pr_merged_at_args:
            return CommandResult(0, "assumed-merged")
        return self.runner.run(
            self.config.command_line(self.config.pr_merged_at_args, pr_url=pr_url),
            cwd=worktree_path,
        )

    def reports_merged(self, pr_url: str, worktree_path: str) -> bool:
        """Return true when the forge reports a pull request has merged."""

        if not self.config.pr_merged_at_args:
            return False
        result = self.merged_at(pr_url, worktree_path)
        return result.returncode == 0 and bool(result.stdout.strip())

    def output_reports_no_checks(self, output: str) -> bool:
        """Return true when configured markers indicate no checks exist."""

        lowered = output.lower()
        return any(
            marker.lower() in lowered for marker in self.config.no_checks_markers
        )

    @staticmethod
    def values(
        *,
        branch: str,
        message: CommitMessage | None = None,
        head_oid: str = "",
        pr_url: str = "",
    ) -> dict[str, str]:
        """Return template values for forge command rendering."""

        return {
            "branch": branch,
            "title": message.title if message is not None else "",
            "body": message.body if message is not None else "",
            "head_oid": head_oid,
            "pr_url": pr_url,
        }
