"""pid worktree automation flow."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from pid.commands import CommandRunner, require_command
from pid.config import PIDConfig
from pid.errors import PIDAbort, abort
from pid.github import Forge
from pid.keepawake import KeepAwake
from pid.messages import parse_commit_message
from pid.models import CommandResult, CommitMessage, OutputMode, ParsedArgs
from pid.output import (
    echo_err,
    echo_out,
    print_commit_message,
    print_merge_success,
    set_session_logger,
    write_collected,
    write_command_output,
)
from pid.parsing import bump_thinking, parse_args
from pid.prompts import (
    build_ci_fix_prompt,
    build_message_prompt,
    build_rebase_fix_prompt,
    build_review_prompt,
)
from pid.repository import Repository, validate_branch_name
from pid.session_logging import SessionLogger
from pid.utils import env_int, has_output, review_target_for, worktree_path_for


class PIDFlow:
    """Implements the pid orchestration lifecycle."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        config: PIDConfig | None = None,
        output_mode: OutputMode = OutputMode.NORMAL,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.runner.set_output_mode(output_mode)
        self.config = config or PIDConfig()
        self.repository = Repository(self.runner)
        self.forge = Forge(self.runner, self.config.forge)
        self.review_rejected_first_pass = False
        self.session_logger: SessionLogger | None = None
        self.keep_awake: KeepAwake | None = None
        self.output_mode = output_mode

    def run(self, argv: list[str]) -> int:
        exit_code = 0
        try:
            self._run(argv)
        except PIDAbort as error:
            exit_code = error.code
        except Exception as error:
            exit_code = 1
            if self.session_logger is not None:
                self.session_logger.event(
                    f"unhandled exception: {type(error).__name__}: {error}"
                )
            raise
        finally:
            if self.keep_awake is not None:
                self.keep_awake.stop()
                self.keep_awake = None
            if self.session_logger is not None:
                self.session_logger.event(f"exit code: {exit_code}")
                self.session_logger.close()
                set_session_logger(None)
                self.runner.set_logger(None)
        return exit_code

    def _run(self, argv: list[str]) -> None:
        parsed = parse_args(
            argv,
            default_thinking=self.config.agent.default_thinking,
            thinking_levels=self.config.agent.thinking_levels,
        )
        self.start_session_logging(argv)
        self.start_keep_awake()
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
                f"pid: main worktree has uncommitted or untracked changes: {main_worktree}"
            )
            abort(1)

        default_branch = self.repository.default_branch(
            main_worktree, fallback=self.forge.default_branch
        )
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

        if self.config.workflow.trust_mise and shutil.which("mise") is not None:
            self.runner.require(["mise", "trust", "."], cwd=worktree_path)

        if parsed.interactive:
            self.run_agent_session(
                parsed.interactive_prompt,
                cwd=worktree_path,
                thinking_level=parsed.thinking_level,
            )
        else:
            self.run_agent_prompt(
                parsed.prompt,
                cwd=worktree_path,
                thinking_level=parsed.thinking_level,
                failure_context="stopping before review/commit/PR",
                step_label=f"{self.config.agent.label} initial",
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
            template=self.config.prompts.review,
        )
        self.run_agent_prompt(
            review_prompt,
            cwd=worktree_path,
            thinking_level=self.config.agent.review_thinking,
            failure_context="stopping before commit/PR",
            label=f"{self.config.agent.label} review",
            step_label=f"{self.config.agent.label} review",
        )

        post_review_state_hash = self.repository.state_hash(worktree_path)
        if pre_review_state_hash != post_review_state_hash:
            self.review_rejected_first_pass = True
            echo_out(
                "pid: review changed first pass; follow-up "
                f"{self.config.agent.label} will keep thinking {followup_thinking_level}"
            )

        post_review_commit_count = self.repository.count_commits(
            base_rev, worktree_path
        )
        post_review_dirty = self.repository.output(
            ["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path
        )
        if post_review_commit_count == 0 and not has_output(post_review_dirty):
            echo_out("pid: no changes or commits after agent; stopping before PR")
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
            echo_err(f"pid: session logging disabled: {error}")
            return

        set_session_logger(self.session_logger)
        self.runner.set_logger(self.session_logger)
        echo_out(f"pid: session log: {self.session_logger.path}")

    def start_keep_awake(self) -> None:
        """Start the optional keep-awake helper for a valid pid run."""

        self.keep_awake = KeepAwake(
            enabled=self.config.runtime.keep_screen_awake,
            logger=self.session_logger,
        )
        self.keep_awake.start()

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
        """Ask the agent to write validated commit/PR metadata."""

        git_dir = self.repository.output(
            ["rev-parse", "--path-format=absolute", "--git-dir"], cwd=worktree_path
        ).strip()
        output_path = Path(git_dir, "pid-message.json")
        output_path.unlink(missing_ok=True)
        pre_message_state_hash = self.repository.state_hash(worktree_path)

        self.run_agent_prompt(
            build_message_prompt(
                original_prompt=parsed.prompt,
                branch=parsed.branch,
                base_rev=base_rev,
                output_path=str(output_path),
                template=self.config.prompts.message,
            ),
            cwd=worktree_path,
            thinking_level=self.config.agent.review_thinking,
            failure_context="stopping before commit/PR",
            label=f"{self.config.agent.label} message",
        )

        post_message_state_hash = self.repository.state_hash(worktree_path)
        if pre_message_state_hash != post_message_state_hash:
            echo_err(
                "pid: agent message changed the worktree; stopping before commit/PR"
            )
            abort(1)
        if not output_path.exists():
            echo_err("pid: agent message did not write commit metadata")
            abort(1)

        return parse_commit_message(output_path.read_text(encoding="utf-8"))

    def require_external_commands(self) -> None:
        """Ensure external CLIs needed for the orchestration flow exist."""

        if self.config.commit.verifier_enabled:
            require_command(
                self.config.commit.executable,
                f"pid: {self.config.commit.executable} is required for "
                "commit message verification",
            )
        require_command(
            self.config.agent.executable,
            f"pid: agent command is required: {self.config.agent.executable}",
        )
        require_command(
            self.config.forge.executable,
            f"pid: {self.config.forge.executable} is required for PR creation",
        )

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
        base_refresh_count = 0
        base_refresh_stage_counts: dict[str, int] = {}
        checks_timeout_seconds = env_int(
            "PID_CHECKS_TIMEOUT_SECONDS", self.config.workflow.checks_timeout_seconds
        )
        checks_poll_interval_seconds = env_int(
            "PID_CHECKS_POLL_INTERVAL_SECONDS",
            self.config.workflow.checks_poll_interval_seconds,
        )
        merge_retry_limit = env_int(
            "PID_MERGE_RETRY_LIMIT", self.config.workflow.merge_retry_limit
        )
        merge_retries = 0
        attempt = 1

        while attempt <= parsed.max_attempts:
            if self.session_logger is not None:
                self.session_logger.separator(
                    f"PR ATTEMPT {attempt}/{parsed.max_attempts}"
                )
            echo_out(f"pid: PR attempt {attempt}/{parsed.max_attempts}")
            commit_title = self.repository.commit_dirty_automated_feedback(
                worktree_path,
                commit_title,
                self.config.commit.automated_feedback_title,
            )
            refresh_result, base_refresh_count = self.refresh_base_if_needed(
                stage="before_message",
                base_refresh_count=base_refresh_count,
                stage_counts=base_refresh_stage_counts,
                default_branch=default_branch,
                original_prompt=parsed.prompt,
                pr_title=commit_message.title,
                pr_body=commit_message.body,
                pr_url=pr_url or "(not opened yet)",
                commit_title=commit_title,
                followup_thinking_level=followup_thinking_level,
                worktree_path=worktree_path,
            )
            if refresh_result in {"limit_reached", "conflict_unresolved"}:
                echo_err(f"pid: base refresh stopped before message: {refresh_result}")
                abort(1)
            refreshed_before_message = refresh_result in {
                "rebased_cleanly",
                "rebased_with_agent_fix",
            }
            if refreshed_before_message:
                need_force_push = True
                commit_title = self.repository.commit_rebase_changes(
                    worktree_path,
                    commit_title,
                    self.config.commit.rebase_feedback_title,
                )
            current_state_hash = self.repository.state_hash(worktree_path)
            if refreshed_before_message or current_state_hash != message_state_hash:
                commit_message = self.generate_commit_message(
                    parsed=parsed,
                    base_rev=base_rev,
                    worktree_path=worktree_path,
                )
                self.verify_commit_title(commit_message)
                message_state_hash = self.repository.state_hash(worktree_path)

            refresh_result, base_refresh_count = self.refresh_base_if_needed(
                stage="before_pr",
                base_refresh_count=base_refresh_count,
                stage_counts=base_refresh_stage_counts,
                default_branch=default_branch,
                original_prompt=parsed.prompt,
                pr_title=commit_message.title,
                pr_body=commit_message.body,
                pr_url=pr_url or "(not opened yet)",
                commit_title=commit_title,
                followup_thinking_level=followup_thinking_level,
                worktree_path=worktree_path,
            )
            if refresh_result in {"limit_reached", "conflict_unresolved"}:
                echo_err(f"pid: base refresh stopped before PR push: {refresh_result}")
                abort(1)
            if refresh_result in {"rebased_cleanly", "rebased_with_agent_fix"}:
                need_force_push = True
                commit_title = self.repository.commit_rebase_changes(
                    worktree_path,
                    commit_title,
                    self.config.commit.rebase_feedback_title,
                )
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

            self.forge.ensure_pr(parsed.branch, commit_message, worktree_path)
            pr_title = commit_message.title
            pr_url = self.forge.pr_url(parsed.branch, worktree_path)

            checks_status, checks_out = self.forge.wait_for_checks(
                parsed.branch,
                checks_timeout_seconds,
                checks_poll_interval_seconds,
                worktree_path,
            )
            if has_output(checks_out) and (
                checks_status != 0 or not self.runner.writes_success_output()
            ):
                write_collected(checks_out, stream=sys.stdout)
            if checks_status != 0:
                if self.forge.output_reports_no_checks(checks_out):
                    echo_out("pid: no CI checks reported; continuing")
                elif attempt >= parsed.max_attempts:
                    echo_err(
                        f"pid: CI checks failed after {attempt} attempts; "
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
                    attempt += 1
                    merge_retries = 0
                    continue

            refresh_result, base_refresh_count = self.refresh_base_if_needed(
                stage="after_checks",
                base_refresh_count=base_refresh_count,
                stage_counts=base_refresh_stage_counts,
                default_branch=default_branch,
                original_prompt=parsed.prompt,
                pr_title=pr_title,
                pr_body=commit_message.body,
                pr_url=pr_url,
                commit_title=commit_title,
                followup_thinking_level=followup_thinking_level,
                worktree_path=worktree_path,
            )
            if refresh_result in {"limit_reached", "conflict_unresolved"}:
                echo_err(f"pid: base refresh stopped after checks: {refresh_result}")
                abort(1)
            if refresh_result in {"rebased_cleanly", "rebased_with_agent_fix"}:
                commit_title = self.repository.commit_rebase_changes(
                    worktree_path,
                    commit_title,
                    self.config.commit.rebase_feedback_title,
                )
                commit_message = self.generate_commit_message(
                    parsed=parsed,
                    base_rev=base_rev,
                    worktree_path=worktree_path,
                )
                self.verify_commit_title(commit_message)
                message_state_hash = self.repository.state_hash(worktree_path)
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
                self.forge.ensure_pr(parsed.branch, commit_message, worktree_path)
                merge_retries = 0
                continue

            pr_head_oid = ""
            if self.config.forge.merge_uses_head_oid:
                pr_head_oid = self.forge.head_oid(parsed.branch, worktree_path)
            merge_result = self.forge.squash_merge(
                parsed.branch,
                pr_head_oid,
                commit_message,
                pr_url,
                worktree_path,
            )
            if has_output(merge_result.stdout) and (
                merge_result.returncode != 0 or not self.runner.writes_success_output()
            ):
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

            if self.forge.reports_merged(pr_url, worktree_path):
                echo_out(
                    f"pid: {self.config.forge.label} reports PR merged despite "
                    "local forge cleanup failure; cleaning up"
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

            merge_retries += 1
            if merge_retries > merge_retry_limit:
                echo_err(
                    f"pid: {self.config.forge.label} squash merge failed after "
                    f"{merge_retry_limit} merge retries; leaving PR open: {pr_url}"
                )
                abort(merge_result.returncode)

            echo_out(
                "pid: merge failed; rebasing onto latest "
                f"origin/{default_branch} before retry "
                f"({merge_retries}/{merge_retry_limit} merge retries; "
                "agent attempts unchanged)"
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
                    original_prompt=parsed.prompt,
                    pr_title=pr_title,
                    pr_body=commit_message.body,
                    pr_url=pr_url,
                    default_branch=default_branch,
                    commit_title=commit_title,
                    merge_out=command_diagnostics(merge_result, rebase_result),
                    followup_thinking_level=followup_thinking_level,
                    worktree_path=worktree_path,
                )

            if self.repository.rebase_in_progress(worktree_path):
                echo_err(
                    "pid: rebase still in progress after agent; "
                    f"leaving PR open: {pr_url}"
                )
                abort(1)

            commit_title = self.repository.commit_rebase_changes(
                worktree_path,
                commit_title,
                self.config.commit.rebase_feedback_title,
            )
            need_force_push = True

        echo_err(
            f"pid: exhausted {parsed.max_attempts} attempts; leaving worktree: {worktree_path}"
        )
        abort(1)

    def refresh_base_if_needed(
        self,
        *,
        stage: str,
        base_refresh_count: int,
        stage_counts: dict[str, int],
        default_branch: str,
        original_prompt: str,
        pr_title: str,
        pr_body: str,
        pr_url: str,
        commit_title: str,
        followup_thinking_level: str,
        worktree_path: str,
    ) -> tuple[str, int]:
        """Refresh branch base at bounded workflow checkpoints."""

        if not self.config.workflow.base_refresh_enabled:
            return "unchanged", base_refresh_count
        if stage not in self.config.workflow.base_refresh_stages:
            return "unchanged", base_refresh_count
        if stage_counts.get(stage, 0) >= 1:
            return "unchanged", base_refresh_count

        self.runner.require(
            ["git", "fetch", "origin", default_branch], cwd=worktree_path
        )
        if self.repository.contains_ref(worktree_path, f"origin/{default_branch}"):
            if self.session_logger is not None:
                self.session_logger.event(f"base refresh {stage}: unchanged")
            return "unchanged", base_refresh_count
        if base_refresh_count >= self.config.workflow.base_refresh_limit:
            echo_err(
                "pid: base refresh limit reached; leaving PR/worktree for manual refresh"
            )
            return "limit_reached", base_refresh_count

        base_refresh_count += 1
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        message = (
            f"pid: {stage} base moved; rebasing onto origin/{default_branch} "
            f"({base_refresh_count}/{self.config.workflow.base_refresh_limit})"
        )
        echo_out(message)
        if self.session_logger is not None:
            self.session_logger.event(message)

        rebase_result = self.runner.run(
            ["git", "rebase", f"origin/{default_branch}"], cwd=worktree_path
        )
        if rebase_result.returncode == 0:
            return "rebased_cleanly", base_refresh_count

        write_command_output(rebase_result)
        if not self.config.workflow.base_refresh_agent_conflict_fix:
            echo_err("pid: base refresh rebase conflicted; leaving worktree")
            return "conflict_unresolved", base_refresh_count

        self.fix_rebase(
            original_prompt=original_prompt,
            pr_title=pr_title,
            pr_body=pr_body,
            pr_url=pr_url,
            default_branch=default_branch,
            commit_title=commit_title,
            merge_out=command_diagnostic(rebase_result),
            followup_thinking_level=followup_thinking_level,
            worktree_path=worktree_path,
        )
        if self.repository.rebase_in_progress(worktree_path):
            echo_err("pid: base refresh rebase still in progress after agent")
            return "conflict_unresolved", base_refresh_count
        return "rebased_with_agent_fix", base_refresh_count

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
            template=self.config.prompts.ci_fix,
            diagnostic_output_limit=self.config.prompts.diagnostic_output_limit,
        )
        self.run_agent_prompt(
            prompt,
            cwd=worktree_path,
            thinking_level=followup_thinking_level,
            failure_context="while fixing CI",
            step_label=f"{self.config.agent.label} CI fix",
        )

        return self.bump_after_review_rejected_followup(followup_thinking_level)

    def fix_rebase(
        self,
        *,
        original_prompt: str,
        pr_title: str,
        pr_body: str,
        pr_url: str,
        default_branch: str,
        commit_title: str,
        merge_out: str,
        followup_thinking_level: str,
        worktree_path: str,
    ) -> str:
        prompt = build_rebase_fix_prompt(
            original_prompt=original_prompt,
            pr_title=pr_title,
            pr_body=pr_body,
            pr_url=pr_url,
            default_branch=default_branch,
            commit_title=commit_title,
            merge_out=merge_out,
            forge_label=self.config.forge.label,
            template=self.config.prompts.rebase_fix,
            diagnostic_output_limit=self.config.prompts.diagnostic_output_limit,
        )
        self.run_agent_prompt(
            prompt,
            cwd=worktree_path,
            thinking_level=followup_thinking_level,
            failure_context="while resolving rebase",
            step_label=f"{self.config.agent.label} rebase fix",
        )

        return followup_thinking_level

    def resolve_repo_root(self) -> str:
        """Return the current git repository root or abort with pid's message."""

        return self.require_git_output(
            ["rev-parse", "--show-toplevel"],
            error_message="pid: not inside a git repository",
        )

    def resolve_main_worktree(self) -> str:
        """Return the main worktree path from git's common directory."""

        common_git_dir = self.require_git_output(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            error_message="pid: could not determine common git dir",
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
        """Verify the generated commit title with the configured verifier."""

        print_commit_message(commit_message)
        if not self.config.commit.verifier_enabled:
            return
        verify_result = self.runner.run(
            self.config.commit.verifier_command_line(title=commit_message.title),
            combine_output=True,
        )
        if verify_result.returncode != 0:
            write_collected(verify_result.stdout, stream=sys.stderr)
            abort(verify_result.returncode)

    def run_agent_session(
        self,
        prompt: str | None,
        *,
        cwd: str,
        thinking_level: str,
    ) -> None:
        """Run the configured agent interactively, then return to pid."""

        agent_args = self.config.agent.interactive_command(
            prompt=prompt, thinking=thinking_level
        )
        log_step = f"{self.config.agent.label} interactive session"
        if self.session_logger is not None:
            self.session_logger.step_start(log_step, cwd=cwd)
            self.session_logger.event(
                f"{self.config.agent.label} thinking level: "
                f"{thinking_level or '(default)'}"
            )

        echo_out(
            "pid: launching interactive agent session; "
            "exit agent to resume review/PR flow"
        )
        agent_result = self.runner.run_interactive(agent_args, cwd=cwd)
        if agent_result.returncode == 0:
            if self.session_logger is not None:
                self.session_logger.step_pass(log_step)
            echo_out("pid: interactive agent session exited; resuming review/PR flow")
            return

        if self.session_logger is not None:
            self.session_logger.step_fail(log_step, agent_result.returncode)
        write_command_output(agent_result)
        echo_err(
            f"pid: {self.config.agent.label} exited with status "
            f"{agent_result.returncode}; stopping before review/commit/PR"
        )
        abort(agent_result.returncode)

    def run_agent_prompt(
        self,
        prompt: str,
        *,
        cwd: str,
        thinking_level: str,
        failure_context: str,
        label: str | None = None,
        step_label: str | None = None,
    ) -> None:
        """Run configured agent with a prompt, preserving failure handling."""

        agent_args = self.config.agent.non_interactive_command(
            prompt=prompt, thinking=thinking_level
        )
        display_label = label or self.config.agent.label
        log_step = step_label or display_label
        if self.session_logger is not None:
            self.session_logger.step_start(log_step, cwd=cwd)
            self.session_logger.event(
                f"{self.config.agent.label} thinking level: "
                f"{thinking_level or '(default)'}"
            )

        agent_result = self.runner.run(agent_args, cwd=cwd)
        if agent_result.returncode == 0:
            if self.session_logger is not None:
                self.session_logger.step_pass(log_step)
            self.write_agent_success_output(agent_result)
            echo_out(f"pid: {log_step} finished")
            return

        if self.session_logger is not None:
            self.session_logger.step_fail(log_step, agent_result.returncode)
        write_command_output(agent_result)
        separator = " " if failure_context.startswith("while ") else "; "
        echo_err(
            f"pid: {display_label} exited with status "
            f"{agent_result.returncode}{separator}{failure_context}"
        )
        abort(agent_result.returncode)

    def write_agent_success_output(self, result: CommandResult) -> None:
        """Show useful successful agent output without flooding normal runs."""

        if self.output_mode == OutputMode.ALL:
            return
        if has_output(result.stdout):
            write_collected(result.stdout, stream=sys.stdout)
        if self.output_mode == OutputMode.AGENT and has_output(result.stderr):
            write_collected(result.stderr, stream=sys.stderr)

    def bump_after_review_rejected_followup(self, followup_thinking_level: str) -> str:
        if not self.review_rejected_first_pass or not followup_thinking_level:
            return followup_thinking_level
        bumped_level = bump_thinking(
            followup_thinking_level, self.config.agent.thinking_levels
        )
        if bumped_level != followup_thinking_level:
            echo_out(
                "pid: review-rejected follow-up completed; next "
                f"{self.config.agent.label} thinking bumped to {bumped_level}"
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
        if not self.wait_for_confirmed_merge(
            pr_url=pr_url, worktree_path=worktree_path
        ):
            abort(1)

        self.cleanup_and_print_success(
            pr_url=pr_url,
            pr_title=pr_title,
            main_worktree=main_worktree,
            default_branch=default_branch,
            branch=branch,
            worktree_path=worktree_path,
        )

    def wait_for_confirmed_merge(self, *, pr_url: str, worktree_path: str) -> bool:
        """Wait until the forge reports a successful merge is actually merged."""

        timeout_seconds = max(
            0,
            env_int(
                "PID_MERGE_CONFIRMATION_TIMEOUT_SECONDS",
                self.config.workflow.merge_confirmation_timeout_seconds,
            ),
        )
        poll_interval_seconds = max(
            0,
            env_int(
                "PID_MERGE_CONFIRMATION_POLL_INTERVAL_SECONDS",
                self.config.workflow.merge_confirmation_poll_interval_seconds,
            ),
        )
        deadline = time.monotonic() + timeout_seconds
        announced_wait = False

        while True:
            merged_at_result = self.forge.merged_at(pr_url, worktree_path)
            if merged_at_result.returncode != 0:
                echo_err(
                    "pid: merge command succeeded, but merged state could not be "
                    f"confirmed; leaving PR/worktree for manual cleanup: {pr_url}"
                )
                return False
            if merged_at_result.stdout.strip():
                return True
            if timeout_seconds <= 0 or time.monotonic() >= deadline:
                echo_err(
                    "pid: merge command succeeded, but PR was not confirmed "
                    f"merged after {timeout_seconds} seconds; leaving PR/worktree: "
                    f"{pr_url}"
                )
                return False

            if not announced_wait:
                echo_out(
                    "pid: merge command succeeded, but PR is not merged yet; "
                    "waiting up to "
                    f"{timeout_seconds} seconds for merge confirmation"
                )
                announced_wait = True

            sleep_seconds = min(
                poll_interval_seconds if poll_interval_seconds > 0 else 0.1,
                max(0.0, deadline - time.monotonic()),
            )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

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
            [
                "git",
                "-C",
                main_worktree,
                "worktree",
                "remove",
                "--force",
                worktree_path,
            ]
        )
        self.runner.require(["git", "-C", main_worktree, "branch", "-D", branch])
        print_merge_success(pr_title, pr_url, self.config.forge.label)


def command_diagnostics(*results: CommandResult) -> str:
    """Return command outputs suitable for an agent diagnostic block."""

    diagnostics = [command_diagnostic(result) for result in results]
    return "\n".join(diagnostic for diagnostic in diagnostics if diagnostic)


def command_diagnostic(result: CommandResult) -> str:
    if result.stdout and result.stderr and not result.stdout.endswith("\n"):
        return f"{result.stdout}\n{result.stderr}"
    return result.stdout + result.stderr


def run_pid(
    argv: list[str],
    *,
    config: PIDConfig | None = None,
    output_mode: OutputMode = OutputMode.NORMAL,
) -> int:
    """Run the pid flow and return a process exit code."""

    return PIDFlow(config=config, output_mode=output_mode).run(argv)
