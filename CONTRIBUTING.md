# Contributing to eth-validator-stats

Thanks for the interest. This is a small project, so the contribution loop is short.

## Dev setup

`uv` is required: <https://docs.astral.sh/uv/getting-started/installation/>

```sh
uv sync
uv run pytest -q
```

That's the full inner loop. No other system dependencies.

## Version bumps

A new release touches several pinned files (`pyproject.toml`, the rpm spec,
`packaging/deb/debian/changelog`, README install snippets, `uv.lock`). Use the
helper rather than editing them by hand:

```sh
scripts/bump.sh 0.3.13          # bumps every file + prepends stub changelog entries
scripts/bump.sh --verify        # confirms every pinned file agrees on the version
```

CI runs `bump.sh --verify` on every push, so drift gets caught before a tag is cut.

## Compatibility / client matrix

Tested-against beacon clients (Lighthouse, Nimbus, Teku, Prysmlodestar, ...) and
versions are tracked in [`COMPATIBILITY.md`](COMPATIBILITY.md). That file has a
"help wanted" ask: if you run against a client or version we don't have a row
for, run `eth-validator-stats info` and send the output as a PR.

## Commit messages

This repo uses Conventional Commits. The scope is whichever part of the project
the change touches — typical scopes: `cli`, `config`, `release`, `packaging`,
`docs`, `tests`.

```
feat(cli): add `validators rm` subcommand
fix(config): walk /etc -> ~/.config when discovering an existing config
chore(release): bump to 0.3.13
docs(readme): clarify pick-your-install table
```

`git log --oneline` has plenty of examples to copy from.

## Pull requests

Use the PR template (it pops up automatically). The short version:

- Tests added/updated.
- `uv run pytest -q` passes locally.
- If you touched a version-pinned file, run `scripts/bump.sh --verify`.
- Update `CHANGELOG.md` (and the deb/rpm changelogs if the change is user-visible).
- Update `README.md` if you changed user-visible behavior or commands.

## Where to ask

- **Questions / discussion**: GitHub Discussions.
- **Bugs / feature requests**: GitHub Issues (templates available).
- **Security issues**: see [`SECURITY.md`](SECURITY.md) — please don't file in public issues.
