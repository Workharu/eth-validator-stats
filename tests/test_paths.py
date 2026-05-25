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


def test_state_path_calls_platformdirs(monkeypatch):
    import platformdirs
    monkeypatch.delenv("ETH_VALIDATOR_STATS_STATE", raising=False)
    calls: list[str] = []
    real = platformdirs.user_data_path

    def spy(app: str, *args, **kwargs):
        calls.append(app)
        return real(app, *args, **kwargs)

    monkeypatch.setattr(platformdirs, "user_data_path", spy)
    state_path()
    assert calls == ["eth-validator-stats"]
