from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from pid.cli import _parse_agent_start, _run_agent_command, _run_status, _runs_table
from pid.commands import CommandRunner
from pid.config import OrchestratorConfig, PIDConfig, parse_config
from pid.context import WorkflowContext
from pid.events import ListEventSink, WorkflowEvent
from pid.extensions import ExtensionError
from pid.failures import FailureKind, WorkflowFailure, failure_from_abort
from pid.models import CommandResult, OutputMode
from pid.orchestrator import AgentStartOptions, OrchestratorAgent, workflow_argv
from pid.policy import DeterministicRecoveryPolicy, RecoveryActionKind
from pid.run_state import RunEventSink, RunStore, project_event
from pid.workflow import PIDFlow
from tests.fakes import assert_success, base_state, combined_output, run_pid


def test_orchestrator_config_defaults_enabled(tmp_path: Path) -> None:
    config = parse_config({}, tmp_path / "config.toml")

    assert config.orchestrator.enabled is True
    assert config.orchestrator.store_dir == ""


def test_orchestrator_config_can_disable_agent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[orchestrator]\nenabled = false\n")

    process, _ = run_pid(
        tmp_path,
        [
            "--config",
            str(config_path),
            "agent",
            "start",
            "--branch",
            "feature/cool-stuff",
            "--prompt",
            "prompt",
        ],
    )

    assert process.returncode == 2
    assert "orchestrator agent is disabled" in process.stderr


