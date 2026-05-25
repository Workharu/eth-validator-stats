# Linux service install (from a git checkout)

This directory installs `eth-validator-stats` as a systemd unit that runs the
`watch` subcommand under the supervisor.

> Most users should install the `.deb` / `.rpm` (see the top-level README), or
> use `pipx install eth-validator-stats`. This directory is for installing
> straight from a git checkout — useful for development, packaging
> experiments, and tweaking the unit file in place.

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
`User=$SUDO_USER` so it runs as the invoking user (not root). Suitable when
you want a system-scope unit but don't want to install the distro package.

## Uninstall

```bash
./uninstall-service.sh
# or
sudo ./uninstall-service.sh --system
```

## Migrating to the distro package

If you installed via this script and now want to switch to the official
`.deb` / `.rpm` package:

```bash
systemctl --user disable --now eth-validator-stats.service
rm ~/.config/systemd/user/eth-validator-stats.service
sudo apt install eth-validator-stats  # or dnf install
```

Your config keeps working — the package reads
`/etc/eth-validator-stats/config.yml` by default but honors the
`ETH_VALIDATOR_STATS_CONFIG` env var if you want to keep pointing at the
existing `~/.config/eth-validator-stats/config.yml`.
