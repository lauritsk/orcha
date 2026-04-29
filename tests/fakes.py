"""Fake command harness for pid flow tests."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from pid.cli import app

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

FAKE_COMMAND = r"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

state_path = Path(os.environ["PID_FAKE_STATE"])


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
        if state.get("worktree_remove_force_fail") and force:
            finish(state, 1, err="worktree remove failed")
        if state.get("worktree_remove_fail") and not force:
            finish(state, 1, err="worktree remove failed")
        shutil.rmtree(path, ignore_errors=True)
        finish(state)

    if args[:2] == ["branch", "-D"]:
        finish(state)

    if args[:2] == ["rev-list", "--count"]:
        finish(state, 0, str(state.get("commit_count", 0)))

    if args[:2] == ["merge-base", "--is-ancestor"]:
        ancestor = args[2]
        descendant = args[3]
        if ancestor == descendant:
            finish(state, 0)
        ancestor_map = state.get("ancestor_map", {})
        key = f"{ancestor}..{descendant}"
        if key in ancestor_map:
            finish(state, 0 if ancestor_map[key] else 1)
        if state.get("base_is_ancestor_sequence"):
            item = state["base_is_ancestor_sequence"].pop(0)
            state["base_is_ancestor"] = bool(item)
        finish(state, 0 if state.get("base_is_ancestor", True) else 1)

    if args[:3] == ["ls-remote", "--heads", "origin"]:
        branch = args[3]
        oid = state.get("remote_branch_oid", "")
        if state.get("ls_remote_fail"):
            finish(state, 1, err="ls-remote failed")
        if oid:
            finish(state, 0, f"{oid}\trefs/heads/{branch}")
        finish(state)

    if args == ["diff", "--binary", "--no-ext-diff"]:
        finish(state, 0, state.get("worktree_diff", ""))

    if args == ["diff", "--cached", "--binary", "--no-ext-diff"]:
        finish(state, 0, state.get("cached_diff", ""))

    if args == ["ls-files", "--others", "--exclude-standard"]:
        finish(state, 0, state.get("untracked_files", ""))

    if args == ["add", "-A"]:
        finish(state)

    if args[:2] == ["reset", "--soft"]:
        state["commit_count"] = 0
        state["head"] = args[2]
        finish(state)

    if args[:2] == ["commit", "-m"]:
        if state.get("commit_fail"):
            finish(state, 1, err="commit failed")
        title = args[2]
        body = args[4] if len(args) >= 5 and args[3] == "-m" else ""
        state["last_commit_title"] = title
        state.setdefault("commit_messages", []).append(title)
        state.setdefault("commit_bodies", []).append(body)
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
        finish(state, 0, state.get("last_commit_title", state.get("generated_commit_title", "chore: work")))

    if args and args[0] == "push":
        if state.get("push_fail"):
            finish(state, 1, err="push failed")
        if "--delete" in args:
            state["remote_branch_oid"] = ""
        else:
            state["remote_branch_oid"] = state.get("head", state["base_rev"])
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
        state["base_is_ancestor"] = True
        finish(state)

    finish(state, 97, err="unhandled git: " + " ".join(args))


def cmd_cog(state, args):
    if args[:1] in (["verify"], ["check"]):
        if "--message" in args:
            title = args[args.index("--message") + 1]
        else:
            title = args[1]
        state["verified_commit_title"] = title
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
            state["merged_at_queries"] = int(state.get("merged_at_queries", 0)) + 1
            if "merged_at_sequence" in state:
                sequence = state.get("merged_at_sequence", [])
                value = sequence.pop(0) if sequence else ""
                state["merged_at_sequence"] = sequence
                finish(state, 0, value)
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
        state["pr_body"] = args[args.index("--body") + 1]
        finish(state, 0, state.get("pr_url", "https://example.invalid/pr/1"))

    if args[:2] == ["pr", "edit"]:
        if state.get("pr_edit_fail"):
            finish(state, 1, err="pr edit failed")
        state["pr_exists"] = True
        state["pr_title"] = args[args.index("--title") + 1]
        state["pr_body"] = args[args.index("--body") + 1]
        finish(state)

    if args[:2] == ["pr", "checks"]:
        item = checks_item(state)
        finish(state, int(item.get("status", 0)), item.get("out", ""), item.get("err", ""))

    if args[:2] == ["pr", "merge"]:
        state["merge_subject"] = args[args.index("--subject") + 1]
        state["merge_body"] = args[args.index("--body") + 1]
        item = merge_item(state)
        finish(state, int(item.get("status", 0)), item.get("out", ""), item.get("err", ""))

    finish(state, 97, err="unhandled gh: " + " ".join(args))


def prompt_from_args(args):
    for flag in ("-p", "--prompt", "--message"):
        if flag in args:
            return args[args.index(flag) + 1]

    messages = []
    skip_next = False
    value_flags = {
        "--thinking",
        "--mode",
        "--model-reasoning-effort",
        "--model",
        "--provider",
        "--session",
    }
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in value_flags:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        messages.append(arg)
    return " ".join(messages)


def thinking_from_args(args):
    for flag in ("--thinking", "--mode", "--model-reasoning-effort"):
        if flag in args:
            return args[args.index(flag) + 1]
    return ""


def cmd_pi(state, args):
    prompt = prompt_from_args(args)
    prompt_lower = prompt.lower()
    if prompt.startswith("Review the work") or prompt.startswith("CUSTOM REVIEW"):
        kind = "review"
    elif prompt.startswith("Write commit and pull request metadata") or "Output path:" in prompt:
        kind = "message"
    elif prompt.startswith("CI checks failed") or prompt.startswith("CUSTOM CI"):
        kind = "ci_fix"
    elif "squash merge failed" in prompt_lower or prompt.startswith("CUSTOM REBASE"):
        kind = "rebase_fix"
    else:
        kind = "initial"

    state.setdefault("pi_calls", []).append(
        {
            "kind": kind,
            "thinking": thinking_from_args(args),
            "prompt": prompt,
            "args": args,
            "interactive": "-p" not in args,
        }
    )

    if kind in state.get("pi_fail_kinds", []):
        finish(state, int(state.get("pi_fail_status", 42)), err=f"pi {kind} failed")

    pi_out = state.get(f"{kind}_pi_out", state.get("pi_out", ""))
    pi_err = state.get(f"{kind}_pi_err", state.get("pi_err", ""))

    if kind == "initial" and state.get("initial_pi_commit_count") is not None:
        state["commit_count"] = state["initial_pi_commit_count"]
        state["head"] = state.get(
            "initial_pi_head", f"agent-commit-{state['initial_pi_commit_count']}"
        )
    if kind == "initial" and state.get("initial_push_remote_oid"):
        state["remote_branch_oid"] = state["initial_push_remote_oid"]

    if kind == "review":
        if state.get("review_changes"):
            state["worktree_diff"] = state.get("review_changed_diff", "review changed diff")
        if state.get("review_sets_dirty"):
            state["worktree_dirty"] = state["review_sets_dirty"]

    if kind == "message":
        match = re.search(r"^Output path: (.+)$", prompt, re.MULTILINE)
        if not match:
            finish(state, 43, err="missing output path")
        output_path = Path(match.group(1))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        message_index = int(state.get("message_index", 0))
        state["message_index"] = message_index + 1
        generated_messages = state.get("generated_messages", [])
        if message_index < len(generated_messages):
            generated_message = generated_messages[message_index]
            title = generated_message.get("title", "feat: implement cool stuff")
            body = generated_message.get(
                "body",
                "- Implements the requested cool stuff.\n- Updates tests and docs as needed.",
            )
        else:
            title = state.get("generated_commit_title", "feat: implement cool stuff")
            body = state.get(
                "generated_commit_body",
                "- Implements the requested cool stuff.\n- Updates tests and docs as needed.",
            )
        if not state.get("message_skip_output"):
            output_path.write_text(
                state.get("message_json", json.dumps({"title": title, "body": body}))
            )
        if state.get("message_agent_changes"):
            state["worktree_dirty"] = state.get("message_agent_dirty", " M message-change\n")

    if kind == "ci_fix":
        state["worktree_dirty"] = state.get("dirty_after_ci_fix", " M ci-fix\n")

    if kind == "rebase_fix":
        if not state.get("rebase_still_in_progress"):
            remove_rebase_dirs(state)
        if state.get("dirty_after_rebase_fix"):
            state["worktree_dirty"] = state["dirty_after_rebase_fix"]

    finish(state, out=pi_out, err=pi_err)


def cmd_mise(state, args):
    if args == ["trust", "."]:
        if state.get("mise_trust_fail"):
            finish(state, 1, err="mise trust failed")
        finish(state)
    finish(state, 97, err="unhandled mise: " + " ".join(args))


def cmd_custom_setup(state, args):
    state["custom_setup_args"] = args
    if state.get("custom_setup_fail"):
        finish(state, 1, err="custom setup failed")
    finish(state)


def main():
    state = load_state()
    cmd = Path(sys.argv[0]).name
    args = sys.argv[1:]
    state.setdefault("calls", []).append({"cmd": cmd, "args": args, "cwd": os.getcwd()})

    dispatch = {
        "git": cmd_git,
        "cog": cmd_cog,
        "convco": cmd_cog,
        "gh": cmd_gh,
        "glab": cmd_gh,
        "tea": cmd_gh,
        "pi": cmd_pi,
        "agentx": cmd_pi,
        "mise": cmd_mise,
        "custom-setup": cmd_custom_setup,
    }
    if cmd not in dispatch:
        finish(state, 127, err=f"unknown fake command: {cmd}")
    dispatch[cmd](state, args)


main()
"""


