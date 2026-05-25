from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from enum import Enum as _Enum
from pathlib import Path

# Importing readline (when available) hooks GNU readline into every
# subsequent built-in input() call in the process, enabling cursor
# movement (left/right arrows), Ctrl-A/E/U line editing, and proper
# backspace. Without it the wizard prints raw escape sequences like
# `^[[D` when the user hits an arrow key, which is jarring during
# `init` when they're typing the beacon node URL. readline is stdlib
# on Linux/macOS; Windows builds may not ship it, hence the guard.
try:
    import readline  # noqa: F401 — imported for side effect only
except ImportError:  # pragma: no cover — Windows / minimal Python builds
    pass

import click  # for exception types; transitive via typer
import httpx
import typer
from rich.console import Console

from ._simulate import EVENTS
from .alerts import (
    NtfyNotifier,
    clear_blind_if_recovered,
    make_notifier,
    post_heartbeat_url,
    process_blind,
    process_lifecycle_alerts,
    process_proposal_outcomes,
    process_upcoming_proposals,
    process_validator_alerts,
    process_withdrawals,
    prune_scheduled_proposals,
    record_scheduled_proposals,
    send_daily_heartbeat,
)
from .beacon import BeaconClient, ChainInfo, ValidatorInfo, epoch_of
from .config_io import AppConfig, ConfigEntry, load_config
from .onboarding import WizardArgs, run_wizard
from .render import DisplayRow, build_table

logger = logging.getLogger(__name__)

LIVENESS_BUFFER_LEN = 10
N_ATTS_DISPLAYED = 5
SYSTEM_CONFIG_PATH = Path("/etc/eth-validator-stats/config.yml")
SYSTEM_STATE_DIR = Path("/var/lib/eth-validator-stats")


def state_path() -> Path:
    override = os.environ.get("ETH_VALIDATOR_STATS_STATE")
    if override:
        return Path(override)
    # When the system install directory is present and readable, every
    # caller (root or unprivileged) should converge on the same file the
    # systemd watch service writes to. Without this, `evs status`,
    # `sudo evs status`, and the watcher each maintain their own copy.
    if SYSTEM_STATE_DIR.is_dir() and os.access(SYSTEM_STATE_DIR, os.R_OK):
        return SYSTEM_STATE_DIR / "state.json"
    import platformdirs
    return platformdirs.user_data_path("eth-validator-stats") / "state.json"


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"chain_info": None, "validators": {}}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique tmp name so two writers (interactive `status` + the `watch`
    # loop) can't collide on the same `.json.tmp` and corrupt each other.
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(state, indent=2))
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def chain_info_from_state(state: dict) -> ChainInfo | None:
    ci = state.get("chain_info")
    if not ci:
        return None
    return ChainInfo(
        genesis_time=int(ci["genesis_time"]),
        seconds_per_slot=int(ci["seconds_per_slot"]),
        slots_per_epoch=int(ci["slots_per_epoch"]),
    )


def append_liveness(buffer: list, epoch: int, attested: bool) -> list:
    for entry in buffer:
        if int(entry[0]) == epoch:
            return buffer
    buffer.append([epoch, 1 if attested else 0])
    return buffer[-LIVENESS_BUFFER_LEN:]


