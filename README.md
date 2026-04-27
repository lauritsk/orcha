# Orcha

Orcha is a small CLI that orchestrates an AI coding agent through a full
GitHub pull request lifecycle.

It creates an isolated git worktree, runs `pi` on your request, asks `pi` to
review the result, commits changes with a Conventional Commit title derived
from the branch name, opens or updates a pull request, waits for checks, asks
`pi` to fix failures, retries when needed, squash-merges the PR, and cleans up
the worktree.

## Features

- Creates a clean branch in a sibling git worktree.
- Runs non-interactive `pi -p` with selectable thinking level.
- Performs an automated review pass before committing.
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

1. Validates the branch name and derives a Conventional Commit title from it.
2. Verifies the title with `cog verify`.
3. Finds and updates the default branch in the main worktree.
4. Creates a sibling worktree for the new branch.
5. Runs `pi --thinking <level> -p <prompt>`.
6. Runs a high-thinking `pi` review pass.
7. Commits dirty changes, opens or updates a PR, then waits for GitHub checks.
8. If checks fail, asks `pi` to fix them and retries.
9. If squash merge fails because the base moved, rebases and retries.
10. On confirmed merge, pulls the default branch and removes the worktree and branch.

Branch names drive commit titles:

| Branch | Commit title |
| --- | --- |
| `feature/add-api` | `feat: add api` |
| `fix/ci-timeout` | `fix: ci timeout` |
| `docs/update-readme` | `docs: update readme` |
| `weird/add-thing` | `chore: weird add thing` |

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

Use `mise run fix` to run hk-managed formatters and fixers.
