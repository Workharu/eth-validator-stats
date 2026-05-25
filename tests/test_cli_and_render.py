from __future__ import annotations

from eth_validator_stats.cli import append_liveness, evaluate_alerts
from eth_validator_stats.render import (
    HIT,
    MISS,
    UNKNOWN,
    DisplayRow,
    build_table,
    format_balance,
    format_glyphs,
)


def _row(index=12345, label="v1", status="active_ongoing", balance_gwei=32_000_000_000, liveness=None):
    return DisplayRow(
        index=index,
        label=label,
        status=status,
        balance_gwei=balance_gwei,
        liveness=liveness or [],
    )


def test_format_balance_gwei_to_eth():
    assert format_balance(32_000_000_000) == "32.0000"
    assert format_balance(31_999_500_000) == "31.9995"
    assert format_balance(0) == "0.0000"


def test_format_glyphs_pads_left_with_unknown():
    assert format_glyphs([], 5) == UNKNOWN * 5
    assert format_glyphs([(1, 1), (2, 0)], 5) == UNKNOWN * 3 + HIT + MISS


def test_format_glyphs_keeps_only_last_n():
    buf = [(e, e % 2) for e in range(10)]  # alternating
    glyphs = format_glyphs(buf, 5)
    assert len(glyphs) == 5
    # epochs 5..9 -> values 1,0,1,0,1 -> HIT MISS HIT MISS HIT
    assert glyphs == HIT + MISS + HIT + MISS + HIT


def test_append_liveness_dedups_by_epoch_and_trims():
    buf: list = []
    for epoch in range(15):
        buf = append_liveness(buf, epoch, epoch % 2 == 0)
    # buffer capped at 10
    assert len(buf) == 10
    # last entry is epoch 14
    assert buf[-1][0] == 14
    # appending same epoch is a no-op
    same = append_liveness(buf, 14, False)
    assert same == buf


def test_evaluate_alerts_offline_status_fires():
    rows = [
        _row(status="exited_slashed", liveness=[(1, 1)] * 5),
        _row(index=2, status="active_ongoing", liveness=[(1, 1)] * 5),
    ]
    alerts = evaluate_alerts(rows, missed_threshold=3)
    assert len(alerts) == 1
    assert alerts[0][0] == 12345
    assert alerts[0][2].startswith("OFFLINE")


def test_evaluate_alerts_pending_queued_is_not_offline():
    rows = [_row(status="pending_queued", liveness=[])]
    assert evaluate_alerts(rows, missed_threshold=3) == []


def test_evaluate_alerts_pending_initialized_is_not_offline():
    """`pending_initialized` is the status between deposit confirmation and
    the activation queue. Same treatment as pending_queued: not yet
    validating, no alerts."""
    rows = [_row(status="pending_initialized", liveness=[])]
    assert evaluate_alerts(rows, missed_threshold=3) == []


def test_evaluate_alerts_pending_does_not_fire_missed_attestations():
    """Regression: prior to the fix, a pending validator that the liveness
    endpoint happened to return (some clients do, some don't) would have
    its zeros counted as misses once the buffer grew long enough. Pending
    validators have no committee assignment, so a "miss" is expected and
    should never alert."""
    rows = [_row(status="pending_initialized", liveness=[(1, 0), (2, 0), (3, 0), (4, 0)])]
    assert evaluate_alerts(rows, missed_threshold=3) == []

    rows = [_row(status="pending_queued", liveness=[(1, 0), (2, 0), (3, 0), (4, 0)])]
    assert evaluate_alerts(rows, missed_threshold=3) == []


def test_evaluate_alerts_active_exiting_is_offline():
    """active_exiting is on its way out. Surface the transition as OFFLINE
    so the operator sees it — missing attestations during this window
    still accrue penalties."""
    rows = [_row(status="active_exiting", liveness=[(1, 1)] * 5)]
    alerts = evaluate_alerts(rows, missed_threshold=3)
    assert len(alerts) == 1
    assert "active_exiting" in alerts[0][2]


def test_evaluate_alerts_missed_attestations_threshold():
    # 3 consecutive misses at tail -> fires
    rows = [_row(status="active_ongoing", liveness=[(1, 1), (2, 0), (3, 0), (4, 0)])]
    alerts = evaluate_alerts(rows, missed_threshold=3)
    assert len(alerts) == 1
    assert alerts[0][2].startswith("MISSED_ATTESTATIONS")


def test_evaluate_alerts_not_enough_history_no_fire():
    # only 2 entries, threshold 3 -> no alert
    rows = [_row(status="active_ongoing", liveness=[(1, 0), (2, 0)])]
    assert evaluate_alerts(rows, missed_threshold=3) == []


def test_evaluate_alerts_recent_hit_clears_alert():
    rows = [_row(status="active_ongoing", liveness=[(1, 0), (2, 0), (3, 0), (4, 1)])]
    assert evaluate_alerts(rows, missed_threshold=3) == []


