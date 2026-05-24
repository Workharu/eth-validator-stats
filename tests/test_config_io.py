from __future__ import annotations

import stat
from pathlib import Path

import pytest

from eth_validator_stats.alerts import AlertsConfig
from eth_validator_stats.config_io import (
    AppConfig,
    ConfigEntry,
    load_config,
    migrate_from_toml,
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


def test_loads_legacy_toml_with_deprecation_hint(tmp_path: Path, capsys, monkeypatch):
    cfg_dir = tmp_path / "eth-validator-stats"
    cfg_dir.mkdir()
    toml_path = cfg_dir / "config.toml"
    toml_path.write_text(
        'beacon_node_url = "http://localhost:3500"\n'
        '[[validators]]\n'
        'index = 12345\n'
        'label = "v1"\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("ETH_VALIDATOR_STATS_CONFIG", raising=False)
    monkeypatch.delenv("BEACON_NODE_URL", raising=False)
    monkeypatch.delenv("BEACON_NODE_AUTH_TOKEN", raising=False)

    cfg = load_config()
    err = capsys.readouterr().err
    assert cfg.validators[0].index == 12345
    assert "config.toml" in err and "deprecated" in err


def test_explicit_toml_path_does_not_print_deprecation(tmp_path: Path, capsys):
    toml_path = tmp_path / "explicit.toml"
    toml_path.write_text('beacon_node_url = "http://x:1"\n[[validators]]\nindex = 1\n')
    cfg = load_config(toml_path)
    err = capsys.readouterr().err
    assert cfg.validators[0].index == 1
    assert "deprecated" not in err


def test_yaml_load_rejects_python_object_tag(tmp_path: Path):
    """A malicious YAML with !!python/object must not deserialize into Python objects."""
    p = tmp_path / "bad.yml"
    p.write_text(
        "beacon_node_url: !!python/object/apply:os.system ['echo PWNED']\n"
        "validators:\n"
        "  - index: 1\n"
    )
    with pytest.raises(Exception):  # YAMLError or ConstructorError — both acceptable
        load_config(p)


def test_migrate_from_toml_creates_equivalent_yaml(tmp_path: Path):
    src = tmp_path / "config.toml"
    src.write_text(
        'beacon_node_url = "http://localhost:3500"\n'
        '[[validators]]\n'
        'pubkey = "0xabc"\n'
        'label = "v1"\n'
        '[[validators]]\n'
        'index = 42\n'
        '[alerts]\n'
        'ntfy_topic = "https://ntfy.sh/eth-vstats-x"\n'
        'cooldown_minutes = 15\n'
    )
    dst = tmp_path / "config.yml"
    backup = migrate_from_toml(src, dst)

    assert dst.exists()
    assert backup.exists()
    assert backup.name == "config.toml.bak"
    assert not src.exists()  # original was renamed

    loaded = load_config(dst)
    assert loaded.beacon_node_url == "http://localhost:3500"
    assert len(loaded.validators) == 2
    assert loaded.validators[0].pubkey == "0xabc"
    assert loaded.validators[1].index == 42
    assert loaded.alerts.cooldown_minutes == 15


def test_migrate_from_toml_refuses_if_yaml_exists(tmp_path: Path):
    src = tmp_path / "config.toml"
    src.write_text('beacon_node_url = "http://x"\n[[validators]]\nindex = 1\n')
    dst = tmp_path / "config.yml"
    dst.write_text("# pre-existing\n")
    with pytest.raises(SystemExit, match="already exists"):
        migrate_from_toml(src, dst)


def test_alerts_config_new_fields_defaults_round_trip(tmp_path: Path):
    """New alert fields land in YAML with their declared defaults and round-trip."""
    cfg = AppConfig(
        beacon_node_url="http://localhost:3500",
        validators=[ConfigEntry(identifier="1", label="v", pubkey=None, index=1)],
        beacon_auth_token="",
        alerts=AlertsConfig(),  # all defaults
    )
    target = tmp_path / "config.yml"
    write_config(cfg, target)
    text = target.read_text()
    assert "missed_attestations_threshold: 2" in text
    assert "withdrawal_threshold_gwei: 1000000" in text
    assert "withdrawal_max_gap_slots: 64" in text
    assert "proposal_lookahead_epochs: 1" in text
    loaded = load_config(target)
    assert loaded.alerts.missed_attestations_threshold == 2
    assert loaded.alerts.withdrawal_threshold_gwei == 1_000_000
    assert loaded.alerts.withdrawal_max_gap_slots == 64
    assert loaded.alerts.proposal_lookahead_epochs == 1


def test_alerts_config_custom_overrides_load(tmp_path: Path):
    target = tmp_path / "config.yml"
    target.write_text(
        "beacon_node_url: http://x\n"
        "validators:\n"
        "  - index: 1\n"
        "alerts:\n"
        "  missed_attestations_threshold: 5\n"
        "  withdrawal_threshold_gwei: 50000000\n"
        "  proposal_lookahead_epochs: 2\n"
    )
    cfg = load_config(target)
    assert cfg.alerts.missed_attestations_threshold == 5
    assert cfg.alerts.withdrawal_threshold_gwei == 50_000_000
    assert cfg.alerts.proposal_lookahead_epochs == 2
