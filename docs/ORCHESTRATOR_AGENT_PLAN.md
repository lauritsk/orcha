# Higher-Level Orchestrator Agent Implementation Plan

Status: draft, rebased onto current codebase state
Scope: design and implementation plan only; this file intentionally is not
`PLAN.md`.

## Goal

Add a higher-level Orchestrator Agent above the existing pid workflow. The user
interacts with this agent, the agent launches the current pid workflow, watches
structured progress, persists state, and reacts to selected recoverable terminal
failures instead of only letting the workflow abort.

The existing `pid [ATTEMPTS] [THINKING] BRANCH PROMPT...` command must remain
backward compatible.

## Current codebase state

pid is no longer a purely linear workflow. Current implementation has these
important foundations already in place:

- `src/pid/workflow.py` has `PIDFlow`, with a step-based lifecycle.
- `src/pid/context.py` has `WorkflowContext` and `PRLoopState` for mutable run
  state.
- `src/pid/events.py` has the event model and sinks:
  - `WorkflowEvent`
  - `EventSink`
  - `NullEventSink`
  - `JsonlEventSink`
  - `CompositeEventSink`
  - `ListEventSink`
- `PIDFlow` already accepts an optional event sink.
- The workflow emits `workflow.created`, `step.started`, `step.completed`,
  `step.failed`, `step.retrying`, `workflow.completed`, and `workflow.failed`.
- The workflow is extension-aware. Steps, hooks, policies, and service
  replacements can be supplied through `ExtensionRegistry`.
- The PR loop is already split into fine-grained substeps.

Current primary gap:

- Failures are still mostly `PIDAbort(code)` from core helpers, repository
  helpers, forge helpers, and workflow methods.
- There is no persisted run state store.
- There is no supervised API that lets classified failures escape to a higher
  controller.
- There is no `pid-agent` entry point.
- Resume is not durable yet because context reconstruction is not implemented.

Existing recovery already handles CI failures, base refresh, merge retries,
rebase conflict fixing, force-with-lease handling for agent-rewritten history,
and cleanup after successful merge. The Orchestrator Agent should reuse this
logic, not duplicate it.

## Recommended product shape

### Keep legacy CLI unchanged

Keep this working exactly as today:

```sh
pid 3 high feature/add-thing "add thing"
```

### Add new agent entry point

Recommended first entry point:

```sh
pid-agent --branch feature/add-thing --prompt "add thing"
pid-agent --resume <run-id>
pid-agent --status <run-id>
pid-agent --runs
```

Reason: adding `pid agent` can conflict with the current flexible positional
parser because `agent` can be a valid branch name. A separate `pid-agent`
console script avoids a breaking change. A `pid agent` alias can be considered
later only after compatibility tests prove it safe.

### Agent interaction modes

1. **Structured mode** — MVP path
   - User supplies branch, prompt, attempts, and thinking as options.
   - Agent launches the existing workflow with a supervised event sink.
   - Agent persists state and prints progress/final status.

2. **Interactive mode** — later
   - User describes desired work in natural language.
   - Agent asks clarifying questions when needed.
   - Agent proposes branch, prompt, attempts, and thinking level.
   - User approves before launch.
   - Agent asks for decisions only on risky or ambiguous failures.

## Core design principles

1. **Bounded autonomy**
   - The agent chooses from typed actions.
   - It must not execute arbitrary shell commands produced by an LLM.

2. **Reuse existing workflow behavior**
   - Do not reimplement CI fixing, merge recovery, base refresh, push safety, or
     cleanup in the supervisor when `PIDFlow` already owns that behavior.
   - The supervisor handles terminal failures and cross-run state.

3. **Observable workflow**
   - Use the existing event model and step names.
   - Add run ID and sequence metadata around the current event stream instead of
     replacing `WorkflowEvent`.

4. **Recoverable terminal failures become decisions**
   - Preserve legacy `PIDAbort` behavior for `pid`.
   - In supervised mode, convert selected aborts/results to classified
     `WorkflowFailure`s that the supervisor can decide on.

