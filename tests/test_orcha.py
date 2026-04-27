from typer.testing import CliRunner

from orcha.cli import app

runner = CliRunner()


def test_app_prints_greeting():
    result = runner.invoke(app)

    assert result.exit_code == 0
    assert result.output == "Hello from orcha!\n"


def test_app_shows_help():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Run Orcha." in result.output
