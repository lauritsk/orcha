from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from pid.cli import (
    _parse_agent_start,
    _run_agent_command,
    _orchestrator_status,
    _run_orchestrator_command,
    _run_status,
    _runs_table,
)
from pid.commands import CommandRunner
from pid.config import OrchestratorConfig, PIDConfig, parse_config
from pid.context import WorkflowContext
from pid.events import ListEventSink, WorkflowEvent
from pid.extensions import ExtensionError
from pid.github import Forge
from pid.failures import FailureKind, WorkflowFailure, failure_from_abort
from pid.models import CommandResult, OutputMode
from pid.orchestrator import (
    AgentStartOptions,
    OrchestratorAgent,
    OrchestratorDisabled,
    OrchestratorStartOptions,
    OrchestratorSupervisor,
    build_child_prompt,
    build_child_records,
    child_agent_command,
    load_plan,
    select_followup_targets,
    select_thinking,
    string_list,
    workflow_argv,
)
from pid.policy import DeterministicRecoveryPolicy, RecoveryActionKind
from pid.repository import Repository
from pid.run_state import RunEventSink, RunStore, project_event
from pid.workflow import PIDFlow
from tests.fakes import assert_success, base_state, combined_output, run_pid


def test_orchestrator_config_defaults_enabled(tmp_path: Path) -> None:
    config = parse_config({}, tmp_path / "config.toml")

    assert config.orchestrator.enabled is True
    assert config.orchestrator.store_dir == ""
    assert config.orchestrator.max_parallel_agents == 4
    assert config.orchestrator.validation_commands == ()


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


def test_agent_start_allows_startless_happy_path(tmp_path: Path) -> None:
    process, final_state = run_pid(
        tmp_path,
        [
            "agent",
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
    state = json.loads(next(runs_root.glob("*/state.json")).read_text())
    assert state["status"] == "succeeded"


def test_agent_short_alias_starts_supervised_run(tmp_path: Path) -> None:
    process, final_state = run_pid(
        tmp_path,
        ["a", "--branch", "feature/cool-stuff", "--prompt", "prompt"],
        state=base_state(tmp_path),
    )

    assert_success(process)
    runs_root = Path(final_state["common_git_dir"]) / "pid" / "runs"
    state = json.loads(next(runs_root.glob("*/state.json")).read_text())
    assert state["status"] == "succeeded"


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
        ]
    )

    assert options.branch == "feature/x"
    assert options.attempts == 2
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
            ["status", "bad"], config=config, output_mode=OutputMode.NORMAL
        )
        == 1
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
    assert "unknown agent command" in capsys.readouterr().err

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
    assert policy.decide(failure).kind == RecoveryActionKind.MARK_DONE
    cleanup = failure_from_abort(code=1, step="pr_cleanup", context=None)
    assert policy.decide(cleanup).kind == RecoveryActionKind.CLEANUP_RETRY
    dirty = failure_from_abort(
        code=1, step="validate_clean_main_worktree", context=None
    )
    assert policy.decide(dirty).kind == RecoveryActionKind.ASK_USER

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


def test_agent_follow_up_cli_queues_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = RunStore(tmp_path / "runs")
    state = store.create_run(branch="feature/f", prompt="prompt", argv=[])
    run_id = str(state["run_id"])
    config = PIDConfig(orchestrator=OrchestratorConfig(store_dir=str(store.root)))

    code = _run_agent_command(
        [
            "follow-up",
            run_id,
            "--message",
            "Use the new copy everywhere",
            "--type",
            "scope_change",
        ],
        config=config,
        output_mode=OutputMode.NORMAL,
    )

    assert code == 0
    pending = store.pending_followups(run_id)
    assert pending[0]["id"] == "fu-000001"
    assert pending[0]["kind"] == "scope_change"
    assert pending[0]["message"] == "Use the new copy everywhere"
    assert "queued follow-up fu-000001" in capsys.readouterr().out


