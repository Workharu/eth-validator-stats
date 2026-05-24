from __future__ import annotations

import json

import httpx
import pytest

from eth_validator_stats.alerts import (
    AlertsConfig,
    BLIND_KEY,
    NtfyNotifier,
    clear_blind_if_recovered,
    make_notifier,
    process_blind,
    process_proposal_outcomes,
    process_upcoming_proposals,
    process_validator_alerts,
    process_withdrawals,
    prune_scheduled_proposals,
    record_scheduled_proposals,
)


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, body: str) -> None:
        self.sent.append((title, body))


def _cfg(cooldown_minutes=30, storm_threshold=10, ntfy=""):
    return AlertsConfig(ntfy_topic=ntfy, cooldown_minutes=cooldown_minutes, storm_threshold=storm_threshold)


def test_first_alert_notifies():
    state = {"validators": {}}
    notif = FakeNotifier()
    cfg = _cfg()
    process_validator_alerts(state, {1}, [(1, "v1", "OFFLINE x")], notif, cfg, now=1000)
    assert len(notif.sent) == 1
    assert "validator 1 v1" in notif.sent[0][0]
    assert state["validators"]["1"]["alerted_rule"] == "OFFLINE x"
    assert state["validators"]["1"]["alerted_until_ts"] == 1000 + 30 * 60


def test_same_alert_within_cooldown_is_suppressed():
    state = {"validators": {}}
    notif = FakeNotifier()
    cfg = _cfg(cooldown_minutes=30)
    process_validator_alerts(state, {1}, [(1, "v1", "OFFLINE x")], notif, cfg, now=1000)
    process_validator_alerts(state, {1}, [(1, "v1", "OFFLINE x")], notif, cfg, now=1000 + 60)
    assert len(notif.sent) == 1  # only the first one


def test_same_alert_after_cooldown_refires():
    state = {"validators": {}}
    notif = FakeNotifier()
    cfg = _cfg(cooldown_minutes=30)
    process_validator_alerts(state, {1}, [(1, "v1", "OFFLINE x")], notif, cfg, now=1000)
    process_validator_alerts(state, {1}, [(1, "v1", "OFFLINE x")], notif, cfg, now=1000 + 30 * 60 + 1)
    assert len(notif.sent) == 2


def test_rule_transition_fires_even_in_cooldown():
    state = {"validators": {}}
    notif = FakeNotifier()
    cfg = _cfg(cooldown_minutes=30)
    process_validator_alerts(state, {1}, [(1, "v1", "OFFLINE")], notif, cfg, now=1000)
    process_validator_alerts(state, {1}, [(1, "v1", "MISSED_ATTESTATIONS last=3")], notif, cfg, now=1100)
    assert len(notif.sent) == 2
    assert "MISSED_ATTESTATIONS" in notif.sent[1][1]


def test_recovery_message_sent_when_alert_clears():
    state = {"validators": {}}
    notif = FakeNotifier()
    cfg = _cfg()
    process_validator_alerts(state, {1}, [(1, "v1", "OFFLINE")], notif, cfg, now=1000)
    notif.sent.clear()
    process_validator_alerts(state, {1}, [], notif, cfg, now=2000)
    assert len(notif.sent) == 1
    assert "RECOVERED" in notif.sent[0][0]
    assert state["validators"]["1"]["alerted_rule"] is None


def test_recovery_only_for_configured_validators():
    """A validator removed from config should NOT spuriously fire 'recovered'."""
    state = {"validators": {"99": {"alerted_rule": "OFFLINE", "alerted_until_ts": 5000}}}
    notif = FakeNotifier()
    cfg = _cfg()
    # 99 is not in configured set
    process_validator_alerts(state, {1}, [], notif, cfg, now=1000)
    assert notif.sent == []
    # state untouched for unconfigured 99
    assert state["validators"]["99"]["alerted_rule"] == "OFFLINE"


def test_storm_grouping_collapses_to_single_message():
    state = {"validators": {}}
    notif = FakeNotifier()
    cfg = _cfg(storm_threshold=3)
    current = [(i, f"v{i}", "OFFLINE") for i in range(1, 10)]  # 9 alerts > 3 threshold
    configured = {i for i, _, _ in current}
    process_validator_alerts(state, configured, current, notif, cfg, now=1000)
    assert len(notif.sent) == 1
    assert "VALIDATOR STORM" in notif.sent[0][0]
    assert "9 new validator alerts" in notif.sent[0][1]


