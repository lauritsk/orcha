from typer.testing import CliRunner

from orcha.cli import app

runner = CliRunner()


def test_app_prints_short_usage_by_default() -> None:
    result = runner.invoke(app)

    assert result.exit_code == 0
    assert (
        result.output
        == "usage: orcha [session] [ATTEMPTS] [THINKING] BRANCH [PROMPT...]\n"
    )


def test_app_shows_typer_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Run Orcha." in result.output
    assert "[session] [ATTEMPTS] [THINKING] BRANCH" in result.output
    assert "Show this message and exit" in result.output
