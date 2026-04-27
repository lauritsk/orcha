from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

FAKE_COMMAND = r"""
import json
import os
import shutil
import sys
from pathlib import Path

state_path = Path(os.environ["ORCHA_FAKE_STATE"])


def load_state():
    return json.loads(state_path.read_text())


def save_state(state):
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))


def write_stream(stream, value):
    if value:
        stream.write(value)
        if not value.endswith("\n"):
            stream.write("\n")


def finish(state, status=0, out="", err=""):
    save_state(state)
    write_stream(sys.stdout, out)
    write_stream(sys.stderr, err)
    raise SystemExit(status)


def pop_count(state, key):
    state[key] = int(state.get(key, 0)) + 1
    return state[key]


def real_args(args):
    cwd = os.getcwd()
    if len(args) >= 2 and args[0] == "-C":
        cwd = args[1]
        args = args[2:]
    return cwd, args


def is_main(state, cwd):
    return os.path.realpath(cwd) == os.path.realpath(state["main_worktree"])


def is_worktree(state, cwd):
    worktree = state.get("worktree_path")
    return bool(worktree) and os.path.realpath(cwd) == os.path.realpath(worktree)


def git_status(state, cwd):
    if is_main(state, cwd):
        if state.get("main_status_fail"):
            return 1, ""
        return 0, state.get("main_dirty", "")
    return 0, state.get("worktree_dirty", "")


def ensure_worktree(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    Path(path, ".git").mkdir(exist_ok=True)


def remove_rebase_dirs(state):
    worktree = state.get("worktree_path")
    if not worktree:
        return
    for name in ("rebase-merge", "rebase-apply"):
        shutil.rmtree(Path(worktree, ".git", name), ignore_errors=True)


def cmd_git(state, original_args):
    cwd, args = real_args(original_args)

    if args[:2] == ["check-ref-format", "--branch"]:
        branch = args[2]
        invalid = state.get("invalid_branches", [])
        finish(state, 1 if branch in invalid else 0)

    if args == ["rev-parse", "--show-toplevel"]:
        if state.get("repo_root_fail"):
            finish(state, 1, err="not a git repository")
        finish(state, 0, state["repo_root"])

    if args == ["rev-parse", "--path-format=absolute", "--git-common-dir"]:
        if state.get("common_git_dir_fail"):
            finish(state, 1, err="no common git dir")
        finish(state, 0, state["common_git_dir"])

    if args == ["rev-parse", "HEAD"]:
        finish(state, 0, state.get("head", state["base_rev"]))

    if args == ["rev-parse", "--path-format=absolute", "--git-dir"]:
        git_dir = str(Path(cwd, ".git"))
        Path(git_dir).mkdir(parents=True, exist_ok=True)
        finish(state, 0, git_dir)

    if args == ["status", "--porcelain", "--untracked-files=all"]:
        status, out = git_status(state, cwd)
        finish(state, status, out)

    if args == ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"]:
        if state.get("symbolic_ref_empty"):
            finish(state, 1)
        finish(state, 0, f"origin/{state['default_branch']}")

    if args[:3] == ["show-ref", "--verify", "--quiet"]:
        ref = args[3]
        default = state["default_branch"]
        branch = state.get("branch", "")
        if ref == f"refs/heads/{default}":
            finish(state, 0 if state.get("local_default_exists", True) else 1)
        if ref == f"refs/remotes/origin/{default}":
            finish(state, 0 if state.get("remote_default_exists", False) else 1)
        if ref == f"refs/heads/{branch}":
            finish(state, 0 if state.get("branch_exists", False) else 1)
        if ref == f"refs/remotes/origin/{branch}":
            finish(state, 0 if state.get("remote_branch_exists", False) else 1)
        finish(state, 1)

    if args and args[0] == "switch":
        if state.get("switch_fail"):
            finish(state, 1, err="switch failed")
        finish(state)

    if args and args[0] == "pull":
        if pop_count(state, "pull_calls") <= int(state.get("pull_fail_times", 0)):
            finish(state, 1, err="pull failed")
        finish(state)

    if args == ["config", "extensions.worktreeConfig", "true"]:
        if state.get("worktree_config_global_fail"):
            finish(state, 1, err="config failed")
        finish(state)

    if args == ["config", "--worktree", "commit.gpgSign", "false"]:
        if state.get("worktree_config_fail"):
            finish(state, 1, err="worktree config failed")
        finish(state)

    if args[:2] == ["worktree", "add"]:
        if state.get("worktree_add_fail"):
            finish(state, 1, err="worktree add failed")
        path = args[2]
        branch = args[4]
        state["worktree_path"] = path
        state["branch"] = branch
        ensure_worktree(path)
        finish(state)

    if args[:2] == ["worktree", "remove"]:
        force = "--force" in args
        path = args[-1]
        if state.get("worktree_remove_fail") and not force:
            finish(state, 1, err="worktree remove failed")
        shutil.rmtree(path, ignore_errors=True)
        finish(state)

    if args[:2] == ["branch", "-D"]:
        finish(state)

    if args[:2] == ["rev-list", "--count"]:
        finish(state, 0, str(state.get("commit_count", 0)))

    if args == ["diff", "--binary", "--no-ext-diff"]:
        finish(state, 0, state.get("worktree_diff", ""))

    if args == ["diff", "--cached", "--binary", "--no-ext-diff"]:
        finish(state, 0, state.get("cached_diff", ""))

    if args == ["ls-files", "--others", "--exclude-standard"]:
        finish(state, 0, state.get("untracked_files", ""))

    if args == ["add", "-A"]:
        finish(state)

    if args[:2] == ["commit", "-m"]:
        if state.get("commit_fail"):
            finish(state, 1, err="commit failed")
        title = args[2]
        state["last_commit_title"] = title
        state.setdefault("commit_messages", []).append(title)
        state["commit_count"] = max(1, int(state.get("commit_count", 0)) + 1)
        if state.get("dirty_after_commit"):
            state["worktree_dirty"] = state.get("dirty_after_commit_value", " M still-dirty\n")
        else:
            state["worktree_dirty"] = ""
        state["head"] = f"commit-{state['commit_count']}"
        finish(state)

    if args == ["log", "-1", "--format=%s"]:
        if state.get("dirty_after_log_once") and not state.get("dirty_after_log_used"):
            state["dirty_after_log_used"] = True
            state["worktree_dirty"] = state["dirty_after_log_once"]
        finish(state, 0, state.get("last_commit_title", state.get("branch_commit_title", "chore: work")))

    if args and args[0] == "push":
        if state.get("push_fail"):
            finish(state, 1, err="push failed")
        finish(state)

    if args and args[0] == "fetch":
        if state.get("fetch_fail"):
            finish(state, 1, err="fetch failed")
        finish(state)

    if args and args[0] == "rebase":
        if state.get("rebase_conflict_once") and not state.get("rebase_conflict_used"):
            state["rebase_conflict_used"] = True
            git_dir = Path(state["worktree_path"], ".git")
            Path(git_dir, "rebase-merge").mkdir(parents=True, exist_ok=True)
            finish(state, 1, err="rebase conflict")
        if state.get("rebase_fail"):
            finish(state, 1, err="rebase failed")
        if state.get("dirty_after_rebase_success"):
            state["worktree_dirty"] = state["dirty_after_rebase_success"]
        finish(state)

    finish(state, 97, err="unhandled git: " + " ".join(args))


def cmd_cog(state, args):
    if args[:1] == ["verify"]:
        title = args[1]
        state["branch_commit_title"] = title
        if state.get("cog_fail"):
            finish(state, int(state.get("cog_status", 2)), err="bad conventional commit")
        finish(state, 0, state.get("cog_out", ""))
    finish(state, 97, err="unhandled cog: " + " ".join(args))


def checks_item(state):
    seq = state.get("checks_sequence", [{"status": 0, "out": "checks passed"}])
    index = int(state.get("checks_index", 0))
    state["checks_index"] = index + 1
    if index >= len(seq):
        item = seq[-1]
    else:
        item = seq[index]
    return item


def merge_item(state):
    seq = state.get("merge_sequence", [{"status": 0, "out": "merged"}])
    index = int(state.get("merge_index", 0))
    state["merge_index"] = index + 1
    if index >= len(seq):
        item = seq[-1]
    else:
        item = seq[index]
    state["last_merge_status"] = int(item.get("status", 0))
    return item


def cmd_gh(state, args):
    if args[:2] == ["repo", "view"]:
        if state.get("default_branch_query_fail"):
            finish(state, 1, err="no default branch")
        finish(state, 0, state["default_branch"])

    if args[:2] == ["pr", "view"]:
        target = args[2]
        rest = args[3:]
        if not rest:
            exists = state.get("pr_exists", state.get("pr_exists_before", False))
            finish(state, 0 if exists else 1)
        if rest == ["--json", "url", "--jq", ".url"]:
            finish(state, 0, state.get("pr_url", "https://example.invalid/pr/1"))
        if rest == ["--json", "headRefOid", "--jq", ".headRefOid"]:
            finish(state, 0, state.get("head_ref_oid", "abc123"))
        if rest == ["--json", "mergedAt", "--jq", ".mergedAt // \"\""]:
            if state.get("merged_at_query_fail"):
                finish(state, 1, err="mergedAt query failed")
            if state.get("last_merge_status") == 0:
                finish(state, 0, state.get("merged_at_after_success", "2026-01-01T00:00:00Z"))
            if state.get("merged_after_failed_merge"):
                finish(state, 0, state.get("merged_at_after_failure", "2026-01-01T00:00:00Z"))
            finish(state, 0, "")
        finish(state, 97, err="unhandled gh pr view: " + " ".join(args))

    if args[:2] == ["pr", "create"]:
        if state.get("pr_create_fail"):
            finish(state, 1, err="pr create failed")
        state["pr_exists"] = True
        state["pr_title"] = args[args.index("--title") + 1]
        finish(state, 0, state.get("pr_url", "https://example.invalid/pr/1"))

    if args[:2] == ["pr", "edit"]:
        if state.get("pr_edit_fail"):
            finish(state, 1, err="pr edit failed")
        state["pr_exists"] = True
        state["pr_title"] = args[args.index("--title") + 1]
        finish(state)

    if args[:2] == ["pr", "checks"]:
        item = checks_item(state)
        finish(state, int(item.get("status", 0)), item.get("out", ""), item.get("err", ""))

    if args[:2] == ["pr", "merge"]:
        item = merge_item(state)
        finish(state, int(item.get("status", 0)), item.get("out", ""), item.get("err", ""))

    finish(state, 97, err="unhandled gh: " + " ".join(args))


def prompt_from_args(args):
    if "-p" in args:
        return args[args.index("-p") + 1]
    return ""


def thinking_from_args(args):
    if "--thinking" in args:
        return args[args.index("--thinking") + 1]
    return ""


def cmd_pi(state, args):
    prompt = prompt_from_args(args)
    if prompt.startswith("Review the work"):
        kind = "review"
    elif prompt.startswith("CI checks failed"):
        kind = "ci_fix"
    elif prompt.startswith("GitHub squash merge failed"):
        kind = "rebase_fix"
    else:
        kind = "initial"

    state.setdefault("pi_calls", []).append(
        {"kind": kind, "thinking": thinking_from_args(args), "prompt": prompt, "args": args}
    )

    if kind in state.get("pi_fail_kinds", []):
        finish(state, int(state.get("pi_fail_status", 42)), err=f"pi {kind} failed")

    if kind == "initial" and state.get("initial_pi_commit_count") is not None:
        state["commit_count"] = state["initial_pi_commit_count"]

    if kind == "review":
        if state.get("review_changes"):
            state["worktree_diff"] = state.get("review_changed_diff", "review changed diff")
        if state.get("review_sets_dirty"):
            state["worktree_dirty"] = state["review_sets_dirty"]

    if kind == "ci_fix":
        state["worktree_dirty"] = state.get("dirty_after_ci_fix", " M ci-fix\n")

    if kind == "rebase_fix":
        if not state.get("rebase_still_in_progress"):
            remove_rebase_dirs(state)
        if state.get("dirty_after_rebase_fix"):
            state["worktree_dirty"] = state["dirty_after_rebase_fix"]

    finish(state)


def cmd_mise(state, args):
    if args == ["trust", "."]:
        if state.get("mise_trust_fail"):
            finish(state, 1, err="mise trust failed")
        finish(state)
    finish(state, 97, err="unhandled mise: " + " ".join(args))


def main():
    state = load_state()
    cmd = Path(sys.argv[0]).name
    args = sys.argv[1:]
    state.setdefault("calls", []).append({"cmd": cmd, "args": args, "cwd": os.getcwd()})

    dispatch = {
        "git": cmd_git,
        "cog": cmd_cog,
        "gh": cmd_gh,
        "pi": cmd_pi,
        "mise": cmd_mise,
    }
    if cmd not in dispatch:
        finish(state, 127, err=f"unknown fake command: {cmd}")
    dispatch[cmd](state, args)


main()
"""


