from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pid.cli import app
from pid.config import ExtensionConfig, parse_config
from pid.events import JsonlEventSink, WorkflowEvent
from pid.errors import PIDAbort
from pid.extensions import ExtensionError, ExtensionRegistry, StepResult, WorkflowStep
from pid.models import CommandResult
from pid.workflow import PIDFlow, command_diagnostic
from tests.fakes import assert_success, base_state, calls, run_pid


def run_engine_step(flow: PIDFlow, context: Any, step: WorkflowStep) -> None:
    flow.engine.execute_step(
        context,
        step,
        flow.registry,
        checkpoint=flow.apply_queued_followups,
        current_step_callback=flow._set_current_step,
    )


DEMO_EXTENSION = "\n".join(
    [
        "from pid.extensions import WorkflowStep",
        "from pid.output import echo_out",
        "",
        "def before_initial(ctx):",
        "    echo_out(f'demo before {ctx.branch}')",
        "",
        "def extra_step(ctx):",
        "    echo_out('demo extra step')",
        "",
        "def replace_review(ctx):",
        "    echo_out('demo review replacement')",
        "",
        "def doctor(ctx):",
        "    echo_out('doctor ' + ' '.join(ctx.argv))",
        "    return 0",
        "",
        "class DemoExtension:",
        "    name = 'demo'",
        "    api_version = '1'",
        "    def register(self, registry):",
        "        registry.add_hook('before.run_initial_agent', before_initial)",
        "        registry.add_step(WorkflowStep('demo_extra_step', extra_step), after='run_initial_agent')",
        "        registry.replace_step('run_review_agent', replace_review)",
        "        registry.add_cli_command('doctor', doctor)",
        "",
        "extension = DemoExtension()",
        "",
    ]
)


def test_registry_adds_replaces_disables_steps_and_orders_hooks() -> None:
    registry = ExtensionRegistry()
    seen: list[str] = []

    def step(name: str):
        return lambda _ctx: seen.append(name)

    registry.add_step(WorkflowStep("middle", step("middle")), after="one")
    registry.replace_step("two", WorkflowStep("two", step("two-replaced")))
    registry.disable_step("three")
    registry.add_hook("before.one", lambda _ctx: seen.append("late"), order=10)
    registry.add_hook("before.one", lambda _ctx: seen.append("early"), order=-10)

    steps = registry.resolve_steps(
        [
            WorkflowStep("one", step("one")),
            WorkflowStep("two", step("two")),
            WorkflowStep("three", step("three")),
        ]
    )

    assert [item.name for item in steps] == ["one", "middle", "two", "three"]

    class Context:
        def emit(self, *_args: Any, **_kwargs: Any) -> None:
            return

    engine = PIDFlow(load_extensions=False).engine
    context = Context()
    for item in steps:
        engine.execute_step(context, item, registry)
    assert seen == ["early", "late", "one", "middle", "two-replaced"]


def test_hook_can_stop_workflow() -> None:
    registry = ExtensionRegistry()
    registry.add_hook("before.step", lambda _ctx: StepResult.stop(7, "blocked"))

    result = registry.run_hooks("before.step", object())

    assert result == StepResult.stop(7, "blocked")
    assert StepResult.skip("nope").action == "skip"
    assert StepResult.retry("again").action == "retry"


def test_workflow_event_jsonl_sink_serializes_events() -> None:
    stream = io.StringIO()
    sink = JsonlEventSink(stream)

    sink.emit(WorkflowEvent("step.started", step="run_review_agent", fields={"x": 1}))

    data = json.loads(stream.getvalue())
    assert data["fields"] == {"x": 1}
    assert data["level"] == "info"
    assert data["name"] == "step.started"
    assert data["step"] == "run_review_agent"
    assert data["timestamp"]


def test_extension_config_parses_loader_controls_and_raw_tables(tmp_path: Path) -> None:
    config = parse_config(
        {
            "extensions": {
                "enabled": ["demo"],
                "paths": [".pid/extensions"],
                "demo": {"flag": True, "label": "Demo"},
            }
        },
        tmp_path / "config.toml",
    )

    assert config.extensions == ExtensionConfig(
        enabled=("demo",),
        paths=(".pid/extensions",),
        config={"demo": {"flag": True, "label": "Demo"}},
    )


