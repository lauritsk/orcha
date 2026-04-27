from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fakes import (
    assert_success,
    base_state,
    calls,
    combined_output,
    run_orcha,
)


def test_no_args_prints_short_usage(tmp_path: Path) -> None:
    process, _ = run_orcha(tmp_path, [], commands=())

    assert_success(process)
    assert (
        process.stdout
        == "usage: orcha [session] [ATTEMPTS] [THINKING] BRANCH [PROMPT...]\n"
    )


@pytest.mark.parametrize("args", [["--help"], ["-h"]])
def test_help_uses_typer_output(tmp_path: Path, args: list[str]) -> None:
    process, _ = run_orcha(tmp_path, args, commands=())

    assert_success(process)
    assert "Run Orcha." in process.stdout
    assert "[session] [ATTEMPTS] [THINKING] BRANCH" in process.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["0", "feature/x", "prompt"], "ATTEMPTS must be a positive integer"),
        (["01", "feature/x", "prompt"], "ATTEMPTS must be a positive integer"),
        (["high"], "branch required"),
        (["feature/x"], "prompt required"),
        (["", "prompt"], "branch must be non-empty"),
    ],
)
def test_argument_validation_errors(
    tmp_path: Path, args: list[str], message: str
) -> None:
    process, _ = run_orcha(tmp_path, args, commands=())

    assert process.returncode == 2
    assert message in process.stderr
    assert (
        "usage: orcha [session] [ATTEMPTS] [THINKING] BRANCH [PROMPT...]"
        in process.stderr
    )


def test_invalid_branch_name_stops_before_repo_setup(tmp_path: Path) -> None:
    state = base_state(tmp_path, branch="bad branch", invalid_branches=["bad branch"])

    process, final_state = run_orcha(
        tmp_path, ["bad branch", "prompt"], state=state, commands=("git",)
    )

    assert process.returncode == 1
    assert "orcha: invalid branch name: bad branch" in process.stderr
    assert calls(final_state, "git", "check-ref-format")
    assert not calls(final_state, "git", "rev-parse", "--show-toplevel")


def test_generated_commit_title_is_validated_with_cog(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        branch="work/rough-name",
        generated_commit_title="docs: explain setup flow",
    )

    process, final_state = run_orcha(
        tmp_path,
        ["work/rough-name", "prompt"],
        state=state,
    )

    assert_success(process)
    assert ["verify", "docs: explain setup flow"] in [
        call["args"] for call in calls(final_state, "cog")
    ]


@pytest.mark.parametrize(
    ("overrides", "commands", "message", "code"),
    [
        ({"repo_root_fail": True}, ("git",), "not inside a git repository", 1),
        ({}, ("git",), "cog is required", 1),
        ({}, ("git", "cog"), "pi is required", 1),
        ({}, ("git", "cog", "pi"), "gh is required", 1),
    ],
)
def test_preflight_dependency_failures(
    tmp_path: Path,
    overrides: dict[str, Any],
    commands: tuple[str, ...],
    message: str,
    code: int,
) -> None:
    state = base_state(tmp_path, **overrides)

    process, _ = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state, commands=commands
    )

    assert process.returncode == code
    assert message in combined_output(process)


