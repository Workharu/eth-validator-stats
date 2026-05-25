from __future__ import annotations

import stat
from pathlib import Path

import pytest

from eth_validator_stats.alerts import AlertsConfig
from eth_validator_stats.config_io import (
    AppConfig,
    ConfigEntry,
    _resolve_existing_config,
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


def test_unsupported_suffix_errors_clearly(tmp_path: Path):
    """Anything other than .yml/.yaml is rejected with an informative message."""
    p = tmp_path / "config.toml"
    p.write_text("# nope\n")
    with pytest.raises(SystemExit, match="unsupported config suffix"):
        load_config(p)


def test_yaml_load_rejects_python_object_tag(tmp_path: Path):
    """A malicious YAML with !!python/object must not deserialize into Python objects."""
    p = tmp_path / "bad.yml"
    p.write_text(
        "beacon_node_url: !!python/object/apply:os.system ['echo PWNED']\n"
        "validators:\n"
        "  - index: 1\n"
    )
    with pytest.raises(Exception):  # noqa: B017 — YAMLError or ConstructorError — both acceptable
        load_config(p)


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


# --- config-lookup precedence (env -> /etc -> ~/.config -> legacy) -----------

def _minimal_yml_text() -> str:
    return (
        "beacon_node_url: http://from-resolver\n"
        "validators:\n"
        "  - index: 42\n"
    )


def test_resolver_finds_system_config_when_user_config_absent(
    tmp_path: Path, monkeypatch
):
    """If /etc/eth-validator-stats/config.yml exists and there is no user
    config, the resolver returns the system path."""
    fake_etc = tmp_path / "etc" / "eth-validator-stats" / "config.yml"
    fake_etc.parent.mkdir(parents=True)
    fake_etc.write_text(_minimal_yml_text())

    # Point both candidate paths at tmp_path (no user config will exist).
    monkeypatch.setattr(
        "eth_validator_stats.config_io.SYSTEM_CONFIG_PATH", fake_etc
    )
    monkeypatch.setenv(
        "ETH_VALIDATOR_STATS_CONFIG_HOME", str(tmp_path / "no-such-home")
    )
    # Ensure no env override interferes.
    monkeypatch.delenv("ETH_VALIDATOR_STATS_CONFIG", raising=False)
    # And reroute the user-default path via platformdirs.
    monkeypatch.setattr(
        "platformdirs.user_config_path",
        lambda _: tmp_path / "no-such-home" / ".config",
    )

    p = _resolve_existing_config()
    assert p == fake_etc


def test_resolver_prefers_system_config_over_user_config(
    tmp_path: Path, monkeypatch
):
    """When both /etc and ~/.config configs exist, /etc wins (per design)."""
    fake_etc = tmp_path / "etc" / "config.yml"
    fake_etc.parent.mkdir(parents=True)
    fake_etc.write_text("beacon_node_url: http://from-etc\nvalidators:\n  - index: 1\n")

    fake_user = tmp_path / "home" / "config.yml"
    fake_user.parent.mkdir(parents=True)
    fake_user.write_text("beacon_node_url: http://from-user\nvalidators:\n  - index: 2\n")

    monkeypatch.setattr(
        "eth_validator_stats.config_io.SYSTEM_CONFIG_PATH", fake_etc
    )
    monkeypatch.setattr(
        "platformdirs.user_config_path", lambda _: fake_user.parent
    )
    monkeypatch.delenv("ETH_VALIDATOR_STATS_CONFIG", raising=False)

    cfg = load_config()
    assert cfg.beacon_node_url == "http://from-etc"


def test_resolver_falls_back_to_user_config_when_no_system_config(
    tmp_path: Path, monkeypatch
):
    fake_user = tmp_path / "home" / "config.yml"
    fake_user.parent.mkdir(parents=True)
    fake_user.write_text(_minimal_yml_text())

    monkeypatch.setattr(
        "eth_validator_stats.config_io.SYSTEM_CONFIG_PATH",
        tmp_path / "nope" / "config.yml",  # doesn't exist
    )
    monkeypatch.setattr(
        "platformdirs.user_config_path", lambda _: fake_user.parent
    )
    monkeypatch.delenv("ETH_VALIDATOR_STATS_CONFIG", raising=False)

    cfg = load_config()
    assert cfg.beacon_node_url == "http://from-resolver"


def test_exists_safely_handles_permission_denied():
    """The helper must return None (not raise) on EACCES."""
    from eth_validator_stats.config_io import _exists_safely

    class _DeniedPath:
        def exists(self):
            raise PermissionError(13, "Permission denied")

    assert _exists_safely(_DeniedPath()) is None


def test_resolver_skips_unreadable_system_config(tmp_path: Path, monkeypatch):
    """A PermissionError on the system path must not crash the resolver.

    Regression for the 0.3.5 traceback when a non-root user ran
    `eth-validator-stats status` against an .deb install whose
    /etc/eth-validator-stats was mode 0750 (no traversal for `other`).
    """
    fake_user = tmp_path / "home" / "config.yml"
    fake_user.parent.mkdir(parents=True)
    fake_user.write_text("beacon_node_url: http://from-user\nvalidators:\n  - index: 1\n")

    class _DeniedPath:
        def exists(self):
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(
        "eth_validator_stats.config_io.SYSTEM_CONFIG_PATH", _DeniedPath()
    )
    monkeypatch.setattr("platformdirs.user_config_path", lambda _: fake_user.parent)
    monkeypatch.delenv("ETH_VALIDATOR_STATS_CONFIG", raising=False)

    # Must not raise; falls through to the per-user config.
    cfg = load_config()
    assert cfg.beacon_node_url == "http://from-user"


def test_load_config_unreadable_explicit_path_gives_helpful_message(tmp_path: Path):
    """When the caller passes a specific path that EACCES's on stat, the
    error message must point at sudo / group fix, not just propagate the
    raw PermissionError."""

    class _DeniedPath:
        suffix = ".yml"
        def exists(self):
            raise PermissionError(13, "Permission denied")
        def __str__(self):
            return "/etc/eth-validator-stats/config.yml"

    with pytest.raises(SystemExit) as exc:
        load_config(_DeniedPath())  # type: ignore[arg-type]
    msg = str(exc.value)
    assert "not readable" in msg
    assert "sudo" in msg
    assert "eth-validator-stats" in msg


def test_load_config_unreadable_system_with_no_user_config_hints_at_sudo(
    tmp_path: Path, monkeypatch, capsys
):
    """When the resolver falls through to a non-existent ~/.config and the
    /etc path is unreadable, the not-found error includes a hint about the
    inaccessible system config."""
    class _DeniedPath:
        def exists(self):
            raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(
        "eth_validator_stats.config_io.SYSTEM_CONFIG_PATH", _DeniedPath()
    )
    monkeypatch.setattr(
        "platformdirs.user_config_path",
        lambda _: tmp_path / "no-such-home" / ".config",
    )
    monkeypatch.delenv("ETH_VALIDATOR_STATS_CONFIG", raising=False)

    with pytest.raises(SystemExit) as exc:
        load_config()
    msg = str(exc.value)
    assert "config file not found" in msg
    assert "appears to exist but is not" in msg
    assert "sudo eth-validator-stats" in msg


def test_resolver_env_override_wins_over_system_and_user(
    tmp_path: Path, monkeypatch
):
    """ETH_VALIDATOR_STATS_CONFIG=/path beats both /etc and ~/.config."""
    fake_etc = tmp_path / "etc.yml"
    fake_etc.write_text("beacon_node_url: http://from-etc\nvalidators:\n  - index: 1\n")

    fake_explicit = tmp_path / "explicit.yml"
    fake_explicit.write_text(
        "beacon_node_url: http://from-env\nvalidators:\n  - index: 9\n"
    )

    monkeypatch.setattr(
        "eth_validator_stats.config_io.SYSTEM_CONFIG_PATH", fake_etc
    )
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(fake_explicit))

    cfg = load_config()
    assert cfg.beacon_node_url == "http://from-env"
