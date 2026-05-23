from __future__ import annotations

import stat
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


def test_written_yaml_has_0600_perms(tmp_path: Path):
    target = tmp_path / "config.yml"
    write_config(_sample_cfg(), target)
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600


def test_atomic_write_no_leftover_tmp_on_success(tmp_path: Path):
    target = tmp_path / "config.yml"
    write_config(_sample_cfg(), target)
    assert target.exists()
    assert not (tmp_path / "config.yml.tmp").exists()


def test_overwrite_does_not_leak_old_content(tmp_path: Path):
    target = tmp_path / "config.yml"
    write_config(_sample_cfg(), target)
    cfg2 = AppConfig(
        beacon_node_url="http://other:5052",
        validators=[ConfigEntry(identifier="99", label="z", pubkey=None, index=99)],
        beacon_auth_token="",
        alerts=AlertsConfig(),
    )
    write_config(cfg2, target)
    loaded = load_config(target)
    assert loaded.beacon_node_url == "http://other:5052"
    assert len(loaded.validators) == 1
    assert loaded.validators[0].index == 99
