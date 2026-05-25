# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For the Debian-format release notes (used by the `.deb` package), see
[`packaging/deb/debian/changelog`](packaging/deb/debian/changelog).

## [Unreleased]
### Added
- **Liveness for the monitor itself** so a dead `eth-validator-stats` doesn't go unnoticed.
  - `alerts.daily_heartbeat` (default `false`, auto-enabled by `init` when ntfy is configured) sends one low-priority `MONITOR ALIVE` push per day at `alerts.daily_heartbeat_hour` (default 9 local). The signal is the absence: if your morning ping doesn't show up, you investigate.
  - `alerts.heartbeat_url` POSTs a zero-byte heartbeat after every successful poll. Compatible with healthchecks.io, Better Stack, Cronitor, self-hosted uptime-kuma, or any URL that accepts an unauthenticated POST. We recommend healthchecks.io — free, GitHub-login signup, built-in ntfy integration so alerts route to your existing topic. Setup is documented in [`docs/USAGE.md`](docs/USAGE.md#monitoring-the-monitor).
  - Failures of `heartbeat_url` POSTs are logged at WARNING and never crash the watch loop — best-effort by design.

### Changed
- **`evs status` is now read-only by default.** Renders the latest snapshot from on-disk state instead of polling the beacon node. Use `evs status --refresh` to opt back into the old poll-and-save behavior. Background: when both `evs status` and the systemd `watch` service ran together, they raced on the same state file and could overwrite each other's history. `status` no longer writes, so the race is gone. Cron users were already supposed to use `evs check`; if you cron'd `evs status`, switch to `evs check` (it's the documented cron command and was already exit-code-aware).
- **State file is now auto-shared across callers when a system install is present.** `evs status` (as your user), `sudo evs status`, and the `watch` service all now read/write `/var/lib/eth-validator-stats/state.json` when that directory exists. Previously each user kept its own copy under `~/.local/share/eth-validator-stats/`, so the three views diverged. Falls back to the per-user platformdirs path on pipx/`--user` installs.
- **`evs status` now shows a "last updated: 2m ago" footer** so a dead `watch` service is obvious at a glance. Coarse-grained (s/m/h/d ago) — operators want "is it alive?", not exact seconds.
- **First-run `evs status` (no state yet) prints an onboarding hint** listing three ways to populate state (start the watcher, run `evs check` once, or `evs status --refresh`), instead of an empty table.
- CLI internals migrated from `argparse` to [`typer`](https://typer.tiangolo.com/). All command names, flags, and exit codes preserved; help output is now Rich-styled (colored panels). `--log-level` is now case-insensitive but still rejects invalid values.

## [0.4.0] - 2026-05-25
### Removed
- **Breaking:** legacy TOML config support. The CLI now reads YAML only (`.yml` / `.yaml`). The `--migrate` flag, the interactive "found legacy TOML" prompt on `init`, the `legacy_toml_path` helper, and the `tomllib` import are all gone. Every release since 0.2.0 has shipped YAML as the canonical format, so this should affect no real-world installs. Anything with a `.toml` suffix is now rejected with `unsupported config suffix`.

### Added
- **Validator lifecycle notifications.** Each stage transition on the Beacon API status enum now fires its own ntfy push, deduplicated per-validator per-transition:
  - `ACTIVATED` — `pending_*` → `active_ongoing`. Celebrates the activation queue clearing.
  - `EXIT INITIATED` — `active_ongoing` → `active_exiting`. Confirms a voluntary exit landed.
  - `SLASHED` — anything → `active_slashed` or `exited_slashed`. Sent with ntfy `Priority: urgent` so it bypasses Do-Not-Disturb on most phones.
  - `EXITED` — `active_exiting` → `exited_unslashed`. Clean exit complete.
  - `WITHDRAWAL READY` — exited → `withdrawal_possible`. Funds claimable.
  - All five available via `simulate <event>` (`activated`, `exit-initiated`, `slashed`, `exited`, `withdrawal-ready`) so you can verify push delivery and DND bypass without waiting for a real transition.
- `Notifier.send()` now accepts an optional `priority` keyword that maps to ntfy's [`Priority:` HTTP header](https://docs.ntfy.sh/publish/#message-priority).
- `CHANGELOG.md` (Keep a Changelog 1.1.0), `SECURITY.md`, `CONTRIBUTING.md`, GitHub issue and PR templates, and `.github/dependabot.yml` (weekly, grouped runtime vs dev).
- `ruff` (E/F/I/UP/B/W) and `pytest-cov` as dev dependencies, plus a new `lint` job in `pre-release-check.yml`. Baseline coverage on `main`: 88%.
- CI test matrix expanded to Python 3.11, 3.12, and 3.13. Matching trove classifier added to `pyproject.toml`.
- `scripts/install.sh` now uses `jq` when present, falling back to the existing grep parser on minimal hosts.

### Changed
- `save_state` now uses `tempfile.mkstemp` for a unique tmp filename instead of a fixed `<name>.json.tmp`. An interactive `status` running alongside the `watch` loop could previously clobber each other's tmp file; with unique names, concurrent writers are safe.
- `prompt()` in the init wizard no longer prints an empty `[]` bracket when the default is the empty-string sentinel (used for "Enter is OK, nothing to display").
- `_resolve_existing_config()` returns a plain `Path` (was `tuple[Path, bool]`). The `is_legacy` boolean from the old return type disappeared along with TOML support.
- `pre-release-check.yml` pins `permissions: contents: read` at the workflow root so the `GITHUB_TOKEN` is least-privilege regardless of repo or org defaults. `release.yml` already had explicit permissions everywhere.
- `packaging/linux/README.md` rewritten to drop the obsolete "Phase 1 / Phase 2" framing — that directory is now framed as the "install from a git checkout" path with a migration note for users who want to switch to the distro package.
- `scripts/install.sh` documents its trust model (downloads release artifacts directly from `github.com/Workharu/eth-validator-stats` over HTTPS, no third-party mirror, no bundled key).

### Fixed
- **Spurious missed-attestation alerts on freshly-deposited validators.** `evaluate_alerts` correctly skipped the OFFLINE check for `pending_*` validators but then fell through to the missed-attestations check below — so if the beacon node's liveness endpoint happened to return a pending validator (Lighthouse and Prysm differ on this), zeros accumulated and a spurious `MISSED_ATTESTATIONS` alert would fire for a validator that has no committee assignment yet. Restructured around the actual Beacon API lifecycle: `pending_initialized` and `pending_queued` are now hard-no-alert (not validating yet); `active_ongoing` is the only state subject to missed-attestation checks; `active_exiting` / `active_slashed` / `exited_*` / `withdrawal_*` now correctly surface as OFFLINE so slow exits aren't silent.
- Pre-existing `B904` lint findings: `raise SystemExit(...)` inside `except` clauses now uses `raise ... from e` or `raise ... from None`, so the underlying cause stays in the traceback chain (or is explicitly suppressed) instead of looking like a bug inside the exception handler.

## [0.3.12] - 2026-05-25
### Added
- `eth-validator-stats validators add|list|rm` subcommand group: edit the configured validator list without hand-editing YAML. `add` verifies the validator exists on the beacon node (skip with `--no-verify`); `list --status` shows live state and balance; `rm` accepts an index, pubkey, or label and prompts unless `--yes` is passed. All three preserve file mode and ownership and trigger a systemd restart.

### Changed
- `init` wizard's "next steps" hint now points at `validators add` instead of telling users to hand-edit `config.yml`, and surfaces the `evs` short alias on the same screen.

### Fixed
- `init` no longer silently accepts an empty bearer token when the user answers "yes" to "Does this node need auth?" — it re-prompts (or asks them to confirm "no" after all).
- Three pubkey-only edge cases in the new `validators` module: `list --status` for a pubkey-only entry now resolves live data via the pubkey; `add --no-verify` for a pubkey input echoes the shortened pubkey on success; `rm` for a pubkey-only entry prints the shortened pubkey in the confirmation prompt.

## [0.3.11] - 2026-05-25
### Added
- One-liner installer at `scripts/install.sh` that auto-detects OS (Debian/Ubuntu, Fedora/RHEL, macOS) and arch (amd64/arm64) and pulls the matching `.deb` / `.rpm` / pipx artifact from the latest GitHub Release.

### Changed
- `init` now prints a tip about the `evs` short alias on success.

### Fixed
- `evs status` no longer returns "command not found" after a `.deb` install — `debian/rules` now symlinks both `eth-validator-stats` and `evs` into `/usr/bin/`.
- Arrow keys and other line-editing now work in `init` prompts (added `import readline`).
- The ntfy verification push sent by `init` now carries the project icon, matching every real alert that follows.

## [0.3.10] - 2026-05-25
### Fixed
- `sudo eth-validator-stats init` (without `--system`) on a `.deb`-installed host previously wrote the config to `/root/.config/eth-validator-stats/config.yml` because `HOME=/root` under sudo and platformdirs follows XDG, leaving the systemd unit stuck "inactive (dead) - start condition unmet". `init` now auto-promotes to `--system` when `EUID==0` and the `eth-validator-stats` system user exists, prints a clear note, and starts the service. Set `ETH_VALIDATOR_STATS_CONFIG` to override.

## [0.3.9] - 2026-05-25
### Fixed
- `.rpm` build only: `0.3.8` failed with "error: line 205: second install" because `rpmbuild` matches bare section keywords (`install`, `post`, `postun`, `files`) anywhere in the spec, including inside shell `#` comments and changelog bullet text. Rephrased every offending occurrence. The `.deb` side was unaffected and shipped correctly in `0.3.8`.

## [0.3.8] - 2026-05-25
### Added
- Every ntfy push now carries a branded app icon. Default URL points at `assets/notification-icon.png` in this repo, served via `raw.githubusercontent.com`. Configurable via `alerts.icon_url` — set to `""` to disable, or to your own URL.

## [0.3.7] - 2026-05-25
### Changed
- `init --system` now writes `/etc/<pkg>/config.yml` with mode `0644` instead of `0640`, so any user can run read-only subcommands without sudo or group membership. Validator pubkeys/indices are public on chain, beacon URLs are typically local, and the ntfy topic is unguessable but low-value. Tighten manually to `0640` if you store a `beacon_auth_token` for a hosted provider.

### Fixed
- `postinst` now auto-upgrades existing config files still at the old `0640` default to `0644` on `apt install`. Files at any other mode are left alone.
- Uninstall no longer prints a stack of `dpkg: warning: directory ... not empty so not removed` messages for venv dirs; `postrm` now `rm -rf /opt/<pkg>` on `remove`.

## [0.3.6] - 2026-05-25
### Fixed
- `eth-validator-stats status` (and the other read-only subcommands) no longer crash with a `PermissionError` traceback when invoked as a non-root user against a `.deb`-installed system config. Python 3.11+ propagates `PermissionError` from `Path.exists()` rather than silently returning `False`. The CLI now produces a clear actionable message pointing at `sudo` or `usermod -aG eth-validator-stats $USER`.

### Changed
- `/etc/eth-validator-stats` is now `0755` (was `0750`) so non-group users can see the config file exists. The config file itself stays `0640` — secrets remain group-readable only.

## [0.3.5] - 2026-05-25
### Added
- `eth-validator-stats --version` (and `evs --version`) prints the installed package version via `importlib.metadata`, reflecting the actual installed dist regardless of install method.
- `eth-validator-stats init --system` now starts the systemd service automatically after writing `/etc/<pkg>/config.yml`. Best-effort and non-fatal: silently skips when `systemctl` is unavailable, when the unit isn't installed (e.g. pipx-only), or when start fails.

### Changed
- Internal: uses `systemctl restart` (not `start`) so re-running `init --system --force` after a config edit picks up the new config without a separate restart step.
- `postinst`'s first-install message simplified to a single "Next step: sudo eth-validator-stats init --system". Same for the rpm spec `%post` message and the four README install sections.

## [0.3.4] - 2026-05-24
### Added
- Short CLI alias `evs` (3 chars). Same binary as `eth-validator-stats`.

### Changed
- Config resolver order is now: `$ETH_VALIDATOR_STATS_CONFIG` -> `/etc` -> `~/.config`.
- Substantial README polish for onboarding: pick-your-install table, example `status` output, per-install cron path table, `simulate` subcommand visibility, accurate "what it does NOT do" list, removed stale legacy TOML migration section.

### Fixed
- `eth-validator-stats status` (and `check`, `info`, `simulate`) now find `/etc/eth-validator-stats/config.yml` written by `init --system` or the `.deb` post-install. Previously they only checked `~/.config/eth-validator-stats/config.yml`.
- `postinst` no longer prints the alarming `home dir /var/lib/eth-validator-stats can't be accessed` warning during first install. Config and state directories are now created before `adduser` references the service user's home.
- `postrm` now removes the `eth-validator-stats` system group on purge (Debian's `deluser --system` only removes the user). Without this, `apt purge && apt install` was not a true first-install test.

## [0.3.2] - 2026-05-24
### Added
- `scripts/bump.sh` helper to sync version across `pyproject.toml`, the rpm spec, `debian/changelog`, README install snippets, and `uv.lock`, and to prepend stub changelog entries for a new release.
- CI now runs `bump.sh --verify` on every push (dedicated version-consistency job) and at release time across all release jobs, catching version drift across pinned files before a tag is cut.

### Fixed
- README install-snippet filenames carried `0.3.0` at the `0.3.1` release; corrected in a follow-up commit.

## [0.3.1] - 2026-05-24
### Added
- `eth-validator-stats simulate <event>` subcommand fires a single ntfy push using the exact title/body template real alerts produce. Supports `missed-attestation`, `offline`, `withdrawal`, `proposing-soon`, `proposed`, `missed-proposal`, `blind`, `recovered`.

### Fixed
- Packaging hardening so the `.deb` builds cleanly with debhelper 13: skip `dh_shlibdeps` scanning of the bundled python-build-standalone interpreter (its `libpython3.so` uses `$ORIGIN`-relative RPATH which `dpkg-shlibdeps` cannot resolve), and rewrite buildroot paths in the entire `/opt/<pkg>` tree.

## [0.3.0] - 2026-05-24
### Added
- Self-contained `.deb`: bundles python-build-standalone via uv, drops the runtime dependency on system `python3.11`. `apt install` now works on Ubuntu 24.04+ without the deadsnakes PPA prerequisite.
- amd64 + arm64 packages from a CI matrix.
- `eth-validator-stats install-service` / `uninstall-service` subcommands for pipx-installed users.

## [0.2.1] - 2026-05-24
### Changed
- Docs-only: correct install instructions in README for Ubuntu 24.04 (needs deadsnakes PPA) and Fedora; use `apt install ./path.deb` instead of `dpkg -i` so dependencies auto-resolve.

## [0.2.0] - 2026-05-24
### Added
- Phase 2 release: PyPI + `.deb` + `.rpm` distribution.
- Bundled `python3.11` venv at `/opt/eth-validator-stats/venv`.
- `eth-validator-stats init --system` flag for system-service onboarding.

[0.4.0]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.4.0
[0.3.12]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.12
[0.3.11]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.11
[0.3.10]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.10
[0.3.9]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.9
[0.3.8]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.8
[0.3.7]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.7
[0.3.6]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.6
[0.3.5]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.5
[0.3.4]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.4
[0.3.2]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.2
[0.3.1]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.1
[0.3.0]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.3.0
[0.2.1]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.2.1
[0.2.0]: https://github.com/Workharu/eth-validator-stats/releases/tag/v0.2.0
