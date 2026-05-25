# Usage reference

This is the long-form reference. For the quick path, the [main README](../README.md) has you covered. Read this when you need to:

- Tune alert thresholds and cooldowns
- Set up the binary in cron
- Run it as a systemd service from a pipx or source install
- Override paths via environment variables
- Understand what the CLI actually asks your beacon node for

## Alert details

### Cooldown and storm grouping

- **Per-validator cooldown.** Once a validator alerts on a rule, the same alert is suppressed for `alerts.cooldown_minutes` (default 30). Rule transitions break the cooldown — going from `OFFLINE` to `MISSED_ATTESTATIONS` re-pages immediately.
- **Storm grouping.** If more than `alerts.storm_threshold` *new* alerts fire in one `check` run (default 10), they collapse into a single `VALIDATOR STORM` summary with a sample of indices. Recoveries get the same treatment.
- **Beacon-down detection.** If the beacon node is unreachable, you get one `MONITOR BLIND` push (not 1000 per-validator pushes), and one `MONITOR RECOVERED` when it's back.
- **Failures are loud only in logs.** Network blips or an ntfy server outage land in stderr but never crash `check`.

### Lifecycle priority

`SLASHED` is the only alert sent with [ntfy `Priority: urgent`](https://docs.ntfy.sh/publish/#message-priority) — it bypasses Do-Not-Disturb on most phones. Test it before you trust it:

```bash
eth-validator-stats simulate slashed
```

All other lifecycle and health alerts ride the default priority.

### Notification fields you can tune

| Config field | Default | What it does |
|---|---|---|
| `alerts.ntfy_topic` | (empty — no pushes) | Full URL of your ntfy topic, e.g. `https://ntfy.sh/eth-vstats-9f8e7d6c5b4a` |
| `alerts.cooldown_minutes` | `30` | How long the same alert is suppressed |
| `alerts.storm_threshold` | `10` | New alerts per run that collapse into a single STORM message |
| `alerts.missed_attestations_threshold` | `2` | Consecutive misses required to fire `MISSED_ATTESTATIONS` |
| `alerts.withdrawal_threshold_gwei` | `1_000_000` (0.001 ETH) | Minimum balance drop to flag as a withdrawal |
| `alerts.withdrawal_max_gap_slots` | `64` (~12.8 min) | Drops between snapshots wider than this are skipped (avoids false positives during a `check` outage) |
| `alerts.proposal_lookahead_epochs` | `1` (~6 min) | How early to fire `proposing soon` |
| `alerts.icon_url` | repo-hosted PNG | Push notification icon. `""` disables; any public URL works |
| `alerts.daily_heartbeat` | `false` | Send a daily `MONITOR ALIVE` ntfy. Absence of the morning push tells you the monitor is dead. `init` defaults this to `true` when ntfy is configured. |
| `alerts.daily_heartbeat_hour` | `9` | Local-time hour (0–23) when the daily push fires. |
| `alerts.heartbeat_url` | `""` | If set, every successful poll POSTs to this URL. Compatible with healthchecks.io, Better Stack, Cronitor, self-hosted uptime-kuma, or any service that accepts an unauthenticated POST. See "Monitoring the monitor" below. |

## Monitoring the monitor

If `eth-validator-stats` itself dies — process crash, kernel panic, network outage, you forgot to renew your domain — your phone goes quiet, and silence is indistinguishable from "everything is fine." There are two layers to fix this.

### Layer 1: daily heartbeat (zero setup)

`init` enables this by default when ntfy is configured. Once a day at 9 AM local time, you get a low-content ntfy push:

```
MONITOR ALIVE
2 validators tracked, 2 active_ongoing
```

The push itself is uninteresting — the **absence** is the signal. If you don't get your morning ping, the monitor is dead and you should investigate. To change the hour:

```yaml
alerts:
  daily_heartbeat: true
  daily_heartbeat_hour: 7   # send at 7 AM instead
```

Detection latency: up to 24 hours. Good enough for most validator operators; if you want tighter, add Layer 2.

### Layer 2: external watchdog URL (5-minute detection)

Set `alerts.heartbeat_url` to a URL that a third-party service polls. We POST to it on every successful watch cycle; if the service stops seeing posts, it pages you. The recommended provider is **[healthchecks.io](https://healthchecks.io/)** — it's open source, free for up to 20 checks, and has built-in ntfy integration so alerts route to your existing topic.

**Setup (~90 seconds):**

1. Go to [healthchecks.io](https://healthchecks.io/) → sign in with GitHub or Google (one click, no password).
2. Click **Add Check**, give it a name, set the grace period to 10 min (or whatever you want).
3. Copy the **Ping URL** (looks like `https://hc-ping.com/abc-123-def-456`).
4. Paste into `config.yml`:
   ```yaml
   alerts:
     heartbeat_url: https://hc-ping.com/abc-123-def-456
   ```
5. Back on the healthchecks.io check page, click **Integrations** → **ntfy** → paste your existing ntfy topic URL.
6. Restart `eth-validator-stats` (`sudo systemctl restart eth-validator-stats` on `.deb`/`.rpm` installs).

Now your existing ntfy topic gets both kinds of alerts — validator events from us, and "monitor itself is down" from healthchecks.io. No second app, no second account beyond healthchecks.io.

Alternatives if you'd rather not use healthchecks.io:

- **Better Stack** (free, 10 monitors) — modern UX, but routes notifications through their channels; takes one extra step to hook into ntfy.
- **Self-hosted [uptime-kuma](https://github.com/louislam/uptime-kuma)** — Docker one-liner if you already run docker-compose somewhere.

The `heartbeat_url` config field is provider-agnostic. We just POST to whatever URL you give us; any heartbeat-compatible service works.

## Push notifications (ntfy)

If `ntfy_topic` is set in `config.yml`, `check` POSTs to that topic on every **new** alert transition.

The fastest way to wire this up is `eth-validator-stats init`, which generates an unguessable topic and sends a verification push so you can confirm delivery. To wire one up by hand:

1. Install the **ntfy** app (Play Store, App Store, F-Droid).
2. In the app: Subscribe → enter an unguessable topic name (e.g. `eth-vstats-9f8e7d6c5b4a`). Anyone who knows the name can read pushes, so treat it like a secret.
3. Set `alerts.ntfy_topic: "https://ntfy.sh/<your-topic>"` in `config.yml`.
4. Run `eth-validator-stats simulate missed-attestation` to confirm the wire works.

The public `ntfy.sh` server is free and Apache-2.0 open source. To self-host, run `ntfy serve` on your own box and set `ntfy_topic` to `http://your-host:80/your-topic`.

## Running in cron

Don't shell out via `uv run` from cron — it pays the resolver cost every minute. Call the binary directly. The right path depends on how you installed:

| Install method | Binary path |
|---|---|
| `.deb` / `.rpm` | `/usr/bin/eth-validator-stats` (or just `eth-validator-stats` — it's on `$PATH`) |
| `pipx` | `~/.local/bin/eth-validator-stats` |
| From source | `/path/to/checkout/.venv/bin/eth-validator-stats` |

Example crontab (every 5 min, fall back to a desktop notification if the alert path failed):

```cron
*/5 * * * * eth-validator-stats check --missed 3 || notify-send "validator alert"
```

`check` prints one line per offender (`<index> <label>\t<rule>`) and exits **2** if any alerts fire — friendly for cron.

## Running as a service

If you installed via `.deb` or `.rpm`, the systemd unit is **already there** — `sudo eth-validator-stats init --system` writes the config and starts the service in one step.

For pipx installs, register the same unit:

```bash
sudo eth-validator-stats install-service     # system scope (recommended)
eth-validator-stats install-service --user   # or: systemctl --user unit, no sudo
sudo eth-validator-stats init --system       # writes config and starts the service
```

For source-mode users or anyone who prefers the manual scripts:

```bash
cd packaging/linux
./install-service.sh                # systemctl --user
sudo ./install-service.sh --system  # system scope
```

Cron and the service unit don't conflict, but running both will double the load on your beacon node. Pick one.

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `BEACON_NODE_URL` | `http://localhost:3500` | Beacon node HTTP endpoint. Wins over `beacon_node_url` in config. |
| `BEACON_NODE_AUTH_TOKEN` | (none) | Optional Bearer token for hosted providers / proxied nodes. Wins over `beacon_auth_token` in config. |
| `ETH_VALIDATOR_STATS_CONFIG` | auto-discovered (see below) | Override config path. When set, wins over both `/etc/` and `~/.config/`. |
| `ETH_VALIDATOR_STATS_STATE` | `$XDG_DATA_HOME/eth-validator-stats/state.json` | Override the state-file path. |
| `ETH_VALIDATOR_STATS_LOG_LEVEL` | `INFO` | One of DEBUG / INFO / WARNING / ERROR. CLI `--log-level` wins over this. |

### Config lookup order

When **reading** config, the CLI checks in order and uses the first match:

1. `$ETH_VALIDATOR_STATS_CONFIG` — explicit override (used by the systemd unit and tests).
2. `/etc/eth-validator-stats/config.yml` — system-wide, written by `init --system` and the `.deb` / `.rpm` post-install scripts. **If you installed via a distro package, this is where your config lives, and it's found regardless of which user invokes the CLI.**
3. `~/.config/eth-validator-stats/config.yml` — per-user XDG default, written by `init` (without `--system`).

When **writing** (`init`), the system path is used only with `--system`; otherwise writes go to the per-user XDG path. A per-user config is never accidentally overshadowed by a system config you didn't intend to write.

## What the CLI asks your beacon node

Each invocation makes at most three calls:

- `GET /eth/v1/beacon/headers/head` — current slot.
- `POST /eth/v1/beacon/states/head/validators` — status + balance for all configured validators in one call (POST form has no 64-id cap).
- `POST /eth/v1/validator/liveness/{current_epoch - 1}` — did each validator participate in the just-finished epoch?

The first run also fetches `/eth/v1/beacon/genesis` and `/eth/v1/config/spec` once and caches them in the state file.

Liveness answers "the validator was seen in the epoch", which is what most people mean when they ask "did I miss an attestation". On-chain head/target correctness is planned for v2 (via `POST /eth/v1/beacon/rewards/attestations/{epoch}` on finalized epochs).

The state file at `~/.local/share/eth-validator-stats/state.json` keeps a per-validator rolling buffer of the last ~10 epochs of liveness across runs. The "last 5 attestations" column is sparse-by-design — it shows the last 5 epochs the CLI has *observed*, not the last 5 epochs of chain history. Run frequently (cron at 1–5 min is fine) and the buffer stays current.

## What it does NOT do (yet)

- No live TUI / dashboard. `watch` is a headless service-mode loop, not a re-rendered terminal UI.
- No historical query command — `status` is current-slot, `check` is recent-buffer (~10 epochs). For long-range history, use a block explorer or beaconcha.in.
- No on-chain head/target attestation-correctness scoring (only "seen / not seen" via the liveness endpoint).
- No Telegram / Discord / email transport — ntfy only. Telegram is the planned follow-up.

Open an issue or PR if any of these matter to you.
