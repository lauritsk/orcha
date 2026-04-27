"""Git repository and worktree operations for pid."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

from pid.commands import CommandRunner
from pid.errors import abort
from pid.models import CommitMessage
from pid.output import echo_err, echo_out, write_command_output
from pid.utils import has_output


class Repository:
    """Git-backed repository operations used by the pid workflow."""

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def output(self, args: list[str], *, cwd: str | Path) -> str:
        """Run a git command and return stdout."""

        return self.runner.output(["git", *args], cwd=cwd)

    def count_commits(self, base_rev: str, cwd: str | Path) -> int:
        """Count commits between a base revision and HEAD."""

        output = self.output(["rev-list", "--count", f"{base_rev}..HEAD"], cwd=cwd)
        return int(output.strip() or "0")

    def default_branch(
        self, main_worktree: str, fallback: Callable[[str], str] | None = None
    ) -> str:
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
        elif fallback is not None:
            default_branch = fallback(main_worktree)
        else:
            default_branch = ""

        if not default_branch:
            echo_err("pid: could not determine default branch")
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
            echo_err(f"pid: default branch not found locally: {default_branch}")
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
            echo_err(f"pid: branch already exists: {branch}")
            abort(1)
        if self.show_ref(main_worktree, f"refs/remotes/origin/{branch}"):
            echo_err(f"pid: remote branch already exists: origin/{branch}")
            abort(1)
        if Path(worktree_path).exists():
            echo_err(f"pid: path already exists: {worktree_path}")
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
        echo_err(f"pid: failed to configure worktree; cleaning up {worktree_path}")
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
        self, base_rev: str, worktree_path: str, commit_message: CommitMessage
    ) -> None:
        """Commit reviewed agent output as one generated-message commit."""

        commit_count = self.count_commits(base_rev, worktree_path)
        dirty = self.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )

        if commit_count == 0 and not has_output(dirty):
            echo_out("pid: no changes or commits after agent; stopping before PR")
            abort(0)

        if commit_count > 0:
            self.runner.require(["git", "reset", "--soft", base_rev], cwd=worktree_path)

        self.runner.require(["git", "add", "-A"], cwd=worktree_path)
        self.runner.require(
            [
                "git",
                "commit",
                "-m",
                commit_message.title,
                "-m",
                commit_message.body,
            ],
            cwd=worktree_path,
        )

        dirty = self.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        if has_output(dirty):
            echo_err(
                "pid: worktree still has uncommitted changes after commit; "
                "stopping before PR"
            )
            abort(1)

    def commit_dirty_automated_feedback(
        self, worktree_path: str, commit_title: str, feedback_title: str
    ) -> str:
        """Commit dirty automated feedback before a PR attempt."""

        dirty = self.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        if not has_output(dirty):
            return commit_title

        self.runner.require(["git", "add", "-A"], cwd=worktree_path)
        self.runner.require(
            ["git", "commit", "-m", feedback_title],
            cwd=worktree_path,
        )
        return self.output(["log", "-1", "--format=%s"], cwd=worktree_path).strip()

    def commit_rebase_changes(
        self, worktree_path: str, commit_title: str, rebase_title: str
    ) -> str:
        """Commit dirty files left after a successful rebase resolution."""

        dirty = self.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        if not has_output(dirty):
            return commit_title

        self.runner.require(["git", "add", "-A"], cwd=worktree_path)
        self.runner.require(
            ["git", "commit", "-m", rebase_title],
            cwd=worktree_path,
        )
        return self.output(["log", "-1", "--format=%s"], cwd=worktree_path).strip()

    def contains_ref(self, worktree_path: str, ref: str) -> bool:
        """Return true when HEAD contains ref."""

        result = self.runner.run(
            ["git", "merge-base", "--is-ancestor", ref, "HEAD"], cwd=worktree_path
        )
        return result.returncode == 0

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
        echo_err(f"pid: invalid branch name: {branch}")
        abort(1)
