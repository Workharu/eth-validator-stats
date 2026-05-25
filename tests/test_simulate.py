from __future__ import annotations

from eth_validator_stats._simulate import (
    build_missed_attestation,
    build_missed_proposal,
    build_offline,
    build_proposed,
    build_proposing_soon,
    build_withdrawal,
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


# Re-imports below intentionally split out for readability — these
# blocks deal with the *cli.cmd_simulate* path, not the alert builders.
from eth_validator_stats._simulate import (  # noqa: E402
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


# --- lifecycle builders -----------------------------------------------------

def test_build_activated():
    from eth_validator_stats._simulate import build_activated
    title, body = build_activated(1234, "home-1")
    assert title == "validator 1234 home-1 ACTIVATED"
    assert "was pending_queued" in body


def test_build_exit_initiated():
    from eth_validator_stats._simulate import build_exit_initiated
    title, body = build_exit_initiated(1234, "home-1")
    assert title == "validator 1234 home-1 EXIT INITIATED"
    assert "voluntary exit" in body


def test_build_slashed():
    from eth_validator_stats._simulate import build_slashed
    title, body = build_slashed(1234, "home-1")
    assert title == "validator 1234 home-1 SLASHED"
    assert "active_slashed" in body


def test_build_exited():
    from eth_validator_stats._simulate import build_exited
    title, body = build_exited(1234, "home-1")
    assert title == "validator 1234 home-1 EXITED"
    assert "exit complete" in body


def test_build_withdrawal_ready():
    from eth_validator_stats._simulate import build_withdrawal_ready
    title, body = build_withdrawal_ready(1234, "home-1")
    assert title == "validator 1234 home-1 WITHDRAWAL READY"
    assert "claimable" in body


def test_events_table_has_all_registered_events():
    expected = {
        "missed-attestation", "offline", "withdrawal",
        "proposing-soon", "proposed", "missed-proposal",
        "blind", "recovered",
        # Lifecycle additions
        "activated", "exit-initiated", "slashed", "exited", "withdrawal-ready",
    }
    assert set(EVENTS.keys()) == expected


def test_events_table_scopes_correct():
    validator_scoped = {
        "missed-attestation", "offline", "withdrawal",
        "proposing-soon", "proposed", "missed-proposal",
        "activated", "exit-initiated", "slashed", "exited", "withdrawal-ready",
    }
    for name, (_, scope) in EVENTS.items():
        if name in validator_scoped:
            assert scope == "validator", f"{name} should be validator-scoped"
        else:
            assert scope == "global", f"{name} should be global-scoped"


import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

from eth_validator_stats.cli import cmd_simulate  # noqa: E402
from eth_validator_stats.config_io import ConfigEntry  # noqa: E402


def _cfg_with(tmp_path: Path, monkeypatch, *, validators, ntfy_topic="https://ntfy.example/t", icon_url=None):
    """Write a YAML config to tmp_path and point ETH_VALIDATOR_STATS_CONFIG at it."""
    import yaml
    alerts: dict = {"ntfy_topic": ntfy_topic, "cooldown_minutes": 30}
    if icon_url is not None:
        alerts["icon_url"] = icon_url
    data = {
        "beacon_node_url": "http://localhost:3500",
        "validators": [
            {"index": v.index, "label": v.label} if v.pubkey is None
            else {"pubkey": v.pubkey, "label": v.label}
            for v in validators
        ],
        "alerts": alerts,
    }
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))
    return cfg_path


def _args(event, *, validator=None, last=None, status=None, amount_eth=None, slot=None, delay=None, log_level=None):
    return argparse.Namespace(
        event=event, validator=validator, last=last, status=status,
        amount_eth=amount_eth, slot=slot, delay=delay, log_level=log_level,
        cmd="simulate",
    )


def test_cmd_simulate_missed_attestation_uses_first_validator(tmp_path, monkeypatch, capsys):
    v = ConfigEntry(identifier="1234", label="home-1", pubkey=None, index=1234)
    _cfg_with(tmp_path, monkeypatch, validators=[v])

    captured = []
    class StubNotifier:
        def send(self, title, body, *, priority=None):
            captured.append((title, body))

    rc = cmd_simulate(_args("missed-attestation"), _notifier=StubNotifier())
    assert rc == 0
    assert captured == [("validator 1234 home-1", "MISSED_ATTESTATIONS last=2")]


