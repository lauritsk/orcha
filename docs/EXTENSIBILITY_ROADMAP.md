# Extensibility roadmap

Status: validated against the current `src/pid` workflow on 2026-04-28.
Initial API version 1 is implemented; see `docs/EXTENSIONS.md` for the current
extension API. Continue to preserve the compatibility guardrails below: keep the
legacy `pid [session] [ATTEMPTS] [THINKING] BRANCH [PROMPT...]` flow, preserve
existing base-refresh and merge-confirmation behavior, and reserve new CLI
command words deliberately.

Goal: keep `pid` tiny, stable, and useful by default, while making most
behavior replaceable without forking. Core should be a small orchestration
kernel; user/project code should provide new commands, workflow steps,
policies, prompts, forge adapters, event sinks, and output behavior.

## Design principles

- Small stable core: config loading, command execution, git/worktree safety,
  event model, and default workflow.
- Replace by interface, not by patching internals.
- Additive extension points first; overrides only where needed.
- Plain Python extension API plus config-first escape hatches.
- Deterministic execution order and explicit failure behavior.
- Strong boundaries: extension hooks get context objects and services, not free
  access to hidden mutable state.
- Safe defaults: missing/broken extensions fail with clear diagnostics before
  destructive steps.
- Backward compatibility: zero-extension runs must keep the current CLI,
  stdout/stderr expectations, exit codes, and cleanup behavior.

## Current shape

`src/pid/workflow.py` owns the lifecycle through `PIDFlow._run`,
`PIDFlow.run_pr_loop`, and helper methods. This keeps behavior easy to follow,
but makes it hard to override one piece of the workflow.

Already configurable:

- Agent command and prompt templates in `AgentConfig` / `PromptConfig`.
- Forge command templates in `ForgeConfig`.
- Commit verifier in `CommitConfig`.
- Runtime and workflow tuning in `RuntimeConfig` / `WorkflowConfig`.
- Output detail via `OutputMode`.

Important current behavior to preserve:

- Flexible positional CLI parsing, including `session` mode.
- Strict config validation for existing core tables.
- Main worktree cleanliness and branch/worktree guards before generated work.
- Optional `mise trust` after worktree creation.
- Review and message-generation passes before commit/PR creation.
- PR loop with configurable check waits, CI fix attempts, base refresh stages,
  merge retries, merge confirmation polling, and cleanup.
- Session logs with current user-visible progress messages.

Main friction:

- No first-class extension loading.
- No stable hook names.
- No step registry.
- No structured workflow events beyond session log strings.
- No way to add extension commands without reserving a top-level CLI word or a
  separate script.
- Strict config parsing currently rejects a top-level `[extensions]` table.
- `PIDFlow` directly constructs `Repository`, `Forge`, `SessionLogger`, and
  `KeepAwake`.

## Target architecture

```text
CLI
  -> config loader
  -> installed extension loader
  -> service container
  -> workflow engine
       -> ordered workflow steps
       -> before/after/error hooks
       -> policies
       -> events
       -> project-local extension loader after repo resolution
```

Core package remains small:

- `pid.config`: load/validate config.
- `pid.extensions`: discover/register extensions.
- `pid.context`: typed runtime context and services.
- `pid.events`: structured workflow events and sinks.
- `pid.workflow`: generic step engine plus default workflow definition.
- `pid.builtins`: default steps, policies, adapters.

## Extension API

Expose one stable Python protocol:

```python
from pid.extensions import Extension, ExtensionRegistry

class MyExtension(Extension):
    name = "my-extension"
    api_version = "1"

    def register(self, registry: ExtensionRegistry) -> None:
        registry.add_hook("before.run_initial_agent", my_hook)
        registry.replace_step("run_review_agent", my_review_step)
        registry.add_cli_command("doctor", doctor_command)
```

Load installed packages from Python entry points:

```toml
[project.entry-points."pid.extensions"]
my_extension = "my_pkg.pid_ext:MyExtension"
```

Also load project-local extensions from config for quick project-specific
behavior:

```toml
[extensions]
enabled = ["my_extension"]
paths = [".pid/extensions"]
```

Project-local paths are resolved relative to the repository root. They load
after `parse_args` and `resolve_repo_root`, but before worktree creation or any
destructive step. Installed entry-point extensions can load earlier because they
do not need a repository path.

## Registry capabilities

Start minimal:

- `add_hook(name, fn, *, order=0)`
- `add_step(step, *, before=None, after=None)`
- `replace_step(name, step)`
- `disable_step(name)`
- `add_policy(name, policy)`
- `replace_service(name, factory)`
- `add_cli_command(name, callback)`

Registry rules:

- Built-ins register first; extensions modify that default registry.
- Step names, hook names, and extension command names must be unique after
  registration.
- Ordering is deterministic: `(order, extension_name, registration_index)` for
  hooks and explicit `before`/`after` constraints for steps.
- Registration failures include the extension name and fail before destructive
  steps.
- Keep hook and step names stable once documented.

## Workflow steps