def poll(cfg: AppConfig, state: dict) -> list[DisplayRow]:
    """Fetch fresh data, merge into state in place, return display rows for configured validators."""
    with BeaconClient(cfg.beacon_node_url, auth_token=cfg.beacon_auth_token or None) as client:
        info = chain_info_from_state(state)
        if info is None:
            info = client.get_chain_info()
            state["chain_info"] = {
                "genesis_time": info.genesis_time,
                "seconds_per_slot": info.seconds_per_slot,
                "slots_per_epoch": info.slots_per_epoch,
            }
        head = client.get_head()
        state["current_slot"] = head.slot
        current_epoch = epoch_of(head.slot, info)
        target_liveness_epoch = max(current_epoch - 1, 0)

        ids = [e.identifier for e in cfg.validators]
        fresh: list[ValidatorInfo] = client.get_validators(ids)
        indices = [v.index for v in fresh]

        # Once per epoch boundary, fetch proposer duties for current + next epoch.
        last_duties_epoch = int(state.get("last_duties_epoch", -1))
        if current_epoch > last_duties_epoch and indices:
            configured_set = set(indices)
            try:
                duties = client.get_proposer_duties(current_epoch)
                duties += client.get_proposer_duties(current_epoch + 1)
                record_scheduled_proposals(state, duties, configured_set)
                state["last_duties_epoch"] = current_epoch
            except Exception as e:
                logger.warning("proposer duties fetch failed: %s", e)

        liveness_result = client.get_liveness(target_liveness_epoch, indices) if indices else {}
        if liveness_result is None:
            if not state.get("liveness_unsupported_warned"):
                logger.warning(
                    "beacon node does not implement /eth/v1/validator/liveness — "
                    "'last N attestations' will remain blank. "
                    "Run 'eth-validator-stats info' for client diagnostics."
                )
                state["liveness_unsupported_warned"] = True
            liveness: dict[int, bool] = {}
        else:
            liveness = liveness_result

    vstate = state.setdefault("validators", {})
    by_pubkey: dict[str, ValidatorInfo] = {v.pubkey.lower(): v for v in fresh}
    by_index: dict[int, ValidatorInfo] = {v.index: v for v in fresh}

    rows: list[DisplayRow] = []
    for entry in cfg.validators:
        info_v: ValidatorInfo | None = None
        if entry.pubkey is not None:
            info_v = by_pubkey.get(entry.pubkey.lower())
        if info_v is None and entry.index is not None:
            info_v = by_index.get(entry.index)
        if info_v is None:
            logger.warning("beacon node did not return validator %s", entry.identifier)
            continue

        key = str(info_v.index)
        record = vstate.setdefault(key, {})
        # Snapshot the prior balance so process_withdrawals() can detect drops.
        # Also stash the slot at which we observed it so we can refuse to attribute
        # very-old drops to a withdrawal (process_withdrawals' gap check).
        prev_balance = record.get("last_balance_gwei")
        if prev_balance is not None:
            record["previous_balance_gwei"] = int(prev_balance)
            prev_slot = record.get("last_balance_at_slot")
            if prev_slot is not None:
                record["previous_balance_at_slot"] = int(prev_slot)
        # Snapshot the prior status so process_lifecycle_alerts can detect
        # transitions (pending->active, active->slashed, etc.). First poll
        # has no previous status, so transitions can't fire on day one.
        prev_status = record.get("last_status")
        if prev_status:
            record["previous_status"] = prev_status
        record["pubkey"] = info_v.pubkey
        record["label"] = entry.label
        record["last_status"] = info_v.status
        record["last_balance_gwei"] = info_v.balance_gwei
        record["last_balance_at_slot"] = head.slot
        buffer = record.get("liveness", [])
        if info_v.index in liveness:
            buffer = append_liveness(buffer, target_liveness_epoch, liveness[info_v.index])
        record["liveness"] = buffer

        rows.append(
            DisplayRow(
                index=info_v.index,
                label=entry.label,
                status=info_v.status,
                balance_gwei=info_v.balance_gwei,
                liveness=[(int(e), int(a)) for e, a in buffer],
            )
        )
    state["last_poll_ts"] = int(time.time())
    return rows


def evaluate_alerts(rows: list[DisplayRow], missed_threshold: int) -> list[tuple[int, str, str]]:
    """Return [(index, label, rule), ...] for validators that should alert.

    Status semantics (Beacon API):
      pending_initialized / pending_queued — deposit accepted but the
        validator has not yet been activated. It has no committee
        assignment, so the liveness endpoint won't list it and any
        "missed" attestations are expected. Not OFFLINE, not missed.
      active_ongoing — the only state in which we count attestations.
        active_exiting / active_slashed are technically still required
        to attest but are on their way out; treat them as OFFLINE so
        the operator sees the transition.
      anything else (exited_*, withdrawal_*) — OFFLINE.
    """
    alerts: list[tuple[int, str, str]] = []
    for r in rows:
        if r.status.startswith("pending"):
            # Not yet validating — nothing to alert on.
            continue
        if r.status != "active_ongoing":
            alerts.append((r.index, r.label, f"OFFLINE status={r.status}"))
            continue
        tail = r.liveness[-missed_threshold:]
        if len(tail) >= missed_threshold and all(a == 0 for _, a in tail):
            alerts.append((r.index, r.label, f"MISSED_ATTESTATIONS last={missed_threshold}"))
    return alerts


def _probe_endpoint(label: str, fn) -> tuple[str, str, str]:
    """Returns (label, status, detail). status in {'OK','UNSUPPORTED','ERROR'}."""
    try:
        detail = fn()
        return (label, "OK", detail or "")
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 405, 501):
            return (label, "UNSUPPORTED", f"HTTP {e.response.status_code}")
        return (label, "ERROR", f"HTTP {e.response.status_code}")
    except Exception as e:
        return (label, "ERROR", f"{type(e).__name__}: {e}")


