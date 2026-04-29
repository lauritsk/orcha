# Agent Instructions

Use GitHub Issues for task tracking.

## GitHub

```bash
gh issue list
gh issue view <number>
gh issue create \
  --template agent_task.md \
  --title "type(scope): concise outcome"
gh issue create \
  --template bug_report.md \
  --title "fix(scope): concise bug summary"
gh issue create \
  --template feature_request.md \
  --title "feat(scope): concise feature summary"
gh issue edit <number> --add-assignee @me
gh issue close <number>
```

## Templates

- Agent work: `.github/ISSUE_TEMPLATE/agent_task.md`
- Bugs: `.github/ISSUE_TEMPLATE/bug_report.md`
- Features: `.github/ISSUE_TEMPLATE/feature_request.md`
- PRs: `.github/pull_request_template.md`
- Security: follow `SECURITY.md`; no public issue.

## Standards

- Issue and PR titles use Conventional Commits.
- Record status, decisions, scope changes, validation, and handoff in issue.
- Use `mise run lint`, `mise run fix`, or `mise run check` for quality gates.

## Session Completion

1. Check existing issues before work.
2. Update or close relevant issue.
3. Run quality gate if code changed.
4. Push committed work:

   ```bash
   git pull --rebase
   git push
   git status
   ```

5. Verify branch is up to date with origin.
6. Hand off useful context.
