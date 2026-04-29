from __future__ import annotations

import importlib
import importlib.metadata
import io
import os
import re
import runpy
import sys
from pathlib import Path
from typing import Any, cast

import click
import pytest

import pid
import pid.config as config_module
import pid.cli as cli_module
import pid.interactive as interactive_module
from pid.commands import CommandRunner, require_command
from pid.config import AgentConfig, PIDConfig, load_config, parse_config
from pid.errors import PIDAbort
from pid.interactive import resolve_interactive_args
from pid.models import CommandResult, OutputMode
from pid.output import (
    get_session_logger,
    set_session_logger,
    write_collected,
    write_command_output,
)
from pid.parsing import bump_thinking, parse_args
from pid.repository import Repository
from pid.session_logging import CommandLogHandle, SessionLogger
from pid.utils import env_int, has_output, worktree_path_for
from pid.workflow import PIDFlow


def test_package_version_falls_back_when_metadata_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_version(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError("pid")

    with monkeypatch.context() as patch:
        patch.setattr(importlib.metadata, "version", missing_version)
        assert importlib.reload(pid).__version__ == "0.0.0"

    importlib.reload(pid)


def test_module_entrypoint_invokes_cli_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_app() -> None:
        calls.append("called")

    monkeypatch.setattr(cli_module, "app", fake_app)

    runpy.run_module("pid.__main__", run_name="__main__")

    assert calls == ["called"]


def test_agent_config_appends_interactive_prompt_when_template_has_no_prompt() -> None:
    config = AgentConfig(command=("agent",), interactive_args=("session",))

    assert config.interactive_command(prompt="look around", thinking="high") == [
        "agent",
        "session",
        "look around",
    ]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"unexpected": {}}, "unknown top-level key: unexpected"),
        ({"agent": []}, "[agent] must be a table"),
        ({"agent": {"unexpected": True}}, "unknown [agent] key: unexpected"),
        ({"agent": {"command": []}}, "agent.command must not be empty"),
        ({"agent": {"command": [""]}}, "agent.command executable must not be empty"),
        (
            {"agent": {"non_interactive_args": []}},
            "agent.non_interactive_args must not be empty",
        ),
        ({"agent": {"thinking_levels": []}}, "agent.thinking_levels must not be empty"),
        (
            {"agent": {"thinking_levels": ["low", ""]}},
            "agent.thinking_levels must not contain empty strings",
        ),
        ({"agent": {"label": ""}}, "agent.label must not be empty"),
        (
            {"agent": {"default_thinking": "huge"}},
            "agent.default_thinking must be in agent.thinking_levels",
        ),
        (
            {"agent": {"review_thinking": "huge"}},
            "agent.review_thinking must be in agent.thinking_levels",
        ),
        (
            {"agent": {"non_interactive_args": "-p {prompt}"}},
            "agent.non_interactive_args must be an array of strings",
        ),
        ({"agent": {"interactive_args": 1}}, "agent.interactive_args must be an array"),
        (
            {"agent": {"interactive_args": ["ok", 1]}},
            "agent.interactive_args must contain only strings",
        ),
        (
            {"agent": {"non_interactive_args": ["{prompt", "{prompt}"]}},
            "invalid placeholder syntax",
        ),
        (
            {"agent": {"non_interactive_args": ["{0}", "{prompt}"]}},
            "unsupported placeholder {0}",
        ),
    ],
)
def test_parse_config_rejects_edge_cases(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    data: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(PIDAbort) as exc_info:
        parse_config(data, tmp_path / "config.toml")

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_load_config_reports_invalid_toml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[agent\n", encoding="utf-8")

    with pytest.raises(PIDAbort) as exc_info:
        load_config(path)

    assert exc_info.value.code == 2
    assert "invalid config TOML" in capsys.readouterr().err


def test_load_config_reports_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.touch()

    def raise_oserror(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("boom")

    monkeypatch.setattr(config_module.Path, "read_text", raise_oserror)

    with pytest.raises(PIDAbort) as exc_info:
        load_config(path)

    assert exc_info.value.code == 2
    assert "could not read config" in capsys.readouterr().err


def test_load_config_reports_utf8_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.touch()

    def raise_unicode(self: Path, *args: object, **kwargs: object) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(config_module.Path, "read_text", raise_unicode)

    with pytest.raises(PIDAbort) as exc_info:
        load_config(path)

    assert exc_info.value.code == 2
    assert "config is not valid UTF-8" in capsys.readouterr().err


def test_command_runner_reports_missing_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = CommandRunner()

    result = runner.run(["definitely-missing-pid-command"])

    assert result == CommandResult(
        127, "", "pid: command not found: definitely-missing-pid-command\n"
    )
    with pytest.raises(PIDAbort) as exc_info:
        runner.output(["definitely-missing-pid-command"])
    assert exc_info.value.code == 127
    assert "command not found" in capsys.readouterr().err


def test_command_runner_quiet_output_suppresses_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(PIDAbort):
        CommandRunner().output(["definitely-missing-pid-command"], quiet=True)

    assert capsys.readouterr().err == ""


def test_command_runner_all_output_mode_echoes_successful_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = CommandRunner(output_mode=OutputMode.ALL).run(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ]
    )

    assert result == CommandResult(0, "out\n", "err\n")
    captured = capsys.readouterr()
    assert captured.out == "out\n"
    assert captured.err == "err\n"


def test_command_runner_run_interactive_missing_command() -> None:
    result = CommandRunner().run_interactive(["definitely-missing-pid-command"])

    assert result.returncode == 127
    assert "command not found" in result.stderr


def test_command_runner_require_quiet_suppresses_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(PIDAbort) as exc_info:
        CommandRunner().require(["definitely-missing-pid-command"], quiet=True)

    assert exc_info.value.code == 127
    assert capsys.readouterr().err == ""


def test_require_command_reports_missing_executable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("pid.commands.shutil.which", lambda _command: None)

    with pytest.raises(PIDAbort) as exc_info:
        require_command("tool", "tool missing")

    assert exc_info.value.code == 1
    assert "tool missing" in capsys.readouterr().err


def test_output_helpers_preserve_newline_behavior(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    set_session_logger(None)
    assert get_session_logger() is None
    write_collected("no newline", stream=sys.stdout)
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    write_command_output(CommandResult(1, "out", "err\n"))

    captured = capsys.readouterr()
    assert captured.out == "no newline\n"
    assert stdout.getvalue() == "out\n"
    assert stderr.getvalue() == "err\n"


class TTYStream:
    def isatty(self) -> bool:
        return True


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def test_interactive_display_renders_rich_panel_with_values() -> None:
    display = interactive_module._InteractiveDisplay()
    rendered = display._render(
        {
            "attempts": "3",
            "thinking": "medium",
            "branch": "feature/x",
            "prompt": "build thing",
        },
        error=None,
    )

    assert "╭" in rendered
    assert "pid prompt" in rendered
    assert "enter to accept defaults" in rendered
    assert "attempts" in rendered
    assert "feature/x" in rendered


def test_interactive_display_renders_validation_error() -> None:
    display = interactive_module._InteractiveDisplay()
    rendered = display._render(
        {
            "attempts": "3",
            "thinking": "medium",
            "branch": "(unset)",
            "prompt": "(unset)",
        },
        error="Enter a positive integer, e.g. 3.",
    )

    assert "✗ Enter a positive integer, e.g. 3." in rendered


def test_interactive_display_tty_width_matches_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pid.interactive.click.get_text_stream", lambda _name: TTYStream()
    )
    monkeypatch.setattr(
        "pid.interactive.shutil.get_terminal_size",
        lambda _fallback: os.terminal_size((40, 24)),
    )
    display = interactive_module._InteractiveDisplay()

    rendered = display._render(
        {
            "attempts": "3",
            "thinking": "medium",
            "branch": "feature/long-name-that-wraps",
            "prompt": "build a nicer interactive prompt UI",
        },
        error=None,
    )
    plain = ANSI_ESCAPE_RE.sub("", rendered)

    assert display._console.width == 40
    assert max(len(line) for line in plain.splitlines()) <= 40


def test_interactive_display_clears_previous_tty_render(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pid.interactive.click.get_text_stream", lambda _name: TTYStream()
    )
    display = interactive_module._InteractiveDisplay()
    values = {
        "attempts": "3",
        "thinking": "medium",
        "branch": "(unset)",
        "prompt": "(unset)",
    }

    display.render(values)
    display.render({**values, "branch": "feature/x"})

    output = capsys.readouterr().out
    assert "\033[6A" in output
    assert "\033[2K\r" in output


def test_interactive_display_clears_validation_error_tty_render(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pid.interactive.click.get_text_stream", lambda _name: TTYStream()
    )
    display = interactive_module._InteractiveDisplay()
    values = {
        "attempts": "3",
        "thinking": "medium",
        "branch": "(unset)",
        "prompt": "(unset)",
    }

    display.render(values, error="Enter a positive integer, e.g. 3.")
    capsys.readouterr()
    display.render(values)

    output = capsys.readouterr().out
    assert output.startswith("\033[7A")


def test_interactive_display_clears_wrapped_prompt_input(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pid.interactive.click.get_text_stream", lambda _name: TTYStream()
    )
    monkeypatch.setattr(
        "pid.interactive.shutil.get_terminal_size",
        lambda _fallback: os.terminal_size((40, 24)),
    )
    display = interactive_module._InteractiveDisplay()
    values = {
        "attempts": "3",
        "thinking": "medium",
        "branch": "feature/x",
        "prompt": "(unset)",
    }

    display.render(values)
    display.record_prompt_result(
        "Prompt (example: Add OAuth login and tests)",
        "please implement the orchestrator agent plan for this project",
    )
    capsys.readouterr()
    display.render(values)

    output = capsys.readouterr().out
    assert output.startswith("\033[9A")


def test_resolve_interactive_args_prompts_all_values_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(["3", "high", "feature/new-cli", "add guided prompts"])
    monkeypatch.setattr("pid.interactive.click.prompt", lambda *_, **__: next(answers))
    monkeypatch.setattr("pid.interactive.click.confirm", lambda *_, **__: True)

    assert resolve_interactive_args([], PIDConfig()) == [
        "3",
        "high",
        "feature/new-cli",
        "add",
        "guided",
        "prompts",
    ]


def test_resolve_interactive_args_prompts_only_missing_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pid.interactive.click.prompt", lambda *_, **__: "build thing")
    monkeypatch.setattr("pid.interactive.click.confirm", lambda *_, **__: True)

    assert resolve_interactive_args(["feature/x"], PIDConfig()) == [
        "feature/x",
        "build",
        "thing",
    ]


def test_resolve_interactive_args_preserves_help_and_complete_args() -> None:
    assert resolve_interactive_args(["--help"], PIDConfig()) == ["--help"]
    assert resolve_interactive_args(["session", "--help"], PIDConfig()) == [
        "session",
        "--help",
    ]
    assert resolve_interactive_args(
        ["2", "high", "feature/x", "build", "thing"], PIDConfig()
    ) == ["2", "high", "feature/x", "build", "thing"]


def test_resolve_interactive_args_prompts_branch_after_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(["feature/x", "build thing"])
    monkeypatch.setattr("pid.interactive.click.prompt", lambda *_, **__: next(answers))
    monkeypatch.setattr("pid.interactive.click.confirm", lambda *_, **__: True)

    assert resolve_interactive_args(["high"], PIDConfig()) == [
        "high",
        "feature/x",
        "build",
        "thing",
    ]


def test_resolve_interactive_args_reprompts_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(["0", "2", "bad", "medium", "", "feature/x", "build"])
    monkeypatch.setattr("pid.interactive.click.prompt", lambda *_, **__: next(answers))
    monkeypatch.setattr("pid.interactive.click.confirm", lambda *_, **__: True)

    assert resolve_interactive_args([], PIDConfig()) == [
        "2",
        "medium",
        "feature/x",
        "build",
    ]


def test_resolve_interactive_args_abort_on_rejected_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pid.interactive.click.prompt", lambda *_, **__: "feature/x")
    monkeypatch.setattr("pid.interactive.click.confirm", lambda *_, **__: False)

    with pytest.raises(click.Abort):
        resolve_interactive_args(["high"], PIDConfig())


def test_resolve_interactive_args_preserves_invalid_supplied_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_prompt(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("invalid supplied attempts should not prompt")

    monkeypatch.setattr("pid.interactive.click.prompt", fail_prompt)

    assert resolve_interactive_args(["0"], PIDConfig()) == ["0"]


def test_main_resolves_interactive_args_when_stdin_is_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeContext:
        args = ["prompt"]

    class FakeStdin:
        def isatty(self) -> bool:
            return True

    loaded_config = PIDConfig()
    calls: list[tuple[str, list[str]]] = []

    def fake_resolve(argv: list[str], config: PIDConfig) -> list[str]:
        assert config is loaded_config
        calls.append(("resolve", argv))
        return ["resolved-branch", "resolved prompt"]

    def fake_run_pid(
        argv: list[str], *, config: PIDConfig, output_mode: OutputMode
    ) -> int:
        assert config is loaded_config
        assert output_mode == OutputMode.NORMAL
        calls.append(("run", argv))
        return 7

    monkeypatch.setattr(cli_module.sys, "stdin", FakeStdin())
    monkeypatch.setattr(cli_module, "load_config", lambda _path: loaded_config)
    monkeypatch.setattr(cli_module, "resolve_interactive_args", fake_resolve)
    monkeypatch.setattr(cli_module, "run_pid", fake_run_pid)

    with pytest.raises(click.exceptions.Exit) as exc_info:
        cli_module.main(cast(Any, FakeContext()), args=["feature/x"], config=None)

    assert exc_info.value.exit_code == 7
    assert calls == [
        ("resolve", ["feature/x", "prompt"]),
        ("run", ["resolved-branch", "resolved prompt"]),
    ]


def test_parse_args_session_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(PIDAbort) as exc_info:
        parse_args(["session", "--help"])

    assert exc_info.value.code == 0
    assert "usage: pid session" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("level", "levels", "expected"),
    [
        ("unknown", ("low", "high"), "unknown"),
        ("high", ("low", "high"), "high"),
        ("low", ("low", "high"), "high"),
    ],
)
def test_bump_thinking_boundaries(
    level: str, levels: tuple[str, ...], expected: str
) -> None:
    assert bump_thinking(level, levels) == expected


def test_utils_env_int_and_path_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PID_TEST_INT", raising=False)
    assert env_int("PID_TEST_INT", 7) == 7
    monkeypatch.setenv("PID_TEST_INT", "not-int")
    assert env_int("PID_TEST_INT", 7) == 7
    monkeypatch.setenv("PID_TEST_INT", "11")
    assert env_int("PID_TEST_INT", 7) == 11
    assert has_output(" \n\t") is False
    assert has_output(" x ") is True
    assert worktree_path_for(str(tmp_path / "repo"), "feat/foo") == str(
        tmp_path / "repo-feat-foo"
    )


class StateHashRunner:
    def __init__(self, outputs: dict[tuple[str, ...], str]) -> None:
        self.outputs = outputs

    def output(self, args: list[str], *, cwd: str | Path) -> str:
        return self.outputs[tuple(args)]


def test_repository_state_hash_includes_untracked_file_content(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "tracked.txt").write_text("new file", encoding="utf-8")
    runner = StateHashRunner(
        {
            ("git", "rev-parse", "HEAD"): "abc123\n",
            (
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
            ): "?? tracked.txt\n?? dir\n",
            ("git", "diff", "--binary", "--no-ext-diff"): "",
            ("git", "diff", "--cached", "--binary", "--no-ext-diff"): "",
            ("git", "ls-files", "--others", "--exclude-standard"): "tracked.txt\ndir\n",
        }
    )
    repository = Repository(cast(CommandRunner, runner))

    first_hash = repository.state_hash(str(worktree))
    (worktree / "tracked.txt").write_text("changed", encoding="utf-8")
    second_hash = repository.state_hash(str(worktree))

    assert first_hash != second_hash


class UnclosedStringIO(io.StringIO):
    def close(self) -> None:
        pass


def test_session_logger_closes_open_step_once() -> None:
    stream = UnclosedStringIO()
    logger = SessionLogger(Path("session.log"), stream)

    logger.step_start("one")
    logger.step_start("two")
    logger.output_block("BLOCK", "value")
    logger.close()
    logger.close()

    log = stream.getvalue()
    assert "STEP END: one" in log
    assert "STEP END: two" in log
    assert log.count("SESSION END") == 1
    assert "BLOCK\nvalue\n" in log


def test_session_logger_records_command_exception() -> None:
    stream = io.StringIO()
    logger = SessionLogger(Path("session.log"), stream)

    logger.command_exception(CommandLogHandle(4, 0.0), RuntimeError("boom"))

    assert "COMMAND EXCEPTION #4" in stream.getvalue()
    assert "RuntimeError: boom" in stream.getvalue()


def test_pid_flow_disables_session_logging_when_log_creation_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "pid.workflow.SessionLogger.create",
        lambda _argv: (_ for _ in ()).throw(OSError("no log")),
    )
    flow = PIDFlow()

    flow.start_session_logging(["feature/x", "prompt"])
    flow.log_parsed_args(parse_args(["feature/x", "prompt"]))

    assert flow.session_logger is None
    assert "session logging disabled: no log" in capsys.readouterr().err