5. **Persistent state without dirtying the repo**
   - Do not write run state into the worktree by default.
   - Store run data under the repository common git dir by default:
     - `<common-git-dir>/pid/runs/<run-id>/state.json`
     - `<common-git-dir>/pid/runs/<run-id>/events.jsonl`
     - `<common-git-dir>/pid/runs/<run-id>/diagnostics/`
   - Allow override with `PID_AGENT_RUNS_DIR` later.

6. **Extension compatibility**
   - The supervisor must preserve current hooks, step replacements, policies,
     and service replacements.
   - New supervised APIs should call through existing `PIDFlow`/`WorkflowStep`
     machinery.

## Current step map

Use current step names as the stable implementation surface.

Bootstrap steps:

```text
parse_args
start_session_logging
start_keep_awake
render_run_summary
validate_branch
resolve_repo_root
```

Main workflow steps:

```text
require_commands
resolve_main_worktree
validate_clean_main_worktree
resolve_default_branch
update_default_branch
capture_base_rev
create_worktree
trust_mise
run_initial_agent
inspect_initial_changes
run_review_agent
inspect_review_changes
stop_if_no_changes
generate_message
verify_commit_title
commit_changes
run_pr_loop
```

PR-loop substeps:

```text
pr_prepare_attempt
pr_refresh_base_before_message
pr_regenerate_message
pr_refresh_base_before_pr
pr_push_branch
pr_ensure_pr
pr_wait_for_checks
pr_handle_checks
pr_refresh_base_after_checks
pr_squash_merge
pr_recover_merge
pr_confirm_merge
pr_cleanup
```

Optional UI grouping can derive higher-level stages from these steps:

| UI stage | Current steps |
| --- | --- |
| arguments | `parse_args`, `render_run_summary` |
| preflight | `validate_branch`, `resolve_repo_root`, `require_commands`, `resolve_main_worktree`, `validate_clean_main_worktree` |
| worktree | `resolve_default_branch`, `update_default_branch`, `capture_base_rev`, `create_worktree`, `trust_mise` |
| initial_agent | `run_initial_agent`, `inspect_initial_changes` |
| review | `run_review_agent`, `inspect_review_changes`, `stop_if_no_changes` |
| commit | `generate_message`, `verify_commit_title`, `commit_changes` |
| pr | `run_pr_loop` and PR substeps |
| cleanup | `pr_cleanup` |
| done | `workflow.completed` |

## Event model

Current event model in `src/pid/events.py`:

```python
@dataclass(frozen=True)
class WorkflowEvent:
    name: str
    step: str = ""
    level: str = "info"
    message: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(...)
```

Do not replace this in the MVP. Instead add one of these small extensions:

1. Prefer: wrap the sink with `RunEventSink` that writes records with run
   metadata:

   ```json
   {
     "run_id": "20260427-161530-feature-add-thing",
     "sequence": 12,
     "event": {
       "timestamp": "...",
       "name": "step.started",
       "step": "run_initial_agent",
       "level": "info"
     }
   }
   ```

2. Alternative: add optional `run_id` and `sequence` fields to `WorkflowEvent`.

The wrapper approach is less invasive and keeps existing tests/extensions stable.

## Run state model

Add `src/pid/run_state.py`:

```python
@dataclass
class RunState:
    run_id: str
    status: Literal["created", "running", "waiting", "failed", "succeeded", "aborted"]
    branch: str
    prompt: str
    max_attempts: int
    thinking_level: str
    followup_thinking_level: str = ""
    current_step: str = ""
    current_stage: str = ""
    current_attempt: int = 0
    repo_root: str | None = None
    main_worktree: str | None = None
    default_branch: str | None = None
    base_rev: str | None = None
    worktree_path: str | None = None
    pr_url: str | None = None
    pr_title: str | None = None
    last_error: dict[str, Any] | None = None
```

Add `RunStore`:

- Generate run IDs from timestamp + branch slug.
- Resolve default store under `git rev-parse --path-format=absolute --git-common-dir`.
- Write `state.json` atomically.
- Append `events.jsonl`.
- Keep diagnostics files outside the worktree.
- Provide `list_runs()`, `load(run_id)`, and `save(state)`.

