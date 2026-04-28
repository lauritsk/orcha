# Extensions

Extensions let trusted Python code customize pid without forking it. The API is
versioned with `PID_EXTENSION_API = "1"`.

## Enable extensions

Install an extension package that exposes the `pid.extensions` entry point, then
enable it in config:

```toml
[extensions]
enabled = ["my_extension"]
paths = []
```

For project-local extensions, put Python files in a configured directory:

```toml
[extensions]
enabled = ["local_checks"]
paths = [".pid/extensions"]

[extensions.local_checks]
command = "mise run check"
```

Relative `paths` are resolved from the repository root during normal workflow
runs. Extension commands under `pid x ...` resolve relative paths from the repo
root when available, otherwise from the current directory.

## Extension protocol

```python
from pid.extensions import ExtensionRegistry, WorkflowStep

class MyExtension:
    name = "my_extension"
    api_version = "1"

    def register(self, registry: ExtensionRegistry) -> None:
        registry.add_hook("before.run_initial_agent", before_initial)
        registry.add_step(WorkflowStep("extra_check", extra_check), before="run_pr_loop")
        registry.replace_step("run_review_agent", custom_review)
        registry.add_cli_command("doctor", doctor)
```

An extension object can be exposed as:

- an entry point object or class;
- `extension = MyExtension()` in a local file;
- `get_extension()` in a local file;
- a local class with `name`, `api_version`, and `register()`.

## Registry API

- `add_hook(name, fn, *, order=0)`
- `add_step(step, *, before=None, after=None)`
- `replace_step(name, step)`
- `disable_step(name)`
- `add_policy(name, policy)`
- `replace_service(name, factory)`
- `add_cli_command(name, callback)`

Hook order is deterministic: `order`, extension name, then registration order.
Hooks and workflow steps must return `None` or `StepResult`; other return values
stop the run with an extension diagnostic.

## Built-in workflow steps

Project-local extensions load after `resolve_repo_root`, then can hook, add, or
replace these extension-aware steps:

1. `require_commands`
2. `resolve_main_worktree`
3. `validate_clean_main_worktree`
4. `resolve_default_branch`
5. `update_default_branch`
6. `capture_base_rev`
7. `create_worktree`
8. `trust_mise`
9. `run_initial_agent`
10. `inspect_initial_changes`
11. `run_review_agent`
12. `inspect_review_changes`
13. `stop_if_no_changes`
14. `generate_message`
15. `verify_commit_title`
16. `commit_changes`
17. `run_pr_loop`

`run_pr_loop` is itself split into hookable/replaceable substeps:

1. `pr_prepare_attempt`
2. `pr_refresh_base_before_message`
3. `pr_regenerate_message`
4. `pr_refresh_base_before_pr`
5. `pr_push_branch`
6. `pr_ensure_pr`
7. `pr_wait_for_checks`
8. `pr_handle_checks`
9. `pr_refresh_base_after_checks`
10. `pr_squash_merge`
11. `pr_recover_merge`
12. `pr_confirm_merge`
13. `pr_cleanup`

PR-loop policy names are `pr.push`, `pr.ensure_pr`, `pr.checks`, `pr.ci_fix`,
`pr.base_refresh`, `pr.merge`, `pr.merge_recovery`, `pr.merge_confirmation`, and
`pr.cleanup`. Register a callable with `registry.add_policy(name, fn)` to replace
one behavior while keeping surrounding hooks and loop control. Policy callables
receive `WorkflowContext` and return `None` or `StepResult`; replacements should
update the same context fields as the default policy, such as `ctx.checks_status`
/ `ctx.checks_output`, `ctx.pr_loop.refresh_result`,
`ctx.pr_loop.merge_result`, or `ctx.pr_loop.merge_confirmed`. Replacements that
short-circuit terminal substeps must set either `ctx.pr_loop.completed` or
`ctx.pr_loop.next_iteration`; otherwise pid reports an extension error instead
of looping forever. If `pr_cleanup` is disabled or skipped after merge
confirmation, pid treats the PR loop as complete and leaves cleanup to the
extension or operator.

Entry-point extensions load earlier and can also replace or disable bootstrap
steps, but local extensions intentionally cannot change argument parsing or repo
resolution.

## Context

Hooks and steps receive `WorkflowContext`. Common fields:

- `ctx.config`
- `ctx.extension_config`
- `ctx.parsed`
- `ctx.branch`
- `ctx.repo_root`
- `ctx.main_worktree`
- `ctx.worktree_path`
- `ctx.default_branch`
- `ctx.base_rev`
- `ctx.commit_message`
- `ctx.runner`
- `ctx.repository`
- `ctx.forge`
- `ctx.events`
- `ctx.scratch`
- `ctx.pr_loop` for PR-loop state such as `need_force_push`,
  `refresh_result`, `checks_timeout_seconds`, `merge_result`,
  `merge_confirmed`, `next_iteration`, and `completed`

Useful helpers:

- `ctx.require_parsed()`
- `ctx.require_worktree()`
- `ctx.repo_path()`
- `ctx.set_commit_message(message)`
- `ctx.emit(name, step="...", fields={...})`

## Extension commands

Extensions register commands under `pid x ...`:

```python
def doctor(ctx):
    print("extension ok")
    return 0

class MyExtension:
    name = "my_extension"
    api_version = "1"

    def register(self, registry):
        registry.add_cli_command("doctor", doctor)
```

Run it:

```sh
pid x doctor
```

List enabled extensions:

```sh
pid x extensions list
```

## Example: JSON event log

`.pid/extensions/json_log.py`:

```python
from pathlib import Path

from pid.events import JsonlEventSink

class JsonLogExtension:
    name = "json_log"
    api_version = "1"

    def register(self, registry):
        def install_sink(ctx):
            path = Path(ctx.repo_root) / ".git" / "pid-events.jsonl"
            ctx.events = JsonlEventSink(path)
            return None

        registry.add_hook("before.require_commands", install_sink)

extension = JsonLogExtension()
```

Config:

```toml
[extensions]
enabled = ["json_log"]
paths = [".pid/extensions"]
```

## Example: local check before PR

`.pid/extensions/local_checks.py`:

```python
from pid.extensions import StepResult, WorkflowStep
from pid.output import write_command_output

class LocalChecksExtension:
    name = "local_checks"
    api_version = "1"

    def register(self, registry):
        registry.add_step(
            WorkflowStep("run_local_checks", run_local_checks),
            before="run_pr_loop",
        )


def run_local_checks(ctx):
    result = ctx.runner.run(["mise", "run", "check"], cwd=ctx.require_worktree())
    if result.returncode != 0:
        write_command_output(result)
        return StepResult.stop(result.returncode, "local checks failed")
    return None

extension = LocalChecksExtension()
```

Config:

```toml
[extensions]
enabled = ["local_checks"]
paths = [".pid/extensions"]
```

## Trust boundary

Extensions run in-process with full Python access to the repository and user
environment. Only enable extensions you trust.
