# pid agent and orchestrator plan

<!-- markdownlint-disable MD013 -->

## Product direction

pid should optimize for two first-class user stories:

1. **Run parallel agents yourself** with `pid agent` / `pid a`.
2. **Delegate agent management** to `pid orchestrator` / `pid o`.

The old direct pid flow should disappear from the primary user experience. Running `pid` with no subcommand should route users to one of these two stories. If the direct workflow engine remains, it should be treated as an internal or advanced implementation detail, not as a separate product path.

pid should be a lightweight orchestration framework around the pi agent, with the same philosophy as pi: simple entry point, terminal-native, configurable, extensible, and understandable without a separate app or complex orchestration system.

## Goals

- Minimize clicks and prompts before users reach the desired flow.
- Keep `pid agent` as the reusable worker primitive for both humans and the orchestrator.
- Avoid duplicate workflow engines.
- Preserve branch isolation, worktree isolation, PR lifecycle automation, retries, checks, and cleanup.
- Preserve configurability through config and extensions.
- Make orchestration approachable without requiring a GUI, daemon, external service, or new mental model.

## Non-goals

- No visual DAG editor.
- No separate desktop or web app.
- No new agent execution protocol unless existing pid agent flow cannot support a required behavior.
- No separate scheduler service.
- No database beyond existing durable run state unless strictly required later.
- No complex orchestration framework hidden behind the CLI.

## User story 1: User-managed parallel agents

**As a developer,** I want to start multiple supervised agents quickly, each with its own branch and task prompt, **so that** I can act as the orchestrator while pid handles isolation, PR lifecycle, status, failures, and cleanup.

### Entry points

```sh
pid agent
pid a
```

Running `pid` without arguments should offer this as one of the two main choices.

### Intended user

A user who wants several agents working in parallel, but still wants to personally decide task boundaries, prompts, branches, sequencing, and follow-ups.

### Flow

1. User runs `pid agent` or chooses **Run agents myself** from `pid`.
2. pid prompts for missing startup values:
   - branch name
   - task prompt
   - optional thinking level, default from config
   - optional attempt count, default from config
3. pid starts one supervised agent run.
4. User can start more agents the same way.
5. Each run gets its own worktree, branch, durable state, logs, and PR lifecycle.
6. User monitors work with:

   ```sh
   pid agent runs
   pid agent status <run-id>
   ```

7. User sends follow-ups with:

   ```sh
   pid agent follow-up <run-id> --message "Use the new API name everywhere"
   ```

8. pid applies follow-ups at safe checkpoints and continues normal workflow handling.

### Required behavior

- `pid agent` must be the fastest path for starting supervised parallel work.
- Missing values are prompted only when stdin is a TTY.
- Non-interactive usage remains available through flags.
- Each run has durable state:
  - run ID
  - branch
  - prompt
  - current step
  - status
  - PR URL, when available
  - failure details, when available
  - queued follow-ups
- Multiple agents can run independently without shared mutable worktrees.
- User can inspect active and historical runs.
- User can queue follow-ups safely.
- Existing pid workflow remains the execution engine.

### Acceptance criteria

- `pid agent` can start a supervised run from prompts alone.
- `pid a` works as a short alias.
- `pid` no longer presents the old direct flow as the recommended path.
- User can start two or more agent runs in parallel without extra setup.
- User can see status for all runs from one command.
- User can send a follow-up to one run without affecting others.
- Existing config and extension hooks continue to apply.

## User story 2: Delegated orchestrator

**As a developer,** I want to describe my goal once and talk to an orchestrator, **so that** pid can plan the work, launch subagents, manage dependencies, handle follow-ups, and report completion without me micromanaging every agent.

### Entry points

```sh
pid orchestrator
pid o
```

Running `pid` without arguments should offer this as one of the two main choices.

### Intended user

A user who wants an agent manager, not a pile of individual agent sessions. They want to describe the desired outcome, answer clarifying questions, approve a plan, then let pid coordinate child agents.

### Flow

1. User runs `pid o` or chooses **Let orchestrator manage agents** from `pid`.
2. pid asks for minimal startup information:
   - goal
   - constraints or scope notes
   - branch prefix, default generated from goal
   - validation expectations, default from config
   - max parallel agents, default from config