def cmd_info(args: argparse.Namespace) -> int:
    cfg = load_config()
    console = Console()

    auth_repr = "Bearer (***)" if cfg.beacon_auth_token else "none"
    console.print("[bold]Beacon node probe[/bold]")
    console.print(f"  URL : {cfg.beacon_node_url}")
    console.print(f"  Auth: {auth_repr}")

    sample_id = cfg.validators[0].identifier if cfg.validators else "1"

    with BeaconClient(cfg.beacon_node_url, auth_token=cfg.beacon_auth_token or None) as client:
        results = []
        results.append(_probe_endpoint(
            "GET  /eth/v1/node/version",
            lambda: client.get_node_version().version,
        ))
        results.append(_probe_endpoint(
            "GET  /eth/v1/beacon/genesis + /eth/v1/config/spec",
            lambda: (
                lambda ci: f"genesis={ci.genesis_time}, slot={ci.seconds_per_slot}s, epoch={ci.slots_per_epoch} slots"
            )(client.get_chain_info()),
        ))
        head_result = _probe_endpoint(
            "GET  /eth/v1/beacon/headers/head",
            lambda: f"slot={client.get_head().slot}",
        )
        results.append(head_result)
        results.append(_probe_endpoint(
            "POST /eth/v1/beacon/states/head/validators",
            lambda: f"returned {len(client.get_validators([sample_id]))} validator(s) for sample id={sample_id}",
        ))

        # Liveness probe: pick a recent epoch. If head failed we skip.
        liveness_label = "POST /eth/v1/validator/liveness/{epoch}"
        if head_result[1] == "OK":
            try:
                ci = client.get_chain_info()
                head = client.get_head()
                probe_epoch = max(epoch_of(head.slot, ci) - 1, 0)
                outcome = client.get_liveness(probe_epoch, [1])
                if outcome is None:
                    results.append((liveness_label, "UNSUPPORTED", f"epoch {probe_epoch}: client returns 404/405"))
                else:
                    results.append((liveness_label, "OK", f"epoch {probe_epoch}: {len(outcome)} entries"))
            except Exception as e:
                results.append((liveness_label, "ERROR", f"{type(e).__name__}: {e}"))
        else:
            results.append((liveness_label, "SKIPPED", "head probe failed; cannot determine epoch"))

    console.print("\n[bold]Endpoints[/bold]")
    color = {"OK": "green", "UNSUPPORTED": "yellow", "SKIPPED": "yellow", "ERROR": "red"}
    for label, status, detail in results:
        col = color.get(status, "white")
        console.print(f"  [{col}]{status:12}[/{col}] {label}")
        if detail:
            console.print(f"               [dim]{detail}[/dim]")

    return 0 if all(r[1] == "OK" for r in results) else 1


def _rows_from_state(cfg: AppConfig, state: dict) -> list[DisplayRow]:
    """Render-only path: reconstruct DisplayRows from persisted state without
    contacting the beacon node. Used by `evs status` so it never races the
    `watch` service or writes a stale snapshot back over fresher data."""
    vstate = state.get("validators", {})
    rows: list[DisplayRow] = []
    for entry in cfg.validators:
        key: str | None = None
        record: dict | None = None
        # Match by pubkey first (canonical), index second (legacy entries).
        if entry.pubkey is not None:
            pk = entry.pubkey.lower()
            for k, rec in vstate.items():
                if rec.get("pubkey", "").lower() == pk:
                    key, record = k, rec
                    break
        if record is None and entry.index is not None:
            idx_key = str(entry.index)
            rec = vstate.get(idx_key)
            if rec is not None:
                key, record = idx_key, rec
        if record is None or key is None:
            continue
        rows.append(
            DisplayRow(
                index=int(key),
                label=entry.label,
                status=str(record.get("last_status", "unknown")),
                balance_gwei=int(record.get("last_balance_gwei", 0)),
                liveness=[(int(e), int(a)) for e, a in record.get("liveness", [])],
            )
        )
    return rows


