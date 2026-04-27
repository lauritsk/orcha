"""Git repository and worktree operations for Orcha."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from orcha.commands import CommandRunner
from orcha.errors import abort
from orcha.output import echo_err, echo_out, write_command_output
from orcha.utils import has_output


class Repository:
    """Git-backed repository operations used by the Orcha workflow."""

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def output(self, args: list[str], *, cwd: str | Path) -> str:
        """Run a git command and return stdout."""

        return self.runner.output(["git", *args], cwd=cwd)

    def count_commits(self, base_rev: str, cwd: str | Path) -> int:
        """Count commits between a base revision and HEAD."""

        output = self.output(["rev-list", "--count", f"{base_rev}..HEAD"], cwd=cwd)
        return int(output.strip() or "0")

    def default_branch(self, main_worktree: str) -> str:
        """Resolve the repository default branch name."""

        symbolic_ref = self.runner.run(
            [
                "git",
                "-C",
                main_worktree,
                "symbolic-ref",
                "--quiet",
                "--short",
                "refs/remotes/origin/HEAD",
            ]
        )
        if symbolic_ref.returncode == 0:
            default_branch = re.sub(r"^origin/", "", symbolic_ref.stdout.strip())
        else:
            default_branch = self.runner.run(
                [
                    "gh",
                    "repo",
                    "view",
                    "--json",
                    "defaultBranchRef",
                    "--jq",
                    ".defaultBranchRef.name",
                ],
                cwd=main_worktree,
            ).stdout.strip()

        if not default_branch:
            echo_err("orcha: could not determine default branch")
            abort(1)
        return default_branch

    def switch_and_update_default_branch(
        self, main_worktree: str, default_branch: str
    ) -> None:
        """Switch the main worktree to the default branch and fast-forward it."""

        if self.show_ref(main_worktree, f"refs/heads/{default_branch}"):
            self.runner.require(["git", "-C", main_worktree, "switch", default_branch])
        elif self.show_ref(main_worktree, f"refs/remotes/origin/{default_branch}"):
            self.runner.require(
                [
                    "git",
                    "-C",
                    main_worktree,
                    "switch",
                    "--track",
                    f"origin/{default_branch}",
                ]
            )
        else:
            echo_err(f"orcha: default branch not found locally: {default_branch}")
            abort(1)

        self.runner.require(
            ["git", "-C", main_worktree, "pull", "--ff-only", "origin", default_branch]
        )

    def show_ref(self, cwd: str | Path, ref: str) -> bool:
        """Return true when a git ref exists."""

        result = self.runner.run(
            ["git", "-C", str(cwd), "show-ref", "--verify", "--quiet", ref]
        )
        return result.returncode == 0

    def guard_new_worktree(
        self, main_worktree: str, branch: str, worktree_path: str
    ) -> None:
        """Abort if the branch or sibling worktree path already exists."""

        if self.show_ref(main_worktree, f"refs/heads/{branch}"):
            echo_err(f"orcha: branch already exists: {branch}")
            abort(1)
        if self.show_ref(main_worktree, f"refs/remotes/origin/{branch}"):
            echo_err(f"orcha: remote branch already exists: origin/{branch}")
            abort(1)
        if Path(worktree_path).exists():
            echo_err(f"orcha: path already exists: {worktree_path}")
            abort(1)

    def create_worktree(
        self, main_worktree: str, worktree_path: str, branch: str, base_rev: str
    ) -> None:
        """Create and configure a new git worktree for a branch."""

        self.runner.require(
            ["git", "-C", main_worktree, "config", "extensions.worktreeConfig", "true"]
        )
        self.runner.require(
            [
                "git",
                "-C",
                main_worktree,
                "worktree",
                "add",
                worktree_path,
                "-b",
                branch,
                base_rev,
            ]
        )

        config_result = self.runner.run(
            [
                "git",
                "-C",
                worktree_path,
                "config",
                "--worktree",
                "commit.gpgSign",
                "false",
            ]
        )
        if config_result.returncode == 0:
            return

        write_command_output(config_result)
        echo_err(f"orcha: failed to configure worktree; cleaning up {worktree_path}")
        self.runner.run(
            ["git", "-C", main_worktree, "worktree", "remove", "--force", worktree_path]
        )
        self.runner.run(["git", "-C", main_worktree, "branch", "-D", branch])
        abort(1)

    def state_hash(self, worktree_path: str) -> str:
        """Hash the committed, staged, unstaged, and untracked worktree state."""

        parts: list[bytes] = []
        for args in (
            ["rev-parse", "HEAD"],
            ["status", "--porcelain", "--untracked-files=all"],
            ["diff", "--binary", "--no-ext-diff"],
            ["diff", "--cached", "--binary", "--no-ext-diff"],
        ):
            parts.append(" ".join(args).encode())
            parts.append(b"\0")
            parts.append(self.output(args, cwd=worktree_path).encode())

        untracked = self.output(
            ["ls-files", "--others", "--exclude-standard"], cwd=worktree_path
        )
        for relative_path in untracked.splitlines():
            parts.append(f"untracked {relative_path}\n".encode())
            path = Path(worktree_path, relative_path)
            digest = hashlib.sha256(
                path.read_bytes() if path.is_file() else b""
            ).hexdigest()
            parts.append(f"{digest}  {relative_path}\n".encode())

        return hashlib.sha256(b"".join(parts)).hexdigest()

    def commit_initial_changes(
        self, base_rev: str, worktree_path: str, branch_commit_title: str
    ) -> None:
        """Commit initial pi output, including dirty review changes."""

        commit_count = self.count_commits(base_rev, worktree_path)
        dirty = self.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )

        if commit_count == 0:
            if not has_output(dirty):
                echo_out("orcha: no changes or commits after pi; stopping before PR")
                abort(0)
            self.runner.require(["git", "add", "-A"], cwd=worktree_path)
            self.runner.require(
                ["git", "commit", "-m", branch_commit_title], cwd=worktree_path
            )
        elif has_output(dirty):
            self.runner.require(["git", "add", "-A"], cwd=worktree_path)
            self.runner.require(
                ["git", "commit", "-m", "fix: address follow-up changes"],
                cwd=worktree_path,
            )

        dirty = self.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        if has_output(dirty):
            echo_err(
                "orcha: worktree still has uncommitted changes after commit; "
                "stopping before PR"
            )
            abort(1)

    def commit_dirty_automated_feedback(
        self, worktree_path: str, commit_title: str
    ) -> str:
        """Commit dirty automated feedback before a PR attempt."""

        dirty = self.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        if not has_output(dirty):
            return commit_title

        self.runner.require(["git", "add", "-A"], cwd=worktree_path)
        self.runner.require(
            ["git", "commit", "-m", "fix: address automated feedback"],
            cwd=worktree_path,
        )
        return self.output(["log", "-1", "--format=%s"], cwd=worktree_path).strip()

    def commit_rebase_changes(self, worktree_path: str, commit_title: str) -> str:
        """Commit dirty files left after a successful rebase resolution."""

        dirty = self.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        if not has_output(dirty):
            return commit_title

        self.runner.require(["git", "add", "-A"], cwd=worktree_path)
        self.runner.require(
            ["git", "commit", "-m", "fix: resolve latest base changes"],
            cwd=worktree_path,
        )
        return self.output(["log", "-1", "--format=%s"], cwd=worktree_path).strip()

    def rebase_in_progress(self, worktree_path: str) -> bool:
        """Return true when git reports a rebase state directory."""

        git_dir = self.output(
            ["rev-parse", "--path-format=absolute", "--git-dir"], cwd=worktree_path
        ).strip()
        return (
            Path(git_dir, "rebase-merge").is_dir()
            or Path(git_dir, "rebase-apply").is_dir()
        )


def validate_branch_name(runner: CommandRunner, branch: str) -> None:
    """Validate a prospective branch name using git's ref-format rules."""

    result = runner.run(["git", "check-ref-format", "--branch", branch])
    if result.returncode != 0:
        echo_err(f"orcha: invalid branch name: {branch}")
        abort(1)
