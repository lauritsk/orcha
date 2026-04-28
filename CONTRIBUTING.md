# Contributing

Thanks for improving pid. This project uses `mise` for tooling and task
orchestration.

## Setup

1. Install and enable `mise`.
2. Clone the repository.
3. Trust the project configuration:

```sh
mise trust
```

4. Run the full quality gate:

```sh
mise run check
```

## Development workflow

Use `mise` tasks rather than invoking tools directly:

| Task | Purpose |
| --- | --- |
| `mise run lint` | Run hk-managed checks. |
| `mise run fix` | Run hk-managed formatters and fixers. |
| `mise run test` | Run pytest in parallel with coverage reporting and a 95% coverage gate. |
| `mise run build` | Build the Python package. |
| `mise run check` | Run all project checks. |
| `mise run release:bump` | Bump the package version. |
| `mise run release:publish` | Publish a tagged release. |

`mise run release:publish` also publishes the GHCR image. Release CI requires
repository secrets `DHI_USERNAME` and `DHI_PASSWORD` so GoReleaser can pull
Docker Hardened Images from `dhi.io`.

The CLI can be run from a checkout with:

```sh
mise run pid -- [ATTEMPTS] [THINKING] BRANCH PROMPT...
```

## Code standards

- Keep behavior covered by tests in `tests/`.
- Prefer small, focused changes.
- Keep CLI output stable unless tests and docs are updated together.
- Use type hints for new Python code.
- Let `mise run fix` handle formatting before review.

## Commits and PRs

- Use Conventional Commits for commit messages and PR titles.
- Choose branch names that map cleanly to Conventional Commit titles, for
  example `feature/add-docs`, `fix/retry-checks`, or `docs/update-readme`.
- Run `mise run check` before opening a PR.
- Include concise context in the PR description: what changed, why, and how it
  was validated.

## Reporting issues

When filing bugs, include:

- pid version or commit SHA.
- Operating system and shell.
- Command you ran.
- Expected behavior.
- Actual behavior and relevant output.

Report security issues through the process in `SECURITY.md` instead of public issues.