def test_project_local_extension_can_hook_add_and_replace_workflow_steps(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    (extension_dir / "demo.py").write_text(DEMO_EXTENSION)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[extensions]
enabled = ["demo"]
paths = ["{extension_dir}"]
""".strip()
    )
    state = base_state(tmp_path)

    process, final_state = run_pid(
        tmp_path,
        ["--config", str(config_path), "feature/cool-stuff", "prompt"],
        state=state,
    )

    assert_success(process)
    assert "demo before feature/cool-stuff" in process.stdout
    assert "demo extra step" in process.stdout
    assert "demo review replacement" in process.stdout
    assert [call["kind"] for call in final_state["pi_calls"]] == [
        "initial",
        "message",
    ]


def test_project_local_extension_can_replace_pr_loop_substep(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    (extension_dir / "pr_steps.py").write_text(
        "\n".join(
            [
                "from pid.output import echo_out",
                "",
                "def before_checks(ctx):",
                "    echo_out('extension before PR checks')",
                "",
                "def skip_checks(ctx):",
                "    ctx.checks_status = 0",
                "    ctx.checks_output = 'extension skipped checks'",
                "",
                "class PrStepsExtension:",
                "    name = 'pr_steps'",
                "    api_version = '1'",
                "    def register(self, registry):",
                "        registry.add_hook('before.pr_wait_for_checks', before_checks)",
                "        registry.replace_step('pr_wait_for_checks', skip_checks)",
                "",
                "extension = PrStepsExtension()",
                "",
            ]
        )
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[extensions]
enabled = ["pr_steps"]
paths = ["{extension_dir}"]
""".strip()
    )
    state = base_state(
        tmp_path,
        checks_sequence=[{"status": 1, "out": "unit tests failed"}],
    )

    process, final_state = run_pid(
        tmp_path,
        ["--config", str(config_path), "feature/cool-stuff", "prompt"],
        state=state,
    )

    assert_success(process)
    assert "extension before PR checks" in process.stdout
    assert not calls(final_state, "gh", "pr", "checks")
    assert not [call for call in final_state["pi_calls"] if call["kind"] == "ci_fix"]