Add a small event projector:

```python
class RunStateProjector:
    def apply(self, state: RunState, event: WorkflowEvent) -> RunState:
        ...
```

The projector updates `current_step`, `current_stage`, status, attempts, PR URL,
and last error from events and known context snapshots.

## Failure model

Add `src/pid/failures.py`:

```python
class FailureKind(StrEnum):
    INVALID_ARGS = "invalid_args"
    MISSING_COMMAND = "missing_command"
    DIRTY_MAIN_WORKTREE = "dirty_main_worktree"
    LOCAL_BRANCH_EXISTS = "local_branch_exists"
    REMOTE_BRANCH_EXISTS = "remote_branch_exists"
    WORKTREE_PATH_EXISTS = "worktree_path_exists"
    WORKTREE_SETUP_FAILED = "worktree_setup_failed"
    MISE_TRUST_FAILED = "mise_trust_failed"
    INITIAL_AGENT_FAILED = "initial_agent_failed"
    REVIEW_AGENT_FAILED = "review_agent_failed"
    MESSAGE_AGENT_FAILED = "message_agent_failed"
    NO_CHANGES = "no_changes"
    COMMIT_FAILED = "commit_failed"
    PUSH_FAILED = "push_failed"
    PR_FAILED = "pr_failed"
    CHECKS_FAILED = "checks_failed"
    CHECKS_TIMEOUT = "checks_timeout"
    CI_FIX_FAILED = "ci_fix_failed"
    MERGE_FAILED = "merge_failed"
    REBASE_FAILED = "rebase_failed"
    REBASE_STILL_IN_PROGRESS = "rebase_still_in_progress"
    CLEANUP_FAILED = "cleanup_failed"
    EXTENSION_FAILED = "extension_failed"
    UNKNOWN = "unknown"
```

```python
@dataclass(frozen=True)
class WorkflowFailure(Exception):
    kind: FailureKind
    step: str
    code: int
    message: str
    recoverable: bool
    diagnostics: str = ""
    context: dict[str, Any] = field(default_factory=dict)
```

Keep `PIDAbort` for legacy control flow. Supervised mode should either:

- raise `WorkflowFailure` directly at classified failure points, or
- catch `PIDAbort` at step boundaries and classify it using current step,
  context, command result, and emitted diagnostics.

Prefer direct classification for high-value failure points first, then expand.

## Supervised workflow API

Add a supervised entry path without changing legacy `run()` behavior:

```python
class PIDFlow:
    def run(self, argv: list[str]) -> int:
        ...  # legacy: catches PIDAbort and returns exit code

    def run_supervised(self, argv: list[str]) -> WorkflowContext:
        ...  # lets WorkflowFailure escape; preserves ExtensionError semantics
```

Requirements:

- Legacy `pid` output and exit codes stay unchanged.
- `run_supervised()` still uses `WorkflowContext`, `WorkflowStep`, hooks,
  replacements, policies, and service replacements.
- On success it returns final `WorkflowContext` so `RunState` can capture PR URL,
  worktree path, attempts, etc.
- On failure it raises `WorkflowFailure` with enough context for policy decisions.

## Agent actions

Add deterministic action types in `src/pid/policy.py`:

```python
class RecoveryActionKind(StrEnum):
    RETRY_WORKFLOW = "retry_workflow"
    RETRY_STEP = "retry_step"
    RETRY_WITH_BUMPED_THINKING = "retry_with_bumped_thinking"
    RUN_AGENT_FIX = "run_agent_fix"
    EXTEND_WAIT = "extend_wait"
    ASK_USER = "ask_user"
    ABORT = "abort"
    MARK_DONE = "mark_done"
    CLEANUP_RETRY = "cleanup_retry"
```

```python
@dataclass(frozen=True)
class RecoveryAction:
    kind: RecoveryActionKind
    reason: str
    params: dict[str, Any] = field(default_factory=dict)
```

