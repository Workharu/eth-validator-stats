from __future__ import annotations

from pathlib import Path

import pytest

from eth_validator_stats.beacon import ValidatorInfo
from eth_validator_stats.config_io import load_config
from eth_validator_stats.onboarding.portscan import Found
from eth_validator_stats.onboarding.wizard import WizardArgs, run_wizard


def test_wizard_happy_path_writes_expected_yaml(
    tmp_path: Path, monkeypatch
):
    from tests.conftest import FakeBeaconClient, FakeNotifier, FakePortscanResult, FakePrompts

    cfg_path = tmp_path / "config.yml"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))
    monkeypatch.delenv("BEACON_NODE_URL", raising=False)
    monkeypatch.delenv("BEACON_NODE_AUTH_TOKEN", raising=False)

    prompts = FakePrompts(answers=[
        "192.168.10.15",        # host
        "",                       # accept first found url
        "",                       # no auth token (n is default)
        "1113127",                # validator id
        "validator-1",            # label
        "",                       # enable ntfy (Y default)
        "",                       # accept generated topic
        "",                       # accept arrival confirmation
    ])
    portscan = FakePortscanResult(found=[
        Found(url="http://192.168.10.15:3500", port=3500, client_version="Prysm/v7", latency_ms=12),
    ])
    beacon = FakeBeaconClient(validators=[
        ValidatorInfo(index=1113127, pubkey="0xdeadbeef", status="active_ongoing", balance_gwei=32_000_000_000),
    ])
    notifier = FakeNotifier()

    args = WizardArgs(
        host=None, beacon_url=None, auth_token=None,
        validator=None, label=None,
        ntfy_topic=None, no_ntfy=False,
        yes=False, force=False,
    )

    exit_code = run_wizard(
        args,
        cfg_path=cfg_path,
        io=prompts,
        portscan_fn=portscan,
        beacon_client_factory=lambda url, auth_token=None: beacon,
        notifier_factory=lambda topic: notifier,
        topic_generator=lambda: "eth-vstats-deadbeef",
    )
    assert exit_code == 0
    assert cfg_path.exists()

    loaded = load_config(cfg_path)
    assert loaded.beacon_node_url == "http://192.168.10.15:3500"
    assert loaded.beacon_auth_token == ""
    assert len(loaded.validators) == 1
    assert loaded.validators[0].index == 1113127
    assert loaded.validators[0].label == "validator-1"
    assert loaded.alerts.ntfy_topic == "https://ntfy.sh/eth-vstats-deadbeef"

    # Verify wire test was sent
    assert len(notifier.sent) == 1
    assert "test" in notifier.sent[0][1].lower() or "welcome" in notifier.sent[0][1].lower()


def test_wizard_portscan_finds_nothing_falls_back_to_manual_url(tmp_path: Path, monkeypatch):
    from tests.conftest import FakeBeaconClient, FakeNotifier, FakePortscanResult, FakePrompts

    cfg_path = tmp_path / "config.yml"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))
    monkeypatch.delenv("BEACON_NODE_URL", raising=False)
    monkeypatch.delenv("BEACON_NODE_AUTH_TOKEN", raising=False)

    prompts = FakePrompts(answers=[
        "weird-host",                          # host
        "http://192.168.10.15:24010",          # manual URL fallback
        "",                                    # no auth
        "1",                                   # validator id
        "v1",                                  # label
        "n",                                   # disable ntfy
    ])
    portscan = FakePortscanResult(found=[])  # nothing
    beacon = FakeBeaconClient(validators=[
        ValidatorInfo(index=1, pubkey="0xaaa", status="active_ongoing", balance_gwei=32_000_000_000),
    ])
    notifier = FakeNotifier()

    args = WizardArgs(host=None, beacon_url=None, auth_token=None,
                      validator=None, label=None,
                      ntfy_topic=None, no_ntfy=False, yes=False, force=False)
    rc = run_wizard(
        args, cfg_path=cfg_path, io=prompts,
        portscan_fn=portscan,
        beacon_client_factory=lambda url, auth_token=None: beacon,
        notifier_factory=lambda topic: notifier,
        topic_generator=lambda: "unused",
    )
    assert rc == 0
    loaded = load_config(cfg_path)
    assert loaded.beacon_node_url == "http://192.168.10.15:24010"
    assert loaded.alerts.ntfy_topic == ""
    assert notifier.sent == []
    # Manual-URL fallback must give the user enough context to enter a valid URL
    written = "".join(prompts.written)
    assert "Examples:" in written
    assert "http://localhost:24010" in written
    assert "/eth/v1/node/version" in written
    assert "curl" in written