def test_workflow_applies_follow_up_at_safe_checkpoint(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    state = store.create_run(branch="feature/f", prompt="prompt", argv=[])
    run_id = str(state["run_id"])
    store.append_followup(run_id, message="Switch API name to v2")
    flow = PIDFlow(load_extensions=False, run_store=store, run_id=run_id)
    ctx = WorkflowContext(
        argv=[],
        config=PIDConfig(),
        runner=CommandRunner(),
        repository=Repository(CommandRunner()),
        forge=Forge(CommandRunner(), PIDConfig().forge),
        registry=flow.registry,
        events=ListEventSink(),
    )
    flow.context = ctx

    flow.apply_queued_followups(ctx, "run_initial_agent")

    assert store.pending_followups(run_id) == []
    assert store.read_state(run_id)["applied_follow_up_count"] == 1
    assert "Switch API name to v2" in flow.prompt_with_followups("Base prompt")


def test_workflow_pause_follow_up_stops_at_checkpoint(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    state = store.create_run(branch="feature/f", prompt="prompt", argv=[])
    run_id = str(state["run_id"])
    store.append_followup(run_id, message="Pause before PR", kind="pause")
    flow = PIDFlow(load_extensions=False, run_store=store, run_id=run_id)
    ctx = WorkflowContext(
        argv=[],
        config=PIDConfig(),
        runner=CommandRunner(),
        repository=Repository(CommandRunner()),
        forge=Forge(CommandRunner(), PIDConfig().forge),
        registry=flow.registry,
        events=ListEventSink(),
    )

    with pytest.raises(WorkflowFailure) as caught:
        flow.apply_queued_followups(ctx, "pr_push_branch")

    assert caught.value.kind == FailureKind.FOLLOWUP_PAUSED
    assert store.read_state(run_id)["last_applied_follow_up_id"] == "fu-000001"


def test_orchestrator_startless_happy_path_creates_intake(tmp_path: Path) -> None:
    process, final_state = run_pid(
        tmp_path,
        ["orchestrator", "--goal", "Ship larger change"],
        state=base_state(tmp_path),
    )

    assert_success(process)
    assert "pid: orchestrator run" in process.stdout
    runs_root = Path(final_state["common_git_dir"]) / "pid" / "runs"
    state = json.loads(next(runs_root.glob("*/state.json")).read_text())
    assert state["run_type"] == "orchestrator"
    assert state["status"] == "awaiting_plan"
    assert state["branch_prefix"] == "ship-larger-change"


def test_orchestrator_short_alias_creates_intake(tmp_path: Path) -> None:
    process, final_state = run_pid(
        tmp_path,
        ["o", "--goal", "Ship larger change"],
        state=base_state(tmp_path),
    )

    assert_success(process)
    runs_root = Path(final_state["common_git_dir"]) / "pid" / "runs"
    state = json.loads(next(runs_root.glob("*/state.json")).read_text())
    assert state["run_type"] == "orchestrator"


def test_orchestrator_start_without_plan_grills_user(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = RunStore(tmp_path / "runs")
    config = PIDConfig(orchestrator=OrchestratorConfig(store_dir=str(store.root)))

    code = _run_orchestrator_command(
        ["start", "--goal", "Ship larger change"],
        config=config,
        output_mode=OutputMode.NORMAL,
        config_path=None,
    )

    assert code == 0
    state = store.list_runs()[0]
    assert state["run_type"] == "orchestrator"
    assert state["status"] == "awaiting_plan"
    assert len(state["intake_questions"]) >= 10
    assert "answer these intake questions" in capsys.readouterr().out


def test_orchestrator_start_without_plan_prompts_for_intake_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = RunStore(tmp_path / "runs")
    config = PIDConfig(orchestrator=OrchestratorConfig(store_dir=str(store.root)))
    answers = iter(f"answer {index}" for index in range(1, 20))

    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("pid.cli.typer.prompt", lambda *_, **__: next(answers))

    code = _run_orchestrator_command(
        ["start", "--goal", "Ship larger change"],
        config=config,
        output_mode=OutputMode.NORMAL,
        config_path=None,
    )

    assert code == 0
    state = store.list_runs()[0]
    assert state["status"] == "awaiting_plan"
    assert len(state["intake_answers"]) == len(state["intake_questions"])
    assert state["intake_answers"][0] == {
        "question": "What exact outcome should exist when this is done?",
        "answer": "answer 1",
    }
    output = capsys.readouterr().out
    assert "answer orchestrator intake questions" in output
    assert "intake answers recorded" in output
    assert "answer these intake questions" not in output


def test_orchestrator_plan_dry_run_creates_child_runs_and_routes_follow_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = RunStore(tmp_path / "runs")
    config = PIDConfig(orchestrator=OrchestratorConfig(store_dir=str(store.root)))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "constraints": ["Do not touch billing"],
                "items": [
                    {
                        "id": "api",
                        "title": "Add API workflow",
                        "scope": "Change API orchestration code",
                    },
                    {
                        "id": "docs",
                        "title": "Update README docs",
                        "scope": "Document CLI usage",
                    },
                ],
            }
        )
    )

    code = _run_orchestrator_command(
        [
            "start",
            "--goal",
            "Ship larger change",
            "--plan-file",
            str(plan_path),
            "--branch-prefix",
            "feat",
            "--dry-run",
        ],
        config=config,
        output_mode=OutputMode.NORMAL,
        config_path=None,
    )

    assert code == 0
    state = next(run for run in store.list_runs() if run["run_type"] == "orchestrator")
    children = state["children"]
    assert children[0]["branch"] == "feat/api-add-api-workflow"
    assert children[0]["thinking"] == "high"
    assert children[1]["thinking"] == "low"
    child_run_id = children[0]["child_run_id"]
    assert store.read_state(child_run_id)["status"] == "planned"

    follow_code = _run_orchestrator_command(
        [
            "follow-up",
            str(state["run_id"]),
            "--message",
            "Rename endpoint to /v2/tasks",
            "--target",
            "api",
        ],
        config=config,
        output_mode=OutputMode.NORMAL,
        config_path=None,
    )

    assert follow_code == 0
    assert (
        store.pending_followups(child_run_id)[0]["message"]
        == "Rename endpoint to /v2/tasks"
    )
    assert "routed follow-up" in capsys.readouterr().out


