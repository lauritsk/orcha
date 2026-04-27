from __future__ import annotations

import importlib
import importlib.metadata
import io
import runpy
import sys
from pathlib import Path
from typing import Any, cast

import pytest

import pid
import pid.config as config_module
import pid.cli as cli_module
from pid.commands import CommandRunner, require_command
from pid.config import AgentConfig, load_config, parse_config
from pid.errors import PIDAbort
from pid.models import CommandResult
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
