"""Orcha worktree automation flow."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from orcha.commands import CommandRunner, require_command
from orcha.errors import OrchaAbort, abort
from orcha.github import GitHub
from orcha.messages import parse_commit_message
from orcha.models import CommitMessage, ParsedArgs
from orcha.output import (
    echo_err,
    echo_out,
    print_commit_message,
    print_merge_success,
    set_session_logger,
    write_collected,
    write_command_output,
)
from orcha.parsing import bump_thinking, parse_args
from orcha.prompts import (
    build_ci_fix_prompt,
    build_message_prompt,
    build_rebase_fix_prompt,
    build_review_prompt,
)
from orcha.repository import Repository, validate_branch_name
from orcha.session_logging import SessionLogger
from orcha.utils import env_int, has_output, review_target_for, worktree_path_for


class OrchaFlow:
    """Implements the Orcha orchestration lifecycle."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or CommandRunner()
        self.repository = Repository(self.runner)
        self.github = GitHub(self.runner)
        self.review_rejected_first_pass = False
        self.session_logger: SessionLogger | None = None

    def run(self, argv: list[str]) -> int:
        exit_code = 0
        try:
            self._run(argv)
        except OrchaAbort as error:
            exit_code = error.code
        except Exception as error:
            exit_code = 1
            if self.session_logger is not None:
                self.session_logger.event(
                    f"unhandled exception: {type(error).__name__}: {error}"
                )
            raise
        finally:
            if self.session_logger is not None:
                self.session_logger.event(f"exit code: {exit_code}")
                self.session_logger.close()
                set_session_logger(None)
                self.runner.set_logger(None)
        return exit_code

    def _run(self, argv: list[str]) -> None:
        parsed = parse_args(argv)
        self.start_session_logging(argv)
        self.log_parsed_args(parsed)
        followup_thinking_level = parsed.thinking_level

        validate_branch_name(self.runner, parsed.branch)

        repo_root = self.resolve_repo_root()

        self.require_external_commands()

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

        if parsed.interactive:
            self.run_pi_session(
                parsed.interactive_prompt,
                cwd=worktree_path,
                thinking_level=parsed.thinking_level,
            )
        else:
            self.run_pi_prompt(
                parsed.prompt,
                cwd=worktree_path,
                thinking_level=parsed.thinking_level,
                failure_context="stopping before review/commit/PR",
                step_label="pi initial agent",
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
            step_label="pi review agent",
        )

        post_review_state_hash = self.repository.state_hash(worktree_path)
        if pre_review_state_hash != post_review_state_hash:
            self.review_rejected_first_pass = True
            echo_out(
                "orcha: review changed first pass; follow-up pi will keep "
                f"thinking {followup_thinking_level}"
            )

        post_review_commit_count = self.repository.count_commits(
            base_rev, worktree_path
        )
        post_review_dirty = self.repository.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        if post_review_commit_count == 0 and not has_output(post_review_dirty):
            echo_out("orcha: no changes or commits after pi; stopping before PR")
            abort(0)

        commit_message = self.generate_commit_message(
            parsed=parsed,
            base_rev=base_rev,
            worktree_path=worktree_path,
        )
        self.verify_commit_title(commit_message)

        self.repository.commit_initial_changes(base_rev, worktree_path, commit_message)
        commit_title = self.repository.output(
            ["log", "-1", "--format=%s"], cwd=worktree_path
        ).strip()

        self.run_pr_loop(
            parsed=parsed,
            base_rev=base_rev,
            commit_message=commit_message,
            commit_title=commit_title,
            followup_thinking_level=followup_thinking_level,
            main_worktree=main_worktree,
            default_branch=default_branch,
            worktree_path=worktree_path,
        )

    def start_session_logging(self, argv: list[str]) -> None:
        """Create and announce the per-run session log."""

        try:
            self.session_logger = SessionLogger.create(argv)
        except OSError as error:
            echo_err(f"orcha: session logging disabled: {error}")
            return

        set_session_logger(self.session_logger)
        self.runner.set_logger(self.session_logger)
        echo_out(f"orcha: session log: {self.session_logger.path}")

    def log_parsed_args(self, parsed: ParsedArgs) -> None:
        """Record parsed CLI args in the session log."""

        if self.session_logger is None:
            return
        self.session_logger.event(
            "parsed args: "
            f"attempts={parsed.max_attempts} thinking={parsed.thinking_level} "
            f"branch={parsed.branch!r} prompt={parsed.prompt!r} "
            f"interactive={parsed.interactive}"
        )

    def generate_commit_message(
        self, *, parsed: ParsedArgs, base_rev: str, worktree_path: str
    ) -> CommitMessage:
        """Ask pi to write validated commit/PR metadata without changing worktree."""

        git_dir = self.repository.output(
            ["rev-parse", "--path-format=absolute", "--git-dir"], cwd=worktree_path
        ).strip()
        output_path = Path(git_dir, "orcha-message.json")
        output_path.unlink(missing_ok=True)
        pre_message_state_hash = self.repository.state_hash(worktree_path)

        self.run_pi_prompt(
            build_message_prompt(
                original_prompt=parsed.prompt,
                branch=parsed.branch,
                base_rev=base_rev,
                output_path=str(output_path),
            ),
            cwd=worktree_path,
            thinking_level="high",
            failure_context="stopping before commit/PR",
            label="pi message",
        )

        post_message_state_hash = self.repository.state_hash(worktree_path)
        if pre_message_state_hash != post_message_state_hash:
            echo_err(
                "orcha: pi message changed the worktree; stopping before commit/PR"
            )
            abort(1)
        if not output_path.exists():
            echo_err("orcha: pi message did not write commit metadata")
            abort(1)

        return parse_commit_message(output_path.read_text())

    def require_external_commands(self) -> None:
        """Ensure external CLIs needed for the orchestration flow exist."""

        require_command("cog", "orcha: cog is required for commit message verification")
        require_command("pi", "orcha: pi is required")
        require_command("gh", "orcha: gh is required for PR creation")

    def run_pr_loop(
        self,
        *,
        parsed: ParsedArgs,
        base_rev: str,
        commit_message: CommitMessage,
        commit_title: str,
        followup_thinking_level: str,
        main_worktree: str,
        default_branch: str,
        worktree_path: str,
    ) -> None:
        need_force_push = False
        pr_url = ""
        message_state_hash = self.repository.state_hash(worktree_path)
        checks_timeout_seconds = env_int("ORCHA_CHECKS_TIMEOUT_SECONDS", 1800)
        checks_poll_interval_seconds = env_int("ORCHA_CHECKS_POLL_INTERVAL_SECONDS", 10)

        for attempt in range(1, parsed.max_attempts + 1):
            if self.session_logger is not None:
                self.session_logger.separator(
                    f"PR ATTEMPT {attempt}/{parsed.max_attempts}"
                )
            echo_out(f"orcha: PR attempt {attempt}/{parsed.max_attempts}")
            commit_title = self.repository.commit_dirty_automated_feedback(
                worktree_path, commit_title
            )
            current_state_hash = self.repository.state_hash(worktree_path)
            if current_state_hash != message_state_hash:
                commit_message = self.generate_commit_message(
                    parsed=parsed,
                    base_rev=base_rev,
                    worktree_path=worktree_path,
                )
                self.verify_commit_title(commit_message)
                message_state_hash = self.repository.state_hash(worktree_path)

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

            self.github.ensure_pr(parsed.branch, commit_message, worktree_path)
            pr_title = commit_message.title
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
                commit_message,
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
            step_label="pi CI fix agent",
        )

        return self.bump_after_review_rejected_followup(followup_thinking_level)

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
            step_label="pi rebase fix agent",
        )

        return followup_thinking_level

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

    def verify_commit_title(self, commit_message: CommitMessage) -> None:
        """Verify the generated commit title with cocogitto."""

        print_commit_message(commit_message)
        cog_result = self.runner.run(
            ["cog", "verify", commit_message.title], combine_output=True
        )
        if cog_result.returncode != 0:
            write_collected(cog_result.stdout, stream=sys.stderr)
            abort(cog_result.returncode)

    def run_pi_session(
        self,
        prompt: str | None,
        *,
        cwd: str,
        thinking_level: str,
    ) -> None:
        """Run interactive pi in the worktree, then return to Orcha."""

        pi_args = ["pi"]
        if thinking_level:
            pi_args.extend(["--thinking", thinking_level])
        if prompt:
            pi_args.append(prompt)

        log_step = "pi interactive session"
        if self.session_logger is not None:
            self.session_logger.step_start(log_step, cwd=cwd)
            self.session_logger.event(
                f"pi thinking level: {thinking_level or '(default)'}"
            )

        echo_out(
            "orcha: launching interactive pi session; exit pi to resume review/PR flow"
        )
        pi_result = self.runner.run_interactive(pi_args, cwd=cwd)
        if pi_result.returncode == 0:
            if self.session_logger is not None:
                self.session_logger.step_pass(log_step)
            echo_out("orcha: interactive pi session exited; resuming review/PR flow")
            return

        if self.session_logger is not None:
            self.session_logger.step_fail(log_step, pi_result.returncode)
        write_command_output(pi_result)
        echo_err(
            f"orcha: pi exited with status {pi_result.returncode}; "
            "stopping before review/commit/PR"
        )
        abort(pi_result.returncode)

    def run_pi_prompt(
        self,
        prompt: str,
        *,
        cwd: str,
        thinking_level: str,
        failure_context: str,
        label: str = "pi",
        step_label: str | None = None,
    ) -> None:
        """Run pi with a prompt, preserving Orcha's failure handling."""

        pi_args = ["pi"]
        if thinking_level:
            pi_args.extend(["--thinking", thinking_level])
        pi_args.extend(["-p", prompt])

        log_step = step_label or label
        if self.session_logger is not None:
            self.session_logger.step_start(log_step, cwd=cwd)
            self.session_logger.event(
                f"pi thinking level: {thinking_level or '(default)'}"
            )

        pi_result = self.runner.run(pi_args, cwd=cwd)
        if pi_result.returncode == 0:
            if self.session_logger is not None:
                self.session_logger.step_pass(log_step)
            return

        if self.session_logger is not None:
            self.session_logger.step_fail(log_step, pi_result.returncode)
        write_command_output(pi_result)
        separator = " " if failure_context.startswith("while ") else "; "
        echo_err(
            f"orcha: {label} exited with status {pi_result.returncode}"
            f"{separator}{failure_context}"
        )
        abort(pi_result.returncode)

    def bump_after_review_rejected_followup(self, followup_thinking_level: str) -> str:
        if not self.review_rejected_first_pass or not followup_thinking_level:
            return followup_thinking_level
        bumped_level = bump_thinking(followup_thinking_level)
        if bumped_level != followup_thinking_level:
            echo_out(
                "orcha: review-rejected follow-up completed; next pi thinking bumped to "
                f"{bumped_level}"
            )
        return bumped_level

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
