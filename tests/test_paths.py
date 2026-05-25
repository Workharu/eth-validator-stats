from __future__ import annotations

from pathlib import Path

from eth_validator_stats.cli import state_path
from eth_validator_stats.config_io import config_path


def test_config_path_uses_xdg_config_home_via_platformdirs(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ETH_VALIDATOR_STATS_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # platformdirs.user_config_path("eth-validator-stats") returns
    # $XDG_CONFIG_HOME/eth-validator-stats on Linux.
    assert config_path() == tmp_path / "eth-validator-stats" / "config.yml"


def test_config_path_env_override_wins(monkeypatch, tmp_path: Path):
    custom = tmp_path / "elsewhere" / "my.yml"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(custom))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "ignored"))
    assert config_path() == custom


def test_state_path_uses_xdg_data_home_via_platformdirs(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ETH_VALIDATOR_STATS_STATE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert state_path() == tmp_path / "eth-validator-stats" / "state.json"


def test_state_path_env_override_wins(monkeypatch, tmp_path: Path):
    custom = tmp_path / "elsewhere" / "state.json"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_STATE", str(custom))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "ignored"))
    assert state_path() == custom


def test_config_path_calls_platformdirs(monkeypatch):
    """Locks in the refactor: config_path() must delegate to platformdirs,
    not its own hand-rolled XDG logic, so non-Linux platforms get correct paths."""
    import platformdirs
    monkeypatch.delenv("ETH_VALIDATOR_STATS_CONFIG", raising=False)
    calls: list[str] = []
    real = platformdirs.user_config_path

    def spy(app: str, *args, **kwargs):
        calls.append(app)
        return real(app, *args, **kwargs)

    monkeypatch.setattr(platformdirs, "user_config_path", spy)
    config_path()
    assert calls == ["eth-validator-stats"]


def test_state_path_calls_platformdirs(monkeypatch, tmp_path: Path):
    import platformdirs
    monkeypatch.delenv("ETH_VALIDATOR_STATS_STATE", raising=False)
    # Ensure system dir is absent so we fall through to platformdirs.
    monkeypatch.setattr(
        "eth_validator_stats.cli.SYSTEM_STATE_DIR", tmp_path / "does_not_exist",
    )
    calls: list[str] = []
    real = platformdirs.user_data_path

    def spy(app: str, *args, **kwargs):
        calls.append(app)
        return real(app, *args, **kwargs)

    monkeypatch.setattr(platformdirs, "user_data_path", spy)
    state_path()
    assert calls == ["eth-validator-stats"]


def test_state_path_prefers_system_dir_when_present(monkeypatch, tmp_path: Path):
    """When /var/lib/eth-validator-stats/ exists (system install signature),
    every caller — root or not — should read the same shared state file."""
    monkeypatch.delenv("ETH_VALIDATOR_STATS_STATE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "should_be_ignored"))

    # Pretend the system dir exists and is readable.
    sys_dir = tmp_path / "var-lib-evs"
    sys_dir.mkdir()
    monkeypatch.setattr(
        "eth_validator_stats.cli.SYSTEM_STATE_DIR", sys_dir,
    )

    assert state_path() == sys_dir / "state.json"


def test_state_path_falls_back_to_platformdirs_when_system_dir_absent(monkeypatch, tmp_path: Path):
    """No system install -> per-user path via platformdirs."""
    monkeypatch.delenv("ETH_VALIDATOR_STATS_STATE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(
        "eth_validator_stats.cli.SYSTEM_STATE_DIR", tmp_path / "does_not_exist",
    )
    assert state_path() == tmp_path / "eth-validator-stats" / "state.json"


def test_state_path_env_override_beats_system_dir(monkeypatch, tmp_path: Path):
    """ETH_VALIDATOR_STATS_STATE wins over the system-dir auto-detect."""
    sys_dir = tmp_path / "var-lib-evs"
    sys_dir.mkdir()
    monkeypatch.setattr("eth_validator_stats.cli.SYSTEM_STATE_DIR", sys_dir)
    custom = tmp_path / "elsewhere" / "state.json"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_STATE", str(custom))
    assert state_path() == custom


def test_state_path_skips_system_dir_when_unreadable(monkeypatch, tmp_path: Path):
    """If the system dir exists but we can't read it (other user, 0o700),
    fall back to per-user path rather than raise on later load."""
    monkeypatch.delenv("ETH_VALIDATOR_STATS_STATE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    sys_dir = tmp_path / "var-lib-evs"
    sys_dir.mkdir()
    monkeypatch.setattr("eth_validator_stats.cli.SYSTEM_STATE_DIR", sys_dir)
    # Force os.access to report unreadable for this path.
    import os as _os
    real_access = _os.access
    def fake_access(p, mode):
        if Path(p) == sys_dir:
            return False
        return real_access(p, mode)
    monkeypatch.setattr(_os, "access", fake_access)
    assert state_path() == tmp_path / "eth-validator-stats" / "state.json"
