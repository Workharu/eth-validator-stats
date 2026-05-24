from __future__ import annotations

from eth_validator_stats._simulate import (
    build_missed_attestation,
    build_offline,
    build_withdrawal,
    build_proposing_soon,
    build_proposed,
    build_missed_proposal,
)


def test_build_missed_attestation_default_last_2():
    title, body = build_missed_attestation(1234, "home-1")
    assert title == "validator 1234 home-1"
    assert body == "MISSED_ATTESTATIONS last=2"


def test_build_missed_attestation_custom_last():
    title, body = build_missed_attestation(1234, "home-1", last=5)
    assert body == "MISSED_ATTESTATIONS last=5"


def test_build_missed_attestation_no_label():
    title, body = build_missed_attestation(1234, "")
    assert title == "validator 1234"  # no trailing space


def test_build_offline_default_status_slashed():
    title, body = build_offline(1234, "home-1")
    assert title == "validator 1234 home-1"
    assert body == "OFFLINE status=slashed"


def test_build_offline_custom_status():
    _, body = build_offline(1, "", status="exited_unslashed")
    assert body == "OFFLINE status=exited_unslashed"


def test_build_withdrawal_default_amount():
    title, body = build_withdrawal(1234, "home-1")
    assert title == "validator 1234 home-1 withdrawal"
    assert body == "0.0010 ETH withdrawn"  # 4-decimal format matches alerts.py


def test_build_withdrawal_custom_amount():
    _, body = build_withdrawal(1, "", amount_eth=0.5)
    assert body == "0.5000 ETH withdrawn"


def test_build_proposing_soon_default():
    title, body = build_proposing_soon(1234, "home-1")
    assert title == "validator 1234 home-1 proposing soon"
    assert body == "slot 12345 (~6 min away)"


def test_build_proposing_soon_custom_slot_and_delay():
    _, body = build_proposing_soon(1, "", slot=999, delay="~30s")
    assert body == "slot 999 (~30s away)"


def test_build_proposed_default_slot():
    title, body = build_proposed(1234, "home-1")
    assert title == "validator 1234 home-1 proposed slot 12345"
    assert body == "✓ block landed at slot 12345"


def test_build_missed_proposal_default_slot():
    title, body = build_missed_proposal(1234, "home-1")
    assert title == "validator 1234 home-1 missed proposal at slot 12345"
    assert body == "✗ no block produced at slot 12345"


from eth_validator_stats._simulate import (
    EVENTS,
    build_blind,
    build_recovered,
)


def test_build_blind():
    title, body = build_blind()
    assert title == "MONITOR BLIND"
    assert body == "beacon node unreachable: simulated"


def test_build_recovered():
    title, body = build_recovered()
    assert title == "MONITOR RECOVERED"
    assert body == "beacon node reachable again"


def test_events_table_has_all_eight_events():
    expected = {
        "missed-attestation", "offline", "withdrawal",
        "proposing-soon", "proposed", "missed-proposal",
        "blind", "recovered",
    }
    assert set(EVENTS.keys()) == expected


def test_events_table_scopes_correct():
    validator_scoped = {
        "missed-attestation", "offline", "withdrawal",
        "proposing-soon", "proposed", "missed-proposal",
    }
    for name, (_, scope) in EVENTS.items():
        if name in validator_scoped:
            assert scope == "validator", f"{name} should be validator-scoped"
        else:
            assert scope == "global", f"{name} should be global-scoped"