def test_run_store_persists_wrapped_events_outside_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    git_dir.mkdir(parents=True)
    store = RunStore(git_dir / "pid" / "runs")

    state = store.create_run(branch="feature/x", prompt="secret=123 do work", argv=[])
    store.append_event(str(state["run_id"]), WorkflowEvent("step.started", step="x"))

    run_dir = git_dir / "pid" / "runs" / str(state["run_id"])
    state_data = json.loads((run_dir / "state.json").read_text())
    event_line = (run_dir / "events.jsonl").read_text().splitlines()[0]

    assert state_data["current_step"] == "x"
    assert "[REDACTED]" in state_data["prompt_summary"]
    assert json.loads(event_line)["run_id"] == state["run_id"]
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((run_dir / "state.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((run_dir / "events.jsonl").stat().st_mode) == 0o600
    assert not (repo / "pid" / "runs").exists()


def test_agent_start_runs_workflow_and_persists_state(tmp_path: Path) -> None:
    process, final_state = run_pid(
        tmp_path,
        [
            "agent",
            "start",
            "--branch",
            "feature/cool-stuff",
            "--prompt",
            "prompt",
        ],
        state=base_state(tmp_path),
    )

    assert_success(process)
    assert "pid: agent run" in process.stdout
    runs_root = Path(final_state["common_git_dir"]) / "pid" / "runs"
    states = list(runs_root.glob("*/state.json"))
    assert len(states) == 1
    state = json.loads(states[0].read_text())
    assert state["status"] == "succeeded"
    assert state["branch"] == "feature/cool-stuff"
    assert state["pr_url"] == "https://example.invalid/pr/1"
    assert states[0].with_name("events.jsonl").exists()


def test_agent_start_marks_no_changes_done(tmp_path: Path) -> None:
    process, final_state = run_pid(
        tmp_path,
        [
            "agent",
            "start",
            "--branch",
            "feature/cool-stuff",
            "--prompt",
            "prompt",
        ],
        state=base_state(tmp_path, commit_count=0, worktree_dirty="", worktree_diff=""),
    )

    assert_success(process)
    runs_root = Path(final_state["common_git_dir"]) / "pid" / "runs"
    state = json.loads(next(runs_root.glob("*/state.json")).read_text())
    assert state["status"] == "no_changes"
    assert state["last_failure"]["kind"] == "no_changes"
    assert state["pending_recovery_action"]["kind"] == "mark_done"


def test_agent_start_records_typed_failure(tmp_path: Path) -> None:
    process, final_state = run_pid(
        tmp_path,
        [
            "agent",
            "start",
            "--branch",
            "feature/cool-stuff",
            "--prompt",
            "prompt",
        ],
        state=base_state(tmp_path, pi_fail_kinds=["initial"]),
    )

    assert process.returncode == 42
    assert "pid: agent run" in process.stdout
    runs_root = Path(final_state["common_git_dir"]) / "pid" / "runs"
    state = json.loads(next(runs_root.glob("*/state.json")).read_text())
    assert state["status"] == "failed"
    assert state["last_failure"]["kind"] == "initial_agent_failed"
    assert state["pending_recovery_action"]["kind"] == "abort"


def test_run_state_helpers_cover_projection_listing_and_sink(tmp_path: Path) -> None:
    store = RunStore.discover(configured_dir=str(tmp_path / "runs"))
    state = store.create_run(
        branch="feature/y", prompt="prompt", argv=["feature/y", "prompt"]
    )
    run_id = str(state["run_id"])
    downstream = ListEventSink()
    sink = RunEventSink(store, run_id, downstream)

    sink.emit(WorkflowEvent("step.started", step="prepare"))
    sink.emit(WorkflowEvent("step.completed", step="prepare"))
    sink.emit(WorkflowEvent("workflow.completed"))
    (store.root / "bad" / "state.json").parent.mkdir(parents=True)
    (store.root / "bad" / "state.json").write_text("not-json")

    assert downstream.events[0].name == "step.started"
    listed = store.list_runs(limit=5)
    assert listed[0]["run_id"] == run_id
    assert listed[0]["status"] == "succeeded"
    assert RunStore(tmp_path / "missing").list_runs() == []
    with pytest.raises(ValueError, match="invalid run id"):
        store.paths("bad")

    class BadRunner:
        def run(self, _args: list[str]) -> CommandResult:
            return CommandResult(1, "", "boom")

    with pytest.raises(RuntimeError, match="git common dir"):
        RunStore.discover(cast(CommandRunner, BadRunner()))

    projected = {"current_step": "x"}
    project_event(projected, WorkflowEvent("workflow.failed"))
    assert projected["status"] == "failed"


def test_cli_agent_formatters_and_parser() -> None:
    options = _parse_agent_start(
        [
            "--branch",
            "feature/x",
            "--prompt",
            "do work",
            "--attempts",
            "2",
            "--thinking",
            "high",
            "--non-interactive",
            "--yes",
            "--advisor",
            "policy",
            "--confirm-merge",
        ]
    )

    assert options.branch == "feature/x"
    assert options.attempts == 2
    assert options.confirm_merge is True
    with pytest.raises(ValueError, match="positive integer"):
        _parse_agent_start(["--branch", "x", "--prompt", "p", "--attempts", "0"])
    with pytest.raises(ValueError, match="unexpected"):
        _parse_agent_start(["--branch", "x", "--prompt", "p", "extra"])
    with pytest.raises(ValueError, match="non-empty"):
        _parse_agent_start(["--branch", " ", "--prompt", "p"])
    with pytest.raises(ValueError, match="non-empty"):
        _parse_agent_start(["--branch", "x", "--prompt", " "])
    with pytest.raises(ValueError, match="invalid"):
        _parse_agent_start([])

    table = _runs_table(
        [{"run_id": "r1", "status": "failed", "branch": "b", "current_step": "s"}]
    )
    assert "run_id" in table and "failed" in table
    assert _runs_table([]) == "no pid agent runs\n"
    status = _run_status(
        {
            "run_id": "r1",
            "status": "failed",
            "branch": "b",
            "last_failure": {"kind": "checks_failed", "step": "pr_handle_checks"},
            "pending_recovery_action": {"kind": "abort"},
        }
    )
    assert "failure: checks_failed at pr_handle_checks" in status
    assert "pending_recovery_action: abort" in status


def test_run_agent_command_status_runs_and_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = RunStore(tmp_path / "runs")
    state = store.create_run(branch="feature/z", prompt="prompt", argv=[])
    run_id = str(state["run_id"])
    config = PIDConfig(orchestrator=OrchestratorConfig(store_dir=str(store.root)))

    assert _run_agent_command([], config=config, output_mode=OutputMode.NORMAL) == 0
    assert (
        _run_agent_command(["runs"], config=config, output_mode=OutputMode.NORMAL) == 0
    )
    assert (
        _run_agent_command(
            ["status", run_id], config=config, output_mode=OutputMode.NORMAL
        )
        == 0
    )
    assert (
        _run_agent_command(
            ["resume", run_id], config=config, output_mode=OutputMode.NORMAL
        )
        == 2
    )
    assert (
        _run_agent_command(
            ["status", "bad"], config=config, output_mode=OutputMode.NORMAL
        )
        == 1
    )
    assert (
        _run_agent_command(["resume"], config=config, output_mode=OutputMode.NORMAL)
        == 2
    )
    assert (
        _run_agent_command(
            ["runs", "extra"], config=config, output_mode=OutputMode.NORMAL
        )
        == 2
    )
    assert (
        _run_agent_command(["unknown"], config=config, output_mode=OutputMode.NORMAL)
        == 2
    )
    assert (
        _run_agent_command(
            ["start", "--branch", "x"], config=config, output_mode=OutputMode.NORMAL
        )
        == 2
    )
    assert "agent resume cannot reconstruct" in capsys.readouterr().err

    disabled = PIDConfig(
        orchestrator=OrchestratorConfig(enabled=False, store_dir=str(store.root))
    )
    assert (
        _run_agent_command(
            ["start", "--branch", "x", "--prompt", "p"],
            config=disabled,
            output_mode=OutputMode.NORMAL,
        )
        == 2
    )
    pi_advisor = _run_agent_command(
        ["start", "--branch", "x", "--prompt", "p", "--advisor", "pi"],
        config=config,
        output_mode=OutputMode.NORMAL,
    )
    assert pi_advisor == 2
    invalid_thinking = _run_agent_command(
        ["start", "--branch", "x", "--prompt", "p", "--thinking", "bogus"],
        config=config,
        output_mode=OutputMode.NORMAL,
    )
    assert invalid_thinking == 2
    assert len(store.list_runs(limit=10)) == 1


def test_failure_policy_and_orchestrator_helpers(tmp_path: Path) -> None:
    failure = failure_from_abort(code=0, step="stop_if_no_changes", context=None)
    assert failure.kind == FailureKind.NO_CHANGES
    assert failure.to_dict()["kind"] == "no_changes"
    assert str(failure) == "no changes after agent"
    unknown = failure_from_abort(code=7, step="unknown", context=None)
    assert unknown.kind.value == "pr_failed"
    fake_context = cast(
        WorkflowContext,
        SimpleNamespace(
            branch="feature/x",
            worktree_path="/tmp/worktree",
            repo_root="/tmp/repo",
            main_worktree="/tmp/repo",
            default_branch="main",
            pr_url="",
            pr_title="",
            attempt=0,
            base_refresh_count=0,
            merge_retries=0,
        ),
    )
    worktree_failure = failure_from_abort(
        code=1, step="create_worktree", context=fake_context
    )
    assert worktree_failure.message == "could not create worktree for feature/x"
    assert worktree_failure.context["worktree_path"] == "/tmp/worktree"

    policy = DeterministicRecoveryPolicy()
    assert policy.decide(failure, state={}).kind == RecoveryActionKind.MARK_DONE
    cleanup = failure_from_abort(code=1, step="pr_cleanup", context=None)
    assert policy.decide(cleanup, state={}).kind == RecoveryActionKind.CLEANUP_RETRY
    dirty = failure_from_abort(
        code=1, step="validate_clean_main_worktree", context=None
    )
    assert policy.decide(dirty, state={}).kind == RecoveryActionKind.ASK_USER

    options = AgentStartOptions(
        branch="feature/x", prompt="do work", attempts=2, thinking="high"
    )
    assert workflow_argv(options, default_thinking="medium") == [
        "2",
        "high",
        "feature/x",
        "do work",
    ]
    default_options = AgentStartOptions(branch="feature/x", prompt="do work")
    assert workflow_argv(default_options, default_thinking="medium") == [
        "feature/x",
        "do work",
    ]
    agent = OrchestratorAgent(config=PIDConfig(), store=RunStore(tmp_path / "runs"))
    with pytest.raises(ValueError, match="only deterministic"):
        agent.start(AgentStartOptions(branch="x", prompt="p", advisor="pi"))


def test_run_supervised_records_extension_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow = PIDFlow(load_extensions=False)
    flow.current_step = "run_initial_agent"

    def raise_extension(_argv: list[str]) -> None:
        raise ExtensionError("bad extension")

    monkeypatch.setattr(flow, "_run", raise_extension)

    with pytest.raises(WorkflowFailure) as caught:
        flow.run_supervised(["feature/x", "prompt"])

    assert caught.value.kind == FailureKind.EXTENSION_FAILED
    assert caught.value.step == "run_initial_agent"
    assert caught.value.code == 2


def test_pid_run_command_keeps_main_workflow(tmp_path: Path) -> None:
    process, _ = run_pid(
        tmp_path,
        ["run", "feature/cool-stuff", "prompt"],
        state=base_state(tmp_path),
    )

    assert_success(process)
    assert "usage:" not in combined_output(process)