def test_project_local_extension_can_replace_pr_loop_policy(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    (extension_dir / "pr_policy.py").write_text(
        "\n".join(
            [
                "def checks_policy(ctx):",
                "    ctx.checks_status = 0",
                "    ctx.checks_output = 'policy checks passed'",
                "",
                "class PrPolicyExtension:",
                "    name = 'pr_policy'",
                "    api_version = '1'",
                "    def register(self, registry):",
                "        registry.add_policy('pr.checks', checks_policy)",
                "",
                "extension = PrPolicyExtension()",
                "",
            ]
        )
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[extensions]
enabled = ["pr_policy"]
paths = ["{extension_dir}"]
""".strip()
    )
    state = base_state(
        tmp_path,
        checks_sequence=[{"status": 1, "out": "unit tests failed"}],
    )

    process, final_state = run_pid(
        tmp_path,
        ["--config", str(config_path), "feature/cool-stuff", "prompt"],
        state=state,
    )

    assert_success(process)
    assert not calls(final_state, "gh", "pr", "checks")
    assert not [call for call in final_state["pi_calls"] if call["kind"] == "ci_fix"]


def test_registry_routes_steps_anchored_to_external_phases() -> None:
    registry = ExtensionRegistry()
    registry.add_step(WorkflowStep("top_extra", lambda _ctx: None), after="run_pr_loop")
    registry.add_step(
        WorkflowStep("pr_extra", lambda _ctx: None), after="pr_push_branch"
    )

    top_steps = registry.resolve_steps(
        [WorkflowStep("run_pr_loop", lambda _ctx: None)],
        external_steps=("pr_push_branch",),
    )
    pr_steps = registry.resolve_steps(
        [WorkflowStep("pr_push_branch", lambda _ctx: None)],
        external_steps=("run_pr_loop",),
        include_unanchored=False,
    )

    assert [step.name for step in top_steps] == ["run_pr_loop", "top_extra"]
    assert [step.name for step in pr_steps] == ["pr_push_branch", "pr_extra"]


def test_project_local_extension_can_disable_pr_cleanup_without_looping(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    (extension_dir / "no_cleanup.py").write_text(
        "\n".join(
            [
                "class NoCleanupExtension:",
                "    name = 'no_cleanup'",
                "    api_version = '1'",
                "    def register(self, registry):",
                "        registry.disable_step('pr_cleanup')",
                "",
                "extension = NoCleanupExtension()",
                "",
            ]
        )
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[extensions]
enabled = ["no_cleanup"]
paths = ["{extension_dir}"]
""".strip()
    )

    process, final_state = run_pid(
        tmp_path,
        ["--config", str(config_path), "feature/cool-stuff", "prompt"],
    )

    assert_success(process)
    assert final_state["merged_at_queries"] == 1
    assert not calls(final_state, "git", "worktree", "remove")
    assert not calls(final_state, "git", "branch", "-D")
    assert not [
        call for call in calls(final_state, "git", "push") if "--delete" in call["args"]
    ]


def test_project_local_extension_terminal_pr_loop_misconfiguration_errors(
    tmp_path: Path,
) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    (extension_dir / "no_merge.py").write_text(
        "\n".join(
            [
                "class NoMergeExtension:",
                "    name = 'no_merge'",
                "    api_version = '1'",
                "    def register(self, registry):",
                "        registry.disable_step('pr_squash_merge')",
                "",
                "extension = NoMergeExtension()",
                "",
            ]
        )
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[extensions]
enabled = ["no_merge"]
paths = ["{extension_dir}"]
""".strip()
    )

    process, _ = run_pid(
        tmp_path,
        ["--config", str(config_path), "feature/cool-stuff", "prompt"],
    )

    assert process.returncode == 2
    assert "PR loop did not complete or request another iteration" in process.stderr


def test_pid_x_dispatches_project_local_extension_command(tmp_path: Path) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    (extension_dir / "demo.py").write_text(DEMO_EXTENSION)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[extensions]
enabled = ["demo"]
paths = ["{extension_dir}"]
""".strip()
    )

    result = CliRunner().invoke(
        app,
        ["--config", str(config_path), "x", "doctor", "alpha", "beta"],
    )

    assert result.exit_code == 0
    assert "doctor alpha beta" in result.output


def test_registry_reports_invalid_step_and_command_registration() -> None:
    registry = ExtensionRegistry()

    def noop(_ctx):
        return None

    errors = []
    for action in (
        lambda: registry.add_hook("", noop),
        lambda: registry.add_step(noop),
        lambda: registry.add_step(
            WorkflowStep("both", noop), before="one", after="two"
        ),
        lambda: registry.add_policy("", object()),
        lambda: registry.replace_service("", noop),
        lambda: registry.add_cli_command("", noop),
    ):
        try:
            action()
        except Exception as error:  # noqa: BLE001 - asserting public errors
            errors.append(str(error))

    registry.add_policy("policy", object())
    registry.add_cli_command("cmd", noop)
    for action in (
        lambda: registry.add_policy("policy", object()),
        lambda: registry.add_cli_command("cmd", noop),
    ):
        try:
            action()
        except Exception as error:  # noqa: BLE001 - asserting public errors
            errors.append(str(error))

    assert any("hook name" in message for message in errors)
    assert any("step name required" in message for message in errors)
    assert any("only one of before or after" in message for message in errors)
    assert any("policy already registered" in message for message in errors)
    assert any("CLI command already registered" in message for message in errors)


def test_registry_resolve_steps_reports_invalid_modifications() -> None:
    registry = ExtensionRegistry()
    registry.replace_step("missing", WorkflowStep("missing", lambda _ctx: None))

    try:
        registry.resolve_steps([WorkflowStep("one", lambda _ctx: None)])
    except Exception as error:  # noqa: BLE001 - asserting public errors
        assert "cannot replace unknown step" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected error")

    duplicate = ExtensionRegistry()
    try:
        duplicate.resolve_steps(
            [
                WorkflowStep("one", lambda _ctx: None),
                WorkflowStep("one", lambda _ctx: None),
            ]
        )
    except Exception as error:  # noqa: BLE001 - asserting public errors
        assert "step already registered" in str(error)

    before = ExtensionRegistry()
    before.add_step(WorkflowStep("extra", lambda _ctx: None), before="missing")
    try:
        before.resolve_steps([WorkflowStep("one", lambda _ctx: None)])
    except Exception as error:  # noqa: BLE001 - asserting public errors
        assert "before unknown step" in str(error)

    after = ExtensionRegistry()
    after.add_step(WorkflowStep("extra", lambda _ctx: None), after="missing")
    try:
        after.resolve_steps([WorkflowStep("one", lambda _ctx: None)])
    except Exception as error:  # noqa: BLE001 - asserting public errors
        assert "after unknown step" in str(error)


def test_registry_register_extension_validation_and_hook_errors() -> None:
    registry = ExtensionRegistry()

    class Valid:
        name = "valid"
        api_version = "1"

        def register(self, registry):
            registry.add_hook(
                "before.demo",
                lambda _ctx: (_ for _ in ()).throw(ValueError("boom")),
            )

    class BadApi:
        name = "bad-api"
        api_version = "2"

        def register(self, registry):
            return None

    class BadRegister:
        name = "bad-register"
        api_version = "1"

        def register(self, registry):
            raise RuntimeError("nope")

    registry.register_extension(Valid(), source="test")
    assert registry.loaded_extension_names == {"valid"}
    try:
        registry.run_hooks("before.demo", object())
    except Exception as error:  # noqa: BLE001 - asserting public errors
        assert "hook before.demo failed" in str(error)

    for extension, message in (
        (object(), "no valid name"),
        (BadApi(), "unsupported API version"),
        (Valid(), "already loaded"),
        (BadRegister(), "registration failed"),
    ):
        try:
            registry.register_extension(extension, source="test")
        except Exception as error:  # noqa: BLE001 - asserting public errors
            assert message in str(error)
        else:  # pragma: no cover
            raise AssertionError("expected error")


def test_extension_loader_supports_entry_points_and_local_get_extension(
    monkeypatch, tmp_path: Path
) -> None:
    from pid.extensions import load_enabled_extensions

    class EntryPointExtension:
        name = "entry"
        api_version = "1"

        def register(self, registry):
            registry.add_cli_command("entry", lambda _ctx: 0)

    class FakeEntryPoint:
        name = "entry"

        def load(self):
            return EntryPointExtension

    monkeypatch.setattr("pid.extensions._entry_points", lambda: [FakeEntryPoint()])
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_extension = "\n".join(
        [
            "class LocalExtension:",
            "    name = 'local'",
            "    api_version = '1'",
            "    def register(self, registry):",
            "        registry.add_cli_command('local', lambda _ctx: 0)",
            "",
            "def get_extension():",
            "    return LocalExtension()",
            "",
        ]
    )
    (local_dir / "local_ext.py").write_text(local_extension)
    config = ExtensionConfig(
        enabled=("entry", "local"), paths=(str(local_dir),), config={}
    )
    registry = ExtensionRegistry()

    load_enabled_extensions(config, registry, repo_root=tmp_path)

    assert registry.loaded_extension_names == {"entry", "local"}
    assert set(registry.cli_commands) == {"entry", "local"}


def test_extension_loader_reports_missing_paths_modules_and_enabled_names(
    tmp_path: Path,
) -> None:
    from pid.extensions import load_enabled_extensions

    for config, message in (
        (
            ExtensionConfig(enabled=("missing",), paths=(), config={}),
            "enabled extension not found",
        ),
        (
            ExtensionConfig(
                enabled=("missing",), paths=(str(tmp_path / "missing"),), config={}
            ),
            "extension path does not exist",
        ),
    ):
        try:
            load_enabled_extensions(config, ExtensionRegistry(), repo_root=tmp_path)
        except Exception as error:  # noqa: BLE001 - asserting public errors
            assert message in str(error)
        else:  # pragma: no cover
            raise AssertionError("expected error")

    not_dir = tmp_path / "file.py"
    not_dir.write_text("")
    try:
        load_enabled_extensions(
            ExtensionConfig(enabled=("x",), paths=(str(not_dir),), config={}),
            ExtensionRegistry(),
            repo_root=tmp_path,
        )
    except Exception as error:  # noqa: BLE001 - asserting public errors
        assert "not a directory" in str(error)

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "bad.py").write_text("raise RuntimeError('boom')")
    try:
        load_enabled_extensions(
            ExtensionConfig(enabled=("bad",), paths=(str(bad_dir),), config={}),
            ExtensionRegistry(),
            repo_root=tmp_path,
        )
    except Exception as error:  # noqa: BLE001 - asserting public errors
        assert "could not load extension module" in str(error)


