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
    """Resolve the YAML config path. Honors ETH_VALIDATOR_STATS_CONFIG override."""
    override = os.environ.get("ETH_VALIDATOR_STATS_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "eth-validator-stats" / DEFAULT_CONFIG_FILENAME


def legacy_toml_path() -> Path:
    """Resolve the legacy TOML config path (used for migration / deprecation)."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "eth-validator-stats" / LEGACY_CONFIG_FILENAME


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
    if not p.exists():
        raise SystemExit(
            f"config file not found at {p}\n"
            f"Run 'eth-validator-stats init' to create one."
        )
    if p.suffix in (".yml", ".yaml"):
        raw = yaml.safe_load(p.read_text()) or {}
    elif p.suffix == ".toml":
        import tomllib
        raw = tomllib.loads(p.read_text())
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
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _resolve_existing_config() -> tuple[Path, bool]:
    """Pick the right existing config file.
    Returns (path, is_legacy_auto_resolved).
    """
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
    )
    return AppConfig(
        beacon_node_url=url,
        validators=entries,
        beacon_auth_token=auth_token,
        alerts=alerts,
    )
