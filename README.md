# pid

pid is a CLI that drives an AI coding agent through a full pull request or
merge request lifecycle.

It creates an isolated git worktree, runs a configured agent, reviews the
result, generates a Conventional Commit title and PR body, commits the final
changes, opens or updates a PR, waits for checks, asks the agent to fix failures,
squash-merges the PR, and cleans up.

## Features

- Creates a clean branch in a sibling git worktree.
- Runs non-interactive agent commands or interactive agent sessions.
- Reviews committed and uncommitted changes before committing.
- Generates commit and PR metadata from the final diff.
- Verifies commit titles with a configurable verifier (`cog` by default).
- Creates, updates, checks, retries, and squash-merges PRs with a configurable
  forge CLI (`gh` by default).
- Handles CI failure follow-ups and moved-base rebase retries.
- Offers opt-in `pid agent` supervision with durable run state and typed failures.
- Queues durable follow-ups to supervised runs and applies them at safe
  checkpoints.
- Starts orchestrator runs that ask intake questions, persist plans, and launch
  child pid agent sessions in parallel waves.
- Lists active and historical pid sessions.
- Supports workflow extensions under `pid x ...`.
- Optionally keeps the screen awake on macOS while pid runs.

## Requirements

- Python 3.14 or newer
- Git
- A coding-agent CLI on `PATH` (`pi` by default)
- An authenticated forge CLI on `PATH` (`gh` by default)
- A commit-title verifier on `PATH` (`cog` by default, optional)
- `mise` if you want repository-managed development tasks

> [!IMPORTANT]
> Run pid from a clean main worktree. pid stops if the main worktree has
> uncommitted or untracked changes.

## Installation

Install from a checkout:

```sh
uv tool install .
```

Run from the checkout during development:

```sh
mise trust
mise run pid -- --version
```

Container images are published to GHCR:

```sh
docker pull ghcr.io/lauritsk/pid:latest
docker run --rm ghcr.io/lauritsk/pid:latest --version
```

The image entrypoint is `pid`. The image is intentionally minimal; derive your
own image or provide host tools when full workflow runs need `git`, a forge CLI,
an agent CLI, or a commit-title verifier.

## Quick start

Create the default config file:

```sh
pid init
```

Recommended single-PR flow: start supervised agent mode with durable run state.
In a terminal, `pid agent` prompts for missing startup values; in scripts, pass
options explicitly:

```sh
pid agent
pid agent --branch feature/add-docs --prompt "add project documentation"
pid agent follow-up <run-id> --message "Use the new API name everywhere"
pid agent runs
pid agent status <run-id>
```

Recommended larger-change flow: start an orchestrator. In a terminal,
`pid orchestrator` prompts for the goal and launch defaults. Without a plan file
it prints intake questions to answer before child launch; with an approved JSON
plan it creates child run records and launches dependency-free children in
parallel unless `--dry-run` is set:

```sh
pid orchestrator
pid orchestrator --goal "ship the larger change"
pid orchestrator --goal "ship the larger change" --plan-file plan.json
pid orchestrator follow-up <run-id> --target api --message "Rename endpoint to /v2/tasks"
```

Direct workflow shortcut: `pid`/`pid run` still exists as the fast, unsupervised
single-branch executor and as the internal workflow engine. It is not a separate
product path; agent and orchestrator are modes of the same `pid` executable.

```sh
pid run feature/add-docs "add project documentation"
```

Run an interactive agent session and let pid resume after the session exits:

```sh
pid session feature/explore-api
```

Inspect configuration and sessions:

```sh
pid config show
pid config path
pid sessions
```

## Usage

```sh
pid
pid [OPTIONS] [ATTEMPTS] [THINKING] BRANCH PROMPT...
pid [OPTIONS] run [ATTEMPTS] [THINKING] BRANCH PROMPT...
pid [OPTIONS] session [ATTEMPTS] [THINKING] BRANCH [PROMPT...]
pid agent [start] --branch BRANCH --prompt TEXT [--attempts N] [--thinking LEVEL]
pid agent follow-up RUN_ID --message TEXT [--type TYPE]
pid agent status RUN_ID
pid agent runs
pid agent resume RUN_ID
pid orchestrator [start] --goal TEXT [--plan-file plan.json] [--dry-run]
pid orchestrator follow-up RUN_ID --message TEXT [--target ITEM|--all]
pid orchestrator status RUN_ID
pid orchestrator runs
pid init
pid sessions [--all|-a]
pid config show|default|path
pid x extensions list
pid x <extension-command> [ARGS...]
pid version
pid --version
```

