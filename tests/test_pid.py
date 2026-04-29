import os
from pathlib import Path

import pytest
from click.utils import strip_ansi
from typer.testing import CliRunner

from pid.cli import app
from pid.diagnostics import _pid_is_live, _toml_value

runner = CliRunner()


def test_app_prints_short_usage_by_default() -> None:
    result = runner.invoke(app)

    assert result.exit_code == 0
    assert (
        result.output
        == "usage: pid [session] [ATTEMPTS] [THINKING] BRANCH [PROMPT...]\n"
    )


def test_app_shows_typer_help() -> None:
    result = runner.invoke(app, ["--help"])
    output = strip_ansi(result.output)

    assert result.exit_code == 0
    assert "Run pid. Use `pid init` to create the default config." in output
    assert "[session] [ATTEMPTS] [THINKING] BRANCH" in output
    assert "--config" in output
    assert "pid sessions [--all|-a]" in output
    assert "pid config show|default|path" in output
    assert "--output" in output
    assert "normal|agent|all" in output
    assert "Show this message" in output


def test_init_command_writes_default_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    result = runner.invoke(app, ["init"])

    config_path = tmp_path / "xdg" / "pid" / "config.toml"
    assert result.exit_code == 0
    assert f"pid: wrote config to {config_path}" in result.output
    assert config_path.exists()


def test_init_command_rejects_arguments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    result = runner.invoke(app, ["init", "extra"])

    assert result.exit_code == 2
    assert "pid: init does not accept arguments" in result.stderr
    assert not (tmp_path / "xdg" / "pid" / "config.toml").exists()


def test_init_command_rejects_explicit_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    result = runner.invoke(app, ["--config", str(tmp_path / "custom.toml"), "init"])

    assert result.exit_code == 2
    assert "pid: init does not accept --config" in result.stderr
    assert not (tmp_path / "xdg" / "pid" / "config.toml").exists()


def test_invalid_config_returns_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent]\ndefault_thinking = 1\n")

    result = runner.invoke(app, ["--config", str(config_path), "feature/x", "prompt"])

    assert result.exit_code == 2
    assert "agent.default_thinking must be a string" in result.stderr


def test_version_option_prints_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith("pid ")


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.startswith("pid ")


def test_version_command_rejects_extra_args() -> None:
    result = runner.invoke(app, ["version", "extra"])

    assert result.exit_code == 2
    assert "usage: pid version" in result.stderr


def test_default_config_command_prints_toml() -> None:
    result = runner.invoke(app, ["config", "default"])

    assert result.exit_code == 0
    assert "[agent]" in result.output
    assert 'command = ["pi"]' in result.output
    assert "[workflow]" in result.output


def test_bare_config_command_returns_usage_error() -> None:
    result = runner.invoke(app, ["config"])

    assert result.exit_code == 2
    assert "usage: pid config show|default|path" in result.stderr


def test_current_config_command_prints_loaded_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[agent]\nlabel = "bot"\n')

    result = runner.invoke(app, ["--config", str(config_path), "config", "show"])

    assert result.exit_code == 0
    assert 'label = "bot"' in result.output


def test_current_config_command_reports_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent]\ndefault_thinking = 1\n")

    result = runner.invoke(app, ["--config", str(config_path), "config", "show"])

    assert result.exit_code == 2
    assert "agent.default_thinking must be a string" in result.stderr


def test_config_path_command_prints_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    result = runner.invoke(app, ["--config", str(config_path), "config", "path"])

    assert result.exit_code == 0
    assert f'config_path = "{config_path}"' in result.output
    assert "log_dir = " in result.output


def test_sessions_lists_active_logs(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "pid-session-test.log"
    log_path.write_text(
        "=" * 88
        + "\nSESSION START\n"
        + "=" * 88
        + f"\nstarted: 2026-04-27T00:00:00.000Z\npid: {os.getpid()}\n"
        + "cwd: /repo\nargv: pid session feature/x\n"
        + "STEP START: agent interactive session\n"
    )

    result = runner.invoke(app, ["sessions"], env={"PID_LOG_DIR": str(log_dir)})

    assert result.exit_code == 0
    assert "active" in result.output
    assert "agent interactive session" in result.output
    assert str(log_path) in result.output


def test_sessions_without_logs_reports_none(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["sessions"], env={"PID_LOG_DIR": str(tmp_path / "none")}
    )

    assert result.exit_code == 0
    assert result.output == "no active pid sessions\n"


def test_unknown_sessions_command_returns_usage_error() -> None:
    result = runner.invoke(app, ["sessions", "--bogus"])

    assert result.exit_code == 2
    assert "usage: pid sessions [--all|-a]" in result.stderr


def test_sessions_all_includes_complete_and_stale_logs(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "pid-session-complete.log").write_text(
        "started: now\npid: 999999\ncwd: /done\nargv: pid done\nSESSION END\n"
    )
    (log_dir / "pid-session-stale.log").write_text(
        "started: before\npid: 999999\ncwd: /stale\nargv: pid stale\n"
        "STEP START: review\nSTEP END: review\n"
    )

    result = runner.invoke(
        app, ["sessions", "--all"], env={"PID_LOG_DIR": str(log_dir)}
    )
    short_result = runner.invoke(
        app, ["sessions", "-a"], env={"PID_LOG_DIR": str(log_dir)}
    )

    assert result.exit_code == 0
    assert short_result.exit_code == 0
    assert "complete" in result.output
    assert "ended" in result.output
    assert "stale" in result.output
    assert "running" in result.output
    assert short_result.output == result.output


def test_diagnostics_helpers_cover_edge_cases() -> None:
    assert _pid_is_live(999999) is False
    assert _toml_value(True) == "true"
    assert _toml_value(3) == "3"
    try:
        _toml_value(object())
    except TypeError as error:
        assert "unsupported TOML value" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected TypeError")
