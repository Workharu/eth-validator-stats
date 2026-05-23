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
    process_validator_alerts,
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


def test_ntfy_swallows_errors(capsys):
    """Notification failure must not crash check command."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    transport = httpx.MockTransport(handler)
    n = NtfyNotifier("https://ntfy.sh/eth-test", transport=transport)
    n.send("t", "b")  # must not raise
    err = capsys.readouterr().err
    assert "ntfy notify failed" in err


def test_empty_topic_is_silent_noop():
    """NtfyNotifier with empty topic should not even attempt a request."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    transport = httpx.MockTransport(handler)
    n = NtfyNotifier("", transport=transport)
    n.send("t", "b")  # silent no-op