def _format_staleness(last_poll_ts: int | None, now_ts: int) -> str | None:
    """Return a short 'last updated' string, or None if no poll has happened.
    Granularity is coarse on purpose — operators want 'is it alive?' not exact seconds."""
    if not last_poll_ts:
        return None
    delta = max(0, now_ts - int(last_poll_ts))
    if delta < 60:
        human = f"{delta}s ago"
    elif delta < 3600:
        human = f"{delta // 60}m ago"
    elif delta < 86400:
        human = f"{delta // 3600}h ago"
    else:
        human = f"{delta // 86400}d ago"
    return f"last updated: {human}"


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    state = load_state(state_path())
    refresh = getattr(args, "refresh", False)
    console = Console()

    if refresh:
        rows = poll(cfg, state)
        save_state(state_path(), state)
    else:
        rows = _rows_from_state(cfg, state)
        if not rows and not state.get("last_poll_ts"):
            console.print(
                "[yellow]No state on disk yet.[/yellow]\n"
                "  - Start the watcher:    [bold]sudo systemctl start eth-validator-stats[/bold]\n"
                "  - Or run one check:     [bold]evs check[/bold]\n"
                "  - Or poll inline now:   [bold]evs status --refresh[/bold]"
            )
            return 0

    console.print(build_table(rows, n_atts=N_ATTS_DISPLAYED))
    footer = _format_staleness(state.get("last_poll_ts"), int(time.time()))
    if footer:
        console.print(f"[dim]{footer}[/dim]")
    return 0


def run_check_once(args: argparse.Namespace) -> int:
    """Execute one full scan + alert cycle.

    Returns the same exit-code semantics as `cmd_check`: 0 = clean,
    2 = alerts fired or monitor blind. Extracted so the long-running
    `watch` command can call it in a loop. Behavior is identical to
    the pre-refactor `cmd_check`.
    """
    cfg = load_config()
    state = load_state(state_path())
    notifier = make_notifier(cfg.alerts)
    now = int(time.time())

    try:
        rows = poll(cfg, state)
    except (httpx.HTTPError, OSError) as e:
        process_blind(state, str(e), notifier, cfg.alerts, now)
        save_state(state_path(), state)
        print(f"MONITOR_BLIND\t{e}", file=sys.stderr)
        return 2

    clear_blind_if_recovered(state, notifier)

    # CLI flag wins over config; otherwise use config default (2).
    missed_threshold = args.missed if args.missed is not None else cfg.alerts.missed_attestations_threshold

    alerts = evaluate_alerts(rows, missed_threshold)
    configured = {row.index for row in rows}
    process_validator_alerts(state, configured, alerts, notifier, cfg.alerts, now)
    # Lifecycle transitions (ACTIVATED / EXIT INITIATED / SLASHED / EXITED /
    # WITHDRAWAL READY) are independent of the OFFLINE / MISSED churn —
    # they fire one-shot per transition off the (previous_status,
    # last_status) pair that poll() just refreshed.
    process_lifecycle_alerts(state, configured, notifier)

    current_slot = int(state.get("current_slot", 0))
    info = chain_info_from_state(state)
    process_withdrawals(state, configured, notifier, cfg.alerts, current_slot)

    if info and current_slot > 0:
        process_upcoming_proposals(
            state, current_slot,
            info.seconds_per_slot, info.slots_per_epoch,
            cfg.alerts.proposal_lookahead_epochs,
            notifier,
        )
        try:
            with BeaconClient(cfg.beacon_node_url, auth_token=cfg.beacon_auth_token or None) as bc:
                process_proposal_outcomes(state, current_slot, bc.get_block_header_at_slot, notifier)
        except (httpx.HTTPError, OSError) as e:
            logger.warning("proposal outcome verification failed: %s", e)
        prune_scheduled_proposals(state, current_slot)

    # Liveness layer: daily "monitor alive" push at the configured local
    # hour, and (if configured) a POST to a third-party heartbeat URL.
    # Both no-op when not enabled.
    n_active = sum(1 for r in rows if r.status == "active_ongoing")
    send_daily_heartbeat(
        state,
        notifier,
        cfg.alerts,
        rows_summary=f"{len(rows)} validators tracked, {n_active} active_ongoing",
    )
    post_heartbeat_url(cfg.alerts)

    save_state(state_path(), state)

    if not alerts:
        return 0
    for idx, label, rule in alerts:
        label_part = f" {label}" if label else ""
        print(f"{idx}{label_part}\t{rule}")
    return 2


def cmd_check(args: argparse.Namespace) -> int:
    return run_check_once(args)


