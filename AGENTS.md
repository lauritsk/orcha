# Agent Instructions

This project uses **GitHub Issues** for issue tracking. Review the repo’s open
issues and contributing docs for workflow context.

## Quick Reference

```bash
# View open issues for the repo
gh issue list

# View issue details
gh issue view <number>

# Assign yourself to an issue
gh issue edit <number> --add-assignee @me

# Close an issue
gh issue close <number>

# Push code changes
git push
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on
confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i`
(interactive) mode on some systems, causing the agent to hang indefinitely
waiting for y/n input.

**Use these forms instead:**

```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**

- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

## GitHub Issue Tracker

This project uses **GitHub Issues** for issue tracking. Review open issues and
repo documentation before starting work.

### Quick Reference

```bash
gh issue list
gh issue view <number>
gh issue edit <number> --add-assignee @me
gh issue close <number>
```

### Rules

- Use **GitHub Issues** for ALL task tracking — do NOT use TodoWrite,
  TaskCreate, or markdown TODO lists
- Check existing issues before starting work to avoid duplicates
- Create new GitHub issues for follow-up work instead of local notes or
  MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT
complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create GitHub issues for anything that
   needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished issues, update or comment on
   in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:

   ```bash
   git pull --rebase
   git push
   git status  # MUST show branch is up to date with origin
   ```

5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