def test_storm_threshold_boundary_does_not_collapse():
    state = {"validators": {}}
    notif = FakeNotifier()
    cfg = _cfg(storm_threshold=3)
    # 3 alerts == threshold, not >, so individual messages
    current = [(1, "v1", "OFFLINE"), (2, "v2", "OFFLINE"), (3, "v3", "OFFLINE")]
    process_validator_alerts(state, {1, 2, 3}, current, notif, cfg, now=1000)
    assert len(notif.sent) == 3
    assert all("VALIDATOR STORM" not in t for t, _ in notif.sent)


def test_recovery_storm_grouping_collapses_many_recoveries():
    """A mass-outage recovery sends one summary, not N individual RECOVERED pushes."""
    # Seed state with 5 validators all in 'alerted' status
    state = {"validators": {str(i): {"alerted_rule": "OFFLINE", "alerted_until_ts": 5000, "label": f"v{i}"}
                            for i in range(1, 6)}}
    notif = FakeNotifier()
    cfg = _cfg(storm_threshold=3)  # 5 recoveries > 3 threshold
    # No current alerts — all 5 recovered simultaneously
    process_validator_alerts(state, {1, 2, 3, 4, 5}, [], notif, cfg, now=10_000)
    assert len(notif.sent) == 1
    assert notif.sent[0][0] == "VALIDATOR STORM RECOVERED"
    assert "5 validators recovered" in notif.sent[0][1]


def test_recovery_storm_threshold_boundary_does_not_collapse():
    state = {"validators": {str(i): {"alerted_rule": "OFFLINE", "alerted_until_ts": 5000, "label": f"v{i}"}
                            for i in range(1, 4)}}
    notif = FakeNotifier()
    cfg = _cfg(storm_threshold=3)  # 3 == threshold, not >
    process_validator_alerts(state, {1, 2, 3}, [], notif, cfg, now=10_000)
    assert len(notif.sent) == 3
    assert all("STORM" not in t for t, _ in notif.sent)


def test_blind_notifies_once_within_cooldown():
    state: dict = {}
    notif = FakeNotifier()
    cfg = _cfg(cooldown_minutes=30)
    process_blind(state, "conn refused", notif, cfg, now=1000)
    process_blind(state, "conn refused", notif, cfg, now=1000 + 60)
    assert len(notif.sent) == 1
    assert notif.sent[0][0] == "MONITOR BLIND"


def test_blind_renotifies_after_cooldown():
    state: dict = {}
    notif = FakeNotifier()
    cfg = _cfg(cooldown_minutes=30)
    process_blind(state, "err", notif, cfg, now=1000)
    process_blind(state, "err", notif, cfg, now=1000 + 30 * 60 + 1)
    assert len(notif.sent) == 2


def test_clear_blind_emits_recovery_then_idempotent():
    state = {BLIND_KEY: 9999}
    notif = FakeNotifier()
    clear_blind_if_recovered(state, notif)
    assert len(notif.sent) == 1
    assert notif.sent[0][0] == "MONITOR RECOVERED"
    assert state[BLIND_KEY] == 0
    # second call is no-op
    clear_blind_if_recovered(state, notif)
    assert len(notif.sent) == 1


def test_clear_blind_noop_when_never_blind():
    state: dict = {}
    notif = FakeNotifier()
    clear_blind_if_recovered(state, notif)
    assert notif.sent == []


def test_make_notifier_returns_null_when_no_topic():
    n = make_notifier(_cfg(ntfy=""))
    n.send("x", "y")  # must not raise


def test_make_notifier_returns_ntfy_when_topic_set():
    n = make_notifier(_cfg(ntfy="https://ntfy.sh/some-topic"))
    assert isinstance(n, NtfyNotifier)


def test_ntfy_posts_with_title_header_and_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content.decode()
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    n = NtfyNotifier("https://ntfy.sh/eth-test", transport=transport)
    n.send("validator 12345", "OFFLINE status=exited_slashed")

    assert seen["method"] == "POST"
    assert seen["url"] == "https://ntfy.sh/eth-test"
    assert seen["headers"].get("title") == "validator 12345"
    assert seen["body"] == "OFFLINE status=exited_slashed"


def test_ntfy_swallows_errors(caplog):
    """Notification failure must not crash check command."""
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    transport = httpx.MockTransport(handler)
    n = NtfyNotifier("https://ntfy.sh/eth-test", transport=transport)
    with caplog.at_level(logging.WARNING, logger="eth_validator_stats.alerts"):
        n.send("t", "b")  # must not raise
    assert any("ntfy notify failed" in rec.message for rec in caplog.records)