def test_cmd_simulate_validator_flag_selects_by_index(tmp_path, monkeypatch):
    v1 = ConfigEntry(identifier="1", label="a", pubkey=None, index=1)
    v2 = ConfigEntry(identifier="2", label="b", pubkey=None, index=2)
    _cfg_with(tmp_path, monkeypatch, validators=[v1, v2])

    captured = []
    class StubNotifier:
        def send(self, title, body, *, priority=None):
            captured.append((title, body))

    rc = cmd_simulate(_args("missed-attestation", validator=2), _notifier=StubNotifier())
    assert rc == 0
    assert captured[0][0] == "validator 2 b"


def test_cmd_simulate_validator_flag_not_found_exits_1(tmp_path, monkeypatch, capsys):
    v1 = ConfigEntry(identifier="1", label="a", pubkey=None, index=1)
    _cfg_with(tmp_path, monkeypatch, validators=[v1])

    class StubNotifier:
        def send(self, *_, **__): raise AssertionError("should not send")

    rc = cmd_simulate(_args("missed-attestation", validator=999), _notifier=StubNotifier())
    assert rc == 1
    err = capsys.readouterr().err
    assert "999" in err


def test_cmd_simulate_pubkey_only_default_exits_1_with_hint(tmp_path, monkeypatch, capsys):
    """First-validator default needs a resolved index; pubkey-only without --validator fails clearly."""
    v = ConfigEntry(identifier="0xabc", label="", pubkey="0xabc", index=None)
    _cfg_with(tmp_path, monkeypatch, validators=[v])

    class StubNotifier:
        def send(self, *_, **__): raise AssertionError("should not send")

    rc = cmd_simulate(_args("missed-attestation"), _notifier=StubNotifier())
    assert rc == 1
    err = capsys.readouterr().err
    assert "no resolved index" in err


def test_cmd_simulate_no_ntfy_topic_exits_1(tmp_path, monkeypatch, capsys):
    v = ConfigEntry(identifier="1", label="a", pubkey=None, index=1)
    _cfg_with(tmp_path, monkeypatch, validators=[v], ntfy_topic="")

    rc = cmd_simulate(_args("missed-attestation"))  # no _notifier injection
    assert rc == 1
    err = capsys.readouterr().err
    assert "ntfy_topic" in err


def test_cmd_simulate_slashed_forwards_urgent_priority(tmp_path, monkeypatch):
    """`simulate slashed` must mirror the production lifecycle pipeline by
    flagging the push as ntfy Priority: urgent. Without this, the test push
    arrives as a normal notification and the operator can't verify their
    DND-bypass setup works."""
    v = ConfigEntry(identifier="1", label="a", pubkey=None, index=1)
    _cfg_with(tmp_path, monkeypatch, validators=[v])

    captured: list[tuple[str, str, str | None]] = []
    class StubNotifier:
        def send(self, title, body, *, priority=None):
            captured.append((title, body, priority))

    rc = cmd_simulate(_args("slashed"), _notifier=StubNotifier())
    assert rc == 0
    assert len(captured) == 1
    title, _, priority = captured[0]
    assert "SLASHED" in title
    assert priority == "urgent"


def test_cmd_simulate_other_events_no_priority(tmp_path, monkeypatch):
    """Non-slashed lifecycle events ride the default ntfy priority."""
    v = ConfigEntry(identifier="1", label="a", pubkey=None, index=1)
    _cfg_with(tmp_path, monkeypatch, validators=[v])

    captured: list[tuple[str, str, str | None]] = []
    class StubNotifier:
        def send(self, title, body, *, priority=None):
            captured.append((title, body, priority))

    cmd_simulate(_args("activated"), _notifier=StubNotifier())
    assert captured[0][2] is None


def test_cmd_simulate_blind_event_does_not_require_validator(tmp_path, monkeypatch):
    v = ConfigEntry(identifier="1", label="a", pubkey=None, index=1)
    _cfg_with(tmp_path, monkeypatch, validators=[v])

    captured = []
    class StubNotifier:
        def send(self, title, body, *, priority=None):
            captured.append((title, body))

    rc = cmd_simulate(_args("blind"), _notifier=StubNotifier())
    assert rc == 0
    assert captured == [("MONITOR BLIND", "beacon node unreachable: simulated")]


def test_cmd_simulate_event_kwargs_forwarded(tmp_path, monkeypatch):
    v = ConfigEntry(identifier="1", label="a", pubkey=None, index=1)
    _cfg_with(tmp_path, monkeypatch, validators=[v])

    captured = []
    class StubNotifier:
        def send(self, title, body, *, priority=None):
            captured.append((title, body))

    rc = cmd_simulate(_args("missed-attestation", last=7), _notifier=StubNotifier())
    assert rc == 0
    assert captured[0][1] == "MISSED_ATTESTATIONS last=7"


