from pathlib import Path

import pytest

import orcha.config as config_module
from orcha.config import default_config_path, load_config, parse_config
from orcha.errors import OrchaAbort


def test_default_config_path_uses_absolute_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg_config_home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))

    assert default_config_path() == xdg_config_home / "orcha" / "config.toml"


def test_default_config_path_ignores_relative_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    monkeypatch.setattr(config_module.sys, "platform", "linux")

    assert default_config_path() == home / ".config" / "orcha" / "config.toml"


def test_default_config_path_uses_macos_application_support(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config_module.sys, "platform", "darwin")

    assert (
        default_config_path()
        == home / "Library" / "Application Support" / "orcha" / "config.toml"
    )


def test_explicit_missing_config_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(OrchaAbort) as exc_info:
        load_config(tmp_path / "missing.toml")

    assert exc_info.value.code == 2


def test_default_missing_config_path_uses_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "missing-config-home"))

    assert load_config() == config_module.OrchaConfig()


def test_agent_command_accepts_shell_style_string(tmp_path: Path) -> None:
    config = parse_config(
        {
            "agent": {
                "command": "agentx --profile dev",
                "non_interactive_args": ["run", "--message", "{prompt}"],
                "interactive_args": ["session"],
            }
        },
        tmp_path / "config.toml",
    )

    assert config.agent.command == ("agentx", "--profile", "dev")
    assert config.agent.interactive_command() == [
        "agentx",
        "--profile",
        "dev",
        "session",
    ]
    assert config.agent.non_interactive_command(
        prompt="do work", thinking="medium"
    ) == [
        "agentx",
        "--profile",
        "dev",
        "run",
        "--message",
        "do work",
    ]


@pytest.mark.parametrize(
    ("agent_data", "message"),
    [
        (
            {"non_interactive_args": ["-p", "{prompt}", "--cwd", "{cwd}"]},
            "unsupported placeholder {cwd}",
        ),
        (
            {"non_interactive_args": ["-p", "{prompt.__class__}"]},
            "unsupported placeholder {prompt.__class__}",
        ),
        ({"non_interactive_args": ["-p", "literal"]}, "must include {prompt}"),
        ({"thinking_levels": ["low", "low"]}, "must not contain duplicates"),
        ({"command": "unterminated 'quote"}, "valid shell-style string"),
    ],
)
def test_invalid_agent_config_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    agent_data: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(OrchaAbort) as exc_info:
        parse_config({"agent": agent_data}, tmp_path / "config.toml")

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err
