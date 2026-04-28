# pid

pid is a small CLI that orchestrates an AI coding agent through a full
pull request or merge request lifecycle using a configurable forge CLI.

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
- Verifies the generated commit title with a configurable verifier (`cog` by default).
- Creates, updates, checks, retries, and squash-merges PRs with a configurable
  forge CLI (`gh` by default; `glab`, `tea`, or custom wrappers can be used by
  changing argument templates).
- Handles CI failure follow-ups, bounded default-branch refreshes, and
  moved-base rebase retries without charging agent attempts.
- Uses Rich output for key status panels.
- Optionally keeps the screen awake while pid runs on macOS.

## Requirements

- Python 3.14 or newer
- Git
- A configured coding-agent CLI available on `PATH` (defaults to
  [`pi`](https://github.com/lauritsk/pi))
- A configured forge CLI authenticated for the target repository (defaults to
  [`gh`](https://cli.github.com/))
- A configured commit-title verifier available on `PATH` (defaults to
  [`cog`](https://github.com/cocogitto/cocogitto); can be disabled)
- `mise` is optional at runtime; when present, pid runs `mise trust .` in
  the new worktree unless `workflow.trust_mise = false`

> [!IMPORTANT]
> Run pid from a clean main worktree. pid stops if the main worktree has
> uncommitted or untracked changes.

## Installation

Install from a local checkout:

```sh
uv tool install .
```

Container images are published to GHCR:

```sh
docker pull ghcr.io/lauritsk/pid:latest
docker run --rm ghcr.io/lauritsk/pid:latest --version
```

The image entrypoint is `pid` and uses Docker Hardened Images for the Python
runtime. It is intentionally minimal; provide `git`, your forge CLI, agent CLI,
and commit-title verifier in a derived image or host environment for full
workflow runs.

For development, use `mise` from the repository root. `mise run test` runs
pytest in parallel with the repository coverage gate:

```sh
mise trust
mise run test
```

## Usage

```sh
pid
pid [--output normal|agent|all] [ATTEMPTS] [THINKING] BRANCH PROMPT...
pid [--output normal|agent|all] session [ATTEMPTS] [THINKING] BRANCH [PROMPT...]
pid sessions [--all|-a]
pid config show|default|path
pid --version
```

Arguments:

| Argument | Default | Description |
| --- | --- | --- |
| `session` | off | Use an interactive agent command instead of the non-interactive template; pid resumes after the agent exits. |
| `ATTEMPTS` | `3` | Maximum agent rejection attempts. CI/check failures that require agent follow-up consume attempts; moved-base merge/rebase retries do not. Must be a positive integer. |
| `THINKING` | `medium` | Initial agent thinking level. Values come from `agent.thinking_levels`. |
| `BRANCH` | required | New branch name to create. Must not already exist locally or on `origin`. |
| `PROMPT...` | required for non-interactive; optional for `session` | Prompt passed through the configured non-interactive agent template, or initial message for interactive mode. |

Options:

| Option | Default | Description |
| --- | --- | --- |
| `--output normal\|agent\|all` | `normal` | Console detail level. `normal` shows progress, successful agent stdout summaries, and failures. `agent` also shows successful agent stderr. `all` shows successful output from every captured command. Full command logs are always written to the session log. |

During a run, pid prints a Rich summary panel for branch, attempts, thinking
level, flow, agent, forge, and output mode. Long-running sections are separated
with phase dividers such as Prepare, Agent, Review, Message + commit, and each
PR attempt.

Examples:

```sh
pid
pid feature/add-readme "add project docs"
pid 2 high fix/repair-ci "fix failing tests"
pid docs/update-install update installation instructions
pid session feature/explore-api
pid session high feature/prototype-auth "explore auth UX options"
pid sessions
pid config show
pid config default
pid config path
pid --version
```

### Interactive argument prompts

When stdin is a TTY, pid fills missing values interactively before starting the
workflow:

- `pid` prompts for attempts, thinking level, branch, and prompt. Attempts and
  thinking level show their configured defaults; press Enter to accept them.
- `pid feature/some-feature` uses the supplied branch and prompts only for the
  prompt.
- `pid high` uses the supplied thinking level and prompts for branch and
  prompt.
- `pid session` prompts for branch only because the initial session prompt is
  optional.
- pid shows current values in a Rich summary panel before each prompt,
  updates that panel in place on TTY output, displays validation errors inside
  the panel, and asks for final confirmation before continuing.

If stdin is not a TTY, pid keeps non-interactive behavior: missing required
arguments print usage or validation errors instead of blocking for input.

Inspection commands and options:

| Command/option | Description |
| --- | --- |
| `pid sessions` | List active pid sessions from pid session logs, including current stage and log path. |
| `pid sessions --all` / `-a` | List active, stale, and completed pid session logs. |
| `pid config show` / `--print-config` | Print the loaded config as TOML. Honors `--config PATH`. |
| `pid config default` / `--print-default-config` | Print built-in default config as TOML. |
| `pid config path` | Print default/effective config path and session log directory. |
| `pid x extensions list` | List enabled extensions. |
| `pid x <extension-command> [ARGS...]` | Run an enabled extension command. |
| `pid version` / `--version` / `-v` | Print the installed pid version. |

## How it works

1. Validates the branch name.
2. Finds and updates the default branch in the main worktree.
3. Creates a sibling worktree for the new branch.
4. Runs the configured non-interactive agent command with prompt and thinking
   level, or runs the configured interactive agent command in `session` mode.
   Successful non-interactive agent stdout is shown as the agent summary.
5. In `session` mode, waits until the interactive agent exits.
6. Runs a configured review-thinking agent review pass over committed and
   uncommitted work that fixes incomplete, unsafe, incorrect, untested, or
   undocumented work and updates relevant docs when code, workflow,
   configuration, or behavior changed.
7. Runs a configured review-thinking agent message pass that writes JSON
   metadata under the worktree git directory only.
8. Refuses to continue if the message pass changes the worktree, omits the
   JSON, writes invalid JSON, or fails the configured commit-title verifier.
9. Squashes any agent-authored commits plus dirty changes into one commit with
   the generated title/body, opens or updates a PR with the same title/body,
   then waits for configured forge checks.
10. If checks fail, asks the agent to fix them, commits that feedback,
    regenerates the PR title/body from the updated diff, and retries. These
    agent rejection follow-ups consume `ATTEMPTS`.
11. If squash merge fails because the base moved, rebases, regenerates the PR
    title/body when the branch changes, and retries without consuming
    `ATTEMPTS`.
12. After the merge command succeeds, polls merge confirmation so queued or
    auto-merge PRs can complete, then pulls the default branch and removes the
    worktree and branch.

## Configuration

pid loads TOML config from the platform default path, or from `--config PATH`.
The default path is optional; an explicit `--config PATH` must exist.

Create a default config file with recommended defaults:

```sh
pid init
```

The command writes to the platform default path and prints that path. It accepts
no arguments, does not accept `--config`, and refuses to overwrite an existing
config file.

Default config paths:

| Platform/env | Path |
| --- | --- |
| macOS | `~/Library/Application Support/pid/config.toml` |
| Linux/other Unix | `~/.config/pid/config.toml` |
| `XDG_CONFIG_HOME` set to an absolute path | `$XDG_CONFIG_HOME/pid/config.toml` |

Relative `XDG_CONFIG_HOME` values are ignored as required by the XDG Base
Directory Specification.

Most orchestration behavior is configurable. Key defaults are:

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

[commit]
verifier_command = ["cog"]
verifier_args = ["verify", "{title}"]
automated_feedback_title = "fix: address automated feedback"
rebase_feedback_title = "fix: resolve latest base changes"

[forge]
command = ["gh"]
label = "github"
default_branch_args = [
  "repo",
  "view",
  "--json",
  "defaultBranchRef",
  "--jq",
  ".defaultBranchRef.name",
]
pr_view_args = ["pr", "view", "{branch}"]
pr_create_args = ["pr", "create", "--title", "{title}", "--body", "{body}"]
pr_edit_args = ["pr", "edit", "{branch}", "--title", "{title}", "--body", "{body}"]
pr_url_args = ["pr", "view", "{branch}", "--json", "url", "--jq", ".url"]
pr_head_oid_args = [
  "pr",
  "view",
  "{branch}",
  "--json",
  "headRefOid",
  "--jq",
  ".headRefOid",
]
pr_checks_args = ["pr", "checks", "{branch}"]
pr_merge_args = [
  "pr",
  "merge",
  "{branch}",
  "--squash",
  "--match-head-commit",
  "{head_oid}",
  "--subject",
  "{title}",
  "--body",
  "{body}",
]
pr_merged_at_args = [
  "pr",
  "view",
  "{pr_url}",
  "--json",
  "mergedAt",
  "--jq",
  '.mergedAt // ""',
]
checks_pending_exit_codes = [8]
no_checks_markers = ["no checks"]

[prompts]
diagnostic_output_limit = 20000
# message, review, ci_fix, and rebase_fix are long built-in templates.
# Override any one with a TOML string or multiline string.

[workflow]
checks_timeout_seconds = 1800
checks_poll_interval_seconds = 10
merge_confirmation_timeout_seconds = 1800
merge_confirmation_poll_interval_seconds = 10
merge_retry_limit = 20
trust_mise = true
base_refresh_enabled = true
base_refresh_stages = ["before_pr"]
base_refresh_limit = 3
base_refresh_agent_conflict_fix = true

[extensions]
enabled = []
paths = []
```

`agent.command`, `forge.command`, and `commit.verifier_command` may be arrays of
strings or shell-style strings. Argument templates must be arrays of strings.

Set `runtime.keep_screen_awake = true` to keep the display awake while pid is
running. This is currently implemented on macOS with the built-in `caffeinate`
tool (`caffeinate -d -i`). Linux support is intentionally not enabled yet
because pid does not assume a universal built-in Linux inhibitor.

Agent templates support `{prompt}` and `{thinking}`. `agent.non_interactive_args`
must include `{prompt}`. `agent.interactive_args` may include `{prompt}`; when it
does not, pid appends the optional session prompt as a trailing argument.

Forge templates support `{branch}`, `{title}`, `{body}`, `{pr_url}`, and
`{head_oid}`. Defaults target `gh`, but pid only expands and runs the configured
command; it does not require `gh` internally. Set `pr_checks_args = []` to skip
check polling. Set `pr_merged_at_args = []` when the merge command should be
trusted without a follow-up merged-state query. Set `pr_head_oid_args = []` only
when `pr_merge_args` does not use `{head_oid}`. `no_checks_markers` values must
not be blank strings.

Commit verifier templates support `{title}`. Set `commit.verifier_args = []` to
skip external title verification.

Base refresh fetches `origin/<default-branch>` at configured workflow stages and
rebases when the branch does not contain the latest default branch. Supported
`workflow.base_refresh_stages` values are `before_message`, `before_pr`, and
`after_checks`. Each stage refreshes at most once per run; `base_refresh_limit`
caps total refresh rebases. When `after_checks` rebases, pid force-pushes,
updates the PR, and waits for checks again before merging.

After the merge command succeeds, pid requires `pr_merged_at_args` confirmation
before it considers the run successful. If the forge queued the merge or enabled
auto-merge, pid polls for up to `workflow.merge_confirmation_timeout_seconds`
seconds before cleaning up. If confirmation times out, pid exits nonzero and
leaves the PR/worktree for manual follow-up.

Extensions can hook, add, replace, or disable workflow steps, including
fine-grained PR-loop substeps for push, PR updates, checks, base refresh, merge,
merge confirmation, merge recovery, and cleanup. Extensions can also replace
named PR-loop policies and register commands under `pid x ...`. See
[docs/EXTENSIONS.md](docs/EXTENSIONS.md) for the API, trust boundary, and
runnable local-extension examples.

Prompt templates must be non-blank and support these fields:

| Prompt key | Fields |
| --- | --- |
| `prompts.message` | `{original_prompt}`, `{branch}`, `{base_rev}`, `{output_path}` |
| `prompts.review` | `{original_prompt}`, `{review_target}` |
| `prompts.ci_fix` | `{pr_title}`, `{pr_url}`, `{commit_title}`, `{checks_out}` |
| `prompts.rebase_fix` | `{original_prompt}`, `{pr_title}`, `{pr_body}`, `{pr_url}`, `{default_branch}`, `{commit_title}`, `{merge_out}`, `{forge_label}` |

`prompts.message` must include `{output_path}` so the agent knows where to
write the JSON commit/PR metadata. Use doubled braces (`{{` and `}}`) for
literal braces inside prompt text. `diagnostic_output_limit` truncates
`{checks_out}` and `{merge_out}` before they are inserted into prompts.

Example agent configs:

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

Example forge config that switches the executable and label while keeping the
default `gh`-style argument shape:

```toml
[forge]
command = ["glab"]
label = "gitlab"
```

Real forge CLIs differ, so adjust every `forge.*_args` template for your
installed version. For example, a CLI without head-OID guarded merges might use:

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

Agent and forge CLI flags change over time; treat examples as starting points.

Environment variables override the matching paths or workflow config at runtime:

| Variable | Config default | Description |
| --- | --- | --- |
| `PID_LOG_DIR` | platform state/log directory | Directory where pid writes session logs and where `pid sessions` reads them. |
| `PID_CHECKS_TIMEOUT_SECONDS` | `workflow.checks_timeout_seconds` | How long to wait for pending checks. |
| `PID_CHECKS_POLL_INTERVAL_SECONDS` | `workflow.checks_poll_interval_seconds` | Delay between check polling attempts. |
| `PID_MERGE_CONFIRMATION_TIMEOUT_SECONDS` | `workflow.merge_confirmation_timeout_seconds` | How long to wait for queued/auto-merge confirmation before cleanup. |
| `PID_MERGE_CONFIRMATION_POLL_INTERVAL_SECONDS` | `workflow.merge_confirmation_poll_interval_seconds` | Delay between merge-confirmation polling attempts. |
| `PID_MERGE_RETRY_LIMIT` | `workflow.merge_retry_limit` | Safety cap for moved-base merge/rebase retries that do not consume `ATTEMPTS`. |

## Development

This repository uses `mise` for tools and tasks. The test task runs pytest in
parallel with coverage reporting and fails below 95% total coverage.

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
Tagged releases also publish `ghcr.io/lauritsk/pid` with GoReleaser `dockers_v2`.
The release workflow logs in to `dhi.io` before pulling Docker Hardened Images;
configure `DHI_USERNAME` and `DHI_PASSWORD` repository secrets.

### Project layout

- `src/pid/cli.py` owns Typer command-line wiring.
- `src/pid/workflow.py` coordinates the high-level pid lifecycle.
- `src/pid/repository.py` wraps git, worktree, and commit operations.
- `src/pid/github.py` wraps configurable forge/PR CLI operations.
- `src/pid/prompts.py` builds agent prompts and isolates untrusted output.
- `src/pid/commands.py`, `output.py`, `parsing.py`, `utils.py`,
  `models.py`, and `errors.py` contain shared support code.
- `tests/fakes.py` provides the fake command harness for flow tests.
