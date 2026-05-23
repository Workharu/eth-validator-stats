from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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
