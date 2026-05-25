from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .alerts import AlertsConfig

DEFAULT_BEACON_URL = "http://localhost:3500"
DEFAULT_CONFIG_FILENAME = "config.yml"
LEGACY_CONFIG_FILENAME = "config.toml"
# System-wide config location written by `init --system` and by the
# .deb / .rpm post-install scripts. Searched before the per-user XDG path
# when reading, so a service install is visible to `eth-validator-stats
# status` runs from any interactive shell.
SYSTEM_CONFIG_PATH = Path("/etc/eth-validator-stats/config.yml")


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
    """Where a new per-user config should be WRITTEN (XDG default).

    Honors ETH_VALIDATOR_STATS_CONFIG as an explicit override. Used by
    `init` (without --system) to decide where to drop the new config.
    For READING, use load_config() or _resolve_existing_config() — those
    additionally consult /etc/eth-validator-stats/config.yml.
    """
    override = os.environ.get("ETH_VALIDATOR_STATS_CONFIG")
    if override:
        return Path(override)
    import platformdirs
    return platformdirs.user_config_path("eth-validator-stats") / DEFAULT_CONFIG_FILENAME


def legacy_toml_path() -> Path:
    """Resolve the legacy TOML config path (used for migration / deprecation)."""
    import platformdirs
    return platformdirs.user_config_path("eth-validator-stats") / LEGACY_CONFIG_FILENAME


def load_config(path: Path | None = None) -> AppConfig:
    """Load a YAML or TOML config. Path is auto-resolved if None."""
    if path is None:
        p, is_legacy_auto = _resolve_existing_config()
        if is_legacy_auto:
            sys.stderr.write(
                f"note: {p} is supported but deprecated. "
                f"Run 'eth-validator-stats init --migrate' to convert.\n"
            )
    else:
        p = path

    # Use the EACCES-aware helper here too — when the resolver returned a
    # path that exists but the system config IS unreadable, we want a
    # specific error message rather than a generic "not found".
    exists = _exists_safely(p)
    if exists is None:
        # Path stat raised EACCES. Almost certainly the system config in
        # /etc that the caller can't traverse to.
        raise SystemExit(
            f"config at {p} exists but is not readable by the current user.\n"
            f"\n"
            f"Either:\n"
            f"  - Re-run with sudo:  sudo eth-validator-stats <cmd>\n"
            f"  - Or add yourself to the eth-validator-stats group, then log out\n"
            f"    and back in:        sudo usermod -aG eth-validator-stats $USER"
        )
    if exists is False:
        # Nothing at the resolved path. If a system config exists but is
        # not readable, surface that — otherwise the user would chase the
        # ~/.config path and wonder why init seemingly didn't write anything.
        msg = f"config file not found at {p}\nRun 'eth-validator-stats init' to create one."
        if path is None and _exists_safely(SYSTEM_CONFIG_PATH) is None:
            msg += (
                f"\n\nNote: {SYSTEM_CONFIG_PATH} appears to exist but is not\n"
                f"readable as the current user. To use the system config:\n"
                f"  sudo eth-validator-stats <cmd>\n"
                f"  # or add yourself to the eth-validator-stats group:\n"
                f"  sudo usermod -aG eth-validator-stats $USER  (log out and back in)"
            )
        raise SystemExit(msg)

    try:
        text = p.read_text()
    except PermissionError as e:
        raise SystemExit(
            f"config at {p} is not readable by the current user ({e.strerror}).\n"
            f"\n"
            f"Either:\n"
            f"  - Re-run with sudo:  sudo eth-validator-stats <cmd>\n"
            f"  - Or add yourself to the eth-validator-stats group, then log out\n"
            f"    and back in:        sudo usermod -aG eth-validator-stats $USER"
        )

    if p.suffix in (".yml", ".yaml"):
        raw = yaml.safe_load(text) or {}
    elif p.suffix == ".toml":
        import tomllib
        raw = tomllib.loads(text)
    else:
        raise SystemExit(f"unsupported config suffix: {p.suffix}")
    return _parse_config(raw)