def test_ntfy_title_unicode_silently_fails(caplog):
    """Documents the constraint: non-ASCII in the Title header is rejected by httpx.
    NtfyNotifier swallows the error via logger.warning; nothing reaches the server. This is
    why all caller-supplied titles in alerts.py must stay ASCII (glyphs in body)."""
    import logging

    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["title"] = request.headers.get("title")
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    n = NtfyNotifier("https://ntfy.sh/x", transport=transport)
    with caplog.at_level(logging.WARNING, logger="eth_validator_stats.alerts"):
        n.send("validator 1 ✓ proposed", "body ok")
    assert sent == {}  # never reached the handler
    assert any("ntfy notify failed" in rec.message for rec in caplog.records)


def test_empty_topic_is_silent_noop():
    """NtfyNotifier with empty topic should not even attempt a request."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    transport = httpx.MockTransport(handler)
    n = NtfyNotifier("", transport=transport)
    n.send("t", "b")  # silent no-op


# --- withdrawals -------------------------------------------------------------

def _w_state(prev_gwei, curr_gwei, status="active_ongoing", label="v1"):
    return {
        "validators": {
            "1": {
                "previous_balance_gwei": prev_gwei,
                "last_balance_gwei": curr_gwei,
                "last_status": status,
                "label": label,
            }
        }
    }


def test_withdrawal_drop_above_threshold_fires():
    state = _w_state(prev_gwei=32_050_000_000, curr_gwei=32_000_000_000)  # 0.05 ETH drop
    notif = FakeNotifier()
    out = process_withdrawals(state, {1}, notif, _cfg())
    assert len(out) == 1
    assert out[0] == (1, "v1", 50_000_000)
    assert len(notif.sent) == 1
    assert "withdrawal" in notif.sent[0][0]
    assert "0.0500 ETH" in notif.sent[0][1]


def test_withdrawal_below_threshold_skipped():
    # threshold 1_000_000 gwei = 0.001 ETH
    state = _w_state(prev_gwei=32_000_000_000, curr_gwei=31_999_500_000)  # 0.0005 ETH drop
    notif = FakeNotifier()
    out = process_withdrawals(state, {1}, notif, _cfg())
    assert out == []
    assert notif.sent == []


def test_withdrawal_balance_unchanged_no_fire():
    state = _w_state(prev_gwei=32_000_000_000, curr_gwei=32_000_000_000)
    notif = FakeNotifier()
    out = process_withdrawals(state, {1}, notif, _cfg())
    assert out == []


def test_withdrawal_balance_increased_no_fire():
    state = _w_state(prev_gwei=32_000_000_000, curr_gwei=32_010_000_000)  # earned rewards
    notif = FakeNotifier()
    out = process_withdrawals(state, {1}, notif, _cfg())
    assert out == []


def test_withdrawal_skipped_when_status_not_active():
    """Slashing/exit balance drops are alerted via OFFLINE rule, not withdrawal."""
    state = _w_state(prev_gwei=32_000_000_000, curr_gwei=16_000_000_000, status="exited_slashed")
    notif = FakeNotifier()
    out = process_withdrawals(state, {1}, notif, _cfg())
    assert out == []


def test_withdrawal_skipped_when_previous_missing():
    """First-ever poll has no previous_balance_gwei yet."""
    state = {"validators": {"1": {"last_balance_gwei": 32_000_000_000, "last_status": "active_ongoing"}}}
    notif = FakeNotifier()
    out = process_withdrawals(state, {1}, notif, _cfg())
    assert out == []


def test_withdrawal_skipped_for_unconfigured_validator():
    state = _w_state(prev_gwei=32_050_000_000, curr_gwei=32_000_000_000)
    notif = FakeNotifier()
    out = process_withdrawals(state, set(), notif, _cfg())  # validator 1 not configured
    assert out == []


def test_withdrawal_skipped_when_slot_gap_too_wide():
    """If many slots passed between balance snapshots, the drop is ambiguous
    (could be cumulative attestation losses) — don't attribute it to a withdrawal."""
    state = {"validators": {"1": {
        "previous_balance_gwei": 32_050_000_000,
        "previous_balance_at_slot": 1000,    # snapshot was 200 slots ago
        "last_balance_gwei": 32_000_000_000,
        "last_balance_at_slot": 1200,
        "last_status": "active_ongoing",
        "label": "v1",
    }}}
    notif = FakeNotifier()
    cfg = AlertsConfig(withdrawal_max_gap_slots=64)  # 200 > 64 → skip
    out = process_withdrawals(state, {1}, notif, cfg, current_slot=1200)
    assert out == []
    assert notif.sent == []


