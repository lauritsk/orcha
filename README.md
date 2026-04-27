# Orcha

Orcha is a small CLI that orchestrates an AI coding agent through a full
GitHub pull request lifecycle.

It creates an isolated git worktree, runs `pi` on your request, asks `pi` to
review the result, asks `pi` to generate a Conventional Commit title plus
Markdown description from the completed diff, commits changes as one generated
message commit, opens or updates a pull request with the same title/body, waits
for checks, asks `pi` to fix failures, refreshes the PR message after follow-up
changes, retries when needed, squash-merges the PR, and cleans up the worktree.

## Features

- Creates a clean branch in a sibling git worktree.
- Runs non-interactive `pi -p` with selectable thinking level.
- Performs an automated review pass before committing.
- Generates the commit and PR title/body from the final reviewed work.
- Verifies the generated commit title with `cog`.
- Creates, updates, checks, retries, and squash-merges GitHub PRs with `gh`.
- Handles CI failure follow-ups and moved-base rebase retries.
- Uses Rich output for key status panels.

## Requirements

- Python 3.14 or newer
- Git
- [`pi`](https://github.com/lauritsk/pi) available on `PATH`
- [`gh`](https://cli.github.com/) authenticated for the target repository
- [`cog`](https://github.com/cocogitto/cocogitto) available on `PATH`
- `mise` is optional at runtime; when present, Orcha runs `mise trust .` in
  the new worktree

> [!IMPORTANT]
> Run Orcha from a clean main worktree. Orcha stops if the main worktree has
> uncommitted or untracked changes.

## Installation

Install from a local checkout:

```sh
uv tool install .
```

For development, use `mise` from the repository root:

```sh
mise trust
mise run test
```

## Usage

```sh
orcha [ATTEMPTS] [THINKING] BRANCH PROMPT...
```

Arguments:

| Argument | Default | Description |
| --- | --- | --- |
| `ATTEMPTS` | `3` | Maximum PR/check/merge attempts. Must be a positive integer. |
| `THINKING` | `medium` | Initial `pi` thinking level: `low`, `medium`, `high`, or `xhigh`. |
| `BRANCH` | required | New branch name to create. Must not already exist locally or on `origin`. |
| `PROMPT...` | required | Prompt passed to `pi -p`. |

Examples:

```sh
orcha feature/add-readme "add project docs"
orcha 2 high fix/repair-ci "fix failing tests"
orcha docs/update-install update installation instructions
```

## How it works

1. Validates the branch name.
2. Finds and updates the default branch in the main worktree.
3. Creates a sibling worktree for the new branch.
4. Runs `pi --thinking <level> -p <prompt>`.
5. Runs a high-thinking `pi` review pass.
6. Runs a high-thinking `pi` message pass that writes JSON metadata under the
   worktree git directory only.
7. Refuses to continue if the message pass changes the worktree, omits the
   JSON, writes invalid JSON, or produces an invalid Conventional Commit title.
8. Squashes any agent-authored commits plus dirty changes into one commit with
   the generated title/body, opens or updates a PR with the same title/body,
   then waits for GitHub checks.
9. If checks fail, asks `pi` to fix them, commits that feedback, regenerates the
   PR title/body from the updated diff, and retries.
10. If squash merge fails because the base moved, rebases, regenerates the PR
    title/body when the branch changes, and retries.
11. On confirmed merge, pulls the default branch and removes the worktree and branch.

## Configuration

Orcha reads these optional environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `ORCHA_CHECKS_TIMEOUT_SECONDS` | `1800` | How long to wait for pending GitHub checks. |
| `ORCHA_CHECKS_POLL_INTERVAL_SECONDS` | `10` | Delay between check polling attempts. |

## Development

This repository uses `mise` for tools and tasks.

```sh
mise trust
mise run lint
mise run test
mise run build
mise run audit
mise run check
```

Use `mise run fix` to run hk-managed formatters and fixers. Release helpers are
namespaced under `release:*`: use `mise run release:bump` to bump the package
version and `mise run release:publish` to publish a tagged release.

### Project layout

- `src/orcha/cli.py` owns Typer command-line wiring.
- `src/orcha/workflow.py` coordinates the high-level Orcha lifecycle.
- `src/orcha/repository.py` wraps git, worktree, and commit operations.
- `src/orcha/github.py` wraps GitHub CLI pull request operations.
- `src/orcha/prompts.py` builds pi prompts and isolates untrusted output.
- `src/orcha/commands.py`, `output.py`, `parsing.py`, `utils.py`,
  `models.py`, and `errors.py` contain shared support code.
- `tests/fakes.py` provides the fake command harness for flow tests.