MVP should be conservative. `RETRY_STEP` is only safe for known idempotent or
purpose-built recovery steps. Full arbitrary step replay should wait until
context persistence/reconstruction is robust.

## Failure reaction matrix

- Invalid args: ask user for corrected values in interactive mode; noninteractive
  exits 2.
- Missing configured agent, forge, or commit verifier command: stop with install
  guidance.
- Dirty main worktree: stop and explain clean-worktree requirement; allow user
  retry after cleaning in interactive mode.
- Local/remote branch exists: suggest alternate branch name; ask in interactive
  mode.
- Worktree path exists: suggest cleanup or alternate branch; ask in interactive
  mode.
- `mise trust` fails: stop by default; retry only after explicit user approval.
- Initial agent fails: retry once with bumped thinking and diagnostic prompt;
  then ask/abort.
- Review agent fails: retry once with bumped thinking; then ask/abort.
- No changes after agent/review: mark done only with explicit acceptance in
  agent mode; legacy remains exit 0.
- Commit/message failure: use commit-blocker fix prompt only when diagnostics are
  local-worktree fixable; otherwise stop.
- Push failure: do not overwrite current push safety. Retry transient failures;
  ask on unexpected remote branch changes.
- PR create/edit failure: retry transient failures; otherwise stop with
  diagnostics.
- Checks failure: existing PR loop already runs CI fix until attempts exhaust.
  Supervisor reacts only to terminal exhaustion.
- Checks timeout: extend wait once only if clearly timeout; otherwise stop/ask.
- CI fix agent failure: retry with bumped thinking once; then stop/ask.
- Merge failure: existing PR loop already fetches/rebases/retries. Supervisor
  reacts only after merge retry exhaustion.
- Rebase still in progress: stop with preserved state and manual guidance unless
  an explicit recovery prompt is safe.
- Cleanup failure: retry cleanup once; if still failing, print manual cleanup
  commands and preserve run state.
- Extension failure: stop unless extension marks itself recoverable through a
  future API.

## Deterministic policy backend

Implement `RecoveryPolicy` before any LLM advisor:

```python
class RecoveryPolicy:
    def decide(self, state: RunState, failure: WorkflowFailure) -> RecoveryAction:
        ...
```

Policy must be deterministic, testable, and bounded. Use a separate recovery
budget from pid PR attempts:

```text
agent_recovery_budget = 5
```

The budget prevents infinite loops when the same recovery keeps failing.

## Supervisor controller

Add `src/pid/supervisor.py`:

```python
class OrchestratorAgent:
    def __init__(
        self,
        runner: CommandRunner | None = None,
        policy: RecoveryPolicy | None = None,
        store: RunStore | None = None,
        interactive: bool = True,
    ) -> None: ...

    def start(self, request: AgentRequest) -> int: ...
    def resume(self, run_id: str) -> int: ...
```

Controller loop:

1. Create `RunState` and run directory.
2. Create event sink chain:
   - console/status sink for agent output
   - JSONL sink for event log
   - state-projecting sink for `state.json`
3. Launch `PIDFlow.run_supervised()`.
4. On success, save final context fields and mark succeeded.
5. On `WorkflowFailure`, save failure and diagnostics.
6. Ask `RecoveryPolicy` for a typed action.
7. Execute only known safe actions.
8. Stop on success, abort, user choice, or recovery budget exhaustion.

## `pid-agent` CLI

Add `src/pid/agent_cli.py` and console script:

```toml
[project.scripts]
pid = "pid.cli:app"
pid-agent = "pid.agent_cli:app"
```

MVP commands/options:

```sh
pid-agent --branch BRANCH --prompt TEXT [--attempts N] [--thinking LEVEL]
pid-agent --resume RUN_ID
pid-agent --status RUN_ID
pid-agent --runs
```

Later options:

```sh
pid-agent --non-interactive
pid-agent --yes
pid-agent --advisor policy|pi
pid-agent --confirm-merge
```

## Prompt additions

Add to `src/pid/prompts.py` or `src/pid/agent_prompts.py`.

### Initial retry prompt

Purpose: initial configured agent exits nonzero before producing usable work.

