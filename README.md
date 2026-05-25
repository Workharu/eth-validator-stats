# eth-validator-stats

[![CI](https://github.com/Workharu/eth-validator-stats/actions/workflows/pre-release-check.yml/badge.svg?branch=main)](https://github.com/Workharu/eth-validator-stats/actions/workflows/pre-release-check.yml)
[![PyPI](https://img.shields.io/pypi/v/eth-validator-stats.svg)](https://pypi.org/project/eth-validator-stats/)
[![Python versions](https://img.shields.io/pypi/pyversions/eth-validator-stats.svg)](https://pypi.org/project/eth-validator-stats/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/Workharu/eth-validator-stats?include_prereleases&sort=semver)](https://github.com/Workharu/eth-validator-stats/releases/latest)

A tiny self-hosted CLI for Ethereum validator stats. Talks to your own beacon node (Prysm, Lighthouse, etc.) over the standard Ethereum Beacon API. Built as a minimal replacement for the now-paid features of the beaconcha.in mobile app.

**Commands:**

```
eth-validator-stats init                  # interactive wizard: detect node, set up ntfy
eth-validator-stats status                # rich table snapshot
eth-validator-stats check [--missed N]    # cron mode: prints offenders, exits 2 if any
eth-validator-stats watch [--interval N]  # service mode: loop a check every N seconds
eth-validator-stats info                  # probe beacon node: client/version + endpoint support
eth-validator-stats simulate <event>      # fire a test ntfy push (no real outage needed)
eth-validator-stats validators add <id>   # add a validator (verified against the beacon)
eth-validator-stats validators list       # print what's configured (--status for live state)
eth-validator-stats validators rm <id>    # remove one by index, pubkey, or label
```

> **Short alias:** every command also responds to `evs`. So `evs status`, `evs check --missed 3`, `evs simulate missed-attestation`, etc. Identical behavior — just three characters to type. The long form remains canonical in scripts, systemd units, and these docs.

Works with any client that implements the standard Ethereum Beacon API — Prysm, Lighthouse, Teku, Nimbus, Lodestar. See [COMPATIBILITY.md](COMPATIBILITY.md).

**What `status` looks like:**

```
                         Validators
┏━━━━━━━━┳━━━━━━━━┳════════════════┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃    idx ┃ label  ┃ status         ┃ balance (ETH) ┃ last 5 atts ┃
┡━━━━━━━━╇━━━━━━━━╇════════════════╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ 123456 │ home-1 │ active_ongoing │       32.0182 │ ● ● ● ● ●   │
│ 234567 │ home-2 │ active_ongoing │       32.0177 │ ● · ● ● ●   │
└────────┴────────┴════════════────┴───────────────┴─────────────┘
```

The last column is a sparse glyph row: `●` hit, `·` miss, `?` not yet observed.

## Install

**One-liner (Linux + macOS):**

```bash
curl -fsSL https://raw.githubusercontent.com/Workharu/eth-validator-stats/main/scripts/install.sh | sudo bash
```

Auto-detects your OS and architecture and installs the matching `.deb` / `.rpm` from the latest release. On macOS or any Linux without `apt`/`dnf` it falls back to `pipx install eth-validator-stats` from PyPI. Source: [`scripts/install.sh`](scripts/install.sh).

After install, `sudo eth-validator-stats init` writes the config and starts the systemd service in one step.

**Or pick the path manually:**

| Your situation | Install method | Why |
|---|---|---|
| Beacon node on Debian / Ubuntu | [`.deb` package](#debian--ubuntu) | One command, bundles Python, registers a systemd service |
| Beacon node on Fedora / RHEL / Rocky / Alma | [`.rpm` package](#fedora--rhel--rocky--alma-9) | Same as above for the dnf world |
| Anywhere with Python 3.11+ (laptop, Mac, NAS, container) | [`pipx`](#any-os-with-python-311-pypi) | No sudo, per-user install, easy to upgrade |
| Hacking on the code | [from source](#from-source) | Run uncommitted changes directly |

All four routes give you the same `eth-validator-stats` binary. The `.deb`/`.rpm` packages additionally install a `systemd` unit named `eth-validator-stats.service`.

Pre-built `.deb` and `.rpm` packages (both `amd64`/`x86_64` and `arm64`/`aarch64`) and the source distribution are attached to each tagged release — see the [latest GitHub Release](https://github.com/Workharu/eth-validator-stats/releases/latest). PyPI publishes the wheel and sdist under the same version.

### Debian / Ubuntu

Works on Debian 12+ and Ubuntu 22.04+ (also 24.04, no deadsnakes PPA needed — the `.deb` bundles its own Python).

```bash
# 1. Download the .deb for your architecture from the latest release.
#    https://github.com/Workharu/eth-validator-stats/releases/latest
#    Files: eth-validator-stats_<version>-1_amd64.deb (or _arm64.deb)

# 2. Install with apt so dependencies (e.g. adduser) auto-resolve.
sudo apt install -y ./eth-validator-stats_0.3.11-1_amd64.deb

# 3. Set up config + start the service.
sudo eth-validator-stats init --system           # writes config AND starts the service
sudo systemctl status eth-validator-stats        # confirm it's running
```

> Use `apt install ./path.deb` (not `dpkg -i`). dpkg won't pull `adduser` etc.

**What gets installed:**

| Path | Purpose |
|---|---|
| `/opt/eth-validator-stats/` | Bundled Python interpreter + venv |
| `/usr/bin/eth-validator-stats` | CLI on `$PATH` (symlink into the venv) |
| `/lib/systemd/system/eth-validator-stats.service` | systemd unit (started by you, after init) |
| `/etc/eth-validator-stats/config.yml` | Created by `init --system`, owned by service user |
| `/var/lib/eth-validator-stats/state.json` | Per-validator history, owned by service user |

**Upgrade:** download the new `.deb` and `sudo apt install -y ./<new>.deb` — your config and state are preserved.

**Uninstall:**

```bash
sudo apt remove eth-validator-stats           # leaves /etc and /var/lib alone
sudo apt purge eth-validator-stats            # also wipes config + state + user
```

### Fedora / RHEL / Rocky / Alma 9+

```bash
# 1. Download the .rpm for your architecture from the latest release.
#    Files: eth-validator-stats-<version>-1.fcXX.x86_64.rpm (or .aarch64.rpm)

# 2. Install.
sudo dnf install ./eth-validator-stats-0.3.11-1.fc40.x86_64.rpm

# 3. Set up config + start the service.
sudo eth-validator-stats init --system           # writes config AND starts the service
sudo systemctl status eth-validator-stats        # confirm it's running
```

Paths and uninstall semantics are the same as the `.deb` above — config and state survive `dnf remove` and only get wiped on explicit purge:

```bash
sudo dnf remove eth-validator-stats           # leaves /etc and /var/lib alone
# To wipe /etc/eth-validator-stats and /var/lib/eth-validator-stats:
sudo rm -rf /etc/eth-validator-stats /var/lib/eth-validator-stats
```

### Any OS with Python 3.11+ (PyPI)

For Linux laptops/desktops without sudo, macOS, FreeBSD, or anywhere you'd rather not install a distro package:

```bash
# Install pipx if you don't have it.
#   Debian/Ubuntu:  sudo apt install pipx
#   Fedora:         sudo dnf install pipx
#   macOS:          brew install pipx
#   Generic:        python3 -m pip install --user pipx && pipx ensurepath

pipx install eth-validator-stats
eth-validator-stats init                     # per-user config at ~/.config/eth-validator-stats/
eth-validator-stats status                   # one-shot — see the table
```

**Run as a daemon (Linux only):** if you want the same systemd service the `.deb`/`.rpm` provides but you installed via pipx, use the built-in installer (does the same wiring as the distro packages' postinst, but against the pipx-installed binary):

```bash
sudo eth-validator-stats install-service             # system scope (recommended)
# or, no sudo:
eth-validator-stats install-service --user           # systemctl --user unit

sudo eth-validator-stats init --system               # writes config AND starts the service
# For --user installs: `init --system` requires root; instead use
# `eth-validator-stats init` (per-user config) then `systemctl --user start eth-validator-stats`.
```

**Upgrade:** `pipx install --force eth-validator-stats`.

**Uninstall:**

```bash
sudo eth-validator-stats uninstall-service          # remove the systemd unit (leaves config alone)
sudo eth-validator-stats uninstall-service --purge  # also delete /etc + /var/lib
pipx uninstall eth-validator-stats                  # remove the binary
```

### From source

For development, testing patches, or running uncommitted changes:

```bash
git clone https://github.com/Workharu/eth-validator-stats
cd eth-validator-stats
uv sync                                  # creates .venv and installs deps
uv run eth-validator-stats status        # all commands work via `uv run`
```

The `uv run` prefix is needed for source mode since the binary lives at `.venv/bin/eth-validator-stats` and is not on `$PATH`. To run without the prefix, `source .venv/bin/activate` first.

See [`packaging/linux/README.md`](packaging/linux/README.md) for a manual systemd-unit install path that runs the source-mode binary as a service — useful if you want to run from a checkout without packaging.

## Setup

> All `eth-validator-stats <cmd>` examples below assume you installed via `.deb`/`.rpm`/`pipx`. If you're running [from source](#from-source), prefix every command with `uv run` (e.g. `uv run eth-validator-stats init`).

The fastest path is the onboarding wizard:

```bash
eth-validator-stats init
```

It will:
1. Ask where your beacon node lives, then auto-detect the client by probing the well-known ports (Prysm 3500, Lighthouse 5052, Teku 5051, Nimbus 5052, Lodestar 9596).
2. Ask for one starter validator (pubkey or index), confirm it against the node.
3. Optionally generate an ntfy topic and send a verification push to confirm the wire works end-to-end.
4. Write `~/.config/eth-validator-stats/config.yml` (per-user) or `/etc/eth-validator-stats/config.yml` (with `--system`).

Add more validators afterward by editing that YAML file directly.

**Flag-driven (scriptable):**
```bash
eth-validator-stats init --beacon-url http://localhost:3500 \
    --validator 12345 --label home-1 \
    --ntfy-topic eth-vstats-mysecret --yes
```

If you'd rather skip the wizard entirely, copy `config.yml.example` to `~/.config/eth-validator-stats/config.yml` and edit it.

**Verify the ntfy wire** (no real outage needed):

```bash
eth-validator-stats simulate missed-attestation
```

Your phone should buzz with `MISSED_ATTESTATIONS last=2`. Use this any time you change `ntfy_topic` or want to confirm the alert path before trusting it. Other simulatable events: `offline`, `withdrawal`, `proposing-soon`, `proposed`, `missed-proposal`, `blind`, `recovered`.

## Usage

```bash
eth-validator-stats status                  # one-shot, prints a table
eth-validator-stats check --missed 3        # cron mode, exits 2 if any alerts fired
eth-validator-stats watch --interval 60     # long-running, loops every 60s
eth-validator-stats info                    # probe the beacon node and check endpoint support
```

`status` prints a table and exits 0. `check` prints one line per offender (`<index> <label>\t<rule>`) and exits **2** if any alerts fire — designed for cron. `watch` runs the same check on a loop and is what the systemd service uses.

### Adding / removing validators after init

Use the `validators` subcommand group instead of editing the YAML:

```bash
# Add by index (or pubkey). The beacon node is hit to confirm the validator exists.
eth-validator-stats validators add 12345 --label home-2
eth-validator-stats validators add 0xb1d2a4b9... --label home-3

# Skip the beacon check (e.g. if the validator is pending_initialized).
eth-validator-stats validators add 12345 --label home-2 --no-verify

# List what's configured. --status hits the beacon for live state + balance.
eth-validator-stats validators list
eth-validator-stats validators list --status

# Remove by index, pubkey, or label.
eth-validator-stats validators rm 12345
eth-validator-stats validators rm home-3
eth-validator-stats validators rm --yes 0xb1d2a4b9...    # skip the prompt
```

On `.deb`/`.rpm` installs, the systemd service is restarted automatically after `add`/`rm` so the new list is picked up immediately. The file's mode and ownership (0644, `eth-validator-stats:eth-validator-stats`) are preserved across writes.

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

Don't call `uv run` from cron — it pays the resolver cost every minute. Call the binary directly. The right path depends on how you installed:

| Install method | Binary path |
|---|---|
| `.deb` / `.rpm` | `/usr/bin/eth-validator-stats` (or just `eth-validator-stats` — it's on `$PATH`) |
| `pipx` | `~/.local/bin/eth-validator-stats` |
| From source | `/path/to/checkout/.venv/bin/eth-validator-stats` |

Example crontab (every 5 min, fall back to a desktop notification if the alert path failed):

```cron
*/5 * * * * eth-validator-stats check --missed 3 || notify-send "validator alert"
```

### Run as a service (Linux / Windows)

If you installed via `.deb` or `.rpm`, the systemd unit is **already installed** — see the Install section above. Just `sudo eth-validator-stats init --system` (which writes the config and starts the service in one step).

For pipx installs, register the same unit with `sudo eth-validator-stats install-service` (covered in the [pipx section](#any-os-with-python-311-pypi)).

For source-mode users or anyone who prefers the legacy manual scripts:

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

**Push icon:** every notification carries the eth-validator-stats icon (a small PNG served from this repo) so you can tell at a glance the push is from this tool. To override, set `alerts.icon_url` to any publicly-fetchable URL. To suppress entirely, set it to an empty string:
```yaml
alerts:
  icon_url: ""
```
ntfy clients fetch the image at notification time and cache it; if the URL ever 404s the push still arrives, just without an icon.

#### Alert intelligence

- **Beacon-down detection** — if the beacon node is unreachable, you get one `MONITOR BLIND` push (not 1000 per-validator pushes), and one `MONITOR RECOVERED` when it comes back.
- **Per-validator cooldown** — once a validator alerts on a rule, subsequent `check` runs suppress the same alert until `cooldown_minutes` has passed (default 30). Rule transitions (OFFLINE → MISSED_ATTESTATIONS, etc.) break the cooldown.
- **Storm grouping** — if more than `storm_threshold` *new* alerts fire in a single run (default 10), they collapse into one `VALIDATOR STORM` summary with a sample of indices.
- **Recovery messages** — when a validator returns to `active_ongoing`, you get a `RECOVERED` push.

Notification failures (network blip, ntfy server down) are logged to stderr but never crash `check`.

The state file in `~/.local/share/eth-validator-stats/state.json` builds up a per-validator rolling buffer of the last ~10 epochs of liveness across runs. **Last-5-attestations** is sparse-by-design: it shows the last 5 epochs the CLI has observed, not the last 5 epochs of chain history. Run frequently (cron at 1–5 minutes is fine) and the buffer stays current.

## Where is my config?

When reading, the CLI searches in this order and uses the first one it finds:

1. **`$ETH_VALIDATOR_STATS_CONFIG`** — explicit override (used by the systemd unit and tests).
2. **`/etc/eth-validator-stats/config.yml`** — system-wide, written by `init --system` and the `.deb`/`.rpm` post-install scripts. **If you installed via a distro package, this is where your config lives, and it's found regardless of which user is invoking the CLI.**
3. **`~/.config/eth-validator-stats/config.yml`** — per-user XDG default, written by `init` (without `--system`).
4. *(legacy)* `~/.config/eth-validator-stats/config.toml` — pre-YAML format; prints a deprecation hint.

When **writing** (i.e. `init`), the system path is used only with `--system`; otherwise writes go to the per-user XDG path. The lookup order means a per-user config is never accidentally overshadowed by a system config you didn't intend to write — you have to deliberately run `init --system` to put one there.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `BEACON_NODE_URL` | `http://localhost:3500` | Beacon node HTTP endpoint. Wins over the `beacon_node_url` field in config. |
| `BEACON_NODE_AUTH_TOKEN` | (none) | Optional Bearer token for hosted providers / proxied nodes. Wins over `beacon_auth_token` in config. |
| `ETH_VALIDATOR_STATS_CONFIG` | (auto-discovered, see above) | Override config path. When set, it wins over both `/etc` and `~/.config`. |
| `ETH_VALIDATOR_STATS_STATE` | `$XDG_DATA_HOME/eth-validator-stats/state.json` | Override state file path. |

## Diagnosing a new node

```bash
eth-validator-stats info
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

- No live TUI / dashboard. `watch` is a headless service-mode loop, not a re-rendered terminal UI.
- No historical query command — `status` is current-slot, `check` is recent-buffer (~10 epochs). For long-range history you still want a block explorer or beaconcha.in.
- No on-chain head/target attestation-correctness scoring (only "seen / not seen" per epoch via the liveness endpoint). On-chain correctness is a planned v2 feature.
- ~~No `validators add/list/rm` CRUD command — edit `config.yml` directly.~~ Added in 0.3.12; see the [validators subcommand](#adding--removing-validators-after-init).
- No Telegram / Discord / email transport — ntfy only. Telegram is the planned follow-up.

See [docs/superpowers/plans/](docs/superpowers/plans/) and [docs/superpowers/specs/](docs/superpowers/specs/) for what's actively in flight.

## Tests

```bash
uv sync       # installs dev deps automatically (pytest is in [dependency-groups].dev)
uv run pytest
```

Tests use `httpx.MockTransport`; no live beacon node required.
