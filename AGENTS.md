# Agent Instructions

Use GitHub Issues for all task tracking. Check existing issues before starting work.

## GitHub

```bash
gh issue list
gh issue view <number>
gh issue edit <number> --add-assignee @me
gh issue close <number>
git push
```

Rules:

- Do not use TodoWrite, TaskCreate, markdown TODO lists, or MEMORY.md for task tracking.
- Create GitHub issues for follow-up work.
- Update/close relevant issues before ending work.

## Shell Safety

Use non-interactive flags so commands never hang:

```bash
cp -f source dest
mv -f source dest
rm -f file
rm -rf directory
cp -rf source dest
```

Other prompt-prone commands:

- `scp`: use `-o BatchMode=yes`
- `ssh`: use `-o BatchMode=yes`
- `apt-get`: use `-y`
- `brew`: use `HOMEBREW_NO_AUTO_UPDATE=1`

## Session Completion

Before ending a work session:

1. File GitHub issues for remaining work.
2. Run quality gates if code changed.
3. Update/close relevant issues.
4. Push all committed work:

   ```bash
   git pull --rebase
   git push
   git status
   ```

5. Verify branch is up to date with origin.
6. Hand off useful context.

Work is not complete until `git push` succeeds.