Inputs:

- original prompt
- previous exit code
- stdout/stderr diagnostics

Behavior:

- Tell agent it is continuing the same task.
- Include diagnostics as untrusted evidence.
- Ask it to inspect the worktree and finish the requested work.

### Commit blocker fix prompt

Purpose: commit/message step fails due hooks, formatting, tests, generated files,
or metadata output issues.

Inputs:

- command output
- status/diff summary
- intended commit title

Behavior:

- Ask agent to fix local blockers only.
- Leave worktree committable.
- Do not push, merge, or cleanup.

### Generic recovery prompt

Purpose: fallback for local-worktree failures that policy classifies as fixable
but not CI/rebase.

Behavior:

- Use untrusted diagnostic wrappers.
- Fix local worktree issues only.
- Do not ask agent to perform remote/destructive actions directly.

## State and resume behavior

MVP resume must be conservative.

Supported in MVP:

- list runs
- show run state
- show worktree path and PR URL if known
- print last error and diagnostics path
- retry only from explicitly supported terminal states:
  - before PR push, if context can be reconstructed safely
  - checks failed after attempts exhausted
  - merge failed after retry exhaustion
  - cleanup failed

Not promised in MVP:

- perfect resume from every arbitrary step
- replay of non-idempotent steps
- reconstruction of full `WorkflowContext` from partial state

Durable resume later requires persisted context snapshots and safe entry points:

```python
resume_from_step(run_state.current_step, context)
```

## Live monitoring strategy

MVP:

- Monitor at workflow/step boundaries.
- Print current step/stage, attempt, PR URL, and final result.
- Command output can remain captured after command completion.

Later:

- Add command events:
  - `command.started`
  - `command.stdout_line`
  - `command.stderr_line`
  - `command.finished`
- This likely requires extending `CommandRunner` beyond current plumbum `.run()`
  capture behavior.

## Safety and security

1. Treat CI, merge, command, PR, and extension text as untrusted.
2. Keep existing untrusted diagnostic wrappers in prompts.
3. Redact likely secrets before writing diagnostics:
   - tokens
   - GitHub auth headers
   - common env var secret names
4. Never let advisor output execute arbitrary shell commands.
5. Require explicit approval for behavior more destructive than current pid.
6. Preserve current automatic squash-merge behavior for legacy CLI.
7. In interactive agent mode, consider `--confirm-merge` as an opt-in safer
   default.

## Optional LLM advisor

Add only after deterministic supervision is stable.

```sh
pid-agent --advisor pi
```

Advisor receives structured state, failure kind/step, redacted diagnostics, and
an allow-list schema. It must return JSON only. The supervisor validates the JSON
and executes only known actions. Invalid output falls back to deterministic
policy.

Do not ship advisor in MVP.

## Updated implementation roadmap

### PR 1: Run store and state projection

Already available: base events and step machinery.

Add:

- `run_state.py`
- run ID generation
- `RunStore`
- atomic `state.json` writes
- JSONL event wrapping with run ID/sequence
- event-to-state projector
- tests proving run files do not dirty the worktree

Done when:

- A workflow can be run with a store-backed event sink.
- Legacy CLI still writes no run files unless explicitly enabled.

### PR 2: Failure classification foundation

Add:

- `failures.py`
- `WorkflowFailure`
- `FailureKind`
- supervised classification for high-value terminal failures:
  - initial agent failure
  - review agent failure
  - message/commit failure
  - terminal checks failure
  - terminal merge failure
  - rebase still in progress
  - cleanup failure
  - missing command
  - dirty main worktree

Done when:

- Legacy exit codes/messages stay unchanged.
- Supervised path gets typed failures with step, code, message, diagnostics, and
  context.

### PR 3: Supervised workflow API

Add:

- `PIDFlow.run_supervised()`
- context return on success
- failure escape on classified failures
- tests around step-specific supervised failures

Done when:

- Existing `PIDFlow.run()` behavior is unchanged.
- Supervisor code can invoke the workflow without parsing stdout/stderr.

### PR 4: Deterministic supervisor MVP