def test_event_sinks_and_context_helpers(tmp_path: Path) -> None:
    from pid.commands import CommandRunner
    from pid.context import WorkflowContext
    from pid.events import CompositeEventSink, ListEventSink, NullEventSink
    from pid.forge import Forge
    from pid.models import CommitMessage, ParsedArgs
    from pid.repository import Repository

    event = WorkflowEvent("demo", message="hello")
    NullEventSink().emit(event)
    first = ListEventSink()
    second = ListEventSink()
    CompositeEventSink(first, second).emit(event)
    assert first.events == [event]
    assert second.events == [event]

    path = tmp_path / "events" / "events.jsonl"
    JsonlEventSink(path).emit(event)
    assert json.loads(path.read_text())["message"] == "hello"

    runner = CommandRunner()
    config = parse_config({}, tmp_path / "config.toml")
    context = WorkflowContext(
        argv=["feature/x", "prompt"],
        config=config,
        runner=runner,
        repository=Repository(runner),
        forge=Forge(runner, config.forge),
        registry=ExtensionRegistry(),
        events=first,
    )
    assert context.extension_config == {}
    assert context.branch == ""
    for helper in (context.require_parsed, context.require_worktree, context.repo_path):
        try:
            helper()
        except RuntimeError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected RuntimeError")
    context.parsed = ParsedArgs(3, "medium", "feature/x", "prompt", False, None)
    context.repo_root = str(tmp_path)
    context.worktree_path = str(tmp_path / "worktree")
    context.set_commit_message(CommitMessage("feat: x", "body"))
    context.emit("custom")
    assert context.branch == "feature/x"
    assert context.require_parsed().branch == "feature/x"
    assert context.require_worktree().endswith("worktree")
    assert context.repo_path() == tmp_path
    assert context.pr_title == "feat: x"
    assert first.events[-1].name == "custom"