def base_state(
    tmp_path: Path, *, branch: str = "feature/cool-stuff", **overrides: Any
) -> dict[str, Any]:
    repo = tmp_path / "orcha"
    repo.mkdir()
    (repo / ".git").mkdir()
    state: dict[str, Any] = {
        "repo_root": str(repo),
        "main_worktree": str(repo),
        "common_git_dir": str(repo / ".git"),
        "base_rev": "base123",
        "head": "base123",
        "default_branch": "main",
        "local_default_exists": True,
        "remote_default_exists": False,
        "branch": branch,
        "commit_count": 0,
        "worktree_dirty": " M file.txt\n",
        "worktree_diff": "diff --git a/file.txt b/file.txt\n",
        "cached_diff": "",
        "main_dirty": "",
        "pr_url": "https://example.invalid/pr/1",
    }
    state.update(overrides)
    return state


def install_fake_commands(bin_dir: Path, commands: tuple[str, ...]) -> None:
    script = bin_dir / "fake-command.py"
    script.write_text(f"#!{sys.executable}\n{FAKE_COMMAND}")
    script.chmod(0o755)
    for command in commands:
        os.symlink(script, bin_dir / command)


def run_orcha(
    tmp_path: Path,
    args: list[str],
    *,
    state: dict[str, Any] | None = None,
    commands: tuple[str, ...] = (
        "git",
        "cog",
        "pi",
        "gh",
        "mise",
    ),
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    state = state or base_state(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    install_fake_commands(bin_dir, commands)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "ORCHA_FAKE_STATE": str(state_path),
            "TERM": "dumb",
            "NO_COLOR": "1",
            "PYTHONPATH": str(SRC),
            "ORCHA_CHECKS_TIMEOUT_SECONDS": str(
                state.get("checks_timeout_seconds", 1800)
            ),
            "ORCHA_CHECKS_POLL_INTERVAL_SECONDS": str(
                state.get("checks_poll_interval_seconds", 0)
            ),
        }
    )
    process = subprocess.run(
        [sys.executable, "-m", "orcha", *args],
        cwd=state["repo_root"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    final_state = json.loads(state_path.read_text())
    return process, final_state


def calls(state: dict[str, Any], cmd: str, *prefix: str) -> list[dict[str, Any]]:
    matching = [call for call in state.get("calls", []) if call["cmd"] == cmd]
    if not prefix:
        return matching
    return [call for call in matching if call["args"][: len(prefix)] == list(prefix)]


def combined_output(process: subprocess.CompletedProcess[str]) -> str:
    return process.stdout + process.stderr


def assert_success(process: subprocess.CompletedProcess[str]) -> None:
    assert process.returncode == 0, combined_output(process)


def test_no_args_prints_short_usage(tmp_path: Path) -> None:
    process, _ = run_orcha(tmp_path, [], commands=())

    assert_success(process)
    assert process.stdout == "usage: orcha [ATTEMPTS] [THINKING] BRANCH PROMPT...\n"


@pytest.mark.parametrize("args", [["--help"], ["-h"]])
def test_help_uses_typer_output(tmp_path: Path, args: list[str]) -> None:
    process, _ = run_orcha(tmp_path, args, commands=())

    assert_success(process)
    assert "Run Orcha." in process.stdout
    assert "[ATTEMPTS] [THINKING] BRANCH" in process.stdout


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
    assert "usage: orcha [ATTEMPTS] [THINKING] BRANCH PROMPT..." in process.stderr


def test_invalid_branch_name_stops_before_repo_setup(tmp_path: Path) -> None:
    state = base_state(tmp_path, branch="bad branch", invalid_branches=["bad branch"])

    process, final_state = run_orcha(
        tmp_path, ["bad branch", "prompt"], state=state, commands=("git",)
    )

    assert process.returncode == 1
    assert "orcha: invalid branch name: bad branch" in process.stderr
    assert calls(final_state, "git", "check-ref-format")
    assert not calls(final_state, "git", "rev-parse", "--show-toplevel")


@pytest.mark.parametrize(
    ("branch", "expected_title"),
    [
        ("feature/add_api", "feat: add api"),
        ("fix/---", "fix: work"),
        ("weird/add-thing", "chore: weird add thing"),
        ("docs(scope)!/update-readme", "docs(scope)!: update readme"),
    ],
)
def test_commit_title_is_derived_from_branch(
    tmp_path: Path, branch: str, expected_title: str
) -> None:
    state = base_state(tmp_path, branch=branch)

    process, final_state = run_orcha(
        tmp_path,
        [branch, "prompt"],
        state=state,
        commands=("git", "cog"),
    )

    assert process.returncode == 1
    assert "pi is required" in process.stderr
    assert ["verify", expected_title] in [
        call["args"] for call in calls(final_state, "cog")
    ]


@pytest.mark.parametrize(
    ("overrides", "commands", "message", "code"),
    [
        ({"repo_root_fail": True}, ("git",), "not inside a git repository", 1),
        ({}, ("git",), "cog is required", 1),
        (
            {"cog_fail": True, "cog_status": 7},
            ("git", "cog"),
            "bad conventional commit",
            7,
        ),
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
    assert "╭─ orcha commit message" in process.stdout
    assert "╭──── orcha github squash merged" in process.stdout
    assert final_state["commit_messages"] == ["feat: cool stuff"]
    assert final_state["pi_calls"][0]["thinking"] == "high"
    assert final_state["pi_calls"][0]["prompt"] == "build thing"
    assert ["pr", "create", "--title", "feat: cool stuff", "--body", ""] in [
        call["args"] for call in calls(final_state, "gh", "pr", "create")
    ]
    assert ["push", "-u", "origin", "feature/cool-stuff"] in [
        call["args"][-4:] for call in calls(final_state, "git", "push")
    ]
    assert ["push", "origin", "--delete", "feature/cool-stuff"] in [
        call["args"][-4:] for call in calls(final_state, "git", "push")
    ]


def test_existing_commits_with_dirty_review_changes_commit_followup(
    tmp_path: Path,
) -> None:
    state = base_state(tmp_path, commit_count=1, last_commit_title="feat: existing")

    process, final_state = run_orcha(
        tmp_path, ["feature/cool-stuff", "prompt"], state=state
    )

    assert_success(process)
    assert "fix: address follow-up changes" in final_state["commit_messages"]


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