def test_wizard_multiple_ports_respond_triggers_selection_prompt(tmp_path: Path, monkeypatch):
    from tests.conftest import FakeBeaconClient, FakeNotifier, FakePortscanResult, FakePrompts

    cfg_path = tmp_path / "config.yml"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))
    monkeypatch.delenv("BEACON_NODE_URL", raising=False)

    prompts = FakePrompts(answers=[
        "localhost",      # host
        "2",              # select second port
        "",               # accept that url
        "",               # no auth
        "1",              # validator
        "v1",             # label
        "n",              # no ntfy
    ])
    portscan = FakePortscanResult(found=[
        Found(url="http://localhost:3500", port=3500, client_version="Prysm/v7", latency_ms=10),
        Found(url="http://localhost:5052", port=5052, client_version="Lighthouse/v5", latency_ms=11),
    ])
    beacon = FakeBeaconClient(validators=[
        ValidatorInfo(index=1, pubkey="0xaaa", status="active_ongoing", balance_gwei=32_000_000_000),
    ])
    notifier = FakeNotifier()

    args = WizardArgs(host=None, beacon_url=None, auth_token=None,
                      validator=None, label=None,
                      ntfy_topic=None, no_ntfy=False, yes=False, force=False)
    rc = run_wizard(
        args, cfg_path=cfg_path, io=prompts,
        portscan_fn=portscan,
        beacon_client_factory=lambda url, auth_token=None: beacon,
        notifier_factory=lambda topic: notifier,
    )
    assert rc == 0
    loaded = load_config(cfg_path)
    # User selected port 2 → second entry → Lighthouse on 5052
    assert loaded.beacon_node_url == "http://localhost:5052"


def test_wizard_no_ntfy_flag_skips_notification_step(tmp_path: Path, monkeypatch):
    from tests.conftest import FakeBeaconClient, FakeNotifier, FakePortscanResult, FakePrompts

    cfg_path = tmp_path / "config.yml"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))
    monkeypatch.delenv("BEACON_NODE_URL", raising=False)

    prompts = FakePrompts(answers=[
        "localhost", "", "",     # host + accept-url + no-auth
        "1", "v1",                # validator + label
        # NO ntfy prompts because --no-ntfy bypasses the step
    ])
    portscan = FakePortscanResult(found=[
        Found(url="http://localhost:3500", port=3500, client_version="Prysm", latency_ms=1),
    ])
    beacon = FakeBeaconClient(validators=[
        ValidatorInfo(index=1, pubkey="0xaaa", status="active_ongoing", balance_gwei=32_000_000_000),
    ])
    notifier = FakeNotifier()

    args = WizardArgs(host=None, beacon_url=None, auth_token=None,
                      validator=None, label=None,
                      ntfy_topic=None, no_ntfy=True, yes=False, force=False)
    rc = run_wizard(
        args, cfg_path=cfg_path, io=prompts,
        portscan_fn=portscan,
        beacon_client_factory=lambda url, auth_token=None: beacon,
        notifier_factory=lambda topic: notifier,
    )
    assert rc == 0
    assert notifier.sent == []
    loaded = load_config(cfg_path)
    assert loaded.alerts.ntfy_topic == ""


def test_wizard_yes_flag_skips_arrival_confirmation(tmp_path: Path, monkeypatch):
    """With --yes and --ntfy-topic provided, no Enter-to-confirm prompt fires."""
    from tests.conftest import FakeBeaconClient, FakeNotifier, FakePrompts

    cfg_path = tmp_path / "config.yml"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))

    prompts = FakePrompts(answers=[])  # no prompts should fire
    beacon = FakeBeaconClient(validators=[
        ValidatorInfo(index=1, pubkey="0xaaa", status="active_ongoing", balance_gwei=32_000_000_000),
    ])
    notifier = FakeNotifier()

    args = WizardArgs(
        host=None, beacon_url="http://localhost:3500",
        auth_token="", validator="1", label="v1",
        ntfy_topic="eth-vstats-script", no_ntfy=False,
        yes=True, force=False,
    )
    rc = run_wizard(
        args, cfg_path=cfg_path, io=prompts,
        portscan_fn=None,  # unused because --beacon-url bypasses portscan
        beacon_client_factory=lambda url, auth_token=None: beacon,
        notifier_factory=lambda topic: notifier,
    )
    assert rc == 0
    assert len(notifier.sent) == 1
    assert prompts.answers == []  # nothing consumed
    loaded = load_config(cfg_path)
    assert "eth-vstats-script" in loaded.alerts.ntfy_topic