def test_cmd_simulate_notifier_raises_exits_1(tmp_path, monkeypatch, capsys):
    """If the real notifier raises (e.g. ntfy 500), simulate exits 1."""
    v = ConfigEntry(identifier="1", label="a", pubkey=None, index=1)
    _cfg_with(tmp_path, monkeypatch, validators=[v])

    class BoomNotifier:
        def send(self, *_, **__):
            import httpx
            req = httpx.Request("POST", "https://ntfy.example/t")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("500", request=req, response=resp)

    rc = cmd_simulate(_args("missed-attestation"), _notifier=BoomNotifier())
    assert rc == 1
    err = capsys.readouterr().err
    assert "simulate failed" in err.lower() or "500" in err


def test_simulate_end_to_end_via_main_hits_real_notifier(tmp_path, monkeypatch, capsys):
    """Drive `main(['simulate', 'missed-attestation'])` end-to-end. Inject a
    MockTransport into the real NtfyNotifier by monkey-patching the class
    at the cli import site so the POST is captured without hitting ntfy.sh.
    """
    import httpx

    from eth_validator_stats import cli as cli_mod
    from eth_validator_stats.alerts import NtfyNotifier as RealNtfyNotifier

    v = ConfigEntry(identifier="42", label="end2end", pubkey=None, index=42)
    _cfg_with(tmp_path, monkeypatch, validators=[v])

    captured: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.headers.get("title", ""), request.content.decode()))
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)

    def _factory(topic_url, *, timeout=5.0, raise_on_error=False, icon_url=""):
        return RealNtfyNotifier(
            topic_url, timeout=timeout, transport=transport,
            raise_on_error=raise_on_error, icon_url=icon_url,
        )

    monkeypatch.setattr(cli_mod, "NtfyNotifier", _factory)

    rc = cli_mod.main(["simulate", "missed-attestation"])
    assert rc == 0
    assert captured == [("validator 42 end2end", "MISSED_ATTESTATIONS last=2")]
    out = capsys.readouterr().out
    assert "sent:" in out


def _patch_e2e_notifier(monkeypatch, captured: list):
    """Replace cli's NtfyNotifier with a MockTransport-backed real one that
    records (title, icon, body) for each POST. Returns nothing — side-effect
    only. Mirrors the pattern in test_simulate_end_to_end_via_main_hits_real_notifier
    but exposes the Icon header so icon-routing tests can assert on it.
    """
    import httpx

    from eth_validator_stats import cli as cli_mod
    from eth_validator_stats.alerts import NtfyNotifier as RealNtfyNotifier

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((
            request.headers.get("title", ""),
            request.headers.get("icon", ""),
            request.content.decode(),
        ))
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)

    def _factory(topic_url, *, timeout=5.0, raise_on_error=False, icon_url=""):
        return RealNtfyNotifier(
            topic_url, timeout=timeout, transport=transport,
            raise_on_error=raise_on_error, icon_url=icon_url,
        )

    monkeypatch.setattr(cli_mod, "NtfyNotifier", _factory)


def test_simulate_sends_default_icon_header(tmp_path, monkeypatch):
    """Simulate must send the configured icon URL (defaulted to the app icon)
    as an Icon header so push receivers render the brand alongside the alert.
    """
    from eth_validator_stats import cli as cli_mod
    from eth_validator_stats.alerts import DEFAULT_NTFY_ICON_URL

    v = ConfigEntry(identifier="42", label="home", pubkey=None, index=42)
    _cfg_with(tmp_path, monkeypatch, validators=[v])  # no icon_url override → default

    captured: list[tuple[str, str, str]] = []
    _patch_e2e_notifier(monkeypatch, captured)

    rc = cli_mod.main(["simulate", "missed-attestation"])
    assert rc == 0
    assert len(captured) == 1
    assert captured[0][1] == DEFAULT_NTFY_ICON_URL


def test_simulate_sends_custom_icon_header(tmp_path, monkeypatch):
    """A user-configured icon_url must reach the ntfy push from the simulate path."""
    from eth_validator_stats import cli as cli_mod

    custom = "https://example.com/my-icon.png"
    v = ConfigEntry(identifier="42", label="home", pubkey=None, index=42)
    _cfg_with(tmp_path, monkeypatch, validators=[v], icon_url=custom)

    captured: list[tuple[str, str, str]] = []
    _patch_e2e_notifier(monkeypatch, captured)

    rc = cli_mod.main(["simulate", "missed-attestation"])
    assert rc == 0
    assert captured[0][1] == custom