def test_pid_x_builtin_errors_and_extensions_list(tmp_path: Path) -> None:
    runner = CliRunner()

    missing = runner.invoke(app, ["x"])
    assert missing.exit_code == 2
    assert "usage: pid x" in missing.stderr

    listed = runner.invoke(app, ["x", "extensions", "list"])
    assert listed.exit_code == 0
    assert "no enabled pid extensions" in listed.output

    unknown = runner.invoke(app, ["x", "missing"])
    assert unknown.exit_code == 2
    assert "unknown extension command" in unknown.stderr

    config_path = tmp_path / "config.toml"
    config_path.write_text("[extensions]\nenabled = ['missing']\n")
    bad_config = runner.invoke(app, ["--config", str(config_path), "x", "missing"])
    assert bad_config.exit_code == 2
    assert "enabled extension not found" in bad_config.stderr


def test_extension_command_errors_are_mapped_to_exit_codes(tmp_path: Path) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    fail_extension = "\n".join(
        [
            "from pid.errors import abort",
            "from pid.extensions import ExtensionError",
            "def abort_cmd(ctx):",
            "    abort(5)",
            "def extension_error(ctx):",
            "    raise ExtensionError('bad extension')",
            "def boom(ctx):",
            "    raise RuntimeError('boom')",
            "class FailExtension:",
            "    name = 'fail'",
            "    api_version = '1'",
            "    def register(self, registry):",
            "        registry.add_cli_command('abort', abort_cmd)",
            "        registry.add_cli_command('extension-error', extension_error)",
            "        registry.add_cli_command('boom', boom)",
            "extension = FailExtension()",
            "",
        ]
    )
    (extension_dir / "fail.py").write_text(fail_extension)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"[extensions]\nenabled = ['fail']\npaths = ['{extension_dir}']\n"
    )
    runner = CliRunner()

    assert (
        runner.invoke(app, ["--config", str(config_path), "x", "abort"]).exit_code == 5
    )
    extension_error = runner.invoke(
        app, ["--config", str(config_path), "x", "extension-error"]
    )
    assert extension_error.exit_code == 2
    assert "bad extension" in extension_error.stderr
    boom = runner.invoke(app, ["--config", str(config_path), "x", "boom"])
    assert boom.exit_code == 1
    assert "extension command boom failed" in boom.stderr


def test_invalid_extension_config_is_rejected(tmp_path: Path, capsys) -> None:
    from pid.errors import PIDAbort

    invalid_cases = [
        ({"extensions": []}, "[extensions] must be a table"),
        ({"extensions": {"enabled": ["demo", "demo"]}}, "duplicates"),
        ({"extensions": {"paths": [""]}}, "empty strings"),
        ({"extensions": {"demo": "bad"}}, "[extensions.demo] must be a table"),
    ]

    for data, message in invalid_cases:
        try:
            parse_config(data, tmp_path / "config.toml")
        except PIDAbort as error:
            assert error.code == 2
            assert message in capsys.readouterr().err
        else:  # pragma: no cover
            raise AssertionError("expected config error")