### Arguments

| Argument | Default | Description |
| --- | --- | --- |
| `session` | off | Use an interactive agent command. pid resumes after the agent exits. |
| `ATTEMPTS` | `3` | Maximum agent rejection attempts. CI/check fixes consume attempts; moved-base merge/rebase retries do not. |
| `THINKING` | `medium` | Initial agent thinking level. Values come from `agent.thinking_levels`. |
| `BRANCH` | required | New branch name. It must not already exist locally or on `origin`. |
| `PROMPT...` | required for non-interactive; optional for `session` | Prompt passed to the configured agent command. |

### Options and commands

| Command or option | Description |
| --- | --- |
| `--config PATH`, `-c PATH` | Load config from a specific TOML file. |
| `--output normal` | Show progress, successful agent summaries, and failures. |
| `--output agent` | Also show successful agent stderr. |
| `--output all` | Show successful output from every captured command. Full logs are always written to the session log. |
| `pid init` | Write recommended defaults to the platform config path. Refuses to overwrite an existing file. |
| `pid agent`, `pid agent start` | Run supervised workflow mode. Stores state under the git common dir by default. In a TTY, missing startup options are prompted. |
| `pid agent follow-up RUN_ID` | Queue a durable follow-up for a supervised run. Running children apply it at the next safe checkpoint. |
| `pid agent status RUN_ID` | Show current step, status, PR URL, failure, and follow-up counts for a run. |
| `pid agent runs` | List recent supervised runs. |
| `pid agent resume RUN_ID` | Reserved for resumable recovery; currently reports saved state and exits with guidance. |
| `pid orchestrator`, `pid orchestrator start` | Create a larger-run coordinator. In a TTY, missing startup options are prompted. Without `--plan-file`, prints intake questions; with a plan, creates child runs and launches ready children. |
| `pid orchestrator follow-up RUN_ID` | Record a global follow-up or route it to child run inboxes with `--target` or `--all`. |
| `pid orchestrator status RUN_ID` | Show orchestrator status and child run IDs/statuses. |
| `pid orchestrator runs` | List recent orchestrator runs. |
| `pid sessions` | List active pid sessions from session logs. |
| `pid sessions --all`, `-a` | Include stale and completed sessions. |
| `pid config show` | Print the loaded config as TOML. Honors `--config PATH`. |
| `pid config default` | Print the built-in default config. |
| `pid config path` | Print config and session-log paths. |
| `pid x extensions list` | List enabled extensions. |
| `pid x <extension-command>` | Run an enabled extension command. |
| `pid version`, `--version`, `-v` | Print the installed pid version. |

When stdin is a TTY, pid prompts for missing values before starting. In
non-interactive shells, missing required arguments fail instead of blocking.

### Orchestrator plan files

`pid orchestrator start --plan-file plan.json` expects JSON with an `items`
array and optional `constraints` array. Each item may set `id`, `title`,
`scope`, `acceptance`, `validation`, `dependencies`, `branch`, `thinking`, and
`prompt`. Missing branch names use `<prefix>/<item-id>-<slug>`. Missing thinking
levels are chosen from configured agent levels using item risk and complexity.
Missing prompts are built from the global goal, constraints, item scope,
dependencies, acceptance criteria, and validation commands.

## How pid works

1. Validates the branch name and checks that the main worktree is clean.
2. Finds and updates the default branch.
3. Creates a sibling worktree for the new branch.
4. Runs the configured agent command, or starts an interactive session.
5. Reviews the resulting committed and uncommitted work.
6. Generates JSON commit/PR metadata under the worktree git directory.
7. Refuses to continue if metadata is missing, invalid, changes the worktree, or
   fails commit-title verification.
8. Squashes agent-authored commits and dirty changes into one generated-message
   commit.
9. Opens or updates a PR and waits for configured forge checks.
10. On check failure, asks the agent to fix the failure, commits the feedback,
    regenerates the PR message, and retries.
11. On moved-base merge failure, rebases, regenerates the PR message if needed,
    and retries without consuming an agent attempt.
12. After merge succeeds, confirms the PR is merged, pulls the default branch,
    removes the worktree, and deletes the branch.

## Configuration

pid loads TOML config from the platform default path, or from `--config PATH`.
The default path is optional; an explicit config path must exist.

Create a default config:

```sh
pid init
```

Default config paths:

| Platform/env | Path |
| --- | --- |
| macOS | `~/Library/Application Support/pid/config.toml` |
| Linux/other Unix | `~/.config/pid/config.toml` |
| `XDG_CONFIG_HOME` set to an absolute path | `$XDG_CONFIG_HOME/pid/config.toml` |

Most workflow behavior is configurable. Important sections include:

- `[agent]`: agent command, interactive/non-interactive args, thinking levels,
  review thinking, and display label.
- `[runtime]`: runtime behavior such as macOS screen-awake support.
- `[orchestrator]`: enable/disable `pid agent` and optionally set a custom
  run-state directory.
- `[commit]`: title verifier and automated feedback commit titles.
- `[forge]`: forge command, PR create/edit/check/merge templates, merge
  confirmation, and check polling behavior.
- `[prompts]`: message, review, CI-fix, and rebase-fix prompt templates.
- `[workflow]`: check timeouts, merge confirmation, moved-base retry limits,
  base refresh behavior, and `mise trust` behavior.
- `[extensions]`: enabled extension modules and local extension paths.

Print the full built-in config with:

```sh
pid config default
```

Disable supervised agent mode, or move run state to an absolute directory:

```toml
[orchestrator]
enabled = false
store_dir = "/var/lib/pid/runs"
```

When `store_dir` is empty, `pid agent` writes under
`<git-common-dir>/pid/runs/`, outside the worktree. Run directories and state
files are created with user-private permissions where supported.

### Agent examples

```toml
# pi
[agent]
command = ["pi"]
non_interactive_args = ["--thinking", "{thinking}", "-p", "{prompt}"]
interactive_args = ["--thinking", "{thinking}"]
default_thinking = "medium"
review_thinking = "high"
thinking_levels = ["low", "medium", "high", "xhigh"]
label = "pi"
```

```toml
# OpenCode
[agent]
command = ["opencode"]
non_interactive_args = ["run", "--prompt", "{prompt}"]
interactive_args = []
label = "opencode"
```

```toml
# Codex
[agent]
command = ["codex"]
non_interactive_args = ["exec", "--model-reasoning-effort", "{thinking}", "{prompt}"]
interactive_args = []
thinking_levels = ["low", "medium", "high"]
label = "codex"
```

```toml
# Claude
[agent]
command = ["claude"]
non_interactive_args = ["-p", "{prompt}"]
interactive_args = []
label = "claude"
```

### Forge examples

Switch the executable and label while keeping the default `gh`-style templates:

```toml
[forge]
command = ["glab"]
label = "gitlab"
```

Use a forge CLI without head-OID guarded merges:

```toml
[forge]
command = ["tea"]
label = "tea"
pr_head_oid_args = []
pr_merge_args = [
  "pulls",
  "merge",
  "{branch}",
  "--squash",
  "--title",
  "{title}",
  "--body",
  "{body}",
]
pr_merged_at_args = []
```

## Extensions

Extensions can hook, add, replace, or disable workflow steps. They can also add
commands under `pid x ...`.

See [docs/EXTENSIONS.md](docs/EXTENSIONS.md) for the extension API, trust
boundary, and runnable local-extension examples.

## Development

This repository uses `mise` for tools and tasks.

```sh
mise trust
mise run lint
mise run test
mise run build
mise run check
```

Use `mise run fix` to run formatters and fixers. The test task runs pytest in
parallel and enforces 95% total coverage.

Release helpers are namespaced under `release:*`:

```sh
mise run release:bump
mise run release:publish
```

Tagged releases publish `ghcr.io/lauritsk/pid` with GoReleaser. Release CI must
have `DHI_USERNAME` and `DHI_PASSWORD` secrets so it can pull Docker Hardened
Images from `dhi.io`.

### Project layout

- `src/pid/cli.py`: Typer command-line wiring.
- `src/pid/workflow.py`: high-level pid lifecycle.
- `src/pid/orchestrator.py`, `run_state.py`, `failures.py`, and `policy.py`:
  supervised agent mode, durable state, typed failures, and deterministic
  recovery policy.
- `src/pid/repository.py`: git, worktree, and commit operations.
- `src/pid/github.py`: configurable forge/PR CLI operations.
- `src/pid/prompts.py`: agent prompts and untrusted output isolation.
- `src/pid/commands.py`, `output.py`, `parsing.py`, `utils.py`, `models.py`, and
  `errors.py`: shared support code.
- `tests/fakes.py`: fake command harness for flow tests.