@pytest.mark.parametrize(
    ("overrides", "expected_call", "expected_message", "expected_code"),
    [
        ({}, ["switch", "main"], "branch already exists", 1),
        (
            {"local_default_exists": False, "remote_default_exists": True},
            ["switch", "--track", "origin/main"],
            "branch already exists",
            1,
        ),
        (
            {"symbolic_ref_empty": True, "default_branch": "trunk"},
            ["switch", "trunk"],
            "branch already exists",
            1,
        ),
        (
            {"symbolic_ref_empty": True, "default_branch_query_fail": True},
            None,
            "could not determine default branch",
            1,
        ),
        (
            {"local_default_exists": False, "remote_default_exists": False},
            None,
            "default branch not found locally: main",
            1,
        ),
    ],
)
def test_default_branch_resolution(
    tmp_path: Path,
    overrides: dict[str, Any],
    expected_call: list[str] | None,
    expected_message: str,
    expected_code: int,
) -> None:
    state = base_state(tmp_path, branch_exists=True, **overrides)

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert process.returncode == expected_code
    assert expected_message in combined_output(process)
    if expected_call is not None:
        assert expected_call in [
            call["args"][-len(expected_call) :] for call in calls(final_state, "git")
        ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"switch_fail": True}, "switch failed"),
        ({"pull_fail_times": 1}, "pull failed"),
    ],
)
def test_default_branch_switch_or_pull_failures_return_error(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    state = base_state(tmp_path, **overrides)

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert message in process.stderr


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"common_git_dir_fail": True}, "could not determine common git dir"),
        (
            {"main_dirty": " M dirty\n"},
            "main worktree has uncommitted or untracked changes",
        ),
        ({"branch_exists": True}, "branch already exists"),
        ({"remote_branch_exists": True}, "remote branch already exists"),
    ],
)
def test_worktree_precreation_guards(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    state = base_state(tmp_path, **overrides)

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert message in process.stderr


def test_existing_worktree_path_is_rejected(tmp_path: Path) -> None:
    state = base_state(tmp_path)
    Path(state["repo_root"]).with_name("orcha-feature-cool-stuff").mkdir()

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert "path already exists" in process.stderr


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"worktree_config_global_fail": True}, "config failed"),
        ({"worktree_add_fail": True}, "worktree add failed"),
    ],
)
def test_worktree_setup_failures_return_error(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    state = base_state(tmp_path, **overrides)

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert message in process.stderr


def test_worktree_config_failure_cleans_up(tmp_path: Path) -> None:
    state = base_state(tmp_path, worktree_config_fail=True)

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert process.returncode == 1
    assert "failed to configure worktree" in process.stderr
    assert ["worktree", "remove", "--force", final_state["worktree_path"]] in [
        call["args"][-4:] for call in calls(final_state, "git")
    ]
    assert ["branch", "-D", "feature/cool-stuff"] in [
        call["args"][-3:] for call in calls(final_state, "git")
    ]


def test_mise_trust_failure_stops_after_worktree_creation(tmp_path: Path) -> None:
    state = base_state(tmp_path, mise_trust_fail=True)

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert process.returncode == 1
    assert "mise trust failed" in process.stderr
    assert calls(final_state, "mise", "trust", ".")
    assert not calls(final_state, "pi")


def test_mise_is_optional_when_not_on_path(tmp_path: Path) -> None:
    state = base_state(tmp_path, worktree_dirty="", worktree_diff="")

    process, final_state = run_orcha(
        tmp_path,
        ["feature/cool-stuff", "prompt"],
        state=state,
        commands=("git", "cog", "pi", "gh"),
    )

    assert_success(process)
    assert "no changes or commits after pi" in process.stdout
    assert not calls(final_state, "mise")


def test_initial_pi_failure_stops_before_review(tmp_path: Path) -> None:
    state = base_state(tmp_path, pi_fail_kinds=["initial"], pi_fail_status=13)

    process, final_state = run_orcha(
        tmp_path, ["low", "feature/cool-stuff", "do work"], state=state
    )

    assert process.returncode == 13
    assert "pi exited with status 13" in process.stderr
    assert [call["kind"] for call in final_state["pi_calls"]] == ["initial"]
    assert final_state["pi_calls"][0]["thinking"] == "low"


def test_session_mode_runs_interactive_pi_then_resumes_flow(tmp_path: Path) -> None:
    state = base_state(tmp_path)

    process, final_state = run_orcha(
        tmp_path,
        ["session", "2", "high", "feature/cool-stuff", "explore", "idea"],
        state=state,
    )

    assert_success(process)
    assert "launching interactive pi session" in process.stdout
    assert "interactive pi session exited; resuming review/PR flow" in process.stdout
    assert "orcha: PR attempt 1/2" in process.stdout
    initial_call = final_state["pi_calls"][0]
    assert initial_call["kind"] == "initial"
    assert initial_call["interactive"] is True
    assert "-p" not in initial_call["args"]
    assert initial_call["thinking"] == "high"
    assert initial_call["prompt"] == "explore idea"
    assert final_state["pi_calls"][1]["kind"] == "review"


def test_session_mode_allows_no_initial_prompt(tmp_path: Path) -> None:
    state = base_state(tmp_path, pi_fail_kinds=["review"], pi_fail_status=17)

    process, final_state = run_orcha(
        tmp_path, ["session", "feature/cool-stuff"], state=state
    )

    assert process.returncode == 17
    initial_call = final_state["pi_calls"][0]
    review_call = final_state["pi_calls"][1]
    assert initial_call["interactive"] is True
    assert initial_call["prompt"] == ""
    assert "Original request: Interactive pi session." in review_call["prompt"]


def test_session_pi_failure_stops_before_review(tmp_path: Path) -> None:
    state = base_state(tmp_path, pi_fail_kinds=["initial"], pi_fail_status=13)

    process, final_state = run_orcha(
        tmp_path, ["session", "low", "feature/cool-stuff"], state=state
    )

    assert process.returncode == 13
    assert "pi exited with status 13" in process.stderr
    assert [call["kind"] for call in final_state["pi_calls"]] == ["initial"]
    assert final_state["pi_calls"][0]["interactive"] is True


@pytest.mark.parametrize(
    ("overrides", "expected_review_target"),
    [
        (
            {"commit_count": 2, "worktree_dirty": ""},
            "Review the commits in base123..HEAD.",
        ),
        (
            {"commit_count": 0, "worktree_dirty": " M file.txt\n"},
            "Review the uncommitted changes in this worktree.",
        ),
        (
            {"commit_count": 0, "worktree_dirty": "", "worktree_diff": ""},
            "No commits or uncommitted changes exist yet",
        ),
    ],
)
def test_review_prompt_targets_commits_dirty_or_empty_work(
    tmp_path: Path, overrides: dict[str, Any], expected_review_target: str
) -> None:
    state = base_state(
        tmp_path,
        pi_fail_kinds=["review"],
        pi_fail_status=17,
        **overrides,
    )

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "original request"], state=state
    )

    assert process.returncode == 17
    review_call = final_state["pi_calls"][1]
    assert review_call["kind"] == "review"
    assert review_call["thinking"] == "high"
    assert expected_review_target in review_call["prompt"]
    assert "Original request: original request" in review_call["prompt"]


