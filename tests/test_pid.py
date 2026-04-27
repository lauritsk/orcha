from pathlib import Path

from click.utils import strip_ansi
from typer.testing import CliRunner

from pid.cli import app

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
    assert "Run pid." in output
    assert "[session] [ATTEMPTS] [THINKING] BRANCH" in output
    assert "--config" in output
    assert "Show this message and exit" in output


def test_invalid_config_returns_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[agent]\ndefault_thinking = 1\n")

    result = runner.invoke(app, ["--config", str(config_path), "feature/x", "prompt"])

    assert result.exit_code == 2
    assert "agent.default_thinking must be a string" in result.stderr
