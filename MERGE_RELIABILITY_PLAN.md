# Merge Reliability Improvement Plan

Status: draft thoughts  
Scope: reduce chance that pid reaches final PR merge, discovers default branch
moved, and has to recover late.

## Short answer

Yes, concern is valid.

Checking whether `main`/default branch moved earlier can reduce late merge
failures when many agents work at once. But blindly pulling/rebasing after every
detected update can also waste time, create conflict churn, confuse the coding
agent, and drift away from original task.

Best path: add bounded, deterministic base-refresh checkpoints. Do not let the
agent freely chase every new main update.

## Current behavior

pid already does useful recovery:

1. Starts from updated default branch.
2. Builds feature in isolated worktree.
3. Opens/updates PR.
4. Waits for checks.
5. Tries squash merge.
6. If squash merge fails and the forge does not already report it merged,
   fetches/rebases, asks agent only if conflicts need help, then retries
   without consuming normal attempts.

Weak spot: branch can become stale long before merge. CI may pass against old
base, then final merge fails after a long wait.

## Goal

Increase first-try merge probability without making agents loop forever or lose
original intent.

## Non-goals

- Do not continuously rebase forever.
- Do not ask the agent to reinterpret the original task after every base update.
- Do not consume normal agent attempts for clean mechanical rebases.
- Do not hide unresolved conflicts or changed assumptions.

## Recommended idea: bounded freshness checkpoints

Add a default-branch freshness check at safe workflow points:

1. Before review pass.
2. Before commit/message generation.
3. Before pushing/updating PR.
4. After CI passes, immediately before merge.

Each checkpoint:

1. `git fetch origin <default-branch>`.
2. Compare current branch base with `origin/<default-branch>`.
3. If unchanged, continue.
4. If changed, run one deterministic rebase attempt.
5. If rebase is clean, continue without agent.
6. If conflict occurs, ask agent with narrow conflict-resolution prompt.
7. If too many freshness rebases happen, stop or ask user.

## Bounds to avoid infinite loops

Use hard limits:

- `max_base_refreshes_per_run`: maybe 3.
- `max_base_refreshes_per_stage`: maybe 1.
- `max_consecutive_moved_base_retries`: current merge retry limit can remain.
- If default branch moves again after limit, leave PR open with clear message.

This keeps pid from chasing a busy repo forever.

## Preserve original idea

Every base-refresh/conflict prompt should include guardrails:

- Original user prompt.
- Current PR title/body.
- Current diff summary.
- Exact conflict/check failure context.
- Instruction: resolve integration only; do not expand scope.
- Instruction: preserve existing feature behavior unless conflict requires
  adaptation.
- Instruction: stop and explain if new main changes invalidate original
  approach.

Also log every base refresh in session log so user can see what happened.

## Rebase timing tradeoffs

### Earlier rebase before review

Pros:

- Review sees latest main.
- Catches conflicts before commit/PR.

Cons:

- May disturb uncommitted agent work.
- More likely to need conflict help before code is finalized.

Recommendation: only if working tree has changes committed or pid can safely
stash/reapply. Otherwise prefer later checkpoint.

### Rebase before commit/message

Pros:

- Commit message describes final latest-base diff.
- Avoids generating metadata twice.

Cons:

- Conflict here delays PR creation.

Recommendation: good checkpoint.

### Rebase before pushing/PR update

Pros:

- PR starts fresh.
- CI tests closer to merge target.

Cons:

- If main changes during CI wait, still stale later.

Recommendation: good checkpoint.

### Rebase after CI passes, before merge

Pros:

- Highest impact for first merge attempt.
- Cheap if clean.

Cons:

- Rebase after CI technically invalidates CI result unless branch protection
  reruns checks or merge queue handles it.

Recommendation: do it only if checks will rerun and be waited on again, or if
forge/branch protection guarantees merge uses latest tested base. Otherwise
prefer merge queue/auto-merge.

## Important CI caveat

A last-second rebase after green CI can make green status stale. Safer sequence:

1. CI passes.
2. Check base freshness.
3. If stale, rebase and push.
4. Wait for checks again.
5. Merge.

This may add time, but avoids merging code that was not tested on refreshed
base.

## Implementation shape

Add a small deterministic helper, not agent-driven control flow:

```text
refresh_base_if_needed(stage, allow_agent_conflict_fix=True) -> result
```

Possible result values:

- `unchanged`
- `rebased_cleanly`
- `rebased_with_agent_fix`
- `limit_reached`
- `conflict_unresolved`

The helper should:

1. Fetch default branch.
2. Detect whether current branch already contains latest default branch.
3. Rebase if needed.
4. On conflict, call existing rebase-fix prompt with stronger scope limits.
5. Commit/continue as needed.
6. Mark PR push as force-with-lease required.
7. Update commit/PR message if diff changed.

## Config proposal

Add workflow config:

```toml
[workflow]
base_refresh_enabled = true
base_refresh_stages = ["before_pr", "after_checks"]
base_refresh_limit = 3
base_refresh_agent_conflict_fix = true
```

Default should be conservative:

```toml
base_refresh_stages = ["before_pr"]
```

Then users in high-concurrency repos can opt into:

```toml
base_refresh_stages = ["before_message", "before_pr", "after_checks"]
```

## Other ideas that may be better

### 1. Use GitHub merge queue

If repo supports merge queue, prefer it. It exists exactly for this problem:
many PRs, moving base, reliable final integration testing.

pid would submit PR to queue instead of trying direct squash merge. This may
reduce custom rebase logic.

### 2. Enable auto-merge after green checks

pid can open PR, enable auto-merge, and let forge merge when branch protection
is satisfied. Less direct control, but less late-stage race.

### 3. Smaller PRs / shorter CI window

Large PRs and slow checks create stale-base risk. Improving test speed and
encouraging smaller agent tasks may help more than smarter rebasing.

### 4. Conflict-risk detection before work starts

If many active PRs touch same files, pid could warn early:

- list files changed by open PRs targeting default branch
- compare against files this run changed after initial agent pass
- warn or ask before continuing when overlap is high

This avoids late surprise but needs forge API calls and can be noisy.

### 5. Serialized merge bot

Let many agents create PRs, but one deterministic merge worker handles merge
order. Worker rebases one PR at a time, waits for checks, merges, then moves to
next. This gives strong reliability for busy repos.

## Recommendation

Do consider this idea, but implement narrowly.

Phase 1:

- Add `before_pr` freshness checkpoint.
- Clean rebase only.
- If conflict, use existing rebase-fix flow.
- Hard limit refreshes.
- Regenerate commit/PR message after rebase changes.
- Add tests around moved base and no infinite retry.

Phase 2:

- Add optional `after_checks` checkpoint that reruns/waits checks after any
  rebase.
- Keep off by default unless behavior is clearly safe.

Phase 3:

- Investigate merge queue / auto-merge as possibly better high-concurrency
  solution.

## Main risk

Too much rebasing can turn pid from feature finisher into integration-chasing
loop. Limit count, keep prompts narrow, and stop with PR open when repo is
moving faster than pid can safely integrate.