Break `PIDFlow._run` and `PIDFlow.run_pr_loop` into named steps. Initial
built-in order should reflect the current workflow:

1. `parse_args`
2. `start_session_logging`
3. `start_keep_awake`
4. `render_run_summary`
5. `validate_branch`
6. `resolve_repo_root`
7. `require_commands`
8. `resolve_main_worktree`
9. `validate_clean_main_worktree`
10. `resolve_default_branch`
11. `update_default_branch`
12. `capture_base_rev`
13. `create_worktree`
14. `trust_mise`
15. `run_initial_agent`
16. `inspect_initial_changes`
17. `run_review_agent`
18. `inspect_review_changes`
19. `stop_if_no_changes`
20. `generate_message`
21. `verify_commit_title`
22. `commit_changes`
23. `start_pr_attempt`
24. `commit_automated_feedback`
25. `refresh_base_before_message`
26. `regenerate_message_if_needed`
27. `refresh_base_before_pr`
28. `push_pr_branch`
29. `create_or_update_pr`
30. `wait_for_checks`
31. `fix_failed_checks`
32. `refresh_base_after_checks`
33. `repush_after_base_refresh`
34. `refresh_pr_message`
35. `merge_pr`
36. `wait_for_merge_confirmation`
37. `recover_failed_merge`
38. `cleanup`
39. `shutdown`

PR-loop steps can repeat. `fix_failed_checks` consumes `ATTEMPTS` as it does
today. `recover_failed_merge` uses `workflow.merge_retry_limit` as it does
today. Base-refresh steps are conditional on `workflow.base_refresh_enabled`,
`workflow.base_refresh_stages`, and `workflow.base_refresh_limit`.

Each step should be a callable:

```python
def step(ctx: WorkflowContext) -> StepResult:
    ...
```

`StepResult` can be `continue`, `skip`, `stop(code)`, or `retry`. Retry must be
bounded by a policy or an existing workflow limit.

## Hook model

For each step, emit hooks:

- `before.<step>`
- `after.<step>`
- `error.<step>`

Also provide lifecycle hooks:

- `startup`
- `config.loaded`
- `workflow.created`
- `workflow.completed`
- `workflow.failed`
- `shutdown`

Example use cases:

- Run project-specific lint before PR creation.
- Add Jira link to PR body.
- Send Slack notification after merge.
- Block merge if generated docs missing.
- Replace review prompt based on file paths changed.
- Add extra CI failure classifier.

Hook failures should default to fail-fast. A later policy can allow explicitly
non-blocking hooks, but blocking is safer for API version 1.

## Context and services

Create `WorkflowContext` with typed, explicit state:

- config
- extension config
- parsed args
- repo root
- main worktree
- worktree path
- branch
- default branch
- base rev
- output mode
- follow-up thinking level
- review-rejected-first-pass flag
- pre/post state hashes
- commit message
- commit title
- rewritten head
- PR URL
- PR title/body
- checks status/output
- attempt counters
- base-refresh counters
- merge retry counters
- runner
- repository service
- forge service
- keep-awake service
- logger/session logger
- event bus
- scratch dict for extensions

Avoid arbitrary mutation where possible. Prefer helper methods:

- `ctx.set_commit_message(...)`
- `ctx.require_worktree()`
- `ctx.mark_force_push_needed(...)`
- `ctx.emit(event)`

## Override targets

High-value replaceable pieces:

- Agent adapter: non-interactive/session/review/message generation.
- Forge adapter: GitHub/GitLab/Gitea/custom.
- Review strategy: skip, multi-agent, path-aware, security-only.
- Commit message generator: agent, conventional-commit parser, template,
  local model.
- Checks policy: timeout, retry, required checks, ignored checks.
- Base-refresh policy: stages, conflict handling, rebasing strategy.
- Merge policy: squash/rebase/merge commit/manual stop.
- Merge confirmation policy: poll, trust merge command, or manual approval.
- Cleanup policy: always remove worktree vs keep on failure.
- Event sinks: session log, JSONL, notifications.
- Output renderer: Rich, JSON, quiet, machine-readable.

## CLI extensibility

Current CLI accepts flexible positional arguments, so any new top-level word can
change behavior for a branch with that name. API version 1 should therefore
reserve only one extension namespace and document it clearly.

Recommended first namespace:

```sh
pid x doctor
pid x jira link PID-123
pid x templates list
```

Implementation notes:

- Dispatch `pid x ...` before legacy workflow parsing, similar to current
  `config`, `sessions`, and `version` info commands.
- Treat `x` as a reserved command word once this ships.
- Do not let extensions add arbitrary top-level `pid <command>` names in API
  version 1.
- Add compatibility tests proving all non-reserved legacy argument forms still
  parse exactly as before.

Later, allow top-level commands only with explicit config opt-in and release
notes.

## Config extensibility

Keep strict validation for core tables, but reserve an extension namespace:

```toml
[extensions]
enabled = ["my_extension"]
paths = [".pid/extensions"]

[extensions.my_extension]
foo = "bar"
```

Parser rules:

- `enabled` is a list of extension names.
- `paths` is a list of project-local extension directories.
- Other subtables under `[extensions.<name>]` are raw extension config.
- Core validates that extension config is TOML-compatible, but the extension
  validates its own table and reports
  `pid: invalid [extensions.my_extension] ...`.
- Unknown keys inside existing core tables remain errors.
- `pid config show` should include extension config after this table exists.

## Event stream

Use one shared `pid.events.WorkflowEvent` model for both extensibility and the
higher-level orchestrator plan:

```python
WorkflowEvent(
    name="step.started",
    step="run_review_agent",
    fields={"thinking": "high"},
)
```

The final event model may also include run ID, sequence, timestamp, level, and
stage. The first implementation only needs enough structure to avoid parsing
terminal output.

Benefits:

- JSON logs.
- Notifications.
- Replay/debug.
- Extension hooks without parsing terminal output.
- A shared foundation for the orchestrator agent roadmap.

## Stability policy

Document public API tiers:

- Stable: extension protocol, hook names, context properties, built-in step
  names.
- Semi-stable: service interfaces, event fields.
- Internal: helper functions, exact default step implementations.

Add `pid extensions api-version` or constant, e.g. `PID_EXTENSION_API = "1"`.

Extensions declare compatibility:

```python
api_version = "1"
```

Incompatible extensions fail fast with a clear diagnostic before worktree
creation.

## Incremental implementation plan

### Phase 0: compatibility baseline

- Run the existing test suite before refactoring.
- Add characterization tests for legacy CLI parsing, output/exit codes, base
  refresh, merge confirmation, and config validation that the refactor must not
  change.
- Decide and document the reserved command word for extension commands (`x`).

### Phase 1: refactor without behavior changes

- Extract `WorkflowContext` from `PIDFlow` local variables.
- Move `Repository`, `Forge`, `SessionLogger`, and `KeepAwake` construction
  behind context/service factories, while keeping defaults identical.
- Extract default workflow steps from `_run` and `run_pr_loop` into small
  functions.
- Keep `run_pid(argv, config, output_mode)` public behavior identical.

### Phase 2: internal step engine and events

- Add `WorkflowStep` model with `name` and `run(ctx)`.
- Execute built-in steps through an ordered engine.
- Add structured step events while preserving current session-log text.
- No external extension loading yet.

### Phase 3: internal hooks

- Add hook registry.
- Add `before/after/error` hook execution around steps.
- Allow tests to inject hooks directly.
- Test ordering, error handling, stop behavior, and bounded retry behavior.

### Phase 4: extension config and discovery

- Add `[extensions] enabled = [...]` and `paths = [...]` config support.
- Add entry point loading via `importlib.metadata.entry_points`.
- Add project-local path loading after repo root resolution.
- Add `pid extensions list` or `pid x extensions list` info command.
- Fail fast on missing, duplicate, or incompatible API versions.

### Phase 5: replaceable services and policies

- Define interfaces for `Agent`, `Forge`, `Repository`, `MessageGenerator`,
  `ChecksPolicy`, `BaseRefreshPolicy`, `MergePolicy`, and `CleanupPolicy`.
- Register built-ins as defaults.
- Let extensions replace or wrap services.

### Phase 6: CLI extension commands

- Add `pid x <extension-command> ...` dispatcher.
- Keep Typer core simple; extension commands receive argv/context.
- Add docs and examples.

### Phase 7: runnable examples and API docs

- Add at least two runnable example extensions.
- Document stable hook/step names, context fields, and trust boundaries.
- Document migration guidance for config-only customizations to extensions.

## Good first extension examples

- `pid-ext-json-log`: writes workflow events as JSONL.
- `pid-ext-slack`: posts start/fail/merge notifications.
- `pid-ext-jira`: extracts ticket from branch and updates PR body.
- `pid-ext-local-checks`: runs `mise run check` before PR creation.
- `pid-ext-keep-worktree`: keeps worktree on failure and prints path.
- `pid-ext-review-security`: adds extra security review pass.

## Risks and mitigations

- Too much API too early: ship hooks/steps first, services later.
- Extension breakage: version public API and keep internal modules private.
- Hard debugging: emit structured events and show extension names in errors.
- Unsafe hooks: run in-process by default, but document trust boundary. Local
  extensions are trusted code.
- CLI collisions: reserve only `pid x` for extension commands in API version 1.
- Local path ambiguity: resolve project-local extension paths from repo root and
  load them before destructive steps.
- Config sprawl: keep core defaults minimal and put bloat in extension packages.

## Definition of done

- Core pid still works with zero config and zero extensions.
- Default workflow behavior remains unchanged in tests.
- Base refresh, merge retry, merge confirmation, and cleanup behavior remain
  unchanged unless explicitly overridden by an extension.
- User can install one package and enable it from config.
- User can add project-local extension code without forking pid.
- User can add a step, hook a built-in step, and replace a built-in step.
- User can run an extension command under `pid x ...`.
- Public extension API is documented with at least two runnable examples.
- `mise run check` passes.
