"""Implementation of `eth-validator-stats validators {add,list,rm}`.

Three subcommands that mutate the validators list in config.yml so the
user doesn't have to edit YAML by hand. Reuses:

- parse_validator_input() for pubkey/index parsing
- BeaconClient for the existence check during `add`
- load_config / write_config for atomic round-trip
- cli._maybe_start_systemd_service for picking up the change on .deb/.rpm
  installs without a manual `systemctl restart`

All three subcommands preserve the existing file's ownership and mode
when writing — important on .deb/.rpm installs where /etc/<pkg>/config.yml
is owned by the eth-validator-stats service user and chmod-ed 0644.
write_config tightens to 0600 by default; we restore the original here.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .beacon import BeaconClient
from .config_io import (
    AppConfig,
    ConfigEntry,
    _resolve_existing_config,
    load_config,
    write_config,
)
from .onboarding.prompts import parse_validator_input


def _atomic_write_preserving_perms(cfg: AppConfig, path: Path) -> None:
    """Write the config back, preserving the existing file's mode/ownership.

    write_config() always emits with mode 0600 — fine for a fresh per-user
    config but wrong for /etc/<pkg>/config.yml, which init --system writes
    at 0644 owned by eth-validator-stats:eth-validator-stats. Capture mode
    + uid + gid before write, restore after.
    """
    prev_mode: int | None = None
    prev_uid: int | None = None
    prev_gid: int | None = None
    if path.exists():
        st = path.stat()
        prev_mode = st.st_mode & 0o777
        prev_uid = st.st_uid
        prev_gid = st.st_gid

    write_config(cfg, path)

    if prev_mode is not None:
        os.chmod(path, prev_mode)
    if prev_uid is not None and prev_gid is not None:
        try:
            os.chown(path, prev_uid, prev_gid)
        except PermissionError:
            # Non-root user editing their own ~/.config — chown is a no-op
            # for them since they already own the file. Ignore.
            pass


def _resolve_path_to_write() -> Path:
    """Round-trip target = the same file load_config() just read from."""
    p, _ = _resolve_existing_config()
    return p


def _restart_service_if_installed() -> None:
    """Re-runs the service so it picks up the config change.

    No-ops when systemctl is missing or the unit isn't installed (e.g.
    user installed via pipx without install-service). Imported lazily
    to avoid a circular import at module load time.
    """
    from .cli import _maybe_start_systemd_service
    _maybe_start_systemd_service()


# --- add ---------------------------------------------------------------------

def cmd_validators_add(args: argparse.Namespace) -> int:
    cfg = load_config()

    try:
        kind, value = parse_validator_input(args.identifier)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    pubkey_in: str | None = value if kind == "pubkey" else None
    index_in: int | None = value if kind == "index" else None

    # Build the API id the beacon node expects.
    api_id = pubkey_in if pubkey_in is not None else str(index_in)

    canonical_pubkey: str | None = pubkey_in
    canonical_index: int | None = index_in

    if args.no_verify:
        print(
            "warning: --no-verify set — saving without confirming against "
            "the beacon node. A typo will fail silently in `check`.",
            file=sys.stderr,
        )
    else:
        try:
            with BeaconClient(
                cfg.beacon_node_url,
                auth_token=cfg.beacon_auth_token or None,
            ) as bc:
                found = bc.get_validators([api_id])
        except Exception as e:
            print(
                f"error: failed to reach beacon node at {cfg.beacon_node_url}: {e}\n"
                f"       (pass --no-verify to save the entry anyway)",
                file=sys.stderr,
            )
            return 1
        if not found:
            print(
                f"error: beacon node has no validator with id {args.identifier!r}.\n"
                f"       (pass --no-verify to save the entry anyway)",
                file=sys.stderr,
            )
            return 1
        v = found[0]
        canonical_index = v.index
        canonical_pubkey = v.pubkey
        print(f"  verified: index={v.index}  status={v.status}")

    # Duplicate detection — by index OR pubkey.
    for existing in cfg.validators:
        if canonical_index is not None and existing.index == canonical_index:
            print(
                f"error: validator index {canonical_index} is already configured "
                f"(label={existing.label!r}).",
                file=sys.stderr,
            )
            return 1
        if (
            canonical_pubkey is not None
            and existing.pubkey
            and existing.pubkey.lower() == canonical_pubkey.lower()
        ):
            print(
                f"error: validator pubkey {canonical_pubkey} is already configured "
                f"(label={existing.label!r}).",
                file=sys.stderr,
            )
            return 1

    # Choose a stable identifier string for the config row (pubkey if we
    # have it, otherwise the decimal index).
    ident_str = canonical_pubkey if canonical_pubkey is not None else str(canonical_index)

    new_entry = ConfigEntry(
        identifier=ident_str,
        label=args.label or "",
        pubkey=canonical_pubkey,
        index=canonical_index,
    )
    updated = AppConfig(
        beacon_node_url=cfg.beacon_node_url,
        validators=list(cfg.validators) + [new_entry],
        beacon_auth_token=cfg.beacon_auth_token,
        alerts=cfg.alerts,
    )
    _atomic_write_preserving_perms(updated, _resolve_path_to_write())

    label_suffix = f" ({new_entry.label})" if new_entry.label else ""
    # Identify the entry in the success message even when --no-verify left
    # canonical_index unresolved (saved-by-pubkey case): fall back to a
    # shortened pubkey so the user always sees what got written.
    if canonical_index is not None:
        ident_echo = str(canonical_index)
    elif canonical_pubkey is not None:
        ident_echo = _shorten_pubkey(canonical_pubkey)
    else:
        ident_echo = "?"
    print(f"✓ added validator {ident_echo}{label_suffix}")
    _restart_service_if_installed()
    return 0


# --- list --------------------------------------------------------------------

def _shorten_pubkey(pk: str | None) -> str:
    if not pk:
        return ""
    return pk if len(pk) <= 14 else f"{pk[:10]}…{pk[-4:]}"


def cmd_validators_list(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not cfg.validators:
        print("(no validators configured — `eth-validator-stats init` to start)")
        return 0

    from rich.console import Console
    from rich.table import Table

    # When --status, key the beacon response by BOTH index and pubkey so
    # pubkey-only config entries (which have e.index = None) still find
    # their live row in the lookup. get_validators returns items in
    # arbitrary order, so a per-entry positional pair-up isn't safe.
    live_by_index: dict[int, object] = {}
    live_by_pubkey: dict[str, object] = {}
    if args.status:
        try:
            with BeaconClient(
                cfg.beacon_node_url,
                auth_token=cfg.beacon_auth_token or None,
            ) as bc:
                vs = bc.get_validators([e.identifier for e in cfg.validators])
            for v in vs:
                live_by_index[v.index] = v
                live_by_pubkey[v.pubkey.lower()] = v
        except Exception as e:
            print(
                f"warning: --status: could not reach beacon node ({e}); "
                f"showing config-only view.",
                file=sys.stderr,
            )

    t = Table(title="Configured validators", header_style="bold")
    t.add_column("idx", justify="right")
    t.add_column("label")
    t.add_column("pubkey")
    if args.status:
        t.add_column("status")
        t.add_column("balance (ETH)", justify="right")

    for e in cfg.validators:
        row = [
            str(e.index) if e.index is not None else "?",
            e.label or "",
            _shorten_pubkey(e.pubkey),
        ]
        if args.status:
            v: object | None = None
            if e.index is not None:
                v = live_by_index.get(e.index)
            if v is None and e.pubkey is not None:
                v = live_by_pubkey.get(e.pubkey.lower())
            if v is not None:
                row.append(getattr(v, "status", ""))
                bal = getattr(v, "balance_gwei", 0) / 1_000_000_000
                row.append(f"{bal:.4f}")
            else:
                row.append("—")
                row.append("—")
        t.add_row(*row)

    Console().print(t)
    return 0


# --- rm ----------------------------------------------------------------------

def _find_matches(cfg: AppConfig, target: str) -> list[ConfigEntry]:
    """Look up by index → pubkey → label, in that order."""
    target = target.strip()
    if target.isdigit():
        idx = int(target)
        return [e for e in cfg.validators if e.index == idx]
    if target.lower().startswith("0x"):
        t = target.lower()
        return [e for e in cfg.validators if e.pubkey and e.pubkey.lower() == t]
    return [e for e in cfg.validators if e.label == target]


def cmd_validators_rm(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not cfg.validators:
        print("(no validators configured — nothing to remove)")
        return 1

    matches = _find_matches(cfg, args.identifier)
    if not matches:
        print(
            f"error: no validator matching {args.identifier!r} "
            f"(looked up by index, pubkey, then label).",
            file=sys.stderr,
        )
        return 1
    if len(matches) > 1:
        print(
            f"error: {len(matches)} validators match label {args.identifier!r}; "
            f"specify by index or pubkey instead.",
            file=sys.stderr,
        )
        return 1

    victim = matches[0]
    # Identify the victim in the prompt regardless of which key it was
    # saved by — pubkey-only entries have .index = None and would
    # otherwise show "index None" to the user.
    if victim.index is not None:
        desc = f"index {victim.index}"
    elif victim.pubkey is not None:
        desc = f"pubkey {_shorten_pubkey(victim.pubkey)}"
    else:
        desc = "unknown"
    if victim.label:
        desc += f" ({victim.label})"

    if not args.yes:
        try:
            ans = input(f"Remove validator {desc}? [y/N]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("aborted (run with --yes to skip the confirmation prompt)")
            return 0

    remaining = [e for e in cfg.validators if e is not victim]
    if not remaining:
        print(
            f"error: removing this would leave the config with zero validators, "
            f"which load_config refuses to read. Add a different validator first "
            f"with `eth-validator-stats validators add ...`, then retry.",
            file=sys.stderr,
        )
        return 1

    updated = AppConfig(
        beacon_node_url=cfg.beacon_node_url,
        validators=remaining,
        beacon_auth_token=cfg.beacon_auth_token,
        alerts=cfg.alerts,
    )
    _atomic_write_preserving_perms(updated, _resolve_path_to_write())
    print(f"✓ removed validator {desc}")
    _restart_service_if_installed()
    return 0
