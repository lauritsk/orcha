# pid

pid is a small CLI that orchestrates an AI coding agent through a full
GitHub pull request lifecycle.

It creates an isolated git worktree, runs a configured agent on your request,
asks the agent to review the result, asks the agent to generate a Conventional
Commit title plus Markdown description from the completed diff, commits changes
as one generated-message commit, opens or updates a pull request with the same
title/body, waits for checks, asks the agent to fix failures, refreshes the PR
message after follow-up changes, retries when needed, squash-merges the PR, and
cleans up the worktree.

## Features

- Creates a clean branch in a sibling git worktree.
- Runs either a configured non-interactive agent command or an interactive agent
  session.
- Resumes automation after the interactive session exits.
- Performs an automated review pass across committed and uncommitted work,
  including a check that relevant documentation was updated for code,
  behavior, workflow, configuration, and user-facing changes.
- Generates the commit and PR title/body from the final reviewed work.
- Verifies the generated commit title with `cog`.
- Creates, updates, checks, retries, and squash-merges GitHub PRs with `gh`.
- Handles CI failure follow-ups and moved-base rebase retries without charging
  agent attempts.
- Uses Rich output for key status panels.
- Optionally keeps the screen awake while pid runs on macOS.

## Requirements

- Python 3.14 or newer
- Git
- A configured coding-agent CLI available on `PATH` (defaults to
  [`pi`](https://github.com/lauritsk/pi))
- [`gh`](https://cli.github.com/) authenticated for the target repository
- [`cog`](https://github.com/cocogitto/cocogitto) available on `PATH`
- `mise` is optional at runtime; when present, pid runs `mise trust .` in
  the new worktree

> [!IMPORTANT]
> Run pid from a clean main worktree. pid stops if the main worktree has
> uncommitted or untracked changes.

## Installation

Install from a local checkout:

```sh
uv tool install .
```

For development, use `mise` from the repository root. `mise run test` runs
pytest with the repository coverage gate:

```sh
mise trust
mise run test
```

## Usage

```sh
pid [ATTEMPTS] [THINKING] BRANCH PROMPT...
pid session [ATTEMPTS] [THINKING] BRANCH [PROMPT...]
```

Arguments:

| Argument | Default | Description |
| --- | --- | --- |
| `session` | off | Use an interactive agent command instead of the non-interactive template; pid resumes after the agent exits. |
| `ATTEMPTS` | `3` | Maximum agent rejection attempts. CI/check failures that require agent follow-up consume attempts; moved-base merge/rebase retries do not. Must be a positive integer. |
| `THINKING` | `medium` | Initial agent thinking level. Values come from `agent.thinking_levels`. |
| `BRANCH` | required | New branch name to create. Must not already exist locally or on `origin`. |
| `PROMPT...` | required for non-interactive; optional for `session` | Prompt passed through the configured non-interactive agent template, or initial message for interactive mode. |

Examples:

```sh
pid feature/add-readme "add project docs"
pid 2 high fix/repair-ci "fix failing tests"
pid docs/update-install update installation instructions
pid session feature/explore-api
pid session high feature/prototype-auth "explore auth UX options"
```

## How it works

1. Validates the branch name.
2. Finds and updates the default branch in the main worktree.
3. Creates a sibling worktree for the new branch.
4. Runs the configured non-interactive agent command with prompt and thinking
   level, or runs the configured interactive agent command in `session` mode.
5. In `session` mode, waits until the interactive agent exits.
6. Runs a configured review-thinking agent review pass over committed and
   uncommitted work that fixes incomplete, unsafe, incorrect, untested, or
   undocumented work and updates relevant docs when code, workflow,
   configuration, or behavior changed.
7. Runs a configured review-thinking agent message pass that writes JSON
   metadata under the worktree git directory only.
8. Refuses to continue if the message pass changes the worktree, omits the
   JSON, writes invalid JSON, or produces an invalid Conventional Commit title.
9. Squashes any agent-authored commits plus dirty changes into one commit with
   the generated title/body, opens or updates a PR with the same title/body,
   then waits for GitHub checks.
10. If checks fail, asks the agent to fix them, commits that feedback,
    regenerates the PR title/body from the updated diff, and retries. These
    agent rejection follow-ups consume `ATTEMPTS`.
11. If squash merge fails because the base moved, rebases, regenerates the PR
    title/body when the branch changes, and retries without consuming
    `ATTEMPTS`.
12. On confirmed merge, pulls the default branch and removes the worktree and
    branch.

## Configuration

pid loads TOML config from the platform default path, or from `--config PATH`.
The default path is optional; an explicit `--config PATH` must exist.

Default config paths:

| Platform/env | Path |
| --- | --- |
| macOS | `~/Library/Application Support/pid/config.toml` |
| Linux/other Unix | `~/.config/pid/config.toml` |
| `XDG_CONFIG_HOME` set to an absolute path | `$XDG_CONFIG_HOME/pid/config.toml` |

Relative `XDG_CONFIG_HOME` values are ignored as required by the XDG Base
Directory Specification.

The agent command, launch behavior, and selected runtime behavior are
configurable. Defaults are equivalent to:

```toml
[agent]
command = ["pi"]
non_interactive_args = ["--thinking", "{thinking}", "-p", "{prompt}"]
interactive_args = ["--thinking", "{thinking}"]
default_thinking = "medium"
review_thinking = "high"
thinking_levels = ["low", "medium", "high", "xhigh"]
label = "agent"

[runtime]
keep_screen_awake = false
```

`command` may be an array of strings or a shell-style string.
`non_interactive_args` must be an array of strings, must include `{prompt}`, and
may include `{thinking}`. `interactive_args` may include `{prompt}` and
`{thinking}`; when it does not include `{prompt}`, pid appends the optional
session prompt as a trailing argument. Those are the only supported template
fields. pid never assumes a specific agent CLI internally; it only expands
these templates.

Set `runtime.keep_screen_awake = true` to keep the display awake while pid is
running. This is currently implemented on macOS with the built-in `caffeinate`
tool (`caffeinate -d -i`). Linux support is intentionally not enabled yet
because pid does not assume a universal built-in Linux inhibitor.

Example `pi` config:

```toml
[agent]
command = ["pi"]
non_interactive_args = ["--thinking", "{thinking}", "-p", "{prompt}"]
interactive_args = ["--thinking", "{thinking}"]
default_thinking = "medium"
review_thinking = "high"
thinking_levels = ["low", "medium", "high", "xhigh"]
label = "pi"
```

Example OpenCode config:

```toml
[agent]
command = ["opencode"]
non_interactive_args = ["run", "--prompt", "{prompt}"]
interactive_args = []
default_thinking = "medium"
review_thinking = "high"
thinking_levels = ["low", "medium", "high", "xhigh"]
label = "opencode"
```

Example Codex config:

```toml
[agent]
command = ["codex"]
non_interactive_args = ["exec", "--model-reasoning-effort", "{thinking}", "{prompt}"]
interactive_args = []
default_thinking = "medium"
review_thinking = "high"
thinking_levels = ["low", "medium", "high"]
label = "codex"
```

Example Claude config:

```toml
[agent]
command = ["claude"]
non_interactive_args = ["-p", "{prompt}"]
interactive_args = []
default_thinking = "medium"
review_thinking = "high"
thinking_levels = ["low", "medium", "high", "xhigh"]
label = "claude"
```

Agent CLI flags change over time; treat these as starting points and adjust the
argument template for your installed agent version.

pid reads these optional environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `PID_CHECKS_TIMEOUT_SECONDS` | `1800` | How long to wait for pending GitHub checks. |
| `PID_CHECKS_POLL_INTERVAL_SECONDS` | `10` | Delay between check polling attempts. |
| `PID_MERGE_RETRY_LIMIT` | `20` | Safety cap for moved-base merge/rebase retries that do not consume `ATTEMPTS`. |

## Development

This repository uses `mise` for tools and tasks. The test task runs pytest with
coverage reporting and fails below 95% total coverage.

```sh
mise trust
mise run lint
mise run test
mise run build
mise run check
```

Use `mise run fix` to run hk-managed formatters and fixers. Release helpers are
namespaced under `release:*`: use `mise run release:bump` to bump the package
version and `mise run release:publish` to publish a tagged release.

### Project layout

- `src/pid/cli.py` owns Typer command-line wiring.
- `src/pid/workflow.py` coordinates the high-level pid lifecycle.
- `src/pid/repository.py` wraps git, worktree, and commit operations.
- `src/pid/github.py` wraps GitHub CLI pull request operations.
- `src/pid/prompts.py` builds agent prompts and isolates untrusted output.
- `src/pid/commands.py`, `output.py`, `parsing.py`, `utils.py`,
  `models.py`, and `errors.py` contain shared support code.
- `tests/fakes.py` provides the fake command harness for flow tests.
