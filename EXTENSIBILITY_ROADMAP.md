# Extensibility roadmap

Goal: keep `pid` tiny, stable, and useful by default, while making most
behavior replaceable without forking. Core should be a small orchestration
kernel; user/project code should provide new commands, workflow steps,
policies, prompts, forge adapters, and output behavior.

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

## Current shape

`src/pid/workflow.py` currently owns the whole lifecycle in one
`PIDFlow._run` method. This makes behavior easy to follow, but hard to override
one piece of the workflow.

Already configurable:

- Agent command and prompt templates in `AgentConfig` / `PromptConfig`.
- Forge command templates in `ForgeConfig`.
- Commit verifier in `CommitConfig`.
- Runtime and workflow tuning in `RuntimeConfig` / `WorkflowConfig`.

Main friction:

- No first-class extension loading.
- No stable hook names.
- No step registry.
- No way to add CLI subcommands without editing `cli.py`.
- No structured workflow events beyond session log strings.
- `PIDFlow` directly constructs `Repository`, `Forge`, `SessionLogger`, and
  `KeepAwake`.

## Target architecture

```text
CLI
  -> config loader
  -> extension loader
  -> service container
  -> workflow engine
       -> ordered workflow steps
       -> before/after/error hooks
       -> policies
       -> events
```

Core package remains small:

- `pid.config`: load/validate config.
- `pid.extensions`: discover/register extensions.
- `pid.context`: typed runtime context and services.
- `pid.workflow`: generic step engine plus default workflow definition.
- `pid.builtins`: default steps, policies, adapters.

## Extension API

Expose one stable Python protocol:

```python
from pid.extensions import Extension, ExtensionRegistry

class MyExtension(Extension):
    name = "my-extension"

    def register(self, registry: ExtensionRegistry) -> None:
        registry.add_hook("before.run_initial_agent", my_hook)
        registry.replace_step("run_review_agent", my_review_step)
        registry.add_cli_command("doctor", doctor_command)
```

Load from Python entry points:

```toml
[project.entry-points."pid.extensions"]
my_extension = "my_pkg.pid_ext:MyExtension"
```

Also load local project extensions from config for quick hacks:

```toml
[extensions]
enabled = ["my_extension"]
paths = [".pid/extensions"]
```

## Registry capabilities

Start minimal:

- `add_hook(name, fn, *, order=0)`
- `add_step(step, *, before=None, after=None)`
- `replace_step(name, step)`
- `disable_step(name)`
- `add_policy(name, policy)`
- `replace_service(name, factory)`
- `add_cli_command(name, callback)`

Keep hook and step names stable once documented.

## Workflow steps

Break `PIDFlow._run` into named steps. Initial built-in order:

1. `parse_args`
2. `start_session_logging`
3. `start_keep_awake`
4. `validate_branch`
5. `resolve_repo`
6. `require_commands`
7. `validate_clean_main_worktree`
8. `update_default_branch`
9. `create_worktree`
10. `trust_mise`
11. `run_initial_agent`
12. `run_review_agent`
13. `stop_if_no_changes`
14. `generate_message`
15. `verify_commit_title`
16. `commit_changes`
17. `create_or_update_pr`
18. `wait_for_checks`
19. `fix_failed_checks`
20. `refresh_pr_message`
21. `merge_pr`
22. `cleanup`

Each step should be a callable:

```python
def step(ctx: WorkflowContext) -> StepResult:
    ...
```

`StepResult` can be `continue`, `skip`, `stop(code)`, or `retry`.

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

## Context and services

Create `WorkflowContext` with typed, explicit state:

- config
- parsed args
- repo root
- main worktree
- worktree path
- branch
- default branch
- base rev
- commit message
- PR URL
- runner
- repository service
- forge service
- logger
- event bus
- scratch dict for extensions

Avoid arbitrary mutation where possible. Prefer helper methods:

- `ctx.set_commit_message(...)`
- `ctx.require_worktree()`
- `ctx.emit(event)`

## Override targets

High-value replaceable pieces:

- Agent adapter: non-interactive/session/review/message generation.
- Forge adapter: GitHub/GitLab/Gitea/custom.
- Review strategy: skip, multi-agent, path-aware, security-only.
- Commit message generator: agent, conventional-commit parser, template,
  local model.
- Checks policy: timeout, retry, required checks, ignored checks.
- Merge policy: squash/rebase/merge commit/manual stop.
- Cleanup policy: always remove worktree vs keep on failure.
- Output renderer: Rich, JSON, quiet, machine-readable.

## CLI extensibility

Let extensions register subcommands under `pid x ...` first, avoiding
collisions with core commands.

Example:

```sh
pid x doctor
pid x jira link PID-123
pid x templates list
```

Later, allow top-level commands only with explicit config opt-in.

## Config extensibility

Keep strict validation for core tables, but reserve extension namespace:

```toml
[extensions.my_extension]
foo = "bar"
```

Core validates only that extension config is TOML-compatible. Extension
validates its own table and reports
`pid: invalid [extensions.my_extension] ...`.

## Event stream

Replace ad-hoc log strings with structured events, while still rendering nice
text:

```python
WorkflowEvent(
    name="step.started",
    step="run_review_agent",
    fields={"thinking": "high"},
)
```

Benefits:

- JSON logs.
- Notifications.
- Replay/debug.
- Extension hooks without parsing terminal output.

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

## Incremental implementation plan

### Phase 1: refactor without behavior changes

- Extract `WorkflowContext` from `PIDFlow` local variables.
- Extract default workflow steps from `_run` into small functions.
- Add tests proving current behavior unchanged.
- Keep `run_pid(argv, config)` public behavior identical.

### Phase 2: internal step engine

- Add `WorkflowStep` model with `name` and `run(ctx)`.
- Execute built-in steps through ordered engine.
- Add structured step events to session log.
- No external extension loading yet.

### Phase 3: hooks

- Add hook registry.
- Add `before/after/error` hook execution.
- Add config table for local hooks disabled by default.
- Test ordering, error handling, and stop behavior.

### Phase 4: extension discovery

- Add entry point loading via `importlib.metadata.entry_points`.
- Add `[extensions] enabled = [...]` config.
- Add `pid extensions list` info command.
- Fail fast on incompatible API versions.

### Phase 5: replaceable services and policies

- Define interfaces for `Agent`, `Forge`, `Repository`, `MessageGenerator`,
  `ChecksPolicy`, and `MergePolicy`.
- Register built-ins as defaults.
- Let extensions replace or wrap services.

### Phase 6: CLI extension commands

- Add `pid x <extension-command> ...` dispatcher.
- Keep Typer core simple; extension commands receive argv/context.
- Add docs and examples.

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
- Config sprawl: keep core defaults minimal and put bloat in extension packages.

## Definition of done

- Core pid still works with zero config and zero extensions.
- Default workflow behavior remains unchanged in tests.
- User can install one package and enable it from config.
- User can add project-local extension code without forking pid.
- User can add a step, hook a built-in step, and replace a built-in step.
- Public extension API is documented with at least two runnable examples.