def test_withdrawal_fires_when_slot_gap_within_window():
    state = {"validators": {"1": {
        "previous_balance_gwei": 32_050_000_000,
        "previous_balance_at_slot": 1150,    # 50 slots ago
        "last_balance_gwei": 32_000_000_000,
        "last_balance_at_slot": 1200,
        "last_status": "active_ongoing",
        "label": "v1",
    }}}
    notif = FakeNotifier()
    cfg = AlertsConfig(withdrawal_max_gap_slots=64)  # 50 <= 64 → fires
    out = process_withdrawals(state, {1}, notif, cfg, current_slot=1200)
    assert len(out) == 1
    assert "withdrawal" in notif.sent[0][0]


def test_withdrawal_gap_check_disabled_when_current_slot_zero():
    """Backward-compat path used by existing tests: omit current_slot → skip the gap check."""
    state = {"validators": {"1": {
        "previous_balance_gwei": 32_050_000_000,
        "previous_balance_at_slot": 100,     # ancient
        "last_balance_gwei": 32_000_000_000,
        "last_status": "active_ongoing",
        "label": "v1",
    }}}
    notif = FakeNotifier()
    cfg = AlertsConfig(withdrawal_max_gap_slots=64)
    out = process_withdrawals(state, {1}, notif, cfg)  # no current_slot
    assert len(out) == 1  # gap check disabled, alert fires


def test_withdrawal_gap_check_skipped_when_previous_slot_missing():
    """Old state without previous_balance_at_slot must not crash; behaves as pre-gap-check."""
    state = {"validators": {"1": {
        "previous_balance_gwei": 32_050_000_000,
        "last_balance_gwei": 32_000_000_000,
        "last_status": "active_ongoing",
        "label": "v1",
    }}}
    notif = FakeNotifier()
    cfg = AlertsConfig(withdrawal_max_gap_slots=64)
    out = process_withdrawals(state, {1}, notif, cfg, current_slot=10000)
    assert len(out) == 1  # no previous_balance_at_slot → can't apply gap check, fall through


# --- proposals --------------------------------------------------------------

def test_record_scheduled_proposals_persists_only_configured_validators():
    state: dict = {}
    record_scheduled_proposals(
        state, [(3200, 1), (3210, 2), (3220, 999)], {1, 2},
    )
    v1 = state["validators"]["1"]["scheduled_proposals"]
    v2 = state["validators"]["2"]["scheduled_proposals"]
    assert v1 == [{"slot": 3200, "alerted": False, "verified": False}]
    assert v2 == [{"slot": 3210, "alerted": False, "verified": False}]
    assert "999" not in state["validators"]


def test_record_scheduled_proposals_is_idempotent():
    state: dict = {}
    record_scheduled_proposals(state, [(3200, 1)], {1})
    record_scheduled_proposals(state, [(3200, 1)], {1})
    assert len(state["validators"]["1"]["scheduled_proposals"]) == 1


def test_upcoming_proposal_within_lookahead_fires_once():
    state = {"validators": {"1": {"label": "v1", "scheduled_proposals": [
        {"slot": 3220, "alerted": False, "verified": False}]}}}
    notif = FakeNotifier()
    out = process_upcoming_proposals(state, 3200, 12, 32, 1, notif)
    assert len(out) == 1
    assert "proposing soon" in notif.sent[0][0]
    assert "slot 3220" in notif.sent[0][1]
    notif.sent.clear()
    process_upcoming_proposals(state, 3200, 12, 32, 1, notif)
    assert notif.sent == []


def test_upcoming_proposal_outside_window_no_fire():
    state = {"validators": {"1": {"label": "v1", "scheduled_proposals": [
        {"slot": 5000, "alerted": False, "verified": False}]}}}
    notif = FakeNotifier()
    out = process_upcoming_proposals(state, 3200, 12, 32, 1, notif)
    assert out == []
    assert notif.sent == []


def test_upcoming_proposal_already_passed_no_fire():
    state = {"validators": {"1": {"label": "v1", "scheduled_proposals": [
        {"slot": 3100, "alerted": False, "verified": False}]}}}
    notif = FakeNotifier()
    out = process_upcoming_proposals(state, 3200, 12, 32, 1, notif)
    assert out == []


