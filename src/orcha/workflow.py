"""Orcha worktree automation flow."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from orcha.commands import CommandRunner, require_command
from orcha.errors import OrchaAbort, abort
from orcha.github import GitHub
from orcha.models import ParsedArgs
from orcha.output import (
    echo_err,
    echo_out,
    print_commit_message,
    print_merge_success,
    write_collected,
    write_command_output,
)
from orcha.parsing import bump_thinking, derive_commit_title, parse_args
from orcha.prompts import (
    build_ci_fix_prompt,
    build_rebase_fix_prompt,
    build_review_prompt,
)
from orcha.repository import Repository, validate_branch_name
from orcha.utils import env_int, has_output, review_target_for, worktree_path_for


class OrchaFlow:
    """Implements the Orcha orchestration lifecycle."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()
        self.repository = Repository(self.runner)
        self.github = GitHub(self.runner)
        self.review_rejected_first_pass = False

    def run(self, argv: list[str]) -> int:
        try:
            self._run(argv)
        except OrchaAbort as error:
            return error.code
        return 0

    def _run(self, argv: list[str]) -> None:
        parsed = parse_args(argv)
        branch_commit_title = derive_commit_title(parsed.branch)
        followup_thinking_level = parsed.thinking_level

        validate_branch_name(self.runner, parsed.branch)

        repo_root = self.resolve_repo_root()
        self.verify_commit_title(branch_commit_title)

        require_command("pi", "orcha: pi is required")
        require_command("gh", "orcha: gh is required for PR creation")

        main_worktree = self.resolve_main_worktree()

        main_dirty = self.repository.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=main_worktree
        )
        if has_output(main_dirty):
            echo_err(
                f"orcha: main worktree has uncommitted or untracked changes: {main_worktree}"
            )
            abort(1)

        default_branch = self.repository.default_branch(main_worktree)
        self.repository.switch_and_update_default_branch(main_worktree, default_branch)
        base_rev = self.repository.output(
            ["rev-parse", "HEAD"], cwd=main_worktree
        ).strip()

        worktree_path = worktree_path_for(repo_root, parsed.branch)
        self.repository.guard_new_worktree(main_worktree, parsed.branch, worktree_path)
        self.repository.create_worktree(
            main_worktree, worktree_path, parsed.branch, base_rev
        )

        echo_out(f"Created {worktree_path} on branch {parsed.branch}")

        if shutil.which("mise") is not None:
            self.runner.require(["mise", "trust", "."], cwd=worktree_path)

        self.run_pi_prompt(
            parsed.prompt,
            cwd=worktree_path,
            thinking_level=parsed.thinking_level,
            failure_context="stopping before review/commit/PR",
        )

        initial_commit_count = self.repository.count_commits(base_rev, worktree_path)
        initial_dirty = self.repository.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        pre_review_state_hash = self.repository.state_hash(worktree_path)

        review_target = review_target_for(
            base_rev, initial_commit_count, has_output(initial_dirty)
        )
        review_prompt = build_review_prompt(
            original_prompt=parsed.prompt,
            review_target=review_target,
        )
        self.run_pi_prompt(
            review_prompt,
            cwd=worktree_path,
            thinking_level="high",
            failure_context="stopping before commit/PR",
            label="pi review",
        )

        post_review_state_hash = self.repository.state_hash(worktree_path)
        if pre_review_state_hash != post_review_state_hash:
            self.review_rejected_first_pass = True
            followup_thinking_level = bump_thinking(followup_thinking_level)
            echo_out(
                "orcha: review changed first pass; follow-up pi thinking bumped to "
                f"{followup_thinking_level}"
            )

        self.repository.commit_initial_changes(
            base_rev, worktree_path, branch_commit_title
        )
        commit_title = self.repository.output(
            ["log", "-1", "--format=%s"], cwd=worktree_path
        ).strip()

        self.run_pr_loop(
            parsed=parsed,
            branch_commit_title=branch_commit_title,
            commit_title=commit_title,
            followup_thinking_level=followup_thinking_level,
            main_worktree=main_worktree,
            default_branch=default_branch,
            worktree_path=worktree_path,
        )

    def run_pr_loop(
        self,
        *,
        parsed: ParsedArgs,
        branch_commit_title: str,
        commit_title: str,
        followup_thinking_level: str,
        main_worktree: str,
        default_branch: str,
        worktree_path: str,
    ) -> None:
        need_force_push = False
        pr_url = ""
        checks_timeout_seconds = env_int("ORCHA_CHECKS_TIMEOUT_SECONDS", 1800)
        checks_poll_interval_seconds = env_int("ORCHA_CHECKS_POLL_INTERVAL_SECONDS", 10)

        for attempt in range(1, parsed.max_attempts + 1):
            echo_out(f"orcha: PR attempt {attempt}/{parsed.max_attempts}")
            commit_title = self.repository.commit_dirty_automated_feedback(
                worktree_path, commit_title
            )

            if need_force_push:
                self.runner.require(
                    [
                        "git",
                        "push",
                        "--force-with-lease",
                        "-u",
                        "origin",
                        parsed.branch,
                    ],
                    cwd=worktree_path,
                )
                need_force_push = False
            else:
                self.runner.require(
                    ["git", "push", "-u", "origin", parsed.branch], cwd=worktree_path
                )

            self.github.ensure_pr(parsed.branch, branch_commit_title, worktree_path)
            pr_title = branch_commit_title
            pr_url = self.github.pr_url(parsed.branch, worktree_path)

            checks_status, checks_out = self.github.wait_for_checks(
                parsed.branch,
                checks_timeout_seconds,
                checks_poll_interval_seconds,
                worktree_path,
            )
            if has_output(checks_out):
                write_collected(checks_out, stream=sys.stdout)
            if checks_status != 0:
                if "no checks" in checks_out.lower():
                    echo_out("orcha: no CI checks reported; continuing")
                elif attempt >= parsed.max_attempts:
                    echo_err(
                        f"orcha: CI checks failed after {attempt} attempts; "
                        f"leaving PR open: {pr_url}"
                    )
                    abort(checks_status)
                else:
                    followup_thinking_level = self.fix_ci_failures(
                        pr_title=pr_title,
                        pr_url=pr_url,
                        commit_title=commit_title,
                        checks_out=checks_out,
                        followup_thinking_level=followup_thinking_level,
                        worktree_path=worktree_path,
                    )
                    continue

            pr_head_oid = self.github.head_oid(parsed.branch, worktree_path)
            merge_result = self.github.squash_merge(
                parsed.branch,
                pr_head_oid,
                pr_title,
                worktree_path,
            )
            if has_output(merge_result.stdout):
                write_collected(merge_result.stdout, stream=sys.stdout)

            if merge_result.returncode == 0:
                self.finish_successful_merge(
                    pr_url=pr_url,
                    pr_title=pr_title,
                    main_worktree=main_worktree,
                    default_branch=default_branch,
                    branch=parsed.branch,
                    worktree_path=worktree_path,
                )
                return

            if self.github.reports_merged(pr_url, worktree_path):
                echo_out(
                    "orcha: GitHub reports PR merged despite local gh cleanup failure; "
                    "cleaning up"
                )
                self.cleanup_and_print_success(
                    pr_url=pr_url,
                    pr_title=pr_title,
                    main_worktree=main_worktree,
                    default_branch=default_branch,
                    branch=parsed.branch,
                    worktree_path=worktree_path,
                )
                return

            if attempt >= parsed.max_attempts:
                echo_err(
                    f"orcha: github squash merge failed after {attempt} attempts; "
                    f"leaving PR open: {pr_url}"
                )
                abort(merge_result.returncode)

            echo_out(
                "orcha: merge failed; rebasing onto latest "
                f"origin/{default_branch} before retry"
            )
            self.runner.require(
                ["git", "fetch", "origin", default_branch], cwd=worktree_path
            )

            rebase_result = self.runner.run(
                ["git", "rebase", f"origin/{default_branch}"], cwd=worktree_path
            )
            if rebase_result.returncode != 0:
                write_command_output(rebase_result)
                followup_thinking_level = self.fix_rebase(
                    pr_title=pr_title,
                    pr_url=pr_url,
                    default_branch=default_branch,
                    commit_title=commit_title,
                    merge_out=merge_result.stdout,
                    followup_thinking_level=followup_thinking_level,
                    worktree_path=worktree_path,
                )

            if self.repository.rebase_in_progress(worktree_path):
                echo_err(
                    f"orcha: rebase still in progress after pi; leaving PR open: {pr_url}"
                )
                abort(1)

            commit_title = self.repository.commit_rebase_changes(
                worktree_path, commit_title
            )
            need_force_push = True

        echo_err(
            f"orcha: exhausted {parsed.max_attempts} attempts; leaving worktree: {worktree_path}"
        )
        abort(1)

    def fix_ci_failures(
        self,
        *,
        pr_title: str,
        pr_url: str,
        commit_title: str,
        checks_out: str,
        followup_thinking_level: str,
        worktree_path: str,
    ) -> str:
        prompt = build_ci_fix_prompt(
            pr_title=pr_title,
            pr_url=pr_url,
            commit_title=commit_title,
            checks_out=checks_out,
        )
        self.run_pi_prompt(
            prompt,
            cwd=worktree_path,
            thinking_level=followup_thinking_level,
            failure_context="while fixing CI",
        )

        return self.bump_after_followup(followup_thinking_level)

    def fix_rebase(
        self,
        *,
        pr_title: str,
        pr_url: str,
        default_branch: str,
        commit_title: str,
        merge_out: str,
        followup_thinking_level: str,
        worktree_path: str,
    ) -> str:
        prompt = build_rebase_fix_prompt(
            pr_title=pr_title,
            pr_url=pr_url,
            default_branch=default_branch,
            commit_title=commit_title,
            merge_out=merge_out,
        )
        self.run_pi_prompt(
            prompt,
            cwd=worktree_path,
            thinking_level=followup_thinking_level,
            failure_context="while resolving rebase",
        )

        return self.bump_after_followup(followup_thinking_level)

    def resolve_repo_root(self) -> str:
        """Return the current git repository root or abort with Orcha's message."""

        return self.require_git_output(
            ["rev-parse", "--show-toplevel"],
            error_message="orcha: not inside a git repository",
        )

    def resolve_main_worktree(self) -> str:
        """Return the main worktree path from git's common directory."""

        common_git_dir = self.require_git_output(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            error_message="orcha: could not determine common git dir",
        )
        return str(Path(common_git_dir).parent)

    def require_git_output(self, args: list[str], *, error_message: str) -> str:
        """Run a git command and abort when it fails or prints no stdout."""

        result = self.runner.run(["git", *args])
        output = result.stdout.strip()
        if result.returncode == 0 and output:
            return output
        echo_err(error_message)
        abort(1)

    def verify_commit_title(self, branch_commit_title: str) -> None:
        """Verify the derived commit title with cocogitto."""

        require_command("cog", "orcha: cog is required for commit message verification")
        print_commit_message(branch_commit_title)
        cog_result = self.runner.run(
            ["cog", "verify", branch_commit_title], combine_output=True
        )
        if cog_result.returncode != 0:
            write_collected(cog_result.stdout, stream=sys.stderr)
            abort(cog_result.returncode)

    def run_pi_prompt(
        self,
        prompt: str,
        *,
        cwd: str,
        thinking_level: str,
        failure_context: str,
        label: str = "pi",
    ) -> None:
        """Run pi with a prompt, preserving Orcha's failure handling."""

        pi_args = ["pi"]
        if thinking_level:
            pi_args.extend(["--thinking", thinking_level])
        pi_args.extend(["-p", prompt])

        pi_result = self.runner.run(pi_args, cwd=cwd)
        if pi_result.returncode == 0:
            return

        write_command_output(pi_result)
        separator = " " if failure_context.startswith("while ") else "; "
        echo_err(
            f"orcha: {label} exited with status {pi_result.returncode}"
            f"{separator}{failure_context}"
        )
        abort(pi_result.returncode)

    def bump_after_followup(self, followup_thinking_level: str) -> str:
        if not self.review_rejected_first_pass or not followup_thinking_level:
            return followup_thinking_level
        return bump_thinking(followup_thinking_level)

    def finish_successful_merge(
        self,
        *,
        pr_url: str,
        pr_title: str,
        main_worktree: str,
        default_branch: str,
        branch: str,
        worktree_path: str,
    ) -> None:
        merged_at_result = self.github.merged_at(pr_url, worktree_path)
        if merged_at_result.returncode != 0:
            echo_err(
                "orcha: merge command succeeded, but merged state could not be "
                f"confirmed; leaving PR/worktree for manual cleanup: {pr_url}"
            )
            abort(1)
        if not merged_at_result.stdout.strip():
            echo_out(
                "orcha: merge command succeeded, but PR is not merged yet; likely "
                f"queued or auto-merge enabled. Leaving PR/worktree: {pr_url}"
            )
            return

        self.cleanup_and_print_success(
            pr_url=pr_url,
            pr_title=pr_title,
            main_worktree=main_worktree,
            default_branch=default_branch,
            branch=branch,
            worktree_path=worktree_path,
        )

    def cleanup_and_print_success(
        self,
        *,
        pr_url: str,
        pr_title: str,
        main_worktree: str,
        default_branch: str,
        branch: str,
        worktree_path: str,
    ) -> None:
        self.runner.require(
            ["git", "-C", main_worktree, "pull", "--ff-only", "origin", default_branch]
        )
        self.runner.run(
            ["git", "push", "origin", "--delete", branch], cwd=worktree_path
        )
        self.runner.require(
            ["git", "-C", main_worktree, "worktree", "remove", worktree_path]
        )
        self.runner.run(["git", "-C", main_worktree, "branch", "-D", branch])
        print_merge_success(pr_title, pr_url)


def run_orcha(argv: list[str]) -> int:
    """Run the orcha flow and return a process exit code."""

    return OrchaFlow().run(argv)
