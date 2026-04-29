# Orchestrator Agent Implementation Plan

Status: MVP plus child follow-up foundation implemented
Scope: design and implementation plan

## Goal

Add a higher-level `pid agent` mode above the current workflow. The agent owns
run state, observes structured progress, classifies terminal failures, and takes
bounded recovery actions.

MVP note: the first implementation ships deterministic policy, typed failure
classification, durable run state, and `start`/`runs`/`status` commands. The
follow-up slice adds durable child inboxes, `pid agent follow-up`, orchestrator
intake questions, approved plan-file launch, parallel child process launch, and
orchestrator-to-child follow-up routing. `resume` currently reports saved state
and exits with guidance until context reconstruction is implemented.

The project has no external users yet. Prefer the cleanest product and API shape
over preserving early command forms.

## Product shape

```sh
# Supervise one pid workflow.
pid agent start --branch feature/add-thing --prompt "add thing"
pid agent follow-up <run-id> --message "new constraint"
pid agent resume <run-id>
pid agent status <run-id>
pid agent runs

# Orchestrate a larger effort across many pid child sessions.
pid orchestrator start --goal "ship the larger change"
pid orchestrator start --goal "ship the larger change" --plan-file plan.json
pid orchestrator follow-up <run-id> --target api --message "new constraint"
pid orchestrator status <run-id>
pid orchestrator runs
```

Keep the main workflow available through clear commands:

```sh
pid run --branch feature/add-thing --prompt "add thing"
pid session --branch feature/explore-api
```

Implementation can update the current positional parser as part of this work.
Do not add parallel command names just to preserve early experiments.

## Current foundations

- `src/pid/workflow.py` has `PIDFlow` and step-based execution.
- `src/pid/context.py` has `WorkflowContext` and `PRLoopState`.
- `src/pid/events.py` has workflow events and sink implementations.
- `PIDFlow` accepts an optional event sink.
- Workflow steps, hooks, policies, and service replacements are supplied through
  `ExtensionRegistry`.
- The PR loop already has fine-grained substeps.
- Existing recovery handles CI failures, base refresh, merge retries, rebase
  conflict fixing, force-with-lease handling, and cleanup after merge.

## Gaps

- Resume cannot reconstruct context yet.
- Dependency waves beyond the first ready wave need a monitor/resume loop.
- Orchestrator plan generation currently accepts an approved JSON plan file;
  model-assisted planning and approval UI can build on the same state shape.
- Live stdin injection into already-running agent CLIs is not available unless a
  configured agent exposes a control channel; otherwise follow-ups queue until
  safe checkpoints.

## Design principles

1. **Bounded autonomy**
   - The orchestrator chooses from typed actions.
   - It never executes arbitrary shell commands from model output.
2. **Reuse workflow logic**
   - Keep CI fixing, merge recovery, base refresh, push safety, and cleanup in
     `PIDFlow` unless orchestration needs cross-run decisions.
3. **Observable workflow**
   - Extend the existing event stream with run ID and sequence metadata.
4. **Typed failures**
   - Convert high-value terminal failures into `WorkflowFailure` values with
     kind, step, exit code, message, diagnostics, and context.
5. **Persistent state outside the worktree**
   - Store run data under the repository common git dir by default:
     - `<common-git-dir>/pid/runs/<run-id>/state.json`
     - `<common-git-dir>/pid/runs/<run-id>/events.jsonl`
     - `<common-git-dir>/pid/runs/<run-id>/diagnostics/`
6. **Clean API while pre-user**
   - Prefer direct command names and typed options.
   - Remove early command forms instead of carrying extra command names.

## Supervised workflow API

Add a supervised entry path:

```python
class PIDFlow:
    def run(self, argv: list[str]) -> int:
        ...

    def run_supervised(self, argv: list[str]) -> WorkflowContext:
        ...
```

Requirements:

- `run_supervised()` uses `WorkflowContext`, `WorkflowStep`, hooks, policies,
  service replacements, and event sinks.
- On success it returns final `WorkflowContext`.
- On failure it raises `WorkflowFailure`.
- Extension registration/execution errors remain explicit diagnostics.

## Failure model

Add `src/pid/failures.py`:

```python
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FailureKind(StrEnum):
    INVALID_ARGS = "invalid_args"
    MISSING_COMMAND = "missing_command"
    DIRTY_MAIN_WORKTREE = "dirty_main_worktree"
    BRANCH_EXISTS = "branch_exists"
    WORKTREE_EXISTS = "worktree_exists"
    MISE_TRUST_FAILED = "mise_trust_failed"
    INITIAL_AGENT_FAILED = "initial_agent_failed"
    REVIEW_AGENT_FAILED = "review_agent_failed"
    NO_CHANGES = "no_changes"
    MESSAGE_FAILED = "message_failed"
    COMMIT_FAILED = "commit_failed"
    PUSH_FAILED = "push_failed"
    PR_FAILED = "pr_failed"
    CHECKS_FAILED = "checks_failed"
    MERGE_FAILED = "merge_failed"
    REBASE_IN_PROGRESS = "rebase_in_progress"
    CLEANUP_FAILED = "cleanup_failed"
    EXTENSION_FAILED = "extension_failed"


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

Prefer direct classification at important failure sites first, then expand step
boundary classification where useful.

## Recovery actions

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

MVP remains conservative. Only retry known idempotent steps or purpose-built
recovery steps.

## Initial failure matrix

- Invalid args: ask for corrected values in interactive mode; otherwise exit 2.
- Missing configured agent, forge, or verifier command: stop with install
  guidance.
- Dirty main worktree: stop and ask user to clean it.
- Local/remote branch exists: ask for a different branch.
- Worktree path exists: ask for cleanup or a different branch.
- `mise trust` fails: stop unless explicitly approved.
- Initial agent fails: retry once with bumped thinking and diagnostics, then ask
  or abort.
- Review agent fails: retry once with bumped thinking, then ask or abort.
- No changes after agent/review: ask whether to mark done.
- Commit/message failure: run a local-worktree fix prompt only when diagnostics
  are safe and fixable.
- Push failure: retry transient failures; ask on unexpected remote changes.
- PR create/edit failure: retry transient failures; otherwise stop.
- Checks failure: reuse existing PR-loop CI fix behavior until attempts exhaust.
- Merge failure: reuse existing base-refresh/rebase path; classify terminal
  failures.
- Cleanup failure: record run as merged with cleanup pending; allow retry.

## Run store

Add `src/pid/run_state.py`:

- Generate monotonic, sortable run IDs.
- Create per-run directories under the common git dir with private permissions.
- Append wrapped events to `events.jsonl`.
- Atomically write `state.json` with private file permissions.
- Project workflow events into user-facing run state.
- Store redacted diagnostics separately.
- Store durable `followups.jsonl` inbox/ack records per run.

State should include:

- run ID
- parent orchestrator run ID and plan item ID when this is a child run
- status
- branch
- prompt summary
- attempts and thinking
- current step
- PR URL
- worktree path
- started/updated timestamps
- final result
- last failure
- pending recovery action
- follow-up count, applied cursor, and last applied follow-up ID

Orchestrator records also include:

- goal, intake questions, and approved plan
- child session records keyed by plan item ID
- branch names, thinking levels, prompts, and dependency status per child
- follow-up messages, routing decisions, and delivery acknowledgements

## `pid agent` CLI

Add command group:

```sh
pid agent start --branch BRANCH --prompt TEXT [--attempts N] [--thinking LEVEL]
pid agent resume RUN_ID
pid agent status RUN_ID
pid agent runs
```

Useful options:

```sh
pid agent start --non-interactive
pid agent start --yes
pid agent start --advisor policy|pi
pid agent start --confirm-merge
```

MVP should ship deterministic policy only. Add an advisor after state,
classification, and policy tests are solid.

## Orchestrator agent

The orchestrator handles work larger than one pid branch. Its main job is to turn
an under-specified goal into a reviewed plan, then execute independent plan parts
as child `pid agent` runs.

### Intake / grilling

Before launching children, the orchestrator records the goal and asks about:

- desired outcome, non-goals, priority, deadlines, and success criteria
- repo areas allowed to change and areas explicitly out of scope
- UX/API/backwards-compatibility constraints
- migration, data, config, security, privacy, performance, reliability, and
  accessibility concerns
- test strategy, quality gates, docs, release notes, and rollout plan
- branch naming prefix and PR granularity preferences
- whether child sessions may merge independently or must stop at PRs
- conflicts/dependencies between subtasks

Current implementation prints and persists these questions when no approved plan
file is supplied. Non-interactive callers can pass `--non-interactive` to get a
non-zero result with the question list instead of launching children.

### Planning output

Approved plan JSON contains `items` plus optional global `constraints`. Each item
can define:

- stable item ID and title
- scope, files/areas, acceptance criteria, and validation commands
- dependencies and whether it can run in the first parallel wave
- suggested branch name using a deterministic slug
- child prompt with full context and narrow boundaries
- initial thinking level selected from complexity/risk
- expected outputs: commit, PR, docs, test result, or investigation report

When branch, thinking, or prompt are missing, pid derives them deterministically
from branch prefix, item ID/title, risk keywords, global goal, constraints,
dependencies, acceptance criteria, and validation commands.

### Parallel child launch

The orchestrator creates planned child run states, then launches dependency-free
items as parallel `pid agent start` subprocesses up to the configured concurrency
limit. Child runs receive parent run ID, plan item ID, selected branch, selected
thinking level, and scoped prompt. Items with dependencies remain blocked for a
future monitor/resume wave.

### Follow-ups and steering

Follow-up routing is durable and idempotent:

- Users can queue a direct child follow-up with `pid agent follow-up RUN_ID`.
- Users can record an orchestrator follow-up with `pid orchestrator follow-up`.
- `--target ITEM_OR_CHILD_RUN_ID` routes the message to one child inbox.
- `--all` routes the message to every child inbox.
- Running children poll their inbox at safe checkpoints before workflow steps
  and while waiting for checks.
- Applied messages are acknowledged in `followups.jsonl` and reflected in
  `state.json` cursors.

Supported message types are `clarify`, `scope_change`, `pause`, `resume`,
`abort`, `rerun`, `merge_policy`, and `status_request`. Clarifications and scope
changes are appended to future agent prompts with safety framing. `pause` and
`abort` stop at the next safe checkpoint and preserve run state.

## Safety

1. Treat CI, merge, command, PR, follow-up, child output, and extension text as
   untrusted.
2. Keep diagnostic wrappers in prompts.
3. Redact likely secrets before writing diagnostics.
4. Never execute advisor-proposed shell commands.
5. Require explicit approval for destructive actions.
6. Prefer explicit merge confirmation in agent mode.
7. Require confirmation before an orchestrator follow-up expands scope, changes
   merge policy, aborts children, or rewrites an approved plan.

## Roadmap

### PR 1: Run store and state projection

- Add `run_state.py`.
- Add run ID generation.
- Add `RunStore` with atomic state writes.
- Wrap events with run ID and sequence.
- Project events to state.
- Test that run files do not dirty the worktree.

### PR 2: Failure classification

- Add `failures.py`.
- Add `WorkflowFailure` and `FailureKind`.
- Classify important terminal failures.
- Include step, code, message, diagnostics, and context.

### PR 3: Supervised workflow API

- Add `PIDFlow.run_supervised()`.
- Return context on success.
- Let typed failures escape.
- Test step-specific supervised failures.

### PR 4: Deterministic supervisor

- Add `policy.py`.
- Add supervisor loop.
- Retry only safe known actions.
- Persist state before and after each action.

### PR 5: `pid agent` CLI

- Add `pid agent` commands.
- Add run listing/status/resume.
- Add noninteractive and approval options.

### PR 6: Command streaming and diagnostics

- Add command lifecycle events.
- Redact diagnostics before persistence.
- Preserve full local session logs where appropriate.

### PR 7: Follow-up inbox for supervised runs

- Add durable per-run follow-up inboxes.
- Add `pid agent follow-up RUN_ID --message TEXT`.
- Apply queued follow-ups at safe checkpoints.
- Persist applied cursors and acknowledgements.
- Test direct child follow-up behavior.

### PR 8: Orchestrator intake and plan approval

- Add `pid orchestrator start --goal TEXT`.
- Persist intake questions and approved structured plan.
- Support non-interactive abort with unanswered question list.

### PR 9: Parallel child session launcher

- Launch approved plan items as child `pid agent` runs.
- Select branch names, prompts, thinking levels, and dependency waves.
- Add concurrency limit and collision checks.
- Surface aggregate status.

### PR 10: Orchestrator follow-up routing

- Add `pid orchestrator follow-up RUN_ID --message TEXT`.
- Route global and targeted follow-ups to child inboxes.
- Confirm destructive/scope-expanding changes.
- Track delivery and child acknowledgements.

### PR 11: Integration and aggregate completion

- Reconcile child outputs, PR URLs, validation, conflicts, and blocked items.
- Launch dependent integration children when approved.
- Produce final handoff summary.

### PR 12: Optional advisor

- Add JSON-only advisor interface.
- Validate against an allow-list schema.
- Fall back to deterministic policy on invalid output.

## Acceptance criteria

1. `pid agent start --branch BRANCH --prompt TEXT` launches the workflow.
2. Each run gets durable `state.json` and `events.jsonl`.
3. `pid agent status RUN_ID` shows current step, status, PR URL, and failure.
4. `pid agent runs` lists recent runs.
5. `pid agent follow-up RUN_ID --message TEXT` queues and applies a child
   follow-up at a safe checkpoint.
6. `pid orchestrator start --goal TEXT` asks requirement questions before any
   child launch and persists an approved plan.
7. The orchestrator launches independent child runs in parallel with selected
   branch names, thinking levels, and scoped prompts.
8. `pid orchestrator follow-up RUN_ID --message TEXT` can route requirement
   changes to running or future child sessions with durable acknowledgements.
9. Typed failures drive deterministic actions.
10. Unsafe or ambiguous failures ask the user or abort cleanly.
11. No run state is written into the worktree by default.
12. Quality gate: `mise run check`.
