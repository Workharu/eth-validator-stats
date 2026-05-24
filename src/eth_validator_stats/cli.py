from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from rich.console import Console

from .alerts import (
    AlertsConfig,
    clear_blind_if_recovered,
    make_notifier,
    process_blind,
    process_proposal_outcomes,
    process_upcoming_proposals,
    process_validator_alerts,
    process_withdrawals,
    prune_scheduled_proposals,
    record_scheduled_proposals,
)
from .beacon import BeaconClient, ChainInfo, ValidatorInfo, epoch_of
from .config_io import AppConfig, ConfigEntry, config_path, load_config
from .render import DisplayRow, build_table

LIVENESS_BUFFER_LEN = 10
N_ATTS_DISPLAYED = 5


def state_path() -> Path:
    override = os.environ.get("ETH_VALIDATOR_STATS_STATE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "eth-validator-stats" / "state.json"


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"chain_info": None, "validators": {}}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


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
                sys.stderr.write(f"warning: proposer duties fetch failed: {e}\n")

        liveness_result = client.get_liveness(target_liveness_epoch, indices) if indices else {}
        if liveness_result is None:
            if not state.get("liveness_unsupported_warned"):
                sys.stderr.write(
                    "warning: beacon node does not implement /eth/v1/validator/liveness — "
                    "'last N attestations' will remain blank. Run 'eth-validator-stats info' "
                    "for client diagnostics.\n"
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
            sys.stderr.write(f"warning: beacon node did not return validator {entry.identifier}\n")
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
    return rows


def evaluate_alerts(rows: list[DisplayRow], missed_threshold: int) -> list[tuple[int, str, str]]:
    """Return [(index, label, rule), ...] for validators that should alert."""
    alerts: list[tuple[int, str, str]] = []
    for r in rows:
        if r.status != "active_ongoing" and not r.status.startswith("pending"):
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
    cfg = load_config(config_path())
    console = Console()

    auth_repr = "Bearer (***)" if cfg.beacon_auth_token else "none"
    console.print(f"[bold]Beacon node probe[/bold]")
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


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(config_path())
    state = load_state(state_path())
    rows = poll(cfg, state)
    save_state(state_path(), state)
    Console().print(build_table(rows, n_atts=N_ATTS_DISPLAYED))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    cfg = load_config(config_path())
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
        # Outcome verification needs another beacon client call for headers
        try:
            with BeaconClient(cfg.beacon_node_url, auth_token=cfg.beacon_auth_token or None) as bc:
                process_proposal_outcomes(state, current_slot, bc.get_block_header_at_slot, notifier)
        except (httpx.HTTPError, OSError) as e:
            sys.stderr.write(f"warning: proposal outcome verification failed: {e}\n")
        # Drop verified proposals older than ~1000 slots (~3.3h) to keep state.json bounded.
        prune_scheduled_proposals(state, current_slot)

    save_state(state_path(), state)

    if not alerts:
        return 0
    for idx, label, rule in alerts:
        label_part = f" {label}" if label else ""
        print(f"{idx}{label_part}\t{rule}")
    return 2


def cmd_init(args: argparse.Namespace) -> int:
    from .config_io import config_path as _cfg_path, legacy_toml_path
    from .onboarding import WizardArgs, run_wizard

    cfg_path = _cfg_path()
    legacy = legacy_toml_path()

    # Step 0 — Existing-config check
    yml_exists = cfg_path.exists()
    toml_exists = legacy.exists()

    if args.migrate:
        from .config_io import migrate_from_toml
        if not toml_exists:
            print(f"no legacy TOML config to migrate at {legacy}", file=sys.stderr)
            return 1
        if yml_exists and not args.force:
            print(
                f"{cfg_path} already exists. Move/delete it or rerun with --force.",
                file=sys.stderr,
            )
            return 1
        if yml_exists and args.force:
            cfg_path.unlink()
        backup = migrate_from_toml(legacy, cfg_path)
        print(f"✓ migrated. legacy file backed up to {backup}")
        return 0

    if yml_exists and not args.force:
        print(
            f"config already exists at {cfg_path}. "
            f"Run with --force to overwrite, or edit the file directly."
        )
        return 0

    if toml_exists and not yml_exists and not args.force:
        print(f"found legacy TOML config at {legacy}")
        ans = input("Migrate to YAML now? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            from .config_io import migrate_from_toml
            backup = migrate_from_toml(legacy, cfg_path)
            print(f"✓ migrated. legacy file backed up to {backup}")
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
    return run_wizard(w, cfg_path=cfg_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eth-validator-stats",
        description="Tiny self-hosted CLI for Ethereum validator stats.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print a snapshot table of all configured validators.")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="Cron mode: print offenders, exit 2 if any.")
    p_check.add_argument("--missed", type=int, default=None, help="Consecutive missed attestations to alert on (overrides alerts.missed_attestations_threshold in config; config default is 2).")
    p_check.set_defaults(func=cmd_check)

    p_info = sub.add_parser("info", help="Probe the beacon node and report client/version + endpoint support.")
    p_info.set_defaults(func=cmd_info)

    p_init = sub.add_parser("init", help="Interactive (or flag-driven) onboarding wizard.")
    host_group = p_init.add_mutually_exclusive_group()
    host_group.add_argument("--host", help="Beacon node host to scan (mutually exclusive with --beacon-url).")
    host_group.add_argument("--beacon-url", help="Full beacon node URL, skip the scan.")
    p_init.add_argument("--auth-token", help="Bearer token for beacon API auth.")
    p_init.add_argument("--validator", help="Pubkey or index of the starter validator.")
    p_init.add_argument("--label", help="Label for the starter validator.")
    ntfy_group = p_init.add_mutually_exclusive_group()
    ntfy_group.add_argument("--ntfy-topic", help="ntfy topic name or full URL.")
    ntfy_group.add_argument("--no-ntfy", action="store_true", help="Skip notification setup.")
    p_init.add_argument("--yes", action="store_true", help="Accept defaults and skip confirmation prompts.")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config.")
    p_init.add_argument("--migrate", action="store_true", help="Only migrate legacy TOML to YAML, then exit.")
    p_init.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
