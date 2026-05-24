# eth-validator-stats

[![CI](https://github.com/Workharu/eth-validator-stats/actions/workflows/pre-release-check.yml/badge.svg?branch=main)](https://github.com/Workharu/eth-validator-stats/actions/workflows/pre-release-check.yml)
[![PyPI](https://img.shields.io/pypi/v/eth-validator-stats.svg)](https://pypi.org/project/eth-validator-stats/)
[![Python versions](https://img.shields.io/pypi/pyversions/eth-validator-stats.svg)](https://pypi.org/project/eth-validator-stats/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/Workharu/eth-validator-stats?include_prereleases&sort=semver)](https://github.com/Workharu/eth-validator-stats/releases/latest)

A tiny self-hosted CLI for Ethereum validator stats. Talks to your own beacon node (Prysm, Lighthouse, etc.) over the standard Ethereum Beacon API. Built as a minimal replacement for the now-paid features of the beaconcha.in mobile app.

**v1 surface:**

```
eth-validator-stats init                  # interactive wizard: detect node, set up ntfy
eth-validator-stats status                # rich table snapshot
eth-validator-stats check [--missed N]    # cron mode: prints offenders, exits 2 if any
eth-validator-stats info                  # probe beacon node: client/version + endpoint support
```

Works with any client that implements the standard Ethereum Beacon API — Prysm, Lighthouse, Teku, Nimbus, Lodestar. See [COMPATIBILITY.md](COMPATIBILITY.md).

Shows: validator index, label, status, balance (ETH), and the last 5 attestations as a glyph row (`●` hit, `·` miss, `?` not yet observed).

## Install

Pre-built `.deb` and `.rpm` packages (both `amd64`/`x86_64` and `arm64`/`aarch64`) and the source distribution are attached to each tagged release — see the [latest GitHub Release](https://github.com/Workharu/eth-validator-stats/releases/latest). PyPI publishes the wheel and sdist under the same version.

**Debian / Ubuntu (any version supported by Debian 12 or Ubuntu 22.04+):**

```bash
# No system Python prerequisite — the package bundles its own.
sudo apt install -y ./eth-validator-stats_0.3.3-1_amd64.deb     # or _arm64.deb
sudo eth-validator-stats init --system
sudo systemctl start eth-validator-stats
```

> Use `apt install ./path.deb` (not `dpkg -i`) so apt resolves the few
> remaining dependencies (e.g. `adduser`) automatically.

**Fedora / RHEL / Rocky / Alma 9+:**

```bash
sudo dnf install ./eth-validator-stats-0.3.3-1.fc40.x86_64.rpm     # or .aarch64.rpm
sudo eth-validator-stats init --system
sudo systemctl start eth-validator-stats
```

**Any OS with Python 3.11+ (PyPI):**

```bash
pipx install eth-validator-stats
eth-validator-stats init        # per-user config at ~/.config/eth-validator-stats/
```

To run as a daemon after a pipx install, use the built-in service installer
(does the same registration the `.deb`/`.rpm` postinst does, but for the
pipx-installed binary):

```bash
sudo eth-validator-stats install-service       # one-time daemon registration
sudo eth-validator-stats init --system         # populate config
sudo systemctl start eth-validator-stats
```

Pass `--user` to register a `systemctl --user` unit instead of a system-scope
one (no sudo required). To remove:

```bash
sudo eth-validator-stats uninstall-service              # leaves /etc and /var/lib alone
sudo eth-validator-stats uninstall-service --purge      # also deletes config + state
```

## Develop locally

If you want to hack on the code instead of installing:

```bash
git clone <this-repo> eth-validator-stats
cd eth-validator-stats
uv sync
uv run eth-validator-stats status
```

See [`packaging/linux/README.md`](packaging/linux/README.md) for the Phase 1 manual systemd install path — still supported for users who want to run from source without committing to a distro package.

## Setup

The fastest path is the onboarding wizard:

```bash
uv run eth-validator-stats init
```

It will:
1. Ask where your beacon node lives, then auto-detect the client by probing the well-known ports (Prysm 3500, Lighthouse 5052, Teku 5051, Nimbus 5052, Lodestar 9596).
2. Ask for one starter validator (pubkey or index), confirm it against the node.
3. Optionally generate an ntfy topic and send a verification push to confirm the wire works end-to-end.
4. Write `~/.config/eth-validator-stats/config.yml`.

Add more validators afterward by editing that YAML file directly.

**Flag-driven (scriptable):**
```bash
uv run eth-validator-stats init --beacon-url http://localhost:3500 \
    --validator 12345 --label home-1 \
    --ntfy-topic eth-vstats-mysecret --yes
```

**Already on a legacy `config.toml`?** Migrate:
```bash
uv run eth-validator-stats init --migrate
```
This converts to `config.yml` and renames the original to `config.toml.bak`.

If you'd rather skip the wizard entirely, copy `config.yml.example` to `~/.config/eth-validator-stats/config.yml` and edit it.

## Usage

```bash
uv run eth-validator-stats status
uv run eth-validator-stats check --missed 3
```

`status` prints a table and exits 0. `check` prints one line per offender (`<index> <label>\t<rule>`) and exits **2** if any alerts fire — designed for cron.

### What gets notified

Per-event, all routed through the same notifier (ntfy by default):

| Event | When | Dedup |
|---|---|---|
| `OFFLINE` | validator status leaves `active_ongoing`/`pending_*` | once per `cooldown_minutes`, resets on recovery |
| `MISSED_ATTESTATIONS` | last N consecutive liveness records are misses (N default 2, override via `alerts.missed_attestations_threshold` in YAML or `--missed N` flag) | once per `cooldown_minutes` |
| `withdrawal` | balance drops by ≥ `alerts.withdrawal_threshold_gwei` (default 0.001 ETH) while still active AND the two compared balance snapshots are ≤ `alerts.withdrawal_max_gap_slots` apart (default 64 slots ≈ 12.8 min — avoids false positives from cumulative attestation losses during a `check` outage) | once per drop (next poll resets the comparison baseline) |
| `proposing soon` | a configured validator is scheduled to propose within `alerts.proposal_lookahead_epochs` epochs (default 1 ≈ ~6 min) | exactly once per (validator, slot) |
| `✓ proposed` / `✗ missed proposal` | scheduled slot has passed; verified against the canonical block header | once per (validator, slot) |
| `MONITOR BLIND` / `MONITOR RECOVERED` | beacon node unreachable / reachable again | once per `cooldown_minutes` |

`RECOVERED` messages fire when a validator returns to `active_ongoing` after any non-pending status.

### Cron

Cron should not invoke `uv run` (it pays the resolver/lock cost every minute). Use the venv binary directly:

```cron
*/5 * * * * /path/to/eth-validator-stats/.venv/bin/eth-validator-stats check --missed 3 || notify-send "validator alert"
```

### Run as a service (Linux / Windows)

Long-running alternative to cron. The `watch` subcommand loops one check per
`--interval` (default 60s) until the OS supervisor stops it. systemd (Linux)
and WinSW (Windows) wrap the process and handle restart-on-failure.

**Linux (no sudo, systemd --user):**
```bash
cd packaging/linux
./install-service.sh
systemctl --user status eth-validator-stats.service
```

**Linux (system scope, sudo):**
```bash
cd packaging/linux
sudo ./install-service.sh --system
```

**Windows (elevated PowerShell):**
```powershell
cd packaging\windows
.\install-service.ps1
Get-Service eth-validator-stats
```

See `packaging/linux/README.md` and `packaging/windows/README.md` for full
details, uninstall instructions, and the Phase 2 distro-package migration
path. Cron mode (above) continues to work alongside the service unit; the
two do not conflict, but running both at once will double the load on your
beacon node.

### Push notifications (ntfy)

If `ntfy_topic` is set under `alerts:` in `config.yml`, `check` POSTs to that ntfy topic on every **new** alert transition (no spam — see dedup/storm below).

The fastest way to wire this up is `eth-validator-stats init`, which generates an unguessable topic and sends a verification push so you can confirm delivery before trusting the alert path. If you skipped that step or want to change topics later:

1. Install the **ntfy** app (Play Store, App Store, F-Droid) on your phone.
2. In the app: Subscribe → enter an unguessable topic name (e.g. `eth-vstats-9f8e7d6c5b4a`). Anyone who knows the name can read messages, so treat it as a secret.
3. Set `ntfy_topic: "https://ntfy.sh/<your-topic>"` under `alerts:` in `config.yml`.
4. Run `eth-validator-stats check`. The first time an alert fires you'll get a push.

The public `ntfy.sh` server is free and Apache-2.0 open source. If you'd rather self-host, run `ntfy serve` on your beacon-node box and set `ntfy_topic` to `http://your-host:80/your-topic`.

#### Alert intelligence

- **Beacon-down detection** — if the beacon node is unreachable, you get one `MONITOR BLIND` push (not 1000 per-validator pushes), and one `MONITOR RECOVERED` when it comes back.
- **Per-validator cooldown** — once a validator alerts on a rule, subsequent `check` runs suppress the same alert until `cooldown_minutes` has passed (default 30). Rule transitions (OFFLINE → MISSED_ATTESTATIONS, etc.) break the cooldown.
- **Storm grouping** — if more than `storm_threshold` *new* alerts fire in a single run (default 10), they collapse into one `VALIDATOR STORM` summary with a sample of indices.
- **Recovery messages** — when a validator returns to `active_ongoing`, you get a `RECOVERED` push.

Notification failures (network blip, ntfy server down) are logged to stderr but never crash `check`.

The state file in `~/.local/share/eth-validator-stats/state.json` builds up a per-validator rolling buffer of the last ~10 epochs of liveness across runs. **Last-5-attestations** is sparse-by-design: it shows the last 5 epochs the CLI has observed, not the last 5 epochs of chain history. Run frequently (cron at 1–5 minutes is fine) and the buffer stays current.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `BEACON_NODE_URL` | `http://localhost:3500` | Beacon node HTTP endpoint. Wins over the `beacon_node_url` field in config. |
| `BEACON_NODE_AUTH_TOKEN` | (none) | Optional Bearer token for hosted providers / proxied nodes. Wins over `beacon_auth_token` in config. |
| `ETH_VALIDATOR_STATS_CONFIG` | `$XDG_CONFIG_HOME/eth-validator-stats/config.yml` | Override config path. |
| `ETH_VALIDATOR_STATS_STATE` | `$XDG_DATA_HOME/eth-validator-stats/state.json` | Override state file path. |

## Diagnosing a new node

```bash
uv run eth-validator-stats info
```

Prints client name/version (e.g. `Prysm/v6.2.1`) and probes each endpoint we depend on, marking them `OK` / `UNSUPPORTED` / `ERROR`. Run this first against any new beacon node. If `POST /eth/v1/validator/liveness/{epoch}` shows `UNSUPPORTED`, your client is too old for the "last N attestations" feature — everything else still works (you'll get a one-time warning on first `status` run).

See [COMPATIBILITY.md](COMPATIBILITY.md) for the per-client port table and tested-versions matrix.

## How it works

Each invocation makes at most three calls to the beacon node:

- `GET /eth/v1/beacon/headers/head` — current slot.
- `POST /eth/v1/beacon/states/head/validators` — status + balance for all configured validators in one call (POST form has no 64-id cap).
- `POST /eth/v1/validator/liveness/{current_epoch - 1}` — did each validator participate in the just-finished epoch.

The first run also fetches `/eth/v1/beacon/genesis` and `/eth/v1/config/spec` once and caches them in the state file.

Liveness answers "the validator was seen in the epoch", which is what most users mean when they ask "did I miss an attestation". On-chain head/target correctness is a v2 feature (via `POST /eth/v1/beacon/rewards/attestations/{epoch}` on finalized epochs).

## What it does NOT do (yet)

- No watch / live TUI mode.
- No proposer-duty tracking.
- No historical query command.
- No `validators add/list/rm` CRUD — edit the YAML directly.

See [docs/superpowers/plans/](docs/superpowers/plans/) and [docs/superpowers/specs/](docs/superpowers/specs/) for what's next (Telegram interaction is the planned follow-up).

## Tests

```bash
uv sync       # installs dev deps automatically (pytest is in [dependency-groups].dev)
uv run pytest
```

Tests use `httpx.MockTransport`; no live beacon node required.
