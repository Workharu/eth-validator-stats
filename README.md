# eth-validator-stats

A tiny self-hosted CLI for Ethereum validator stats. Talks to your own beacon node (Prysm, Lighthouse, etc.) over the standard Ethereum Beacon API. Built as a minimal replacement for the now-paid features of the beaconcha.in mobile app.

**v1 surface:**

```
eth-validator-stats status                # rich table snapshot
eth-validator-stats check [--missed N]    # cron mode: prints offenders, exits 2 if any
eth-validator-stats info                  # probe beacon node: client/version + endpoint support
```

Works with any client that implements the standard Ethereum Beacon API — Prysm, Lighthouse, Teku, Nimbus, Lodestar. See [COMPATIBILITY.md](COMPATIBILITY.md).

Shows: validator index, label, status, balance (ETH), and the last 5 attestations as a glyph row (`●` hit, `·` miss, `?` not yet observed).

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this-repo> eth-validator-stats
cd eth-validator-stats
uv sync                  # creates .venv and installs from uv.lock
```

Run the CLI via `uv run`:

```bash
uv run eth-validator-stats status
```

Or activate the venv directly (`source .venv/bin/activate`) and call `eth-validator-stats` plain.

## Setup

1. Make sure your beacon node's HTTP API is reachable. Prysm exposes it on `:3500` by default. Confirm:
   ```bash
   curl http://localhost:3500/eth/v1/beacon/headers/head
   ```

2. Create a config file. Default location: `~/.config/eth-validator-stats/config.toml`.

   ```bash
   mkdir -p ~/.config/eth-validator-stats
   cp config.toml.example ~/.config/eth-validator-stats/config.toml
   $EDITOR ~/.config/eth-validator-stats/config.toml
   ```

   Each validator entry takes either a `pubkey` (hex string) or an `index` (integer), with an optional `label`:

   ```toml
   [[validators]]
   pubkey = "0xb1d2..."
   label  = "home-1"

   [[validators]]
   index = 12345
   label  = "home-2"
   ```

## Usage

```bash
uv run eth-validator-stats status
uv run eth-validator-stats check --missed 3
```

`status` prints a table and exits 0. `check` prints one line per offender (`<index> <label>\t<rule>`) and exits **2** if any alerts fire — designed for cron.

### Alert rules

- `OFFLINE`: validator status is anything other than `active_ongoing` and not `pending_*`. Reports the actual status (e.g. `exited_slashed`).
- `MISSED_ATTESTATIONS`: the last N entries in the local liveness ring buffer are all misses. N is configurable via `--missed` (default 3).

### Cron

Cron should not invoke `uv run` (it pays the resolver/lock cost every minute). Use the venv binary directly:

```cron
*/5 * * * * /path/to/eth-validator-stats/.venv/bin/eth-validator-stats check --missed 3 || notify-send "validator alert"
```

### Push notifications (ntfy)

If you set `ntfy_topic` under `[alerts]` in `config.toml`, `check` will POST to that ntfy topic on every **new** alert transition (no spam — see dedup/storm below). Setup:

1. Install the **ntfy** app (Play Store, App Store, F-Droid) on your phone.
2. In the app: Subscribe → enter an unguessable topic name (e.g. `eth-vstats-9f8e7d6c5b4a`). Anyone who knows the name can read messages, so treat it as a secret.
3. Set `ntfy_topic = "https://ntfy.sh/<your-topic>"` in `config.toml`.
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
| `ETH_VALIDATOR_STATS_CONFIG` | `$XDG_CONFIG_HOME/eth-validator-stats/config.toml` | Override config path. |
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
- No `validators add/list/rm` CRUD — edit the TOML directly.

See the plan file in `.claude/plans/` (or the v2 backlog at the bottom of it) for what's next.

## Tests

```bash
uv sync       # installs dev deps automatically (pytest is in [dependency-groups].dev)
uv run pytest
```

Tests use `httpx.MockTransport`; no live beacon node required.
