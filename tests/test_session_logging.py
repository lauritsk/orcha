from __future__ import annotations

import stat
from pathlib import Path

from pid.session_logging import LOG_DIR_ENV, SessionLogger, session_log_dir
from tests.fakes import assert_success, base_state, run_pid


def test_session_log_dir_uses_explicit_override(tmp_path: Path) -> None:
    override = tmp_path / "custom-logs"

    assert session_log_dir({LOG_DIR_ENV: str(override)}, home=tmp_path) == override


def test_session_log_dir_uses_xdg_state_home_when_set(tmp_path: Path) -> None:
    xdg_state = tmp_path / "xdg-state"

    assert (
        session_log_dir({"XDG_STATE_HOME": str(xdg_state)}, platform_name="Darwin")
        == xdg_state / "pid" / "logs"
    )


def test_session_log_dir_ignores_relative_xdg_state_home(tmp_path: Path) -> None:
    assert (
        session_log_dir(
            {"XDG_STATE_HOME": "relative-state"}, platform_name="Linux", home=tmp_path
        )
        == tmp_path / ".local" / "state" / "pid" / "logs"
    )


def test_session_log_dir_defaults_to_macos_native_logs(tmp_path: Path) -> None:
    assert (
        session_log_dir({}, platform_name="Darwin", home=tmp_path)
        == tmp_path / "Library" / "Logs" / "pid"
    )


def test_session_log_dir_defaults_to_xdg_state_home_layout(tmp_path: Path) -> None:
    assert (
        session_log_dir({}, platform_name="Linux", home=tmp_path)
        == tmp_path / ".local" / "state" / "pid" / "logs"
    )


def test_session_logger_creates_private_log_file(tmp_path: Path, monkeypatch) -> None:
    log_dir = tmp_path / "logs"
    monkeypatch.setenv(LOG_DIR_ENV, str(log_dir))

    logger = SessionLogger.create(["feature/private", "prompt"])
    logger.close()

    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(logger.path.stat().st_mode) == 0o600


def test_session_log_captures_agent_steps_commands_and_outputs(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        initial_pi_out="agent did work\n",
        review_pi_out="review agent output\n",
        generated_commit_title="feat: cool stuff",
        cog_out="cog verified\n",
    )

    process, _ = run_pid(
        tmp_path, ["feature/cool-stuff", "build", "thing"], state=state
    )

    assert_success(process)
    logs = sorted((tmp_path / "logs").glob("pid-session-*.log"))
    assert len(logs) == 1
    log = logs[0].read_text()
    assert "SESSION START" in log
    assert (
        "run summary: branch=feature/cool-stuff attempts=3 thinking=medium "
        "mode=normal" in log
    )
    assert "phase: Prepare - validate repo, branch, tools" in log
    assert "phase: Agent - create initial changes" in log
    assert "phase: Review - review uncommitted changes" in log
    assert "phase: Message + commit - generate metadata and create commit" in log
    assert "phase: PR attempt 1/3" in log
    assert "STEP START: agent initial" in log
    assert "COMMAND STDOUT" in log
    assert "agent did work" in log
    assert "STEP PASS: agent initial" in log
    assert "STEP START: agent review" in log
    assert "review agent output" in log
    assert "STEP START: agent message" in log
    assert "$ cog verify 'feat: cool stuff'" in log
    assert "cog verified" in log
    assert "github squash merged: feat: cool stuff" in log
    assert "exit code: 0" in log
