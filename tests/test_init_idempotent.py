from __future__ import annotations

from pathlib import Path

from eth_validator_stats.beacon import ValidatorInfo
from eth_validator_stats.onboarding.wizard import WizardArgs, run_wizard


def _bytes_after_run(cfg_path: Path) -> bytes:
    from tests.conftest import FakeBeaconClient, FakeNotifier, FakePrompts

    prompts = FakePrompts(answers=[])
    beacon = FakeBeaconClient(validators=[
        ValidatorInfo(index=1113127, pubkey="0xdeadbeef", status="active_ongoing", balance_gwei=32_000_000_000),
    ])
    notifier = FakeNotifier()
    args = WizardArgs(
        host=None, beacon_url="http://192.0.2.10:3500",
        auth_token="", validator="1113127", label="v1",
        ntfy_topic="eth-vstats-fixed", no_ntfy=False,
        yes=True, force=False,
    )
    cfg_path.unlink(missing_ok=True)
    rc = run_wizard(
        args, cfg_path=cfg_path, io=prompts,
        portscan_fn=None,
        beacon_client_factory=lambda url, auth_token=None: beacon,
        notifier_factory=lambda topic: notifier,
        topic_generator=lambda: "eth-vstats-fixed",
    )
    assert rc == 0
    return cfg_path.read_bytes()


def test_init_two_force_runs_produce_byte_identical_yaml(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.yml"
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))
    monkeypatch.delenv("BEACON_NODE_URL", raising=False)
    monkeypatch.delenv("BEACON_NODE_AUTH_TOKEN", raising=False)

    b1 = _bytes_after_run(cfg_path)
    b2 = _bytes_after_run(cfg_path)
    assert b1 == b2