def test_orchestrator_cli_errors_and_recorded_follow_up(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RunStore(tmp_path / "runs")
    config = PIDConfig(orchestrator=OrchestratorConfig(store_dir=str(store.root)))
    orch = store.create_orchestrator_run(
        goal="goal", questions=["q?"], status="awaiting_plan"
    )
    run_id = str(orch["run_id"])

    assert (
        _run_orchestrator_command(
            [], config=config, output_mode=OutputMode.NORMAL, config_path=None
        )
        == 0
    )
    assert (
        _run_orchestrator_command(
            ["runs", "extra"],
            config=config,
            output_mode=OutputMode.NORMAL,
            config_path=None,
        )
        == 2
    )
    assert (
        _run_orchestrator_command(
            ["status"], config=config, output_mode=OutputMode.NORMAL, config_path=None
        )
        == 2
    )
    assert (
        _run_orchestrator_command(
            ["status", "bad"],
            config=config,
            output_mode=OutputMode.NORMAL,
            config_path=None,
        )
        == 1
    )
    assert (
        _run_orchestrator_command(
            ["bogus"], config=config, output_mode=OutputMode.NORMAL, config_path=None
        )
        == 2
    )
    assert (
        _run_orchestrator_command(
            ["start", "--goal", ""],
            config=config,
            output_mode=OutputMode.NORMAL,
            config_path=None,
        )
        == 2
    )
    assert (
        _run_orchestrator_command(
            ["start", "--goal", "g", "--concurrency", "0"],
            config=config,
            output_mode=OutputMode.NORMAL,
            config_path=None,
        )
        == 2
    )

    assert (
        _run_orchestrator_command(
            ["follow-up", run_id, "--message", "global note"],
            config=config,
            output_mode=OutputMode.NORMAL,
            config_path=None,
        )
        == 0
    )
    assert store.read_state(run_id)["followups"][0]["status"] == "recorded"

    disabled = PIDConfig(
        orchestrator=OrchestratorConfig(enabled=False, store_dir=str(store.root))
    )
    assert (
        _run_orchestrator_command(
            ["start", "--goal", "g"],
            config=disabled,
            output_mode=OutputMode.NORMAL,
            config_path=None,
        )
        == 2
    )

    def bad_discover(*_args: object, **_kwargs: object) -> RunStore:
        raise RuntimeError("no repo")

    monkeypatch.setattr("pid.cli.RunStore.discover", bad_discover)
    assert (
        _run_orchestrator_command(
            ["runs"], config=config, output_mode=OutputMode.NORMAL, config_path=None
        )
        == 1
    )
    assert "recorded orchestrator follow-up" in capsys.readouterr().out


def test_orchestrator_status_and_runs_show_children(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    child = store.create_run(branch="feature/child", prompt="prompt", argv=[])
    orch = store.create_orchestrator_run(
        goal="goal",
        questions=["q?"],
        status="planned",
        children=[
            {
                "item_id": "api",
                "status": "planned",
                "branch": "feature/child",
                "child_run_id": child["run_id"],
            }
        ],
    )
    config = PIDConfig(orchestrator=OrchestratorConfig(store_dir=str(store.root)))

    assert (
        _run_orchestrator_command(
            ["runs"], config=config, output_mode=OutputMode.NORMAL, config_path=None
        )
        == 0
    )
    assert (
        _run_orchestrator_command(
            ["status", str(orch["run_id"])],
            config=config,
            output_mode=OutputMode.NORMAL,
            config_path=None,
        )
        == 0
    )
    status = _orchestrator_status(cast("dict[str, object]", orch), store)
    assert "api running feature/child" in status


def test_orchestrator_supervisor_launches_ready_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path / "runs")
    config = PIDConfig(orchestrator=OrchestratorConfig(store_dir=str(store.root)))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "one", "title": "One"},
                    {"id": "two", "title": "Two"},
                    {"id": "later", "title": "Later", "dependencies": ["one"]},
                ]
            }
        )
    )
    launched: list[list[str]] = []

    class FakeProcess:
        def __init__(self, command: list[str], **_kwargs: object) -> None:
            launched.append(command)
            self.pid = 1234 + len(launched)

    monkeypatch.setattr("pid.orchestrator.subprocess.Popen", FakeProcess)
    result = OrchestratorSupervisor(config=config, store=store).start(
        OrchestratorStartOptions(
            goal="goal",
            plan_file=plan_path,
            concurrency=1,
            config_path=tmp_path / "config.toml",
        )
    )

    children = result.state["children"]
    assert children[0]["status"] == "launched"
    assert children[1]["status"] == "queued"
    assert children[2]["status"] == "blocked"
    assert launched[0][:3] == [sys.executable, "-m", "pid"]
    assert "--config" in launched[0]