def write_config(cfg: AppConfig, path: Path) -> None:
    """Write a YAML config atomically with 0o600 permissions."""
    data: dict = {
        "beacon_node_url": cfg.beacon_node_url,
    }
    if cfg.beacon_auth_token:
        data["beacon_auth_token"] = cfg.beacon_auth_token
    data["validators"] = []
    for v in cfg.validators:
        entry: dict = {}
        if v.pubkey is not None:
            entry["pubkey"] = v.pubkey
        elif v.index is not None:
            entry["index"] = v.index
        if v.label:
            entry["label"] = v.label
        data["validators"].append(entry)
    data["alerts"] = {
        "ntfy_topic": cfg.alerts.ntfy_topic,
        "cooldown_minutes": cfg.alerts.cooldown_minutes,
        "storm_threshold": cfg.alerts.storm_threshold,
        "missed_attestations_threshold": cfg.alerts.missed_attestations_threshold,
        "withdrawal_threshold_gwei": cfg.alerts.withdrawal_threshold_gwei,
        "withdrawal_max_gap_slots": cfg.alerts.withdrawal_max_gap_slots,
        "proposal_lookahead_epochs": cfg.alerts.proposal_lookahead_epochs,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _exists_safely(p: Path) -> bool | None:
    """Like Path.exists() but tolerant of EACCES.

    Python 3.11+ changed Path.exists() so it propagates PermissionError
    instead of swallowing it. That bites us when /etc/eth-validator-stats
    is mode 0750 (traversal denied for "other") and the CLI is invoked
    by a regular non-group user — the resolver would crash with a stack
    trace just trying to *check* whether a system config is present.

    Three-valued return:
      True   — file exists and is at least stat-able
      False  — file definitely does not exist
      None   — we cannot tell (parent dir denies traversal, file denies
               metadata access, etc.)
    The resolver treats None like False for path-selection purposes but
    keeps the information around so load_config() can surface a helpful
    "use sudo or join the group" hint when nothing else is found.
    """
    try:
        return p.exists()
    except PermissionError:
        return None


def _resolve_existing_config() -> tuple[Path, bool]:
    """Find the first existing config file along the search chain.

    Order:
      1. $ETH_VALIDATOR_STATS_CONFIG (explicit override) — honored even if
         it doesn't exist (load_config will surface the missing-file error).
      2. /etc/eth-validator-stats/config.yml — system-wide, written by
         `init --system` and the .deb / .rpm post-install scripts.
      3. ~/.config/eth-validator-stats/config.yml — per-user XDG default.
      4. ~/.config/eth-validator-stats/config.toml — legacy pre-YAML format.

    Returns (path, is_legacy_auto_resolved). The path is the first match.
    If nothing exists, returns the per-user YAML path so error messages and
    init writes both point at the same conventional location.

    Permission denied on the system path is treated the same as "absent" for
    selection purposes — load_config() detects the EACCES separately and
    rewrites the not-found error into an actionable "use sudo or join the
    group" message.
    """
    override = os.environ.get("ETH_VALIDATOR_STATS_CONFIG")
    if override:
        return (Path(override), False)
    if _exists_safely(SYSTEM_CONFIG_PATH) is True:
        return (SYSTEM_CONFIG_PATH, False)
    yml = config_path()
    if yml.exists():
        return (yml, False)
    legacy = legacy_toml_path()
    if legacy.exists():
        return (legacy, True)
    return (yml, False)


def migrate_from_toml(toml_path: Path, yml_path: Path) -> Path:
    """Read a TOML config, write an equivalent YAML config, rename the TOML to .bak.
    Returns the backup path. Raises SystemExit if yml_path already exists.
    """
    if yml_path.exists():
        raise SystemExit(
            f"refusing to migrate: {yml_path} already exists. "
            f"Move or delete it first."
        )
    # Parse the legacy TOML (without printing the deprecation hint)
    cfg = load_config(toml_path)
    # Write YAML
    write_config(cfg, yml_path)
    # Rename original to .bak
    backup = toml_path.with_suffix(toml_path.suffix + ".bak")
    toml_path.rename(backup)
    return backup


def _parse_config(raw: dict) -> AppConfig:
    url = os.environ.get("BEACON_NODE_URL") or raw.get("beacon_node_url") or DEFAULT_BEACON_URL
    auth_token = os.environ.get("BEACON_NODE_AUTH_TOKEN") or str(raw.get("beacon_auth_token", "") or "")
    entries: list[ConfigEntry] = []
    for v in raw.get("validators", []) or []:
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
        raise SystemExit("config has no validators entries")
    alerts_raw = raw.get("alerts", {}) or {}
    alerts = AlertsConfig(
        ntfy_topic=str(alerts_raw.get("ntfy_topic", "") or ""),
        cooldown_minutes=int(alerts_raw.get("cooldown_minutes", 30)),
        storm_threshold=int(alerts_raw.get("storm_threshold", 10)),
        missed_attestations_threshold=int(alerts_raw.get("missed_attestations_threshold", 2)),
        withdrawal_threshold_gwei=int(alerts_raw.get("withdrawal_threshold_gwei", 1_000_000)),
        withdrawal_max_gap_slots=int(alerts_raw.get("withdrawal_max_gap_slots", 64)),
        proposal_lookahead_epochs=int(alerts_raw.get("proposal_lookahead_epochs", 1)),
    )
    return AppConfig(
        beacon_node_url=url,
        validators=entries,
        beacon_auth_token=auth_token,
        alerts=alerts,
    )