Add:

- `policy.py`
- `supervisor.py`
- `AgentRequest`
- bounded recovery loop
- initial/review agent retry with bumped thinking
- cleanup retry/manual guidance
- terminal CI/merge failure handling that respects existing PR-loop recovery

Done when:

- `OrchestratorAgent.start()` can supervise a full fake workflow in tests.
- Recovery budget exhaustion is tested.

### PR 5: `pid-agent` CLI

Add:

- `agent_cli.py`
- `pid-agent` console script in `pyproject.toml`
- structured noninteractive mode
- `--resume`, `--status`, and `--runs` as inspect/status first
- clear summary output

Done when:

- `pid-agent --branch ... --prompt ...` runs.
- Run state path is printed.
- Success/failure summary is clear.

### PR 6: Rich interactive monitoring

Add:

- Rich panels/tables
- event tail
- failure option prompts
- optional merge confirmation behavior

Done when:

- User can monitor active/progressing runs without reading raw JSON.

### PR 7: Optional advisor

Add:

- advisor prompt
- JSON validation
- `--advisor pi`
- fallback to deterministic policy
- tests with fake advisor responses

Done when:

- Advisor can suggest allowed recovery actions.
- Invalid advisor output is safely ignored.

## Test plan

Unit tests:

- `RunStore` path resolution under common git dir
- atomic state writes
- event JSONL wrapping with run ID/sequence
- event-to-state projection
- `WorkflowFailure` serialization/context
- `RecoveryPolicy` decisions for each high-value `FailureKind`
- prompt builders preserving untrusted diagnostic boundaries

Flow tests:

1. Agent starts workflow and succeeds.
2. Agent writes run state and event log.
3. Initial agent fails once, policy retries with bumped thinking, then succeeds.
4. Review agent fails once, policy retries, then succeeds.
5. Existing CI fix path runs inside PR loop; supervisor records attempts.
6. Terminal CI exhaustion becomes classified failure.
7. Existing merge/rebase recovery runs inside PR loop; supervisor records it.
8. Terminal merge exhaustion becomes classified failure.
9. Cleanup fails; agent preserves run state and prints manual guidance.
10. Missing command stops with classified failure.
11. Dirty main worktree stops without creating worktree.
12. Repeated same failure exhausts recovery budget.
13. Existing legacy tests pass unchanged.
14. Extension hooks/replacements/policies still run under supervised mode.

Quality gates:

```sh
mise run lint
mise run test
mise run check
```

## MVP acceptance criteria

MVP is complete when:

1. Existing `pid` command behaves exactly as before.
2. `pid-agent --branch BRANCH --prompt TEXT` launches the current workflow.
3. Agent writes structured run state and events outside the worktree.
4. Agent prints current step/stage, PR URL, attempt, and final result.
5. Agent reacts to at least:
   - initial agent failure with one bumped-thinking retry
   - review agent failure with one bumped-thinking retry
   - terminal CI failure after built-in CI-fix attempts exhaust
   - terminal merge/rebase failure after built-in recovery exhausts
   - cleanup failure with preserved run state and manual guidance
6. Recovery loops have a hard budget.
7. Tests cover success, recovery, failure classification, extension
   compatibility, and budget exhaustion.
8. `mise run check` passes.

## Open questions

1. Should interactive agent mode require confirmation before squash merge, or
   preserve current fully automatic merge behavior?
2. Should `pid-agent --resume` initially be inspect/status only, with explicit
   retry subcommands added later?
3. Should run state default to git common dir, or should XDG/user state be the
   default with common-dir as metadata?
4. How aggressive should automatic retry be for non-agent failures?
5. Should failed run worktrees be retained indefinitely, or should the agent
   offer stale-run cleanup workflows?
6. Should extensions be able to attach custom failure classifiers/recovery
   actions in a future API?

## Recommended first cut

Build deterministic supervision first:

```text
pid-agent --branch feature/add-orchestrator-agent --prompt "..."
```

Do not add optional LLM advisor until run state, failure classification,
`run_supervised()`, and policy-based recovery are stable.
