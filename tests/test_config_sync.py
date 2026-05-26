from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from eth_validator_stats.alerts import AlertsConfig
from eth_validator_stats.config_io import AppConfig
from eth_validator_stats.config_sync import KeySpec, SyncResult, schema_keys


def test_dataclasses_importable():
    ks = KeySpec(dotted_path="x.y", default=0, type_name="int")
    assert ks.dotted_path == "x.y"
    sr = SyncResult(skipped_reason=None, appended_keys=[])
    assert sr.appended_keys == []


def test_schema_keys_for_appconfig_has_expected_paths():
    paths = [k.dotted_path for k in schema_keys(AppConfig)]
    # Required fields (beacon_node_url) and list fields (validators) excluded.
    # Order follows dataclass declaration.
    assert paths == [
        "beacon_auth_token",
        "alerts.ntfy_topic",
        "alerts.cooldown_minutes",
        "alerts.storm_threshold",
        "alerts.request_timeout_s",
        "alerts.missed_attestations_threshold",
        "alerts.withdrawal_threshold_gwei",
        "alerts.withdrawal_max_gap_slots",
        "alerts.proposal_lookahead_epochs",
        "alerts.icon_url",
        "alerts.daily_heartbeat",
        "alerts.daily_heartbeat_hour",
        "alerts.heartbeat_url",
    ]


def test_schema_keys_carries_defaults_and_types():
    by_path = {k.dotted_path: k for k in schema_keys(AppConfig)}
    assert by_path["alerts.daily_heartbeat"].default is False
    assert by_path["alerts.daily_heartbeat"].type_name == "bool"
    assert by_path["alerts.daily_heartbeat_hour"].default == 9
    assert by_path["alerts.daily_heartbeat_hour"].type_name == "int"
    assert by_path["alerts.heartbeat_url"].default == ""
    assert by_path["alerts.heartbeat_url"].type_name == "str"
    assert by_path["alerts.request_timeout_s"].type_name == "float"


def test_schema_keys_excludes_required_and_collections():
    paths = [k.dotted_path for k in schema_keys(AppConfig)]
    # beacon_node_url: required str (no default)
    assert "beacon_node_url" not in paths
    # validators: list[ConfigEntry] — collections are not auto-populated
    assert "validators" not in paths


def test_schema_keys_handles_optional_scalar_with_default():
    @dataclass
    class _T:
        a: Optional[str] = None  # included: optional scalar with default

    paths = [k.dotted_path for k in schema_keys(_T)]
    assert paths == ["a"]


def test_schema_keys_recurses_into_nested_dataclass():
    @dataclass
    class _Inner:
        x: int = 1
        y: str = "ok"

    @dataclass
    class _Outer:
        inner: _Inner = field(default_factory=_Inner)
        top: bool = True

    paths = [k.dotted_path for k in schema_keys(_Outer)]
    assert paths == ["inner.x", "inner.y", "top"]


from pathlib import Path

from eth_validator_stats.config_sync import find_missing_keys


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yml"
    p.write_text(body, encoding="utf-8")
    return p


def test_find_missing_keys_fresh_config_returns_full_schema(tmp_path):
    p = _write(
        tmp_path,
        "beacon_node_url: http://localhost:3500\nvalidators: []\n",
    )
    missing = find_missing_keys(p, schema_keys(AppConfig))
    assert len(missing) == 13
    assert "beacon_auth_token" in [k.dotted_path for k in missing]
    assert "alerts.heartbeat_url" in [k.dotted_path for k in missing]


def test_find_missing_keys_skips_parsed_values(tmp_path):
    p = _write(
        tmp_path,
        "beacon_node_url: http://localhost:3500\n"
        "validators: []\n"
        "alerts:\n"
        "  heartbeat_url: https://hc-ping.com/abc\n",
    )
    missing = [k.dotted_path for k in find_missing_keys(p, schema_keys(AppConfig))]
    assert "alerts.heartbeat_url" not in missing


def test_find_missing_keys_skips_commented_values(tmp_path):
    p = _write(
        tmp_path,
        "beacon_node_url: http://localhost:3500\n"
        "validators: []\n"
        "# heartbeat_url: \"\"  # disabled for now\n",
    )
    missing = [k.dotted_path for k in find_missing_keys(p, schema_keys(AppConfig))]
    assert "alerts.heartbeat_url" not in missing


def test_find_missing_keys_known_false_positive_in_freetext(tmp_path):
    # Acknowledged in the spec: unrelated comments containing a key name
    # cause skip. Behavior is intentional.
    p = _write(
        tmp_path,
        "beacon_node_url: http://localhost:3500\n"
        "validators: []\n"
        "# Note: cooldown_minutes is discussed in README section 4.\n",
    )
    missing = [k.dotted_path for k in find_missing_keys(p, schema_keys(AppConfig))]
    assert "alerts.cooldown_minutes" not in missing


def test_find_missing_keys_empty_file(tmp_path):
    p = _write(tmp_path, "")
    missing = find_missing_keys(p, schema_keys(AppConfig))
    assert len(missing) == 13