def test_proposal_outcome_success():
    state = {"validators": {"1": {"label": "v1", "scheduled_proposals": [
        {"slot": 3100, "alerted": True, "verified": False}]}}}
    notif = FakeNotifier()
    out = process_proposal_outcomes(state, 3200, lambda s: 1, notif)
    assert out == [(1, "v1", 3100, True)]
    assert "proposed slot 3100" in notif.sent[0][0]
    assert "block landed" in notif.sent[0][1]
    # Title must be ASCII (HTTP header constraint); glyphs live in the body.
    notif.sent[0][0].encode("ascii")
    assert state["validators"]["1"]["scheduled_proposals"][0]["verified"] is True
    assert state["validators"]["1"]["scheduled_proposals"][0]["produced"] is True


def test_proposal_outcome_missed_slot_returns_none_header():
    state = {"validators": {"1": {"label": "v1", "scheduled_proposals": [
        {"slot": 3100, "alerted": True, "verified": False}]}}}
    notif = FakeNotifier()
    out = process_proposal_outcomes(state, 3200, lambda s: None, notif)
    assert out == [(1, "v1", 3100, False)]
    assert "missed proposal" in notif.sent[0][0]
    # Title must be ASCII (HTTP header constraint); glyphs live in the body.
    notif.sent[0][0].encode("ascii")


def test_proposal_outcome_skips_already_verified():
    state = {"validators": {"1": {"label": "v1", "scheduled_proposals": [
        {"slot": 3100, "alerted": True, "verified": True}]}}}
    notif = FakeNotifier()
    out = process_proposal_outcomes(state, 3200, lambda s: 1, notif)
    assert out == []
    assert notif.sent == []


def test_proposal_outcome_future_slot_skipped():
    state = {"validators": {"1": {"label": "v1", "scheduled_proposals": [
        {"slot": 5000, "alerted": False, "verified": False}]}}}
    notif = FakeNotifier()
    out = process_proposal_outcomes(state, 3200, lambda s: 1, notif)
    assert out == []


def test_proposal_outcome_fetch_error_leaves_unverified():
    def boom(s):
        raise ConnectionError("nope")
    state = {"validators": {"1": {"label": "v1", "scheduled_proposals": [
        {"slot": 3100, "alerted": True, "verified": False}]}}}
    notif = FakeNotifier()
    out = process_proposal_outcomes(state, 3200, boom, notif)
    assert out == []
    assert state["validators"]["1"]["scheduled_proposals"][0]["verified"] is False
    assert notif.sent == []


# --- pruning ----------------------------------------------------------------

def test_prune_drops_old_verified_proposals():
    state = {"validators": {"1": {"scheduled_proposals": [
        {"slot": 100, "alerted": True, "verified": True},     # old + verified → drop
        {"slot": 1500, "alerted": True, "verified": True},    # within window → keep
        {"slot": 200, "alerted": True, "verified": False},    # unverified → keep
    ]}}}
    removed = prune_scheduled_proposals(state, current_slot=2000, keep_slots=1000)
    assert removed == 1
    remaining = state["validators"]["1"]["scheduled_proposals"]
    slots_remaining = sorted(p["slot"] for p in remaining)
    assert slots_remaining == [200, 1500]


def test_prune_no_op_when_current_slot_low():
    """If we haven't been running long enough to cross the keep-window, prune is a no-op."""
    state = {"validators": {"1": {"scheduled_proposals": [
        {"slot": 100, "alerted": True, "verified": True},
    ]}}}
    removed = prune_scheduled_proposals(state, current_slot=500, keep_slots=1000)
    assert removed == 0
    assert len(state["validators"]["1"]["scheduled_proposals"]) == 1


def test_prune_keeps_unverified_regardless_of_age():
    """Unverified proposals are always preserved — they still need outcome lookup."""
    state = {"validators": {"1": {"scheduled_proposals": [
        {"slot": 50, "alerted": True, "verified": False},  # ancient but still unverified
    ]}}}
    removed = prune_scheduled_proposals(state, current_slot=10_000, keep_slots=1000)
    assert removed == 0
    assert state["validators"]["1"]["scheduled_proposals"][0]["slot"] == 50


def test_prune_handles_validator_without_scheduled_proposals():
    state = {"validators": {"1": {"label": "v1"}}}  # no scheduled_proposals key
    removed = prune_scheduled_proposals(state, current_slot=5000, keep_slots=1000)
    assert removed == 0


def test_ntfy_failure_is_logged_at_warning(caplog):
    import logging
    import httpx
    from eth_validator_stats.alerts import NtfyNotifier

    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated")

    transport = httpx.MockTransport(failing_handler)
    notifier = NtfyNotifier("https://ntfy.example/topic", transport=transport)

    with caplog.at_level(logging.WARNING, logger="eth_validator_stats.alerts"):
        notifier.send("title", "body")

    assert any(
        "ntfy notify failed" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]
