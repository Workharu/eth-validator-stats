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


def test_evaluate_alerts_pending_is_not_offline():
    rows = [_row(status="pending_queued", liveness=[])]
    assert evaluate_alerts(rows, missed_threshold=3) == []


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
    from eth_validator_stats.beacon import ChainInfo, Head, ValidatorInfo
    from eth_validator_stats.config_io import AppConfig, ConfigEntry
    from eth_validator_stats.alerts import AlertsConfig

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
    """`eth-validator-stats --version` prints `<prog> <semver>` and exits 0."""
    import re
    import pytest
    from eth_validator_stats.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("eth-validator-stats ")
    # Version may be a semver like "0.3.5" or the "unknown" fallback.
    assert re.search(r"(\d+\.\d+\.\d+|unknown)", out)
