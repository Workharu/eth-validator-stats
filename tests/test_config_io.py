from __future__ import annotations

from pathlib import Path

import pytest

from eth_validator_stats.alerts import AlertsConfig
from eth_validator_stats.config_io import (
    AppConfig,
    ConfigEntry,
    load_config,
    write_config,
)


def _sample_cfg() -> AppConfig:
    return AppConfig(
        beacon_node_url="http://localhost:3500",
        validators=[
            ConfigEntry(identifier="0xabc", label="home-1", pubkey="0xabc", index=None),
            ConfigEntry(identifier="12345", label="home-2", pubkey=None, index=12345),
        ],
        beacon_auth_token="",
        alerts=AlertsConfig(ntfy_topic="https://ntfy.sh/eth-vstats-test"),
    )


def test_write_then_load_yaml_round_trip(tmp_path: Path):
    target = tmp_path / "config.yml"
    cfg = _sample_cfg()
    write_config(cfg, target)
    loaded = load_config(target)
    assert loaded == cfg