def test_workflow_engine_handles_extension_results_and_errors(
    tmp_path: Path,
) -> None:
    from pid.commands import CommandRunner
    from pid.context import WorkflowContext
    from pid.forge import Forge
    from pid.repository import Repository

    config = parse_config({}, tmp_path / "config.toml")
    runner = CommandRunner()
    flow = PIDFlow(runner=runner, config=config, load_extensions=False)
    context = WorkflowContext(
        argv=["feature/x", "prompt"],
        config=config,
        runner=runner,
        repository=Repository(runner),
        forge=Forge(runner, config.forge),
        registry=flow.registry,
    )
    seen: list[str] = []

    flow.registry.disable_step("disabled")
    run_engine_step(
        flow, context, WorkflowStep("disabled", lambda _ctx: seen.append("bad"))
    )
    assert seen == []

    flow.registry.add_hook("before.skipped", lambda _ctx: StepResult.skip("skip it"))
    run_engine_step(
        flow, context, WorkflowStep("skipped", lambda _ctx: seen.append("bad"))
    )
    assert seen == []

    retries = {"count": 0}

    def retry_once(_ctx):
        retries["count"] += 1
        return StepResult.retry("again") if retries["count"] == 1 else None

    run_engine_step(flow, context, WorkflowStep("retry_once", retry_once))
    assert retries["count"] == 2

    flow.registry.add_hook("after.after_stop", lambda _ctx: StepResult.stop(6))
    with pytest.raises(PIDAbort) as stop_info:
        run_engine_step(flow, context, WorkflowStep("after_stop", lambda _ctx: None))
    assert stop_info.value.code == 6

    flow.registry.add_hook("error.failing", lambda _ctx: seen.append("error hook"))
    with pytest.raises(ValueError):
        run_engine_step(
            flow,
            context,
            WorkflowStep(
                "failing", lambda _ctx: (_ for _ in ()).throw(ValueError("boom"))
            ),
        )
    assert "error hook" in seen

    with pytest.raises(PIDAbort) as retry_limit_info:
        run_engine_step(
            flow,
            context,
            WorkflowStep("retry_forever", lambda _ctx: StepResult.retry()),
        )
    assert retry_limit_info.value.code == 1

    with pytest.raises(ExtensionError, match="StepResult or None"):
        run_engine_step(flow, context, WorkflowStep("bad_result", lambda _ctx: 42))

    flow.registry.add_hook("before.bad_hook_result", lambda _ctx: "bad")
    with pytest.raises(ExtensionError, match="StepResult or None"):
        run_engine_step(
            flow, context, WorkflowStep("bad_hook_result", lambda _ctx: None)
        )

    flow.engine.handle_step_result(StepResult.retry())
    with pytest.raises(ExtensionError):
        flow.engine.handle_step_result(StepResult("unknown"))


def test_pid_flow_applies_service_replacements(tmp_path: Path) -> None:
    from pid.commands import CommandRunner
    from pid.context import WorkflowContext
    from pid.forge import Forge
    from pid.repository import Repository

    config = parse_config({}, tmp_path / "config.toml")
    runner = CommandRunner()
    flow = PIDFlow(runner=runner, config=config, load_extensions=False)
    context = WorkflowContext(
        argv=["feature/x", "prompt"],
        config=config,
        runner=runner,
        repository=Repository(runner),
        forge=Forge(runner, config.forge),
        registry=flow.registry,
    )
    new_runner = CommandRunner()
    new_repository = Repository(new_runner)
    new_forge = Forge(new_runner, config.forge)
    flow.registry.replace_service("runner", lambda _ctx: new_runner)
    flow.registry.replace_service("repository", lambda _ctx: new_repository)
    flow.registry.replace_service("forge", lambda _ctx: new_forge)

    flow.apply_service_replacements(context)

    assert flow.runner is new_runner
    assert flow.repository is new_repository
    assert flow.forge is new_forge
    assert context.services == {
        "runner": new_runner,
        "repository": new_repository,
        "forge": new_forge,
    }


