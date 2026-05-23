from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from rich.console import Console

from .alerts import (
    AlertsConfig,
    clear_blind_if_recovered,
    make_notifier,
    process_blind,
    process_validator_alerts,
)
from .beacon import BeaconClient, ChainInfo, ValidatorInfo, epoch_of
from .render import DisplayRow, build_table

DEFAULT_BEACON_URL = "http://localhost:3500"
LIVENESS_BUFFER_LEN = 10
N_ATTS_DISPLAYED = 5


@dataclass(frozen=True)
class ConfigEntry:
    identifier: str  # pubkey hex or decimal index, as a string the Beacon API accepts
    label: str
    pubkey: str | None
    index: int | None


@dataclass(frozen=True)
class AppConfig:
    beacon_node_url: str
    validators: list[ConfigEntry]
    beacon_auth_token: str = ""
    alerts: AlertsConfig = field(default_factory=AlertsConfig)


def config_path() -> Path:
    override = os.environ.get("ETH_VALIDATOR_STATS_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "eth-validator-stats" / "config.toml"


def state_path() -> Path:
    override = os.environ.get("ETH_VALIDATOR_STATS_STATE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "eth-validator-stats" / "state.json"


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise SystemExit(
            f"config file not found at {path}\n"
            "Copy config.toml.example to that path, then edit it."
        )
    raw = tomllib.loads(path.read_text())
    url = os.environ.get("BEACON_NODE_URL") or raw.get("beacon_node_url") or DEFAULT_BEACON_URL
    auth_token = os.environ.get("BEACON_NODE_AUTH_TOKEN") or str(raw.get("beacon_auth_token", "") or "")
    entries: list[ConfigEntry] = []
    for v in raw.get("validators", []):
        pubkey = v.get("pubkey")
        index = v.get("index")
        if pubkey is None and index is None:
            raise SystemExit(f"config entry missing both pubkey and index: {v!r}")
        if pubkey is not None:
            ident = pubkey
        else:
            ident = str(int(index))
        entries.append(
            ConfigEntry(
                identifier=ident,
                label=v.get("label", ""),
                pubkey=pubkey,
                index=int(index) if index is not None else None,
            )
        )
    if not entries:
        raise SystemExit("config has no [[validators]] entries")
    alerts_raw = raw.get("alerts", {}) or {}
    alerts = AlertsConfig(
        ntfy_topic=str(alerts_raw.get("ntfy_topic", "") or ""),
        cooldown_minutes=int(alerts_raw.get("cooldown_minutes", 30)),
        storm_threshold=int(alerts_raw.get("storm_threshold", 10)),
    )
    return AppConfig(
        beacon_node_url=url,
        validators=entries,
        beacon_auth_token=auth_token,
        alerts=alerts,
    )


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
        current_epoch = epoch_of(head.slot, info)
        target_liveness_epoch = max(current_epoch - 1, 0)

        ids = [e.identifier for e in cfg.validators]
        fresh: list[ValidatorInfo] = client.get_validators(ids)
        indices = [v.index for v in fresh]
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
        record["pubkey"] = info_v.pubkey
        record["label"] = entry.label
        record["last_status"] = info_v.status
        record["last_balance_gwei"] = info_v.balance_gwei
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
    save_state(state_path(), state)

    alerts = evaluate_alerts(rows, args.missed)
    configured = {row.index for row in rows}
    process_validator_alerts(state, configured, alerts, notifier, cfg.alerts, now)
    save_state(state_path(), state)

    if not alerts:
        return 0
    for idx, label, rule in alerts:
        label_part = f" {label}" if label else ""
        print(f"{idx}{label_part}\t{rule}")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eth-validator-stats",
        description="Tiny self-hosted CLI for Ethereum validator stats.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print a snapshot table of all configured validators.")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="Cron mode: print offenders, exit 2 if any.")
    p_check.add_argument("--missed", type=int, default=3, help="Consecutive missed attestations to alert on (default: 3).")
    p_check.set_defaults(func=cmd_check)

    p_info = sub.add_parser("info", help="Probe the beacon node and report client/version + endpoint support.")
    p_info.set_defaults(func=cmd_info)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