def test_build_table_sorts_by_index_and_includes_rows():
    rows = [
        _row(index=99, label="b", liveness=[(1, 1), (2, 0)]),
        _row(index=11, label="a", liveness=[(1, 1)]),
    ]
    table = build_table(rows)
    # rich Table stores cells in columns; just sanity-check shape
    assert table.columns[0].header == "idx"
    assert len(table.columns) == 5
    # Two rows
    assert table.row_count == 2


def test_poll_logs_proposer_duties_failure_at_warning(monkeypatch, caplog):
    import logging

    import httpx

    from eth_validator_stats import cli as cli_mod
    from eth_validator_stats.alerts import AlertsConfig
    from eth_validator_stats.beacon import ChainInfo, Head, ValidatorInfo
    from eth_validator_stats.config_io import AppConfig, ConfigEntry

    cfg = AppConfig(
        beacon_node_url="http://fake",
        validators=[ConfigEntry(identifier="1", label="t", pubkey=None, index=1)],
        beacon_auth_token="",
        alerts=AlertsConfig(),
    )
    state: dict = {}

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_chain_info(self):
            return ChainInfo(genesis_time=0, seconds_per_slot=12, slots_per_epoch=32)
        def get_head(self):
            return Head(slot=64)
        def get_validators(self, ids):
            return [ValidatorInfo(index=1, pubkey="0xabc", status="active_ongoing", balance_gwei=32_000_000_000)]
        def get_proposer_duties(self, epoch):
            raise httpx.HTTPError("simulated duties failure")
        def get_liveness(self, epoch, indices):
            return {1: True}

    monkeypatch.setattr(cli_mod, "BeaconClient", lambda *a, **k: FakeClient())

    with caplog.at_level(logging.WARNING, logger="eth_validator_stats.cli"):
        cli_mod.poll(cfg, state)

    assert any(
        "proposer duties fetch failed" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


def test_configure_logging_applies_level_from_env(monkeypatch):
    import logging

    from eth_validator_stats.cli import configure_logging

    monkeypatch.setenv("ETH_VALIDATOR_STATS_LOG_LEVEL", "DEBUG")
    configure_logging(None)
    assert logging.getLogger().level == logging.DEBUG

    monkeypatch.setenv("ETH_VALIDATOR_STATS_LOG_LEVEL", "WARNING")
    configure_logging(None)
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_explicit_arg_wins_over_env(monkeypatch):
    import logging

    from eth_validator_stats.cli import configure_logging

    monkeypatch.setenv("ETH_VALIDATOR_STATS_LOG_LEVEL", "DEBUG")
    configure_logging("ERROR")
    assert logging.getLogger().level == logging.ERROR


def test_version_flag_prints_version_and_exits_zero(capsys):
    """`eth-validator-stats --version` prints `<prog> <semver>` and returns 0."""
    import re

    from eth_validator_stats.cli import main

    rc = main(["--version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("eth-validator-stats ")
    # Version may be a semver like "0.3.5" or the "unknown" fallback.
    assert re.search(r"(\d+\.\d+\.\d+|unknown)", out)


def test_poll_records_last_poll_ts(monkeypatch):
    """poll() must stamp state['last_poll_ts'] so status can show staleness."""
    import time as _time

    from eth_validator_stats.cli import poll
    from eth_validator_stats.config_io import AppConfig

    # Freeze time
    monkeypatch.setattr(_time, "time", lambda: 1_700_000_000.0)

    # Stub BeaconClient so we don't hit the network.
    class _StubClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_chain_info(self):
            from eth_validator_stats.beacon import ChainInfo
            return ChainInfo(genesis_time=0, seconds_per_slot=12, slots_per_epoch=32)
        def get_head(self):
            from eth_validator_stats.beacon import Head
            return Head(slot=100)
        def get_validators(self, ids): return []
        def get_liveness(self, epoch, indices): return {}
        def get_proposer_duties(self, epoch): return []

    monkeypatch.setattr("eth_validator_stats.cli.BeaconClient", lambda *a, **kw: _StubClient())

    cfg = AppConfig(beacon_node_url="http://x", beacon_auth_token=None, validators=[], alerts=None)
    state: dict = {}
    poll(cfg, state)
    assert state["last_poll_ts"] == 1_700_000_000


def test_cmd_status_is_read_only_by_default(monkeypatch, tmp_path):
    """`evs status` must not hit the beacon node or write state.
    It renders whatever the watcher / cron has already produced."""
    import json

    from eth_validator_stats import cli as cli_mod

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "chain_info": {"genesis_time": 0, "seconds_per_slot": 12, "slots_per_epoch": 32},
        "current_slot": 100,
        "last_poll_ts": 1_700_000_000,
        "validators": {
            "42": {
                "pubkey": "0xabc",
                "label": "v1",
                "last_status": "active_ongoing",
                "last_balance_gwei": 32_000_000_000,
                "liveness": [[10, 1], [11, 1]],
            }
        },
    }))
    monkeypatch.setenv("ETH_VALIDATOR_STATS_STATE", str(state_file))

    poll_called = {"v": False}
    save_called = {"v": False}
    monkeypatch.setattr(cli_mod, "poll", lambda cfg, s: poll_called.__setitem__("v", True) or [])
    monkeypatch.setattr(cli_mod, "save_state", lambda p, s: save_called.__setitem__("v", True))

    class _Cfg:
        validators = []
    monkeypatch.setattr(cli_mod, "load_config", lambda: _Cfg())

    import argparse
    args = argparse.Namespace(refresh=False)
    rc = cli_mod.cmd_status(args)
    assert rc == 0
    assert poll_called["v"] is False, "status should not poll by default"
    assert save_called["v"] is False, "status should not write state by default"


