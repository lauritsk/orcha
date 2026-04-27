from pathlib import Path

import pytest

import pid.config as config_module
from pid.config import default_config_path, init_config, load_config, parse_config
from pid.errors import PIDAbort
from pid.models import CommitMessage


def test_default_config_path_uses_absolute_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg_config_home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))

    assert default_config_path() == xdg_config_home / "pid" / "config.toml"


def test_default_config_path_ignores_relative_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    monkeypatch.setattr(config_module.sys, "platform", "linux")

    assert default_config_path() == home / ".config" / "pid" / "config.toml"


def test_default_config_path_uses_macos_application_support(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config_module.sys, "platform", "darwin")

    assert (
        default_config_path()
        == home / "Library" / "Application Support" / "pid" / "config.toml"
    )


def test_explicit_missing_config_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PIDAbort) as exc_info:
        load_config(tmp_path / "missing.toml")

    assert exc_info.value.code == 2


def test_default_missing_config_path_uses_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "missing-config-home"))

    assert load_config() == config_module.PIDConfig()


def test_init_config_writes_recommended_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))

    config_path = init_config()

    assert config_path == tmp_path / "config-home" / "pid" / "config.toml"
    assert load_config() == config_module.PIDConfig()
    assert "wrote config to" in capsys.readouterr().out


def test_init_config_refuses_to_overwrite_existing_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    config_path = default_config_path()
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[runtime]\nkeep_screen_awake = true\n")

    with pytest.raises(PIDAbort) as exc_info:
        init_config()

    assert exc_info.value.code == 2
    assert config_path.read_text() == "[runtime]\nkeep_screen_awake = true\n"
    assert "config file already exists" in capsys.readouterr().err


def test_runtime_keep_screen_awake_defaults_off(tmp_path: Path) -> None:
    loaded = parse_config({}, tmp_path / "config.toml")

    assert loaded.runtime.keep_screen_awake is False


def test_runtime_keep_screen_awake_can_be_enabled(tmp_path: Path) -> None:
    loaded = parse_config(
        {"runtime": {"keep_screen_awake": True}}, tmp_path / "config.toml"
    )

    assert loaded.runtime.keep_screen_awake is True


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


def test_invalid_runtime_config_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(PIDAbort) as exc_info:
        parse_config(
            {"runtime": {"keep_screen_awake": "yes"}}, tmp_path / "config.toml"
        )

    assert exc_info.value.code == 2
    assert "runtime.keep_screen_awake must be a boolean" in capsys.readouterr().err


def test_forge_command_and_args_are_configurable(tmp_path: Path) -> None:
    config = parse_config(
        {
            "forge": {
                "command": "glab --repo group/project",
                "label": "gitlab",
                "pr_checks_args": [],
                "pr_head_oid_args": [],
                "pr_merge_args": [
                    "mr",
                    "merge",
                    "{branch}",
                    "--squash",
                    "--title",
                    "{title}",
                    "--description",
                    "{body}",
                ],
                "pr_merged_at_args": [],
                "checks_pending_exit_codes": [2, 3],
                "no_checks_markers": ["no pipeline"],
            }
        },
        tmp_path / "config.toml",
    )

    message = CommitMessage("feat: configurable forge", "- Adds forge config.")
    assert config.forge.command == ("glab", "--repo", "group/project")
    assert config.forge.command_line(
        config.forge.pr_merge_args,
        branch="feature/x",
        title=message.title,
        body=message.body,
    ) == [
        "glab",
        "--repo",
        "group/project",
        "mr",
        "merge",
        "feature/x",
        "--squash",
        "--title",
        "feat: configurable forge",
        "--description",
        "- Adds forge config.",
    ]
    assert config.forge.merge_uses_head_oid is False
    assert config.forge.checks_pending_exit_codes == (2, 3)


def test_prompts_workflow_and_commit_config_are_configurable(tmp_path: Path) -> None:
    config = parse_config(
        {
            "commit": {
                "verifier_args": [],
                "automated_feedback_title": "fix: configured feedback",
                "rebase_feedback_title": "fix: configured rebase",
            },
            "prompts": {
                "review": "CUSTOM REVIEW {review_target} :: {original_prompt}",
                "message": "CUSTOM MESSAGE {branch}\nOutput path: {output_path}",
                "ci_fix": "CUSTOM CI {pr_title} {checks_out}",
                "rebase_fix": "CUSTOM REBASE {forge_label} {merge_out}",
                "diagnostic_output_limit": 12,
            },
            "workflow": {
                "checks_timeout_seconds": 5,
                "checks_poll_interval_seconds": 1,
                "merge_retry_limit": 2,
                "trust_mise": False,
            },
        },
        tmp_path / "config.toml",
    )

    assert config.commit.verifier_enabled is False
    assert config.commit.automated_feedback_title == "fix: configured feedback"
    assert config.prompts.review.startswith("CUSTOM REVIEW")
    assert config.prompts.diagnostic_output_limit == 12
    assert config.workflow.checks_timeout_seconds == 5
    assert config.workflow.trust_mise is False


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
    with pytest.raises(PIDAbort) as exc_info:
        parse_config({"agent": agent_data}, tmp_path / "config.toml")

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"prompts": {"review": "bad {cwd}"}}, "unsupported placeholder {cwd}"),
        (
            {
                "forge": {
                    "pr_merge_args": ["merge", "{head_oid}"],
                    "pr_head_oid_args": [],
                }
            },
            "pr_head_oid_args must not be empty",
        ),
        ({"workflow": {"trust_mise": "no"}}, "workflow.trust_mise must be a boolean"),
        ({"prompts": {"message": "write metadata"}}, "must include {output_path}"),
        ({"prompts": {"ci_fix": "   "}}, "prompts.ci_fix must not be blank"),
        (
            {"forge": {"no_checks_markers": [" "]}},
            "forge.no_checks_markers must not contain blank strings",
        ),
        (
            {"commit": {"verifier_args": ["--bad", "{body}"]}},
            "unsupported placeholder {body}",
        ),
    ],
)
def test_invalid_non_agent_config_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    data: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(PIDAbort) as exc_info:
        parse_config(data, tmp_path / "config.toml")

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err