3. pid launches an interactive orchestrator session.
4. Orchestrator asks clarifying questions until the goal is sufficiently specified.
5. Orchestrator creates a plan containing:
   - tasks
   - dependencies
   - branch names
   - child prompts
   - acceptance criteria
   - validation commands
   - risk notes
6. User approves or edits the plan.
7. Orchestrator launches child work through the existing `pid agent` flow.
8. Dependency-free tasks run in parallel, up to the configured parallelism limit.
9. Orchestrator monitors child status and routes follow-ups.
10. Orchestrator asks the user only for human decisions, such as:
    - ambiguous product choices
    - risky or destructive changes
    - scope changes
    - repeated failures
    - merge conflicts that need judgment
11. When complete, orchestrator reports:
    - completed tasks
    - child run IDs
    - PRs and merge status
    - failures or skipped work
    - validation summary
    - recommended next steps

### Required behavior

- Orchestrator must use `pid agent` as its worker primitive.
- Orchestrator plan must be persisted and inspectable.
- Child agents must use the same branch/worktree/PR safety as normal supervised agent runs.
- Orchestrator must respect dependencies and max parallelism.
- Global follow-ups can be recorded on the orchestrator run.
- Targeted follow-ups can be routed to one child or all children.
- Orchestrator should not require a separate app, daemon, or external orchestration service.
- Orchestrator should keep code simple and avoid duplicating pid agent logic.

### Acceptance criteria

- `pid orchestrator` can start from prompts alone.
- `pid o` works as a short alias.
- Orchestrator can produce a persisted plan before launching children.
- User can approve or edit the plan before execution.
- Orchestrator launches child runs via the same mechanisms as `pid agent`.
- Orchestrator can show status across all child runs.
- Orchestrator can route follow-ups globally, to one child, or to all children.
- Orchestrator stops and asks the user before unsafe or ambiguous actions.

## Top-level CLI behavior

Running `pid` should not start the old direct flow. It should guide the user to one of the two main stories:

```text
What do you want pid to do?

1. Run agents myself        pid agent
2. Let orchestrator manage  pid orchestrator
```

In non-interactive mode, `pid` without a subcommand should print a short help message that emphasizes these two paths.

The old direct workflow may remain available as an explicit advanced command if needed for compatibility:

```sh
pid run <branch> <prompt>
```

But documentation, quick start, and help text should treat it as an implementation detail or advanced escape hatch.

## Minimal shared feature set

These features support both stories and should be reused rather than duplicated:

- durable run state
- run IDs
- status listing
- follow-up queue
- branch and worktree isolation
- configurable agent command
- configurable thinking levels and attempts
- existing PR/check/retry/merge workflow
- session logs
- extension hooks
- prompt templates

## Implementation principles

- Build orchestration on top of `pid agent`, not beside it.
- Make `pid agent` useful to both human users and orchestrator internals.
- Prefer config defaults over extra prompts.
- Ask only for information needed to safely start.
- Persist enough state for recovery and inspection.
- Keep CLI commands composable for scripts.
- Keep advanced configurability available without making first run harder.

## Safety rules

- Never run two agents in the same worktree.
- Never hide destructive actions behind automatic orchestration.
- Confirm ambiguous high-impact choices with the user.
- Keep all child prompts and plans inspectable.
- Make failure state explicit and recoverable.
- Apply follow-ups only at safe checkpoints.
- Preserve clean-main-worktree checks for workflows that need them.

## Suggested command surface

```sh
pid
pid agent
pid a
pid agent follow-up <run-id> --message <text>
pid agent status <run-id>
pid agent runs
pid orchestrator
pid o
pid orchestrator follow-up <run-id> --message <text> [--target <child>|--all]
pid orchestrator status <run-id>
pid orchestrator runs
pid run <branch> <prompt>   # advanced/internal compatibility path
```

## Success metric

A new user should understand pid in one sentence:

> Use `pid agent` when you want to manage parallel agents yourself; use `pid o` when you want an orchestrator to manage them for you.