def cmd_init(args: argparse.Namespace) -> int:
    from .config_io import config_path as _cfg_path

    # Auto-promote `sudo init` to `--system` when the system already
    # expects to find a config at /etc (i.e. the eth-validator-stats
    # service user exists, which means a .deb/.rpm/install-service has
    # populated the system). Without this, running `sudo init` writes
    # to /root/.config/... (because HOME=/root under sudo), which the
    # systemd unit can never see — its ConditionPathExists guards
    # against exactly that path. The escape hatch for the rare case
    # where root really wants a per-user config is to set
    # ETH_VALIDATOR_STATS_CONFIG explicitly.
    if not args.system and os.geteuid() == 0:
        import pwd
        try:
            pwd.getpwnam("eth-validator-stats")
        except KeyError:
            pass  # No service user → no system install → keep per-user default.
        else:
            if not os.environ.get("ETH_VALIDATOR_STATS_CONFIG"):
                print(
                    "note: running as root and the eth-validator-stats service "
                    "user exists; writing the system config at "
                    f"{SYSTEM_CONFIG_PATH} (use ETH_VALIDATOR_STATS_CONFIG=... "
                    "for a custom location)."
                )
                args.system = True

    if args.system:
        if os.geteuid() != 0:
            print(
                "error: --system requires root (re-run with sudo).",
                file=sys.stderr,
            )
            raise SystemExit(1)
        import pwd
        try:
            pwd.getpwnam("eth-validator-stats")
        except KeyError:
            print(
                "error: system user 'eth-validator-stats' does not exist. "
                "Install the distro package first (apt install eth-validator-stats "
                "or dnf install eth-validator-stats).",
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        cfg_path = SYSTEM_CONFIG_PATH
    else:
        cfg_path = _cfg_path()

    if cfg_path.exists() and not args.force:
        print(
            f"config already exists at {cfg_path}. "
            f"Run with --force to overwrite, or edit the file directly."
        )
        return 0

    w = WizardArgs(
        host=args.host,
        beacon_url=args.beacon_url,
        auth_token=args.auth_token,
        validator=args.validator,
        label=args.label,
        ntfy_topic=args.ntfy_topic,
        no_ntfy=args.no_ntfy,
        yes=args.yes,
        force=args.force,
    )

    # Decide write perms BEFORE the wizard runs, so write_config applies
    # them atomically (mode + ownership on the tmp file, then rename).
    # No more post-write chmod/chown that briefly exposes a 0600 file.
    #
    # System install: 0644 (world-readable). Config contents are
    # low-sensitivity — validator pubkeys/indices are public on chain,
    # beacon URLs are local, ntfy topic is unguessable but low-value.
    # If you put a beacon_auth_token for a hosted provider in here,
    # tighten to 0640 manually.
    write_mode = 0o600
    write_uid: int | None = None
    write_gid: int | None = None
    if args.system:
        import pwd as _pwd
        write_mode = 0o644
        ev = _pwd.getpwnam("eth-validator-stats")
        write_uid, write_gid = ev.pw_uid, ev.pw_gid

    rc = run_wizard(
        w,
        cfg_path=cfg_path,
        write_mode=write_mode,
        write_uid=write_uid,
        write_gid=write_gid,
    )

    if rc == 0 and args.system:
        _maybe_start_systemd_service()

    if rc == 0:
        # Surface the short alias right when the user is about to type
        # their first real command. Most users only ever discover `evs`
        # by reading the README.
        print(
            "\nTip: `evs` is a 3-character alias for eth-validator-stats. "
            "Try `evs status` or `evs check`.\n"
        )

    return rc


def _maybe_start_systemd_service() -> None:
    """Start (or restart) eth-validator-stats.service if the unit is installed.

    Best-effort and non-fatal. The .deb / .rpm postinst enables the unit
    on install but can't start it — the unit's `ConditionPathExists` guard
    rejects start attempts until `/etc/eth-validator-stats/config.yml`
    exists. After `init --system` has just written that file, we can
    immediately bring the service up so the user doesn't have to
    remember a separate `systemctl start`.

    Uses `restart` (not `start`) so that re-running `init --system
    --force` against an already-running service picks up the new config
    automatically. `restart` on a stopped service is equivalent to
    `start`, so this is safe in both first-install and re-init paths.

    Silently no-ops when:
      - systemctl is not on PATH (non-systemd host: macOS, certain
        containers, pure-pipx desktops)
      - the eth-validator-stats.service unit is not installed (e.g.
        pipx user who did not run `install-service`)
    """
    import shutil as _shutil
    import subprocess

    systemctl = _shutil.which("systemctl")
    if systemctl is None:
        return

    # Is the unit known to systemd?
    if subprocess.run(
        [systemctl, "cat", "eth-validator-stats.service"],
        capture_output=True,
    ).returncode != 0:
        return

    result = subprocess.run(
        [systemctl, "restart", "eth-validator-stats"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("✓ eth-validator-stats.service started")
    else:
        err = result.stderr.strip() or f"systemctl exited {result.returncode}"
        print(
            f"note: failed to auto-start eth-validator-stats.service: {err}\n"
            f"      Run `sudo systemctl start eth-validator-stats` after fixing.",
            file=sys.stderr,
        )


def _resolve_simulate_validator(cfg: AppConfig, requested_idx: int | None) -> ConfigEntry:
    """Pick a configured validator for simulate.

    Default: first entry. If --validator was passed, find by index.
    Raises ValueError on mismatch; cmd_simulate converts to exit code + stderr.
    """
    if requested_idx is not None:
        for v in cfg.validators:
            if v.index == requested_idx:
                return v
        raise ValueError(f"no configured validator with index {requested_idx}")
    first = cfg.validators[0]
    if first.index is None:
        raise ValueError(
            "first configured validator has no resolved index — "
            "run `eth-validator-stats status` once to populate state, "
            "or pass --validator <idx> explicitly"
        )
    return first


def cmd_simulate(args: argparse.Namespace, *, _notifier=None) -> int:
    """Fire one ntfy push using the exact alert template for the chosen event.

    No state mutation, no cooldown, no dedup — pure send. The `_notifier`
    kwarg is a test seam; production callers go through argparse which
    never sets it, so the real NtfyNotifier is constructed below.
    """
    cfg = load_config()

    builder, scope = EVENTS[args.event]

    notifier = _notifier
    if notifier is None:
        if not cfg.alerts.ntfy_topic:
            print(
                "simulate: no ntfy_topic configured. "
                "Run `eth-validator-stats init` first, or set alerts.ntfy_topic in config.yml.",
                file=sys.stderr,
            )
            return 1
        notifier = NtfyNotifier(
            cfg.alerts.ntfy_topic,
            timeout=cfg.alerts.request_timeout_s,
            raise_on_error=True,
            icon_url=cfg.alerts.icon_url,
        )

    # Build (title, body) for the event.
    try:
        if scope == "validator":
            v = _resolve_simulate_validator(cfg, args.validator)
            kwargs = _simulate_kwargs(args)
            title, body = builder(v.index, v.label, **kwargs)
        else:
            title, body = builder()
    except ValueError as e:
        print(f"simulate: {e}", file=sys.stderr)
        return 1

    # Mirror the priority the production pipeline would set so the
    # simulate output is faithful to what a real transition looks like
    # on the operator's phone. Only SLASHED carries `urgent` today.
    priority = "urgent" if args.event == "slashed" else None
    try:
        notifier.send(title, body, priority=priority)
    except Exception as e:
        print(f"simulate failed: {e}", file=sys.stderr)
        return 1

    print(f"sent: {title} | {body}")
    return 0


def _simulate_kwargs(args: argparse.Namespace) -> dict:
    """Pick only the kwargs relevant to the requested event from args.

    Argparse collects every flag on the same Namespace; this filter keeps
    the builder signatures honest (they don't accept **extra)."""
    relevant = {
        "missed-attestation": {"last"},
        "offline": {"status"},
        "withdrawal": {"amount_eth"},
        "proposing-soon": {"slot", "delay"},
        "proposed": {"slot"},
        "missed-proposal": {"slot"},
    }
    keys = relevant.get(args.event, set())
    out: dict = {}
    for k in keys:
        v = getattr(args, k, None)
        if v is not None:
            out[k] = v
    return out


def configure_logging(level_name: str | None) -> None:
    """Configure the root logger once at process start.

    Resolution order: explicit arg > env var > INFO.
    Output is timestamped UTC, level- and logger-name-tagged, on stderr.
    Calling more than once is supported — the level is always re-applied,
    while basicConfig() is a no-op if any handler is already present.
    """
    level_str = level_name or os.environ.get("ETH_VALIDATOR_STATS_LOG_LEVEL") or "INFO"
    level = getattr(logging, level_str.upper(), logging.INFO)
    logging.Formatter.converter = time.gmtime  # UTC timestamps
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stderr,
    )
    # basicConfig() is a no-op if handlers already exist (e.g. under pytest).
    # Re-apply the level explicitly so --log-level still controls verbosity.
    logging.getLogger().setLevel(level)


def _get_version() -> str:
    """Read the installed package version from its dist metadata.

    Returns 'unknown' as a defensive fallback in the unlikely case the
    package metadata is missing (e.g. running directly from a checkout
    without an editable install).
    """
    try:
        from importlib.metadata import version as _v
        return _v("eth-validator-stats")
    except Exception:
        return "unknown"


def _version_callback(value: bool) -> None:
    """Eager `--version` callback. Prints `<prog> <semver>` and exits 0."""
    if value:
        typer.echo(f"eth-validator-stats {_get_version()}")
        raise typer.Exit()


class _LogLevel(str, _Enum):
    """Valid values for --log-level. Mirrors argparse's old `choices=[...]`."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# Generated dynamically from the EVENTS dict so the two cannot drift. Member
# names are the event keys with hyphens swapped for underscores (Python identifier
# rules); each member's .value is the original key string, which cmd_simulate
# uses to look up EVENTS.
_SimEvent = _Enum(
    "SimEvent",
    {k.replace("-", "_"): k for k in EVENTS.keys()},
    type=str,
)


app = typer.Typer(
    help="Ethereum validator monitor. Polls a beacon node and pushes alerts via ntfy.",
    rich_markup_mode="rich",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.callback()
def _root(
    ctx: typer.Context,
    log_level: _LogLevel | None = typer.Option(
        None, "--log-level",
        help="Log level for stderr output (default: INFO; override via ETH_VALIDATOR_STATS_LOG_LEVEL).",
        case_sensitive=False,
    ),
    version: bool = typer.Option(
        False, "--version",
        help="Print the installed package version and exit.",
        callback=_version_callback, is_eager=True,
    ),
) -> None:
    """Root callback: applies --log-level, handles --version eagerly."""
    configure_logging(log_level.value if log_level is not None else None)


@app.command()
def status(
    refresh: bool = typer.Option(
        False, "--refresh",
        help="Poll the beacon node and update state before rendering. "
             "Avoid when the watch service is running — they will race on the state file.",
    ),
) -> None:
    """Render the latest snapshot from on-disk state (read-only).

    Pass --refresh to also poll the beacon node first."""
    rc = cmd_status(argparse.Namespace(refresh=refresh))
    raise typer.Exit(code=rc)


@app.command()
def check(
    missed: int | None = typer.Option(
        None, "--missed",
        help="Consecutive missed attestations to alert on (overrides "
             "alerts.missed_attestations_threshold in config; config default is 2).",
    ),
) -> None:
    """Cron mode: print offenders, exit 2 if any."""
    rc = cmd_check(argparse.Namespace(missed=missed))
    raise typer.Exit(code=rc)


@app.command()
def watch(
    interval: float = typer.Option(60.0, "--interval", help="Seconds between check iterations (default: 60)."),
    missed: int | None = typer.Option(None, "--missed", help="Consecutive missed attestations to alert on (passed through to each iteration)."),
) -> None:
    """Long-running service mode: loop the check cycle until signalled."""
    from ._watch import cmd_watch as _cmd_watch
    rc = _cmd_watch(argparse.Namespace(interval=interval, missed=missed))
    raise typer.Exit(code=rc)


@app.command()
def info() -> None:
    """Probe the beacon node and report client/version + endpoint support."""
    rc = cmd_info(argparse.Namespace())
    raise typer.Exit(code=rc)


@app.command()
def init(
    host: str | None = typer.Option(None, "--host", help="Beacon node host to scan (mutually exclusive with --beacon-url)."),
    beacon_url: str | None = typer.Option(None, "--beacon-url", help="Full beacon node URL, skip the scan."),
    auth_token: str | None = typer.Option(None, "--auth-token", help="Bearer token for beacon API auth."),
    validator: str | None = typer.Option(None, "--validator", help="Pubkey or index of the starter validator."),
    label: str | None = typer.Option(None, "--label", help="Label for the starter validator."),
    ntfy_topic: str | None = typer.Option(None, "--ntfy-topic", help="ntfy topic name or full URL."),
    no_ntfy: bool = typer.Option(False, "--no-ntfy", help="Skip notification setup."),
    yes: bool = typer.Option(False, "--yes", help="Accept defaults and skip confirmation prompts."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config."),
    system: bool = typer.Option(
        False, "--system",
        help="Write to /etc/eth-validator-stats/config.yml and apply system-service ownership "
             "(requires root and an eth-validator-stats system user; meant for distro-package installs).",
    ),
) -> None:
    """Interactive (or flag-driven) onboarding wizard."""
    if host is not None and beacon_url is not None:
        raise click.exceptions.UsageError("--host and --beacon-url are mutually exclusive")
    if ntfy_topic is not None and no_ntfy:
        raise click.exceptions.UsageError("--ntfy-topic and --no-ntfy are mutually exclusive")
    rc = cmd_init(argparse.Namespace(
        host=host, beacon_url=beacon_url, auth_token=auth_token,
        validator=validator, label=label,
        ntfy_topic=ntfy_topic, no_ntfy=no_ntfy,
        yes=yes, force=force, system=system,
    ))
    raise typer.Exit(code=rc)


@app.command("install-service")
def install_service(
    user: bool = typer.Option(False, "--user", help="Install as a --user unit (no sudo). Default is system scope (needs sudo)."),
    run_as: str | None = typer.Option(None, "--run-as", help="System-scope only: override the user the service runs as (default: $SUDO_USER)."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing unit file even if it is owned by a distro package."),
) -> None:
    """Register a systemd unit for the watcher (one-time setup for pipx installs)."""
    from ._install_service import cmd_install_service as _cmd_install_svc
    rc = _cmd_install_svc(argparse.Namespace(user=user, run_as=run_as, force=force))
    raise typer.Exit(code=rc)


@app.command("uninstall-service")
def uninstall_service(
    user: bool = typer.Option(False, "--user", help="Target the --user unit (default: system scope, needs sudo)."),
    purge: bool = typer.Option(False, "--purge", help="Also delete /etc/eth-validator-stats and /var/lib/eth-validator-stats."),
) -> None:
    """Remove the systemd unit registered by `install-service`."""
    from ._install_service import cmd_uninstall_service as _cmd_uninstall_svc
    rc = _cmd_uninstall_svc(argparse.Namespace(user=user, purge=purge))
    raise typer.Exit(code=rc)


@app.command()
def simulate(
    event: _SimEvent = typer.Argument(..., help="Which alert template to fire."),
    validator: int | None = typer.Option(None, "--validator", help="Validator index to use (default: first configured)."),
    last: int | None = typer.Option(None, "--last", help="missed-attestation: N consecutive misses (default 2)."),
    status: str | None = typer.Option(None, "--status", help="offline: validator status string (default 'slashed')."),
    amount_eth: float | None = typer.Option(None, "--amount-eth", help="withdrawal: ETH amount (default 0.001)."),
    slot: int | None = typer.Option(None, "--slot", help="proposing-soon/proposed/missed-proposal: slot number (default 12345)."),
    delay: str | None = typer.Option(None, "--delay", help="proposing-soon: human-readable delay (default '~6 min')."),
) -> None:
    """Send one test ntfy push matching a real alert template. State-free."""
    rc = cmd_simulate(argparse.Namespace(
        event=event.value, validator=validator,
        last=last, status=status, amount_eth=amount_eth,
        slot=slot, delay=delay,
    ))
    raise typer.Exit(code=rc)


validators_app = typer.Typer(
    help="Add, list, or remove validators (without editing config.yml by hand).",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
app.add_typer(validators_app, name="validators")


@validators_app.command("add")
def validators_add(
    identifier: str = typer.Argument(..., help="Validator pubkey (0x...) or index."),
    label: str | None = typer.Option(None, "--label", help="Friendly label for the validator (optional)."),
    no_verify: bool = typer.Option(False, "--no-verify", help="Skip the beacon-node existence check. Saves the entry as-is."),
) -> None:
    """Add a validator by pubkey (0x...) or numeric index."""
    from ._validators_cmd import cmd_validators_add as _cmd
    rc = _cmd(argparse.Namespace(identifier=identifier, label=label, no_verify=no_verify))
    raise typer.Exit(code=rc)


@validators_app.command("list")
def validators_list(
    status: bool = typer.Option(False, "--status", help="Also hit the beacon node for live status + balance."),
) -> None:
    """Print the validators currently in the config."""
    from ._validators_cmd import cmd_validators_list as _cmd
    rc = _cmd(argparse.Namespace(status=status))
    raise typer.Exit(code=rc)


@validators_app.command("rm")
def validators_rm(
    identifier: str = typer.Argument(..., help="Index, pubkey (0x...), or label of the validator to remove."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Remove a validator (by index, pubkey, or label)."""
    from ._validators_cmd import cmd_validators_rm as _cmd
    rc = _cmd(argparse.Namespace(identifier=identifier, yes=yes))
    raise typer.Exit(code=rc)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns int exit code (does NOT raise SystemExit) so the
    existing test suite that calls `rc = main([...])` continues to work."""
    try:
        # standalone_mode=False makes Click absorb typer.Exit(code=N) internally
        # and return N as the call result instead of re-raising.
        result = app(args=argv, standalone_mode=False)
        if isinstance(result, int):
            return result
        return 0
    except click.exceptions.UsageError as e:
        e.show()
        return e.exit_code  # Click default for UsageError is 2
    except click.exceptions.ClickException as e:
        e.show()
        return e.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
