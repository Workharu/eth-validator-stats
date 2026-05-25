# <img src="assets/icon.png" alt="" width="32" height="32" align="left"> eth-validator-stats

[![CI](https://github.com/Workharu/eth-validator-stats/actions/workflows/pre-release-check.yml/badge.svg?branch=main)](https://github.com/Workharu/eth-validator-stats/actions/workflows/pre-release-check.yml)
[![PyPI](https://img.shields.io/pypi/v/eth-validator-stats.svg)](https://pypi.org/project/eth-validator-stats/)
[![Python versions](https://img.shields.io/pypi/pyversions/eth-validator-stats.svg)](https://pypi.org/project/eth-validator-stats/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/Workharu/eth-validator-stats?include_prereleases&sort=semver)](https://github.com/Workharu/eth-validator-stats/releases/latest)

Watch your Ethereum validators from a tiny self-hosted CLI. It talks to *your* beacon node (no third-party telemetry) and pushes alerts straight to your phone via [ntfy](https://ntfy.sh).

```
                         Validators
┏━━━━━━━━┳━━━━━━━━┳════════════════┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃    idx ┃ label  ┃ status         ┃ balance (ETH) ┃ last 5 atts ┃
┡━━━━━━━━╇━━━━━━━━╇════════════════╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ 123456 │ home-1 │ active_ongoing │       32.0182 │ ● ● ● ● ●   │
│ 234567 │ home-2 │ active_ongoing │       32.0177 │ ● · ● ● ●   │
└────────┴────────┴────────────────┴───────────────┴─────────────┘
```

`●` attested, `·` missed, `?` not yet observed.

## Quick start

```bash
# 1. Install (Linux + macOS, auto-detects .deb / .rpm / pipx)
curl -fsSL https://raw.githubusercontent.com/Workharu/eth-validator-stats/main/scripts/install.sh | sudo bash

# 2. Configure (interactive wizard: finds your node, sets up ntfy)
sudo eth-validator-stats init

# 3. Check it works
eth-validator-stats status
```

That's it. On `.deb` / `.rpm` installs, step 2 also starts a `systemd` service that watches your validators every 60 seconds.

> **Tip:** Every command also responds to `evs` (3 chars). `evs status`, `evs check --missed 3`, etc.

## What you'll be notified about

Every alert is one ntfy push, deduplicated per-validator with a configurable cooldown.

**Health alerts** — fire while something is wrong, clear when it recovers:

| Push | Means |
|---|---|
| `OFFLINE` | Validator left `active_ongoing` (slashed, exiting, exited, etc.) |
| `MISSED_ATTESTATIONS` | N consecutive misses (default 2) for an `active_ongoing` validator |
| `withdrawal` | Balance dropped ≥ 0.001 ETH |
| `MONITOR BLIND` / `MONITOR RECOVERED` | Beacon node unreachable / back up |

**Proposal alerts**:

| Push | When |
|---|---|
| `proposing soon` | A configured validator is scheduled to propose in the next ~6 min |
| `✓ proposed` / `✗ missed proposal` | The slot has passed, verified against the canonical head |

**Lifecycle milestones** — one-shot per validator transition:

| Push | Transition | Priority |
|---|---|---|
| `ACTIVATED` | `pending_*` → `active_ongoing` (joined the active set) | Normal |
| `EXIT INITIATED` | `active_ongoing` → `active_exiting` | Normal |
| **`SLASHED`** | anything → `active_slashed` / `exited_slashed` | **Urgent** (bypasses Do-Not-Disturb) |
| `EXITED` | `active_exiting` → `exited_unslashed` | Normal |
| `WITHDRAWAL READY` | exited → `withdrawal_possible` (funds claimable) | Normal |

Verify any of these without waiting for a real event:

```bash
eth-validator-stats simulate slashed    # check that urgent pushes bypass DND
```

## Commands at a glance

```bash
eth-validator-stats status                # render the last snapshot from disk (read-only;
                                          # add --refresh to also poll the beacon node first)
eth-validator-stats watch                 # loop forever (what the systemd service runs)
eth-validator-stats check --missed 3      # one-shot for cron — exits 2 if any alert fires

eth-validator-stats validators add 12345 --label home-1
eth-validator-stats validators list --status
eth-validator-stats validators rm home-1

eth-validator-stats simulate <event>      # test ntfy pipe end-to-end
eth-validator-stats info                  # probe your beacon node's API
```

## Install

The one-liner above covers most setups. If you'd rather pick a path explicitly:

<details>
<summary><b>Debian / Ubuntu — <code>.deb</code></b></summary>

Works on Debian 12+ and Ubuntu 22.04+ (also 24.04 — no deadsnakes PPA needed; the `.deb` bundles its own Python).

```bash
# Download the .deb for your arch from
# https://github.com/Workharu/eth-validator-stats/releases/latest
sudo apt install -y ./eth-validator-stats_0.4.0-1_amd64.deb
sudo eth-validator-stats init --system
sudo systemctl status eth-validator-stats
```

Use `apt install ./path.deb` (not `dpkg -i`) so deps like `adduser` auto-resolve. Files go to `/opt/eth-validator-stats/`, the binary symlinks into `/usr/bin/`, and config + state live in `/etc/eth-validator-stats/` and `/var/lib/eth-validator-stats/`.

Upgrade by installing a newer `.deb` — config and state are preserved. `apt remove` keeps them; `apt purge` wipes everything.
</details>

<details>
<summary><b>Fedora / RHEL / Rocky / Alma 9+ — <code>.rpm</code></b></summary>

```bash
# Download the .rpm for your arch from the latest release
sudo dnf install ./eth-validator-stats-0.4.0-1.fc40.x86_64.rpm
sudo eth-validator-stats init --system
sudo systemctl status eth-validator-stats
```

Same paths and semantics as the `.deb`. `dnf remove` keeps config + state; manual `rm -rf /etc/eth-validator-stats /var/lib/eth-validator-stats` if you want them gone.
</details>

<details>
<summary><b>macOS, Windows, or any host without <code>.deb</code>/<code>.rpm</code> — <code>pipx</code></b></summary>

```bash
# Install pipx if you don't have it:
#   apt:    sudo apt install pipx
#   dnf:    sudo dnf install pipx
#   brew:   brew install pipx
pipx install eth-validator-stats
eth-validator-stats init        # per-user config at ~/.config/eth-validator-stats/
```

To get the same systemd service the distro packages provide:

```bash
sudo eth-validator-stats install-service    # system-scope unit
eth-validator-stats install-service --user  # or: per-user, no sudo
sudo eth-validator-stats init --system      # writes config + starts service
```

Upgrade with `pipx install --force eth-validator-stats`. Uninstall with `sudo eth-validator-stats uninstall-service --purge && pipx uninstall eth-validator-stats`.
</details>

<details>
<summary><b>From source (development)</b></summary>

```bash
git clone https://github.com/Workharu/eth-validator-stats
cd eth-validator-stats
uv sync
uv run eth-validator-stats status     # prefix every command with `uv run`
uv run pytest -q                      # run the test suite
```

See [`packaging/linux/README.md`](packaging/linux/README.md) for running the source build as a systemd service without packaging.
</details>

Pre-built `.deb` / `.rpm` (amd64 + arm64) and the PyPI wheel are attached to every [tagged release](https://github.com/Workharu/eth-validator-stats/releases/latest).

## Configuration

Generated by `init`. Edit directly anytime — `validators add/list/rm` is just a convenience.

```yaml
beacon_node_url: http://localhost:3500
validators:
  - { index: 123456, label: home-1 }
  - { pubkey: "0xb1d2...", label: home-2 }
alerts:
  ntfy_topic: https://ntfy.sh/eth-vstats-9f8e7d6c5b4a
  cooldown_minutes: 30
  missed_attestations_threshold: 2
```

The CLI looks for config in this order: `$ETH_VALIDATOR_STATS_CONFIG` → `/etc/eth-validator-stats/config.yml` → `~/.config/eth-validator-stats/config.yml`. First match wins. `init` writes the per-user path by default; `init --system` writes `/etc/`.

For the full reference (every alert option, env vars, the on-the-wire request shapes), see [USAGE.md](docs/USAGE.md).

## Compatibility

Works with any client that implements the standard [Ethereum Beacon API](https://ethereum.github.io/beacon-APIs/): Prysm, Lighthouse, Teku, Nimbus, Lodestar. Run `eth-validator-stats info` to probe a new node's endpoint support. Detailed matrix: [COMPATIBILITY.md](COMPATIBILITY.md).

## Contributing

`uv sync && uv run pytest -q` to get going. PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow and [SECURITY.md](SECURITY.md) for how to report vulnerabilities. Full changelog in [CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).