def test_pid_flow_maps_extension_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_load(*_args, **_kwargs):
        raise ExtensionError("bad load")

    monkeypatch.setattr("pid.workflow.load_enabled_extensions", fail_load)
    with pytest.raises(PIDAbort) as exc_info:
        PIDFlow(config=parse_config({}, Path("config.toml")))
    assert exc_info.value.code == 2

    flow = PIDFlow(config=parse_config({}, Path("config.toml")), load_extensions=False)
    monkeypatch.setattr(
        flow,
        "_run",
        lambda _argv: (_ for _ in ()).throw(ExtensionError("bad run")),
    )
    assert flow.run(["feature/x", "prompt"]) == 2


def test_registry_resolve_steps_covers_before_append_and_duplicate_insertions() -> None:
    before = ExtensionRegistry()
    before.add_step(WorkflowStep("zero", lambda _ctx: None), before="one")
    before.add_step(WorkflowStep("tail", lambda _ctx: None))
    assert [
        step.name
        for step in before.resolve_steps([WorkflowStep("one", lambda _ctx: None)])
    ] == [
        "zero",
        "one",
        "tail",
    ]

    duplicate = ExtensionRegistry()
    duplicate.add_step(WorkflowStep("one", lambda _ctx: None))
    with pytest.raises(ExtensionError):
        duplicate.resolve_steps([WorkflowStep("one", lambda _ctx: None)])

    empty = ExtensionRegistry()
    with pytest.raises(ExtensionError):
        empty.resolve_steps([WorkflowStep("", lambda _ctx: None)])

    disabled = ExtensionRegistry()
    disabled.add_step(WorkflowStep("extra", lambda _ctx: None), after="one")
    disabled.disable_step("extra")
    assert [
        step.name
        for step in disabled.resolve_steps([WorkflowStep("one", lambda _ctx: None)])
    ] == ["one", "extra"]


def test_extension_loader_edge_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pid.extensions import (
        _entry_points,
        abort_extension_error,
        load_enabled_extensions,
    )

    class NoRegister:
        name = "no-register"
        api_version = "1"

    with pytest.raises(ExtensionError):
        ExtensionRegistry().register_extension(NoRegister(), source="test")

    class OtherEntryPoint:
        name = "other"

        def load(self):
            raise AssertionError("should be skipped")

    class BadEntryPoint:
        name = "bad"

        def load(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "pid.extensions._entry_points", lambda: [OtherEntryPoint(), BadEntryPoint()]
    )
    with pytest.raises(ExtensionError) as bad_load:
        load_enabled_extensions(
            ExtensionConfig(enabled=("bad",), paths=(), config={}), ExtensionRegistry()
        )
    assert "could not load extension bad" in str(bad_load.value)

    monkeypatch.setattr(
        "pid.extensions.importlib.metadata.entry_points",
        lambda group: [OtherEntryPoint()] if group == "pid.extensions" else [],
    )
    assert _entry_points()[0].name == "other"

    local_dir = tmp_path / "relative"
    local_dir.mkdir()
    (local_dir / "_ignored.py").write_text("raise RuntimeError('ignored')")
    (local_dir / "class_ext.py").write_text(
        "class ClassExtension:\n"
        "    name = 'classy'\n"
        "    api_version = '1'\n"
        "    def register(self, registry):\n"
        "        registry.add_cli_command('classy', lambda _ctx: 0)\n"
    )
    registry = ExtensionRegistry()
    load_enabled_extensions(
        ExtensionConfig(enabled=("classy",), paths=("relative",), config={}),
        registry,
        repo_root=tmp_path,
        include_entry_points=False,
    )
    assert "classy" in registry.cli_commands

    with pytest.raises(PIDAbort):
        abort_extension_error(ExtensionError("bad"))


def test_pid_x_lists_loaded_extensions(tmp_path: Path) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    (extension_dir / "demo.py").write_text(DEMO_EXTENSION)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"[extensions]\nenabled = ['demo']\npaths = ['{extension_dir}']\n"
    )

    result = CliRunner().invoke(
        app, ["--config", str(config_path), "x", "extensions", "list"]
    )

    assert result.exit_code == 0
    assert "demo" in result.output
    assert "local:" in result.output


def test_command_diagnostic_joins_stdout_and_stderr_with_newline() -> None:
    assert command_diagnostic(CommandResult(1, "out", "err")) == "out\nerr"
