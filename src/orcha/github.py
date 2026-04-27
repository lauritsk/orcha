"""GitHub pull-request operations for Orcha."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from orcha.commands import CommandRunner
from orcha.errors import abort
from orcha.models import CommandResult, CommitMessage
from orcha.output import echo_err, write_collected, write_command_output
from orcha.utils import has_output


class GitHub:
    """GitHub CLI operations used by the Orcha workflow."""

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def ensure_pr(
        self, branch: str, message: CommitMessage, worktree_path: str
    ) -> None:
        """Create a pull request or update the title/body of an existing one."""

        view_result = self.runner.run(["gh", "pr", "view", branch], cwd=worktree_path)
        if view_result.returncode != 0:
            create_result = self.runner.run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--title",
                    message.title,
                    "--body",
                    message.body,
                ],
                cwd=worktree_path,
            )
            if create_result.returncode != 0:
                write_command_output(create_result)
                abort(create_result.returncode)
            if has_output(create_result.stdout):
                write_collected(create_result.stdout, stream=sys.stdout)
            return

        self.runner.require(
            [
                "gh",
                "pr",
                "edit",
                branch,
                "--title",
                message.title,
                "--body",
                message.body,
            ],
            cwd=worktree_path,
        )

    def pr_url(self, branch: str, worktree_path: str) -> str:
        """Return the URL for a pull request branch."""

        return self.runner.output(
            ["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"],
            cwd=worktree_path,
        ).strip()

    def head_oid(self, branch: str, worktree_path: str) -> str:
        """Return the pull request head commit object ID."""

        return self.runner.output(
            [
                "gh",
                "pr",
                "view",
                branch,
                "--json",
                "headRefOid",
                "--jq",
                ".headRefOid",
            ],
            cwd=worktree_path,
        ).strip()

    def wait_for_checks(
        self,
        branch: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
        worktree_path: str,
    ) -> tuple[int, str]:
        """Wait for GitHub PR checks to finish or time out."""

        deadline = int(time.time()) + timeout_seconds
        while True:
            checks_result = self.runner.run(
                ["gh", "pr", "checks", branch],
                cwd=worktree_path,
                combine_output=True,
            )
            if checks_result.returncode != 8:
                return checks_result.returncode, checks_result.stdout
            if int(time.time()) >= deadline:
                echo_err(
                    f"orcha: CI checks still pending after {timeout_seconds} seconds"
                )
                return checks_result.returncode, checks_result.stdout
            if poll_interval_seconds > 0:
                time.sleep(poll_interval_seconds)

    def squash_merge(
        self, branch: str, head_oid: str, message: CommitMessage, worktree_path: str
    ) -> CommandResult:
        """Attempt a guarded squash merge for a pull request."""

        return self.runner.run(
            [
                "gh",
                "pr",
                "merge",
                branch,
                "--squash",
                "--match-head-commit",
                head_oid,
                "--subject",
                message.title,
                "--body",
                message.body,
            ],
            cwd=worktree_path,
            combine_output=True,
        )

    def merged_at(self, pr_url: str, worktree_path: str | Path) -> CommandResult:
        """Return the raw `gh pr view` result for the mergedAt field."""

        return self.runner.run(
            [
                "gh",
                "pr",
                "view",
                pr_url,
                "--json",
                "mergedAt",
                "--jq",
                '.mergedAt // ""',
            ],
            cwd=worktree_path,
        )

    def reports_merged(self, pr_url: str, worktree_path: str) -> bool:
        """Return true when GitHub reports a pull request has merged."""

        result = self.merged_at(pr_url, worktree_path)
        return result.returncode == 0 and bool(result.stdout.strip())