def test_orchestrator_helper_error_paths(tmp_path: Path) -> None:
    config = PIDConfig()
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{")
    with pytest.raises(ValueError, match="valid JSON"):
        load_plan(bad_json)
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="could not read"):
        load_plan(missing)
    scalar = tmp_path / "scalar.json"
    scalar.write_text("1")
    with pytest.raises(ValueError, match="JSON object"):
        load_plan(scalar)
    no_items = tmp_path / "no-items.json"
    no_items.write_text(json.dumps({"items": "no"}))
    with pytest.raises(ValueError, match="items array"):
        load_plan(no_items)
    item_array = tmp_path / "items.json"
    item_array.write_text(json.dumps([{"title": "Doc task"}]))
    assert load_plan(item_array)["items"][0]["title"] == "Doc task"

    with pytest.raises(ValueError, match="at least one"):
        build_child_records(
            {"items": []}, goal="g", parent_run_id="p", branch_prefix="b", config=config
        )
    with pytest.raises(ValueError, match="must be an object"):
        build_child_records(
            {"items": ["bad"]},
            goal="g",
            parent_run_id="p",
            branch_prefix="b",
            config=config,
        )
    with pytest.raises(ValueError, match="duplicate"):
        build_child_records(
            {"items": [{"id": "x"}, {"id": "x"}]},
            goal="g",
            parent_run_id="p",
            branch_prefix="b",
            config=config,
        )
    with pytest.raises(ValueError, match="thinking"):
        build_child_records(
            {"items": [{"id": "x", "thinking": "huge"}]},
            goal="g",
            parent_run_id="p",
            branch_prefix="b",
            config=config,
        )

    records = build_child_records(
        {"items": [{"id": "!!!", "branch": "custom/branch", "prompt": "custom"}]},
        goal="g",
        parent_run_id="p",
        branch_prefix="b",
        config=config,
    )
    assert records[0]["item_id"] == "item-1"
    assert records[0]["branch"] == "custom/branch"
    assert records[0]["prompt"] == "custom"

    assert (
        select_thinking(
            title="Security migration",
            scope="auth data",
            acceptance=[],
            validation=[],
            config=config,
        )
        == "xhigh"
    )
    assert string_list(None) == []
    assert string_list("one") == ["one"]
    assert string_list(2) == ["2"]
    prompt = build_child_prompt(
        goal="g",
        constraints=["c"],
        item_id="i",
        title="t",
        scope="s",
        acceptance=["a"],
        validation=["v"],
        dependencies=["d"],
    )
    assert "Dependencies" in prompt and "Validation" in prompt


