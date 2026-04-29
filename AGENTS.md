# Agent Instructions

Use GitHub Issues for all task tracking.

## GitHub

```bash
gh issue list
gh issue view <number>
gh issue edit <number> --add-assignee @me
gh issue close <number>
```

Rules:

- Do not use TodoWrite, TaskCreate, markdown TODO lists, or MEMORY.md for task tracking.

## Session Completion

Before ending a work session:

1. Check existing issues before starting work.
2. Run quality gates if code changed.
3. File, update, or close relevant issues.
4. Push all committed work:

   ```bash
   git pull --rebase
   git push
   git status
   ```

5. Verify branch is up to date with origin.
6. Hand off useful context.