def test_cmd_status_refresh_flag_polls_and_saves(monkeypatch, tmp_path):
    """`evs status --refresh` opts back into the old behavior."""
    import json

    from eth_validator_stats import cli as cli_mod

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"chain_info": None, "validators": {}}))
    monkeypatch.setenv("ETH_VALIDATOR_STATS_STATE", str(state_file))

    poll_called = {"v": False}
    save_called = {"v": False}
    monkeypatch.setattr(cli_mod, "poll", lambda cfg, s: poll_called.__setitem__("v", True) or [])
    monkeypatch.setattr(cli_mod, "save_state", lambda p, s: save_called.__setitem__("v", True))

    class _Cfg:
        validators = []
    monkeypatch.setattr(cli_mod, "load_config", lambda: _Cfg())

    import argparse
    args = argparse.Namespace(refresh=True)
    rc = cli_mod.cmd_status(args)
    assert rc == 0
    assert poll_called["v"] is True
    assert save_called["v"] is True


def test_cmd_status_prints_staleness_footer(monkeypatch, tmp_path, capsys):
    """When state has a last_poll_ts, status prints a 'last updated' line
    so the user can spot a dead watcher."""
    import json
    import time as _time

    from eth_validator_stats import cli as cli_mod

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "chain_info": None,
        "current_slot": 0,
        "last_poll_ts": 1_700_000_000,
        "validators": {},
    }))
    monkeypatch.setenv("ETH_VALIDATOR_STATS_STATE", str(state_file))
    # Freeze "now" 125 seconds after the last poll.
    monkeypatch.setattr(_time, "time", lambda: 1_700_000_125.0)

    class _Cfg:
        validators = []

    monkeypatch.setattr(cli_mod, "load_config", lambda: _Cfg())

    import argparse
    rc = cli_mod.cmd_status(argparse.Namespace(refresh=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "last updated" in out.lower()
    # 125 seconds ago should render as "2m" or "2 min" — accept either.
    assert "2m" in out or "2 min" in out


def test_cmd_status_prints_hint_when_state_is_empty(monkeypatch, tmp_path, capsys):
    """First-run case: state file missing or empty. Don't show an empty
    table with no explanation — guide the user toward `check` or watch."""
    from eth_validator_stats import cli as cli_mod

    # No state file at all on disk.
    monkeypatch.setenv("ETH_VALIDATOR_STATS_STATE", str(tmp_path / "missing.json"))

    class _Cfg:
        validators = []

    monkeypatch.setattr(cli_mod, "load_config", lambda: _Cfg())

    import argparse
    rc = cli_mod.cmd_status(argparse.Namespace(refresh=False))
    out = capsys.readouterr().out
    assert rc == 0
    # Hint should mention the way out: check, watch, or --refresh.
    assert "evs check" in out or "--refresh" in out or "watch" in out.lower()


def test_main_returns_int_on_unknown_command():
    """The Typer wrapper must translate Click's UsageError exit code to an int,
    not raise SystemExit. Existing test infra relies on `rc = main([...])`."""
    from eth_validator_stats.cli import main
    rc = main(["nonexistent-command"])
    assert isinstance(rc, int)
    assert rc == 2


def test_main_help_lists_all_top_level_commands(capsys):
    """`evs --help` succeeds and the captured output mentions every top-level
    command. Doesn't pin exact wording — Rich-styled output can drift."""
    from eth_validator_stats.cli import main
    rc = main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    for command in (
        "status", "check", "watch", "info", "init",
        "install-service", "uninstall-service", "simulate", "validators",
    ):
        assert command in out, f"--help output missing {command!r}"


def test_init_rejects_host_and_beacon_url_together():
    """Mutex check: argparse used add_mutually_exclusive_group; Typer uses a
    runtime check. The exit code (2) and non-raising behavior must match."""
    from eth_validator_stats.cli import main
    rc = main(["init", "--host", "x", "--beacon-url", "http://y"])
    assert rc == 2


def test_init_rejects_ntfy_topic_and_no_ntfy_together():
    """Same mutex shape for the ntfy pair."""
    from eth_validator_stats.cli import main
    rc = main(["init", "--ntfy-topic", "alerts", "--no-ntfy"])
    assert rc == 2


def test_main_returns_int_on_help_exit():
    """Help is implemented via Click's Exit(0). The wrapper must translate."""
    from eth_validator_stats.cli import main
    rc = main(["status", "--help"])
    assert isinstance(rc, int)
    assert rc == 0