def test_follow_up_target_selection_and_agent_commands(tmp_path: Path) -> None:
    child = {
        "item_id": "api",
        "child_run_id": "20260429T010101001Z-abcdef",
        "branch": "feat/api",
        "prompt": "prompt",
        "thinking": "high",
    }
    assert select_followup_targets([child], target="", all_children=True) == [child]
    assert select_followup_targets([child], target="", all_children=False) == []
    assert select_followup_targets("bad", target="api", all_children=True) == []
    with pytest.raises(ValueError, match="no child"):
        select_followup_targets([child], target="docs", all_children=False)

    command = child_agent_command(
        child,
        parent_run_id="parent",
        config_path=tmp_path / "config.toml",
        default_thinking="medium",
    )
    assert command[:3] == [sys.executable, "-m", "pid"]
    assert "--thinking" in command
    child["thinking"] = "medium"
    assert "--thinking" not in child_agent_command(
        child, parent_run_id="parent", config_path=None, default_thinking="medium"
    )


def test_orchestrator_agent_control_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path / "runs")
    disabled = PIDConfig(orchestrator=OrchestratorConfig(enabled=False))
    with pytest.raises(OrchestratorDisabled):
        OrchestratorSupervisor(config=disabled, store=store)
    with pytest.raises(OrchestratorDisabled):
        OrchestratorAgent(config=disabled, store=store)

    def raise_paused(self: PIDFlow, _argv: list[str]) -> WorkflowContext:
        self.context = cast(
            WorkflowContext,
            SimpleNamespace(
                branch="feature/x",
                pr_url="",
                worktree_path="",
                followup_thinking_level="medium",
                attempt=0,
                commit_title="",
            ),
        )
        raise WorkflowFailure(
            FailureKind.FOLLOWUP_PAUSED,
            "run_initial_agent",
            0,
            "paused",
            True,
        )

    monkeypatch.setattr(PIDFlow, "run_supervised", raise_paused)
    paused = OrchestratorAgent(config=PIDConfig(), store=store).start(
        AgentStartOptions(branch="feature/pause", prompt="prompt")
    )
    assert paused.state["status"] == "paused"

    def raise_aborted(self: PIDFlow, _argv: list[str]) -> WorkflowContext:
        self.context = None
        raise WorkflowFailure(
            FailureKind.FOLLOWUP_ABORTED,
            "run_initial_agent",
            1,
            "aborted",
            False,
        )

    monkeypatch.setattr(PIDFlow, "run_supervised", raise_aborted)
    aborted = OrchestratorAgent(config=PIDConfig(), store=store).start(
        AgentStartOptions(branch="feature/abort", prompt="prompt")
    )
    assert aborted.state["status"] == "aborted"


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