def test_no_changes_after_review_stops_before_pr(tmp_path: Path) -> None:
    state = base_state(tmp_path, worktree_dirty="", worktree_diff="")

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    assert "no changes or commits after pi" in process.stdout
    assert not calls(final_state, "gh", "pr", "create")


def test_dirty_work_is_committed_then_pr_created_and_merged(tmp_path: Path) -> None:
    state = base_state(tmp_path)

    process, final_state = run_orcha(
        tmp_path,
        ["2", "high", "feature/cool-stuff", "build", "thing"],
        state=state,
    )

    assert_success(process)
    assert "Created" in process.stdout
    assert "orcha: PR attempt 1/2" in process.stdout
    assert "orcha commit message" in process.stdout
    assert "╭──── orcha github squash merged" in process.stdout
    assert final_state["commit_messages"] == ["feat: implement cool stuff"]
    assert final_state["commit_bodies"] == [
        "- Implements the requested cool stuff.\n- Updates tests and docs as needed."
    ]
    assert final_state["pi_calls"][0]["thinking"] == "high"
    assert final_state["pi_calls"][0]["prompt"] == "build thing"
    assert final_state["pi_calls"][2]["kind"] == "message"
    assert final_state["pi_calls"][2]["thinking"] == "high"
    assert "Original request: build thing" in final_state["pi_calls"][2]["prompt"]
    assert [
        "pr",
        "create",
        "--title",
        "feat: implement cool stuff",
        "--body",
        "- Implements the requested cool stuff.\n- Updates tests and docs as needed.",
    ] in [call["args"] for call in calls(final_state, "gh", "pr", "create")]
    assert final_state["merge_subject"] == "feat: implement cool stuff"
    assert final_state["merge_body"] == (
        "- Implements the requested cool stuff.\n- Updates tests and docs as needed."
    )
    assert ["push", "-u", "origin", "feature/cool-stuff"] in [
        call["args"][-4:] for call in calls(final_state, "git", "push")
    ]
    assert ["push", "origin", "--delete", "feature/cool-stuff"] in [
        call["args"][-4:] for call in calls(final_state, "git", "push")
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"message_skip_output": True}, "did not write commit metadata"),
        ({"message_json": "not json"}, "not valid JSON"),
        ({"message_json": '{"title":"feat: ok","body":""}'}, "body is empty"),
        ({"message_agent_changes": True}, "pi message changed the worktree"),
        ({"cog_fail": True, "cog_status": 7}, "bad conventional commit"),
    ],
)
def test_message_generation_failures_stop_before_commit(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    state = base_state(tmp_path, **overrides)

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert process.returncode != 0
    assert message in combined_output(process)
    assert not final_state.get("commit_messages")
    assert not calls(final_state, "gh", "pr", "create")


def test_prompt_preserves_unknown_option_like_words(tmp_path: Path) -> None:
    state = base_state(tmp_path, worktree_dirty="", worktree_diff="")

    process, final_state = run_orcha(
        tmp_path,
        ["feature/cool-stuff", "use", "--flag", "value"],
        state=state,
    )

    assert_success(process)
    assert final_state["pi_calls"][0]["prompt"] == "use --flag value"


def test_existing_commits_are_squashed_to_generated_message(
    tmp_path: Path,
) -> None:
    state = base_state(tmp_path, commit_count=1, last_commit_title="feat: existing")

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    assert final_state["commit_messages"] == ["feat: implement cool stuff"]
    assert ["reset", "--soft", "base123"] in [
        call["args"] for call in calls(final_state, "git", "reset", "--soft")
    ]


def test_dirty_after_commit_stops_before_pr(tmp_path: Path) -> None:
    state = base_state(tmp_path, dirty_after_commit=True)

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert "worktree still has uncommitted changes after commit" in process.stderr


def test_commit_failure_returns_before_pr(tmp_path: Path) -> None:
    state = base_state(tmp_path, commit_fail=True)

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert "commit failed" in process.stderr


def test_dirty_at_pr_attempt_gets_automated_feedback_commit(tmp_path: Path) -> None:
    state = base_state(tmp_path, dirty_after_log_once=" M generated\n")

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    assert "fix: address automated feedback" in final_state["commit_messages"]


def test_existing_pr_no_checks_and_queued_merge(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        pr_exists_before=True,
        checks_sequence=[{"status": 1, "out": "no checks reported"}],
        merge_sequence=[{"status": 0, "out": "merge queued"}],
        merged_at_after_success="",
    )

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    assert "orcha: no CI checks reported; continuing" in process.stdout
    assert "likely queued or auto-merge enabled" in process.stdout
    assert calls(final_state, "gh", "pr", "edit")
    assert not calls(final_state, "gh", "pr", "create")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"push_fail": True}, "push failed"),
        ({"pr_create_fail": True}, "pr create failed"),
        ({"pr_exists_before": True, "pr_edit_fail": True}, "pr edit failed"),
    ],
)
def test_pr_setup_failures_return_error(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    state = base_state(tmp_path, **overrides)

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert message in process.stderr


def test_ci_failure_invokes_followup_pi_and_retries_with_bumped_thinking(
    tmp_path: Path,
) -> None:
    state = base_state(
        tmp_path,
        review_changes=True,
        checks_sequence=[
            {"status": 1, "out": "unit tests failed"},
            {"status": 0, "out": "checks passed"},
        ],
    )

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    ci_fix_calls = [
        call for call in final_state["pi_calls"] if call["kind"] == "ci_fix"
    ]
    assert len(ci_fix_calls) == 1
    assert ci_fix_calls[0]["thinking"] == "high"
    assert "unit tests failed" in ci_fix_calls[0]["prompt"]
    assert "fix: address automated feedback" in final_state["commit_messages"]
    assert (
        "review changed first pass; follow-up pi thinking bumped to high"
        in process.stdout
    )
    assert "orcha: PR attempt 2/3" in process.stdout


def test_ci_fix_regenerates_pr_and_squash_message(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        checks_sequence=[
            {"status": 1, "out": "unit tests failed"},
            {"status": 0, "out": "checks passed"},
        ],
        generated_messages=[
            {
                "title": "feat: add first draft",
                "body": "- Adds the initial implementation.",
            },
            {
                "title": "fix: harden generated workflow",
                "body": "- Adds the implementation.\n- Fixes CI failures.",
            },
        ],
    )

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    assert [call["kind"] for call in final_state["pi_calls"]].count("message") == 2
    assert final_state["pr_title"] == "fix: harden generated workflow"
    assert final_state["pr_body"] == "- Adds the implementation.\n- Fixes CI failures."
    assert final_state["merge_subject"] == "fix: harden generated workflow"
    assert (
        final_state["merge_body"] == "- Adds the implementation.\n- Fixes CI failures."
    )
    assert ["verify", "fix: harden generated workflow"] in [
        call["args"] for call in calls(final_state, "cog")
    ]


def test_ci_failure_on_last_attempt_leaves_pr_open(tmp_path: Path) -> None:
    state = base_state(tmp_path, checks_sequence=[{"status": 1, "out": "failed"}])

    process, _ = run_orcha(tmp_path, ["1", "feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert "CI checks failed after 1 attempts" in process.stderr


def test_ci_followup_pi_failure_returns_pi_status(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        checks_sequence=[{"status": 1, "out": "failed"}],
        pi_fail_kinds=["ci_fix"],
        pi_fail_status=19,
    )

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 19
    assert "pi exited with status 19 while fixing CI" in process.stderr


def test_pending_checks_time_out_and_fail_on_last_attempt(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        checks_sequence=[{"status": 8, "out": "still pending"}],
        checks_timeout_seconds=0,
    )

    process, _ = run_orcha(tmp_path, ["1", "feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 8
    assert "CI checks still pending after 0 seconds" in process.stderr
    assert "CI checks failed after 1 attempts" in process.stderr


def test_merge_failure_rebases_force_pushes_and_retries(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        merge_sequence=[
            {"status": 1, "err": "base branch moved"},
            {"status": 0, "out": "merged"},
        ],
        dirty_after_rebase_success=" M rebased\n",
    )

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    assert (
        "merge failed; rebasing onto latest origin/main before retry" in process.stdout
    )
    assert "fix: resolve latest base changes" in final_state["commit_messages"]
    assert ["push", "--force-with-lease", "-u", "origin", "feature/cool-stuff"] in [
        call["args"][-5:] for call in calls(final_state, "git", "push")
    ]


def test_fetch_failure_after_merge_failure_returns_error(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        merge_sequence=[{"status": 1, "err": "base branch moved"}],
        fetch_fail=True,
    )

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert "fetch failed" in process.stderr


def test_rebase_conflict_invokes_pi_resolution(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        merge_sequence=[
            {"status": 1, "err": "conflict"},
            {"status": 0, "out": "merged"},
        ],
        rebase_conflict_once=True,
        dirty_after_rebase_fix=" M resolved\n",
    )

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    rebase_calls = [
        call for call in final_state["pi_calls"] if call["kind"] == "rebase_fix"
    ]
    assert len(rebase_calls) == 1
    assert "rebase onto origin/main is now in progress" in rebase_calls[0]["prompt"]
    assert "fix: resolve latest base changes" in final_state["commit_messages"]


def test_rebase_still_in_progress_after_pi_stops(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        merge_sequence=[
            {"status": 1, "err": "conflict"},
            {"status": 0, "out": "merged"},
        ],
        rebase_conflict_once=True,
        rebase_still_in_progress=True,
    )

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert "rebase still in progress after pi" in process.stderr


def test_merge_failure_but_github_reports_merged_cleans_up(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        merge_sequence=[{"status": 1, "err": "cleanup failed"}],
        merged_after_failed_merge=True,
    )

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    assert "GitHub reports PR merged despite local gh cleanup failure" in process.stdout
    assert ["worktree", "remove", final_state["worktree_path"]] in [
        call["args"][-3:] for call in calls(final_state, "git")
    ]


def test_merge_confirmation_failure_returns_error(tmp_path: Path) -> None:
    state = base_state(tmp_path, merged_at_query_fail=True)

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert "merged state could not be confirmed" in process.stderr


def test_merge_success_without_merged_at_leaves_pr_for_queue(tmp_path: Path) -> None:
    state = base_state(tmp_path, merged_at_after_success="")

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert_success(process)
    assert "likely queued or auto-merge enabled" in process.stdout


def test_merge_failure_after_last_attempt_leaves_pr_open(tmp_path: Path) -> None:
    state = base_state(tmp_path, merge_sequence=[{"status": 9, "err": "merge blocked"}])

    process, _ = run_orcha(tmp_path, ["1", "feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 9
    assert "github squash merge failed after 1 attempts" in process.stderr


def test_cleanup_worktree_remove_failure_is_reported(tmp_path: Path) -> None:
    state = base_state(tmp_path, worktree_remove_fail=True)

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 1
    assert "worktree remove failed" in process.stderr


def test_rebase_pi_failure_returns_pi_status(tmp_path: Path) -> None:
    state = base_state(
        tmp_path,
        merge_sequence=[
            {"status": 1, "err": "conflict"},
            {"status": 0, "out": "merged"},
        ],
        rebase_conflict_once=True,
        pi_fail_kinds=["rebase_fix"],
        pi_fail_status=23,
    )

    process, _ = run_orcha(tmp_path, ["feature/cool-stuff", "prompt"], state=state)

    assert process.returncode == 23
    assert "pi exited with status 23 while resolving rebase" in process.stderr
