"""Tests for the `init --system` flag added in Phase 2.

The flag switches default paths to /etc/eth-validator-stats/config.yml
and applies appropriate ownership/perms after the wizard writes the config.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from eth_validator_stats import cli as cli_mod


def _fake_pwd_entry(name: str = "eth-validator-stats"):
    class _Entry:
        pw_name = name
        pw_uid = 999
        pw_gid = 999
    return _Entry()


def _build_args(**overrides):
    import argparse
    defaults = dict(
        host=None, beacon_url=None, auth_token=None,
        validator="12345", label="t",
        ntfy_topic=None, no_ntfy=True,
        yes=True, force=True,
        migrate=False,
        system=False,
        log_level=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_init_system_writes_to_etc_path(monkeypatch, tmp_path: Path):
    captured: dict = {}

    def fake_run_wizard(wargs, *, cfg_path, **kwargs):
        captured["cfg_path"] = cfg_path
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text("beacon_node_url: http://localhost:3500\nvalidators: []\n")
        return 0

    monkeypatch.setattr(cli_mod, "run_wizard", fake_run_wizard)
    monkeypatch.setattr(os, "geteuid", lambda: 0)

    import pwd
    monkeypatch.setattr(pwd, "getpwnam", lambda n: _fake_pwd_entry(n))

    chown_calls: list = []
    chmod_calls: list = []
    import shutil
    monkeypatch.setattr(shutil, "chown", lambda *a, **kw: chown_calls.append((a, kw)))
    monkeypatch.setattr(os, "chmod", lambda *a, **kw: chmod_calls.append((a, kw)))

    system_cfg = tmp_path / "etc" / "eth-validator-stats" / "config.yml"
    monkeypatch.setattr(cli_mod, "SYSTEM_CONFIG_PATH", system_cfg, raising=True)

    args = _build_args(system=True)
    rc = cli_mod.cmd_init(args)

    assert rc == 0
    assert captured["cfg_path"] == system_cfg
    assert system_cfg.exists()
    assert any(system_cfg in call[0] for call in chown_calls), chown_calls
    assert any(system_cfg in call[0] for call in chmod_calls), chmod_calls


def test_init_system_requires_root(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    args = _build_args(system=True)

    with pytest.raises(SystemExit) as excinfo:
        cli_mod.cmd_init(args)

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "root" in err.lower() or "sudo" in err.lower()


def test_init_system_requires_service_user_exists(monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    import pwd
    monkeypatch.setattr(pwd, "getpwnam", lambda n: (_ for _ in ()).throw(KeyError(n)))

    args = _build_args(system=True)
    with pytest.raises(SystemExit) as excinfo:
        cli_mod.cmd_init(args)

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "eth-validator-stats" in err
    assert any(s in err.lower() for s in ("apt", "dnf", "install"))