def base_state(
    tmp_path: Path, *, branch: str = "feature/cool-stuff", **overrides: Any
) -> dict[str, Any]:
    repo = tmp_path / "pid"
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


@dataclass(frozen=True)
class CliProcess:
    returncode: int
    stdout: str
    stderr: str


def _args_for_workflow_tests(args: list[str]) -> list[str]:
    """Keep workflow tests focused on the advanced `pid run` engine path."""

    if not args:
        return args
    commands = {
        "agent",
        "a",
        "orchestrator",
        "o",
        "run",
        "session",
        "init",
        "sessions",
        "config",
        "x",
        "version",
        "--help",
        "-h",
        "--version",
        "-v",
    }
    options_with_values = {"--config", "-c", "--output"}
    index = 0
    while index < len(args):
        value = args[index]
        if value in options_with_values:
            index += 2
            continue
        if value.startswith("--config=") or value.startswith("--output="):
            index += 1
            continue
        if value.startswith("-"):
            index += 1
            continue
        if value in commands:
            return args
        return [*args[:index], "run", *args[index:]]
    return args


def run_pid(
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
) -> tuple[CliProcess, dict[str, Any]]:
    state = state or base_state(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    install_fake_commands(bin_dir, commands)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir),
            "PID_FAKE_STATE": str(state_path),
            "TERM": "dumb",
            "NO_COLOR": "1",
            "PYTHONPATH": str(SRC),
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "PID_LOG_DIR": str(tmp_path / "logs"),
        }
    )
    env_keys = {
        "checks_timeout_seconds": "PID_CHECKS_TIMEOUT_SECONDS",
        "checks_poll_interval_seconds": "PID_CHECKS_POLL_INTERVAL_SECONDS",
        "merge_confirmation_timeout_seconds": "PID_MERGE_CONFIRMATION_TIMEOUT_SECONDS",
        "merge_confirmation_poll_interval_seconds": "PID_MERGE_CONFIRMATION_POLL_INTERVAL_SECONDS",
        "merge_retry_limit": "PID_MERGE_RETRY_LIMIT",
    }
    for state_key, env_key in env_keys.items():
        if state_key in state:
            env[env_key] = str(state[state_key])
    runner = CliRunner()
    previous_cwd = os.getcwd()
    try:
        os.chdir(state["repo_root"])
        result = runner.invoke(app, _args_for_workflow_tests(args), env=env)
    finally:
        os.chdir(previous_cwd)

    process = CliProcess(result.exit_code, result.stdout, result.stderr)
    final_state = json.loads(state_path.read_text())
    return process, final_state


def calls(state: dict[str, Any], cmd: str, *prefix: str) -> list[dict[str, Any]]:
    matching = [call for call in state.get("calls", []) if call["cmd"] == cmd]
    if not prefix:
        return matching
    return [call for call in matching if call["args"][: len(prefix)] == list(prefix)]


def combined_output(process: CliProcess) -> str:
    return process.stdout + process.stderr


def assert_success(process: CliProcess) -> None:
    assert process.returncode == 0, combined_output(process)
