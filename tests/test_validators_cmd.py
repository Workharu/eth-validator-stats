"""Tests for `eth-validator-stats validators {add, list, rm}`.

The cmd_* functions read the active config via load_config() and write
back via _atomic_write_preserving_perms. Tests use ETH_VALIDATOR_STATS_CONFIG
to point at a tmp_path file, and a fake BeaconClient via monkeypatch on
the symbol imported into _validators_cmd.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from eth_validator_stats import _validators_cmd as vmod
from eth_validator_stats.beacon import ValidatorInfo


def _write_initial_config(tmp_path: Path, monkeypatch, validators_yaml: str) -> Path:
    """Write a minimal config.yml at tmp_path and point the env at it."""
    p = tmp_path / "config.yml"
    p.write_text(
        "beacon_node_url: http://localhost:3500\n"
        "validators:\n"
        f"{validators_yaml}"
        "alerts:\n"
        '  ntfy_topic: ""\n'
    )
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(p))
    return p


class _FakeBC:
    """Drop-in for BeaconClient. .get_validators returns a scripted list."""
    def __init__(self, scripted: list[ValidatorInfo]):
        self._scripted = scripted
        self.calls: list[list[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get_validators(self, ids: list[str]) -> list[ValidatorInfo]:
        self.calls.append(list(ids))
        return list(self._scripted)


def _args(**kw) -> argparse.Namespace:
    """Build an argparse.Namespace with defaults so cmd_* don't AttributeError."""
    defaults = dict(
        identifier=None,
        label=None,
        no_verify=False,
        status=False,
        yes=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def _patch_bc(monkeypatch, scripted: list[ValidatorInfo]):
    """Replace BeaconClient in _validators_cmd's namespace."""
    fake = _FakeBC(scripted)
    monkeypatch.setattr(vmod, "BeaconClient", lambda *a, **kw: fake)
    # The cli._maybe_start_systemd_service path is best-effort — short-circuit it.
    from eth_validator_stats import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_maybe_start_systemd_service", lambda: None)
    return fake


# --- add ---------------------------------------------------------------------

def test_add_by_index_verifies_against_beacon_and_persists(tmp_path: Path, monkeypatch):
    """User passes a numeric index, but the beacon node returns the canonical
    pubkey as well. write_config prefers pubkey over index when serializing
    (to keep entries minimal), so the saved row should carry the pubkey."""
    cfg_path = _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: existing\n",
    )
    _patch_bc(monkeypatch, [
        ValidatorInfo(index=42, pubkey="0xabc", status="active_ongoing", balance_gwei=32_000_000_000),
    ])

    rc = vmod.cmd_validators_add(_args(identifier="42", label="home-2"))
    assert rc == 0

    data = yaml.safe_load(cfg_path.read_text())
    pubkeys = [v.get("pubkey") for v in data["validators"]]
    labels = [v.get("label") for v in data["validators"]]
    assert "0xabc" in pubkeys
    assert "home-2" in labels


def test_add_by_pubkey_verifies_against_beacon_and_persists(tmp_path: Path, monkeypatch):
    cfg_path = _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: existing\n",
    )
    _patch_bc(monkeypatch, [
        ValidatorInfo(index=42, pubkey="0xdeadbeef", status="active_ongoing", balance_gwei=32_000_000_000),
    ])

    rc = vmod.cmd_validators_add(_args(identifier="0xdeadbeef", label="new"))
    assert rc == 0
    data = yaml.safe_load(cfg_path.read_text())
    # The new entry should be present
    assert any(v.get("pubkey") == "0xdeadbeef" for v in data["validators"])


