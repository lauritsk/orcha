"""pid worktree automation flow."""

from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pid.commands import CommandRunner, require_command
from pid.config import PIDConfig
from pid.context import PRLoopState, WorkflowContext
from pid.errors import PIDAbort, abort
from pid.events import EventSink, NullEventSink
from pid.extensions import (
    ExtensionError,
    ExtensionRegistry,
    StepResult,
    WorkflowStep,
    abort_extension_error,
    load_enabled_extensions,
    normalize_step_result,
)
from pid.github import Forge
from pid.keepawake import KeepAwake
from pid.messages import parse_commit_message
from pid.models import CommandResult, CommitMessage, OutputMode, ParsedArgs
from pid.output import (
    echo_err,
    echo_out,
    print_attempt_header,
    print_commit_message,
    print_merge_success,
    print_phase,
    print_run_summary,
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

REFRESH_STOP_RESULTS = {"limit_reached", "conflict_unresolved"}
REFRESH_REBASE_RESULTS = {"rebased_cleanly", "rebased_with_agent_fix"}


class PIDFlow:
    """Implements the pid orchestration lifecycle."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        config: PIDConfig | None = None,
        output_mode: OutputMode = OutputMode.NORMAL,
        registry: ExtensionRegistry | None = None,
        events: EventSink | None = None,
        load_extensions: bool = True,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.runner.set_output_mode(output_mode)
        self.config = config or PIDConfig()
        self.registry = registry or ExtensionRegistry()
        if load_extensions:
            try:
                load_enabled_extensions(
                    self.config.extensions,
                    self.registry,
                    include_local=False,
                    fail_missing=False,
                )
            except ExtensionError as error:
                abort_extension_error(error)
        self.events = events or NullEventSink()
        self.repository = Repository(self.runner)
        self.forge = Forge(self.runner, self.config.forge)
        self.review_rejected_first_pass = False
        self.session_logger: SessionLogger | None = None
        self.keep_awake: KeepAwake | None = None
        self.output_mode = output_mode
        self.context: WorkflowContext | None = None

    def run(self, argv: list[str]) -> int:
        exit_code = 0
        try:
            self._run(argv)
        except PIDAbort as error:
            exit_code = error.code
        except ExtensionError as error:
            exit_code = 2
            echo_err(f"pid: {error}")
            if self.session_logger is not None:
                self.session_logger.event(f"extension error: {error}")
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
        ctx = WorkflowContext(
            argv=argv,
            config=self.config,
            runner=self.runner,
            repository=self.repository,
            forge=self.forge,
            registry=self.registry,
            output_mode=self.output_mode,
            events=self.events,
        )
        self.context = ctx
        ctx.emit("workflow.created")
        try:
            bootstrap_steps = self.bootstrap_steps()
            for step in bootstrap_steps:
                self.run_workflow_step(ctx, step)
            self.load_project_extensions(ctx)
            self.apply_service_replacements(ctx)
            for step in self.registry.resolve_steps(
                self.default_steps(),
                known_steps=(step.name for step in bootstrap_steps),
                external_steps=self.pr_loop_step_names(),
            ):
                self.run_workflow_step(ctx, step)
            ctx.emit("workflow.completed")
        except Exception as error:
            ctx.emit(
                "workflow.failed",
                level="error",
                fields={"error": f"{type(error).__name__}: {error}"},
            )
            raise

    def bootstrap_steps(self) -> list[WorkflowStep]:
        """Return fixed pre-extension steps needed to find project extensions."""

        return [
            WorkflowStep("parse_args", self.step_parse_args),
            WorkflowStep("start_session_logging", self.step_start_session_logging),
            WorkflowStep("start_keep_awake", self.step_start_keep_awake),
            WorkflowStep("render_run_summary", self.step_render_run_summary),
            WorkflowStep("validate_branch", self.step_validate_branch),
            WorkflowStep("resolve_repo_root", self.step_resolve_repo_root),
        ]

    def default_steps(self) -> list[WorkflowStep]:
        """Return extension-aware steps after repository resolution."""

        return [
            WorkflowStep("require_commands", self.step_require_commands),
            WorkflowStep("resolve_main_worktree", self.step_resolve_main_worktree),
            WorkflowStep(
                "validate_clean_main_worktree",
                self.step_validate_clean_main_worktree,
            ),
            WorkflowStep("resolve_default_branch", self.step_resolve_default_branch),
            WorkflowStep("update_default_branch", self.step_update_default_branch),
            WorkflowStep("capture_base_rev", self.step_capture_base_rev),
            WorkflowStep("create_worktree", self.step_create_worktree),
            WorkflowStep("trust_mise", self.step_trust_mise),
            WorkflowStep("run_initial_agent", self.step_run_initial_agent),
            WorkflowStep("inspect_initial_changes", self.step_inspect_initial_changes),
            WorkflowStep("run_review_agent", self.step_run_review_agent),
            WorkflowStep("inspect_review_changes", self.step_inspect_review_changes),
            WorkflowStep("stop_if_no_changes", self.step_stop_if_no_changes),
            WorkflowStep("generate_message", self.step_generate_message),
            WorkflowStep("verify_commit_title", self.step_verify_commit_title),
            WorkflowStep("commit_changes", self.step_commit_changes),
            WorkflowStep("run_pr_loop", self.step_run_pr_loop),
        ]

    def default_pr_loop_steps(self) -> list[WorkflowStep]:
        """Return extension-aware PR-loop substeps."""

        return [
            WorkflowStep("pr_prepare_attempt", self.step_pr_prepare_attempt),
            WorkflowStep(
                "pr_refresh_base_before_message",
                self.step_pr_refresh_base_before_message,
            ),
            WorkflowStep("pr_regenerate_message", self.step_pr_regenerate_message),
            WorkflowStep(
                "pr_refresh_base_before_pr", self.step_pr_refresh_base_before_pr
            ),
            WorkflowStep("pr_push_branch", self.step_pr_push_branch),
            WorkflowStep("pr_ensure_pr", self.step_pr_ensure_pr),
            WorkflowStep("pr_wait_for_checks", self.step_pr_wait_for_checks),
            WorkflowStep("pr_handle_checks", self.step_pr_handle_checks),
            WorkflowStep(
                "pr_refresh_base_after_checks",
                self.step_pr_refresh_base_after_checks,
            ),
            WorkflowStep("pr_squash_merge", self.step_pr_squash_merge),
            WorkflowStep("pr_recover_merge", self.step_pr_recover_merge),
            WorkflowStep("pr_confirm_merge", self.step_pr_confirm_merge),
            WorkflowStep("pr_cleanup", self.step_pr_cleanup),
        ]

    def pr_loop_step_names(self) -> tuple[str, ...]:
        """Return known PR-loop substep names for extension validation."""

        return tuple(step.name for step in self.default_pr_loop_steps())

    def run_workflow_step(self, ctx: WorkflowContext, step: WorkflowStep) -> None:
        """Run one step with hooks, events, replacements, and bounded retry."""

        if step.name in self.registry.disabled_steps:
            return
        step = self.registry.replaced_steps.get(step.name, step)
        retries = 0
        while True:
            ctx.emit("step.started", step=step.name)
            before_result = self.registry.run_hooks(f"before.{step.name}", ctx)
            if before_result.action == "skip":
                ctx.emit("step.skipped", step=step.name, message=before_result.reason)
                return
            self.handle_step_result(before_result)
            try:
                result = normalize_step_result(step.run(ctx))
            except Exception as error:
                ctx.emit(
                    "step.failed",
                    step=step.name,
                    level="error",
                    fields={"error": f"{type(error).__name__}: {error}"},
                )
                error_result = self.registry.run_hooks(f"error.{step.name}", ctx)
                if error_result.action != "continue":
                    self.handle_step_result(error_result)
                raise
            after_result = self.registry.run_hooks(f"after.{step.name}", ctx)
            if after_result.action != "continue":
                result = after_result
            if result.action == "retry":
                retries += 1
                if retries > 3:
                    echo_err(f"pid: step retry limit reached: {step.name}")
                    abort(1)
                ctx.emit("step.retrying", step=step.name, message=result.reason)
                continue
            self.handle_step_result(result)
            ctx.emit("step.completed", step=step.name)
            return

    @staticmethod
    def handle_step_result(result: StepResult) -> None:
        """Apply a step or hook result."""

        if result.action == "continue" or result.action == "skip":
            return
        if result.action == "stop":
            abort(result.code)
        if result.action == "retry":
            return
        raise ExtensionError(f"unknown step result action: {result.action}")

    def load_project_extensions(self, ctx: WorkflowContext) -> None:
        """Load configured project-local extensions after repo resolution."""

        load_enabled_extensions(
            self.config.extensions,
            self.registry,
            repo_root=Path(ctx.repo_root),
            include_entry_points=False,
            include_local=True,
            fail_missing=True,
        )

    def apply_service_replacements(self, ctx: WorkflowContext) -> None:
        """Apply extension-provided service replacements before preflight."""

        for name, factory in self.registry.service_factories.items():
            service = factory(ctx)
            ctx.services[name] = service
            if name == "runner":
                self.runner = service
                self.runner.set_output_mode(self.output_mode)
                if self.session_logger is not None:
                    self.runner.set_logger(self.session_logger)
                ctx.runner = service
            elif name == "repository":
                self.repository = service
                ctx.repository = service
            elif name == "forge":
                self.forge = service
                ctx.forge = service

    def step_parse_args(self, ctx: WorkflowContext) -> None:
        parsed = parse_args(
            ctx.argv,
            default_thinking=self.config.agent.default_thinking,
            thinking_levels=self.config.agent.thinking_levels,
        )
        ctx.parsed = parsed
        ctx.followup_thinking_level = parsed.thinking_level

    def step_start_session_logging(self, ctx: WorkflowContext) -> None:
        self.start_session_logging(ctx.argv)
        ctx.session_logger = self.session_logger

    def step_start_keep_awake(self, ctx: WorkflowContext) -> None:
        self.start_keep_awake()
        ctx.keep_awake = self.keep_awake

    def step_render_run_summary(self, ctx: WorkflowContext) -> None:
        parsed = ctx.require_parsed()
        self.log_parsed_args(parsed)
        print_run_summary(
            parsed,
            agent_label=self.config.agent.label,
            forge_label=self.config.forge.label,
            output_mode=self.output_mode,
        )
        print_phase("Prepare", "validate repo, branch, tools")

    def step_validate_branch(self, ctx: WorkflowContext) -> None:
        validate_branch_name(self.runner, ctx.require_parsed().branch)

    def step_resolve_repo_root(self, ctx: WorkflowContext) -> None:
        ctx.repo_root = self.resolve_repo_root()

    def step_require_commands(self, ctx: WorkflowContext) -> None:
        _ = ctx
        self.require_external_commands()

    def step_resolve_main_worktree(self, ctx: WorkflowContext) -> None:
        ctx.main_worktree = self.resolve_main_worktree()

    def step_validate_clean_main_worktree(self, ctx: WorkflowContext) -> None:
        main_dirty = self.repository.output(
            ["status", "--porcelain", "--untracked-files=all"],
            cwd=ctx.main_worktree,
        )
        if has_output(main_dirty):
            echo_err(
                "pid: main worktree has uncommitted or untracked changes: "
                f"{ctx.main_worktree}"
            )
            abort(1)

    def step_resolve_default_branch(self, ctx: WorkflowContext) -> None:
        ctx.default_branch = self.repository.default_branch(
            ctx.main_worktree, fallback=self.forge.default_branch
        )

    def step_update_default_branch(self, ctx: WorkflowContext) -> None:
        self.repository.switch_and_update_default_branch(
            ctx.main_worktree, ctx.default_branch
        )

    def step_capture_base_rev(self, ctx: WorkflowContext) -> None:
        ctx.base_rev = self.repository.output(
            ["rev-parse", "HEAD"], cwd=ctx.main_worktree
        ).strip()

    def step_create_worktree(self, ctx: WorkflowContext) -> None:
        parsed = ctx.require_parsed()
        ctx.worktree_path = worktree_path_for(ctx.repo_root, parsed.branch)
        self.repository.guard_new_worktree(
            ctx.main_worktree, parsed.branch, ctx.worktree_path
        )
        self.repository.create_worktree(
            ctx.main_worktree, ctx.worktree_path, parsed.branch, ctx.base_rev
        )
        echo_out(f"Created {ctx.worktree_path} on branch {parsed.branch}")

    def step_trust_mise(self, ctx: WorkflowContext) -> None:
        if self.config.workflow.trust_mise and shutil.which("mise") is not None:
            self.runner.require(["mise", "trust", "."], cwd=ctx.worktree_path)

    def step_run_initial_agent(self, ctx: WorkflowContext) -> None:
        parsed = ctx.require_parsed()
        print_phase("Agent", "create initial changes")
        if parsed.interactive:
            self.run_agent_session(
                parsed.interactive_prompt,
                cwd=ctx.worktree_path,
                thinking_level=parsed.thinking_level,
            )
            return
        self.run_agent_prompt(
            parsed.prompt,
            cwd=ctx.worktree_path,
            thinking_level=parsed.thinking_level,
            failure_context="stopping before review/commit/PR",
            step_label=f"{self.config.agent.label} initial",
        )

    def step_inspect_initial_changes(self, ctx: WorkflowContext) -> None:
        ctx.initial_commit_count = self.repository.count_commits(
            ctx.base_rev, ctx.worktree_path
        )
        ctx.initial_dirty = self.repository.output(
            ["status", "--porcelain", "--untracked-files=all"],
            cwd=ctx.worktree_path,
        )
        ctx.pre_review_state_hash = self.repository.state_hash(ctx.worktree_path)

    def step_run_review_agent(self, ctx: WorkflowContext) -> None:
        parsed = ctx.require_parsed()
        review_target = review_target_for(
            ctx.base_rev,
            ctx.initial_commit_count,
            has_output(ctx.initial_dirty),
        )
        print_phase("Review", review_target.replace("_", " "))
        review_prompt = build_review_prompt(
            original_prompt=parsed.prompt,
            review_target=review_target,
            template=self.config.prompts.review,
        )
        self.run_agent_prompt(
            review_prompt,
            cwd=ctx.worktree_path,
            thinking_level=self.config.agent.review_thinking,
            failure_context="stopping before commit/PR",
            label=f"{self.config.agent.label} review",
            step_label=f"{self.config.agent.label} review",
        )

    def step_inspect_review_changes(self, ctx: WorkflowContext) -> None:
        post_review_state_hash = self.repository.state_hash(ctx.worktree_path)
        if ctx.pre_review_state_hash != post_review_state_hash:
            self.review_rejected_first_pass = True
            ctx.review_rejected_first_pass = True
            echo_out(
                "pid: review changed first pass; follow-up "
                f"{self.config.agent.label} will keep thinking "
                f"{ctx.followup_thinking_level}"
            )

        ctx.post_review_commit_count = self.repository.count_commits(
            ctx.base_rev, ctx.worktree_path
        )
        ctx.post_review_dirty = self.repository.output(
            ["status", "--porcelain", "--untracked-files=all"],
            cwd=ctx.worktree_path,
        )

    def step_stop_if_no_changes(self, ctx: WorkflowContext) -> None:
        if ctx.post_review_commit_count == 0 and not has_output(ctx.post_review_dirty):
            echo_out("pid: no changes or commits after agent; stopping before PR")
            abort(0)

    def step_generate_message(self, ctx: WorkflowContext) -> None:
        print_phase("Message + commit", "generate metadata and create commit")
        commit_message = self.generate_commit_message(
            parsed=ctx.require_parsed(),
            base_rev=ctx.base_rev,
            worktree_path=ctx.worktree_path,
        )
        ctx.set_commit_message(commit_message)

    def step_verify_commit_title(self, ctx: WorkflowContext) -> None:
        if ctx.commit_message is None:
            raise RuntimeError("commit message has not been generated")
        self.verify_commit_title(ctx.commit_message)

    def step_commit_changes(self, ctx: WorkflowContext) -> None:
        if ctx.commit_message is None:
            raise RuntimeError("commit message has not been generated")
        pre_commit_head = self.repository.output(
            ["rev-parse", "HEAD"], cwd=ctx.worktree_path
        ).strip()
        self.repository.commit_initial_changes(
            ctx.base_rev, ctx.worktree_path, ctx.commit_message
        )
        post_commit_head = self.repository.output(
            ["rev-parse", "HEAD"], cwd=ctx.worktree_path
        ).strip()
        ctx.rewritten_head = (
            pre_commit_head if pre_commit_head != post_commit_head else ""
        )
        ctx.commit_title = self.repository.output(
            ["log", "-1", "--format=%s"], cwd=ctx.worktree_path
        ).strip()

    def step_run_pr_loop(self, ctx: WorkflowContext) -> None:
        if ctx.commit_message is None:
            raise RuntimeError("commit message has not been generated")
        self.run_pr_loop(ctx)

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

    def run_pr_loop(self, ctx: WorkflowContext) -> None:
        """Run the extension-aware PR attempt loop."""

        parsed = ctx.require_parsed()
        ctx.pr_loop = PRLoopState(
            message_state_hash=self.repository.state_hash(ctx.worktree_path),
            checks_timeout_seconds=env_int(
                "PID_CHECKS_TIMEOUT_SECONDS",
                self.config.workflow.checks_timeout_seconds,
            ),
            checks_poll_interval_seconds=env_int(
                "PID_CHECKS_POLL_INTERVAL_SECONDS",
                self.config.workflow.checks_poll_interval_seconds,
            ),
            merge_retry_limit=env_int(
                "PID_MERGE_RETRY_LIMIT", self.config.workflow.merge_retry_limit
            ),
        )
        ctx.base_refresh_count = 0
        ctx.base_refresh_stage_counts = {}
        ctx.merge_retries = 0
        ctx.attempt = 1

        pr_loop_steps = self.registry.resolve_steps(
            self.default_pr_loop_steps(),
            external_steps=(
                *(step.name for step in self.bootstrap_steps()),
                *(step.name for step in self.default_steps()),
            ),
            include_unanchored=False,
        )

        while ctx.attempt <= parsed.max_attempts:
            ctx.pr_loop.next_iteration = False
            ctx.pr_loop.merge_result = None
            ctx.pr_loop.pr_head_oid = ""
            ctx.pr_loop.merge_confirmed = False
            ctx.pr_loop.refreshed_before_message = False
            ctx.pr_loop.refresh_stage = ""
            ctx.pr_loop.refresh_result = ""
            ctx.checks_status = 0
            ctx.checks_output = ""
            for step in pr_loop_steps:
                self.run_workflow_step(ctx, step)
                if ctx.pr_loop.completed:
                    return
                if ctx.pr_loop.next_iteration:
                    break
            if ctx.pr_loop.completed:
                return
            if ctx.pr_loop.merge_confirmed:
                ctx.pr_loop.completed = True
                return
            if ctx.pr_loop.next_iteration:
                continue
            raise ExtensionError(
                "PR loop did not complete or request another iteration; "
                "PR-loop step replacements must set ctx.pr_loop.completed or "
                "ctx.pr_loop.next_iteration, or leave terminal substeps enabled"
            )

        echo_err(
            "pid: exhausted "
            f"{parsed.max_attempts} attempts; leaving worktree: {ctx.worktree_path}"
        )
        abort(1)

    def run_policy(
        self,
        ctx: WorkflowContext,
        name: str,
        default: Callable[[WorkflowContext], StepResult | None],
    ) -> None:
        """Run a replaceable policy callback."""

        policy = ctx.registry.policies.get(name, default)
        handler = policy if callable(policy) else getattr(policy, "run", None)
        if not callable(handler):
            raise ExtensionError(f"policy {name} must be callable or expose run(ctx)")
        try:
            result = normalize_step_result(handler(ctx))
        except ExtensionError, PIDAbort:
            raise
        except Exception as error:
            raise ExtensionError(
                f"policy {name} failed: {type(error).__name__}: {error}"
            ) from error
        self.handle_step_result(result)

    def step_pr_prepare_attempt(self, ctx: WorkflowContext) -> None:
        parsed = ctx.require_parsed()
        if self.session_logger is not None:
            self.session_logger.separator(
                f"PR ATTEMPT {ctx.attempt}/{parsed.max_attempts}"
            )
        print_attempt_header(ctx.attempt, parsed.max_attempts)
        echo_out(f"pid: PR attempt {ctx.attempt}/{parsed.max_attempts}")
        ctx.commit_title = self.repository.commit_dirty_automated_feedback(
            ctx.worktree_path,
            ctx.commit_title,
            self.config.commit.automated_feedback_title,
        )

    def step_pr_refresh_base_before_message(self, ctx: WorkflowContext) -> None:
        self.run_base_refresh_step(ctx, "before_message", "before message")
        ctx.pr_loop.refreshed_before_message = (
            ctx.pr_loop.refresh_result in REFRESH_REBASE_RESULTS
        )
        if ctx.pr_loop.refreshed_before_message:
            ctx.pr_loop.need_force_push = True
            ctx.commit_title = self.repository.commit_rebase_changes(
                ctx.worktree_path,
                ctx.commit_title,
                self.config.commit.rebase_feedback_title,
            )

    def step_pr_regenerate_message(self, ctx: WorkflowContext) -> None:
        current_state_hash = self.repository.state_hash(ctx.worktree_path)
        if (
            not ctx.pr_loop.refreshed_before_message
            and current_state_hash == ctx.pr_loop.message_state_hash
        ):
            return
        self.regenerate_context_message(ctx)

    def step_pr_refresh_base_before_pr(self, ctx: WorkflowContext) -> None:
        self.run_base_refresh_step(ctx, "before_pr", "before PR push")
        if ctx.pr_loop.refresh_result not in REFRESH_REBASE_RESULTS:
            return
        ctx.pr_loop.need_force_push = True
        ctx.commit_title = self.repository.commit_rebase_changes(
            ctx.worktree_path,
            ctx.commit_title,
            self.config.commit.rebase_feedback_title,
        )
        self.regenerate_context_message(ctx)

    def step_pr_push_branch(self, ctx: WorkflowContext) -> None:
        self.run_policy(ctx, "pr.push", self.policy_pr_push_branch)
        ctx.pr_loop.need_force_push = False

    def step_pr_ensure_pr(self, ctx: WorkflowContext) -> None:
        self.run_policy(ctx, "pr.ensure_pr", self.policy_pr_ensure_pr)

    def step_pr_wait_for_checks(self, ctx: WorkflowContext) -> None:
        self.run_policy(ctx, "pr.checks", self.policy_pr_checks)

    def step_pr_handle_checks(self, ctx: WorkflowContext) -> None:
        parsed = ctx.require_parsed()
        if ctx.checks_status == 0:
            return
        if self.forge.output_reports_no_checks(ctx.checks_output):
            echo_out("pid: no CI checks reported; continuing")
            return
        if ctx.attempt >= parsed.max_attempts:
            echo_err(
                f"pid: CI checks failed after {ctx.attempt} attempts; "
                f"leaving PR open: {ctx.pr_url}"
            )
            abort(ctx.checks_status)
        self.run_policy(ctx, "pr.ci_fix", self.policy_pr_ci_fix)
        ctx.attempt += 1
        ctx.merge_retries = 0
        ctx.pr_loop.next_iteration = True

    def step_pr_refresh_base_after_checks(self, ctx: WorkflowContext) -> None:
        self.run_base_refresh_step(ctx, "after_checks", "after checks")
        if ctx.pr_loop.refresh_result not in REFRESH_REBASE_RESULTS:
            return
        ctx.commit_title = self.repository.commit_rebase_changes(
            ctx.worktree_path,
            ctx.commit_title,
            self.config.commit.rebase_feedback_title,
        )
        self.regenerate_context_message(ctx)
        ctx.pr_loop.need_force_push = True
        self.run_workflow_step(
            ctx, WorkflowStep("pr_push_branch", self.step_pr_push_branch)
        )
        self.run_workflow_step(
            ctx, WorkflowStep("pr_ensure_pr", self.step_pr_ensure_pr)
        )
        ctx.merge_retries = 0
        ctx.pr_loop.next_iteration = True

    def step_pr_squash_merge(self, ctx: WorkflowContext) -> None:
        self.run_policy(ctx, "pr.merge", self.policy_pr_merge)

    def step_pr_recover_merge(self, ctx: WorkflowContext) -> None:
        merge_result = ctx.pr_loop.merge_result
        if merge_result is None or merge_result.returncode == 0:
            return
        self.run_policy(ctx, "pr.merge_recovery", self.policy_pr_merge_recovery)

    def step_pr_confirm_merge(self, ctx: WorkflowContext) -> None:
        if ctx.pr_loop.merge_confirmed:
            return
        merge_result = ctx.pr_loop.merge_result
        if merge_result is None or merge_result.returncode != 0:
            return
        self.run_policy(
            ctx,
            "pr.merge_confirmation",
            self.policy_pr_merge_confirmation,
        )

    def step_pr_cleanup(self, ctx: WorkflowContext) -> None:
        if not ctx.pr_loop.merge_confirmed:
            return
        self.run_policy(ctx, "pr.cleanup", self.policy_pr_cleanup)
        ctx.pr_loop.completed = True

    def run_base_refresh_step(
        self, ctx: WorkflowContext, stage: str, stopped_label: str
    ) -> None:
        ctx.pr_loop.refresh_stage = stage
        ctx.pr_loop.refresh_result = "unchanged"
        self.run_policy(ctx, "pr.base_refresh", self.policy_pr_base_refresh)
        self.abort_on_stopped_base_refresh(ctx.pr_loop.refresh_result, stopped_label)

    def regenerate_context_message(self, ctx: WorkflowContext) -> None:
        commit_message, message_state_hash = self.regenerate_commit_message(
            parsed=ctx.require_parsed(),
            base_rev=ctx.base_rev,
            worktree_path=ctx.worktree_path,
        )
        ctx.set_commit_message(commit_message)
        ctx.pr_loop.message_state_hash = message_state_hash

    def policy_pr_base_refresh(self, ctx: WorkflowContext) -> None:
        if ctx.commit_message is None:
            raise RuntimeError("commit message has not been generated")
        refresh_result, ctx.base_refresh_count = self.refresh_base_if_needed(
            stage=ctx.pr_loop.refresh_stage,
            base_refresh_count=ctx.base_refresh_count,
            stage_counts=ctx.base_refresh_stage_counts,
            default_branch=ctx.default_branch,
            original_prompt=ctx.require_parsed().prompt,
            pr_title=ctx.commit_message.title,
            pr_body=ctx.commit_message.body,
            pr_url=ctx.pr_url or "(not opened yet)",
            commit_title=ctx.commit_title,
            followup_thinking_level=ctx.followup_thinking_level,
            worktree_path=ctx.worktree_path,
        )
        ctx.pr_loop.refresh_result = refresh_result

    def policy_pr_push_branch(self, ctx: WorkflowContext) -> None:
        self.push_pr_branch(
            branch=ctx.require_parsed().branch,
            worktree_path=ctx.worktree_path,
            force=ctx.pr_loop.need_force_push,
            rewritten_head=ctx.rewritten_head,
        )

    def policy_pr_ensure_pr(self, ctx: WorkflowContext) -> None:
        if ctx.commit_message is None:
            raise RuntimeError("commit message has not been generated")
        parsed = ctx.require_parsed()
        self.forge.ensure_pr(parsed.branch, ctx.commit_message, ctx.worktree_path)
        ctx.pr_title = ctx.commit_message.title
        ctx.pr_url = self.forge.pr_url(parsed.branch, ctx.worktree_path)

    def policy_pr_checks(self, ctx: WorkflowContext) -> None:
        ctx.checks_status, ctx.checks_output = self.forge.wait_for_checks(
            ctx.require_parsed().branch,
            ctx.pr_loop.checks_timeout_seconds,
            ctx.pr_loop.checks_poll_interval_seconds,
            ctx.worktree_path,
        )
        if has_output(ctx.checks_output) and (
            ctx.checks_status != 0 or not self.runner.writes_success_output()
        ):
            write_collected(ctx.checks_output, stream=sys.stdout)

    def policy_pr_ci_fix(self, ctx: WorkflowContext) -> None:
        ctx.followup_thinking_level = self.fix_ci_failures(
            pr_title=ctx.pr_title,
            pr_url=ctx.pr_url,
            commit_title=ctx.commit_title,
            checks_out=ctx.checks_output,
            followup_thinking_level=ctx.followup_thinking_level,
            worktree_path=ctx.worktree_path,
        )

    def policy_pr_merge(self, ctx: WorkflowContext) -> None:
        if ctx.commit_message is None:
            raise RuntimeError("commit message has not been generated")
        ctx.pr_loop.pr_head_oid = ""
        if self.config.forge.merge_uses_head_oid:
            ctx.pr_loop.pr_head_oid = self.forge.head_oid(
                ctx.require_parsed().branch, ctx.worktree_path
            )
        ctx.pr_loop.merge_result = self.forge.squash_merge(
            ctx.require_parsed().branch,
            ctx.pr_loop.pr_head_oid,
            ctx.commit_message,
            ctx.pr_url,
            ctx.worktree_path,
        )
        if has_output(ctx.pr_loop.merge_result.stdout) and (
            ctx.pr_loop.merge_result.returncode != 0
            or not self.runner.writes_success_output()
        ):
            write_collected(ctx.pr_loop.merge_result.stdout, stream=sys.stdout)

    def policy_pr_merge_recovery(self, ctx: WorkflowContext) -> None:
        merge_result = ctx.pr_loop.merge_result
        if merge_result is None:
            raise RuntimeError("merge result is not available")
        if self.forge.reports_merged(ctx.pr_url, ctx.worktree_path):
            echo_out(
                f"pid: {self.config.forge.label} reports PR merged despite "
                "local forge cleanup failure; cleaning up"
            )
            ctx.pr_loop.merge_confirmed = True
            return

        ctx.merge_retries += 1
        if ctx.merge_retries > ctx.pr_loop.merge_retry_limit:
            echo_err(
                f"pid: {self.config.forge.label} squash merge failed after "
                f"{ctx.pr_loop.merge_retry_limit} merge retries; "
                f"leaving PR open: {ctx.pr_url}"
            )
            abort(merge_result.returncode)

        echo_out(
            "pid: merge failed; rebasing onto latest "
            f"origin/{ctx.default_branch} before retry "
            f"({ctx.merge_retries}/{ctx.pr_loop.merge_retry_limit} merge retries; "
            "agent attempts unchanged)"
        )
        self.runner.require(
            ["git", "fetch", "origin", ctx.default_branch], cwd=ctx.worktree_path
        )

        rebase_result = self.runner.run(
            ["git", "rebase", f"origin/{ctx.default_branch}"], cwd=ctx.worktree_path
        )
        if rebase_result.returncode != 0:
            write_command_output(rebase_result)
            if ctx.commit_message is None:
                raise RuntimeError("commit message has not been generated")
            ctx.followup_thinking_level = self.fix_rebase(
                original_prompt=ctx.require_parsed().prompt,
                pr_title=ctx.pr_title,
                pr_body=ctx.commit_message.body,
                pr_url=ctx.pr_url,
                default_branch=ctx.default_branch,
                commit_title=ctx.commit_title,
                merge_out=command_diagnostics(merge_result, rebase_result),
                followup_thinking_level=ctx.followup_thinking_level,
                worktree_path=ctx.worktree_path,
            )

        if self.repository.rebase_in_progress(ctx.worktree_path):
            echo_err(
                "pid: rebase still in progress after agent; "
                f"leaving PR open: {ctx.pr_url}"
            )
            abort(1)

        ctx.commit_title = self.repository.commit_rebase_changes(
            ctx.worktree_path,
            ctx.commit_title,
            self.config.commit.rebase_feedback_title,
        )
        ctx.pr_loop.need_force_push = True
        ctx.pr_loop.next_iteration = True

    def policy_pr_merge_confirmation(self, ctx: WorkflowContext) -> None:
        if not self.wait_for_confirmed_merge(
            pr_url=ctx.pr_url, worktree_path=ctx.worktree_path
        ):
            abort(1)
        ctx.pr_loop.merge_confirmed = True

    def policy_pr_cleanup(self, ctx: WorkflowContext) -> None:
        self.cleanup_and_print_success(
            pr_url=ctx.pr_url,
            pr_title=ctx.pr_title,
            main_worktree=ctx.main_worktree,
            default_branch=ctx.default_branch,
            branch=ctx.require_parsed().branch,
            worktree_path=ctx.worktree_path,
        )

    def regenerate_commit_message(
        self, *, parsed: ParsedArgs, base_rev: str, worktree_path: str
    ) -> tuple[CommitMessage, str]:
        """Generate, verify, and snapshot refreshed commit metadata."""

        commit_message = self.generate_commit_message(
            parsed=parsed,
            base_rev=base_rev,
            worktree_path=worktree_path,
        )
        self.verify_commit_title(commit_message)
        return commit_message, self.repository.state_hash(worktree_path)

    def abort_on_stopped_base_refresh(self, refresh_result: str, stage: str) -> None:
        """Abort when a base refresh hit a terminal non-rebased state."""

        if refresh_result not in REFRESH_STOP_RESULTS:
            return
        echo_err(f"pid: base refresh stopped {stage}: {refresh_result}")
        abort(1)

    def push_pr_branch(
        self, *, branch: str, worktree_path: str, force: bool, rewritten_head: str
    ) -> None:
        """Push a PR branch, safely tolerating agent-pushed rewritten history."""

        if force:
            self.runner.require(
                ["git", "push", "--force-with-lease", "-u", "origin", branch],
                cwd=worktree_path,
            )
            return

        remote_oid = self.repository.remote_branch_oid(worktree_path, branch)
        if not remote_oid:
            self.runner.require(
                ["git", "push", "-u", "origin", branch], cwd=worktree_path
            )
            return

        local_head = self.repository.output(
            ["rev-parse", "HEAD"], cwd=worktree_path
        ).strip()
        if self.repository.is_ancestor(worktree_path, remote_oid, local_head):
            self.runner.require(
                ["git", "push", "-u", "origin", branch], cwd=worktree_path
            )
            return

        if rewritten_head and self.repository.is_ancestor(
            worktree_path, remote_oid, rewritten_head
        ):
            echo_out(
                "pid: remote branch contains agent-pushed rewritten history; "
                "using force-with-lease"
            )
            self.runner.require(
                [
                    "git",
                    "push",
                    f"--force-with-lease=refs/heads/{branch}:{remote_oid}",
                    "-u",
                    "origin",
                    branch,
                ],
                cwd=worktree_path,
            )
            return

        echo_err(
            "pid: remote branch changed unexpectedly; refusing to overwrite "
            f"origin/{branch}"
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
    registry: ExtensionRegistry | None = None,
    events: EventSink | None = None,
) -> int:
    """Run the pid flow and return a process exit code."""

    return PIDFlow(
        config=config,
        output_mode=output_mode,
        registry=registry,
        events=events,
    ).run(argv)
