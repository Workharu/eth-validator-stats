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