def test_add_refuses_duplicate_index(tmp_path: Path, monkeypatch, capsys):
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: existing\n",
    )
    _patch_bc(monkeypatch, [
        ValidatorInfo(index=100, pubkey="0xabc", status="active_ongoing", balance_gwei=32_000_000_000),
    ])

    rc = vmod.cmd_validators_add(_args(identifier="100"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "already configured" in err


def test_add_refuses_duplicate_pubkey(tmp_path: Path, monkeypatch, capsys):
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - pubkey: '0xabc'\n    label: existing\n",
    )
    _patch_bc(monkeypatch, [
        ValidatorInfo(index=42, pubkey="0xabc", status="active_ongoing", balance_gwei=0),
    ])

    rc = vmod.cmd_validators_add(_args(identifier="42"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "already configured" in err


def test_add_errors_when_beacon_does_not_know_validator(tmp_path: Path, monkeypatch, capsys):
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: existing\n",
    )
    _patch_bc(monkeypatch, [])  # beacon returns no match

    rc = vmod.cmd_validators_add(_args(identifier="999999"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "no validator" in err
    assert "--no-verify" in err  # hint to bypass


def test_add_with_no_verify_skips_beacon_check_and_persists(tmp_path: Path, monkeypatch):
    cfg_path = _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: existing\n",
    )
    # No fake BC patched — if we tried to verify, this would blow up
    from eth_validator_stats import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_maybe_start_systemd_service", lambda: None)

    rc = vmod.cmd_validators_add(_args(identifier="555", no_verify=True))
    assert rc == 0
    data = yaml.safe_load(cfg_path.read_text())
    indices = [v.get("index") for v in data["validators"]]
    assert 555 in indices


def test_add_rejects_bad_identifier(tmp_path: Path, monkeypatch, capsys):
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: existing\n",
    )

    rc = vmod.cmd_validators_add(_args(identifier="not-a-pubkey-or-index"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "not a pubkey" in err or "Invalid" in err or "pubkey" in err.lower()


# --- list --------------------------------------------------------------------

def test_list_prints_existing_validators(tmp_path: Path, monkeypatch, capsys):
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: home-1\n"
        "  - index: 200\n    label: home-2\n",
    )

    rc = vmod.cmd_validators_list(_args(status=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "100" in out
    assert "200" in out
    assert "home-1" in out
    assert "home-2" in out


def test_list_with_status_hits_beacon(tmp_path: Path, monkeypatch, capsys):
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: home-1\n",
    )
    _patch_bc(monkeypatch, [
        ValidatorInfo(index=100, pubkey="0xabc", status="active_ongoing", balance_gwei=32_100_000_000),
    ])

    rc = vmod.cmd_validators_list(_args(status=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "active_ongoing" in out
    assert "32.1" in out  # balance


def test_list_status_resolves_pubkey_only_entries(tmp_path: Path, monkeypatch, capsys):
    """Regression: a config entry saved with only `pubkey:` (no index)
    has e.index = None. The status table was looking up live state by
    e.index, so pubkey-only entries showed "—" even when the beacon
    returned data. Cross-key lookup via pubkey fixes that.
    """
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - pubkey: '0xabc'\n    label: pubkey-only\n",
    )
    _patch_bc(monkeypatch, [
        ValidatorInfo(index=777, pubkey="0xabc", status="active_ongoing", balance_gwei=32_500_000_000),
    ])

    rc = vmod.cmd_validators_list(_args(status=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "active_ongoing" in out, (
        "expected live status from beacon to populate the row for the "
        "pubkey-only entry, but the row appears to show '—'"
    )
    assert "32.5" in out


# --- rm ----------------------------------------------------------------------

def test_rm_by_index(tmp_path: Path, monkeypatch):
    cfg_path = _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: home-1\n"
        "  - index: 200\n    label: home-2\n",
    )
    from eth_validator_stats import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_maybe_start_systemd_service", lambda: None)

    rc = vmod.cmd_validators_rm(_args(identifier="100", yes=True))
    assert rc == 0
    data = yaml.safe_load(cfg_path.read_text())
    indices = [v.get("index") for v in data["validators"]]
    assert 100 not in indices
    assert 200 in indices


def test_rm_by_pubkey(tmp_path: Path, monkeypatch):
    cfg_path = _write_initial_config(
        tmp_path, monkeypatch,
        "  - pubkey: '0xabc'\n    label: home-1\n"
        "  - index: 200\n    label: home-2\n",
    )
    from eth_validator_stats import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_maybe_start_systemd_service", lambda: None)

    rc = vmod.cmd_validators_rm(_args(identifier="0xabc", yes=True))
    assert rc == 0
    data = yaml.safe_load(cfg_path.read_text())
    pubkeys = [v.get("pubkey") for v in data["validators"]]
    assert "0xabc" not in pubkeys


def test_rm_by_label(tmp_path: Path, monkeypatch):
    cfg_path = _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: home-1\n"
        "  - index: 200\n    label: home-2\n",
    )
    from eth_validator_stats import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_maybe_start_systemd_service", lambda: None)

    rc = vmod.cmd_validators_rm(_args(identifier="home-1", yes=True))
    assert rc == 0
    data = yaml.safe_load(cfg_path.read_text())
    labels = [v.get("label") for v in data["validators"]]
    assert "home-1" not in labels


def test_rm_not_found(tmp_path: Path, monkeypatch, capsys):
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: home-1\n",
    )

    rc = vmod.cmd_validators_rm(_args(identifier="999", yes=True))
    assert rc == 1
    err = capsys.readouterr().err
    assert "no validator" in err.lower()


def test_rm_refuses_to_empty_the_config(tmp_path: Path, monkeypatch, capsys):
    """Last validator can't be removed — load_config raises on empty."""
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: home-1\n",
    )

    rc = vmod.cmd_validators_rm(_args(identifier="100", yes=True))
    assert rc == 1
    err = capsys.readouterr().err
    assert "zero validators" in err


def test_rm_ambiguous_label_errors(tmp_path: Path, monkeypatch, capsys):
    """Two validators sharing a label can't be rm'd by that label."""
    _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: home\n"
        "  - index: 200\n    label: home\n",
    )

    rc = vmod.cmd_validators_rm(_args(identifier="home", yes=True))
    assert rc == 1
    err = capsys.readouterr().err
    assert "match" in err.lower()


# --- atomic write preserves perms (smoke test on a per-user-like file) -----

def test_write_preserves_mode_and_does_not_tighten_to_0600(tmp_path: Path, monkeypatch):
    """If the existing file is 0644 (system-mode default), atomic write keeps it 0644.

    Regression guard: write_config tightens to 0600 by default; the
    preserving wrapper must restore the original.
    """
    import os
    cfg_path = _write_initial_config(
        tmp_path, monkeypatch,
        "  - index: 100\n    label: existing\n",
    )
    os.chmod(cfg_path, 0o644)

    _patch_bc(monkeypatch, [
        ValidatorInfo(index=42, pubkey="0xabc", status="active_ongoing", balance_gwei=0),
    ])

    rc = vmod.cmd_validators_add(_args(identifier="42"))
    assert rc == 0
    mode = cfg_path.stat().st_mode & 0o777
    assert mode == 0o644, f"expected 0o644 after add, got {oct(mode)}"
