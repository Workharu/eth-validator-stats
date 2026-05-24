# Linux service install (Phase 1)

This directory installs `eth-validator-stats` as a systemd unit that runs the
`watch` subcommand under the supervisor.

## Prerequisites

- A working `uv sync` run from the repo root, so `.venv/bin/eth-validator-stats`
  exists.
- A populated config at `~/.config/eth-validator-stats/config.yml`. If you
  don't have one, run `uv run eth-validator-stats init` first.
- `systemd` (any modern distro).

## Install as a user unit (default — no sudo)

```bash
./install-service.sh
```

This writes the unit to `~/.config/systemd/user/eth-validator-stats.service`,
enables linger (so the service survives logout), and starts it. If linger
cannot be enabled without sudo on your distro, the script prints the manual
command to run.

Verify:
```bash
systemctl --user status eth-validator-stats.service
journalctl --user -u eth-validator-stats.service -f
```

## Install as a system unit (sudo)

```bash
sudo ./install-service.sh --system
```

Writes the unit to `/etc/systemd/system/eth-validator-stats.service`, sets
`User=$SUDO_USER` so it runs as the invoking user (not root). This path is
mainly for users who want system-scope without waiting for Phase 2's distro
packages.

## Uninstall

```bash
./uninstall-service.sh
# or
sudo ./uninstall-service.sh --system
```

## Phase 2 hand-off

Phase 2's `.deb` and `.rpm` packages will ship a similar unit at
`/lib/systemd/system/eth-validator-stats.service` running as a dedicated
`eth-validator-stats` system user. Users who installed via this script will
need a one-time migration:

```bash
systemctl --user disable --now eth-validator-stats.service
rm ~/.config/systemd/user/eth-validator-stats.service
sudo apt install eth-validator-stats  # or dnf install
```

Config remains compatible (the package will look in `/etc/eth-validator-stats/`
by default but accepts the `ETH_VALIDATOR_STATS_CONFIG` env var to point at
your existing `~/.config/eth-validator-stats/config.yml`).
