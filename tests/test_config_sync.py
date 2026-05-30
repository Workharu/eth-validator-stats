from __future__ import annotations

import datetime
import os
import stat as stat_mod
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from eth_validator_stats.config_io import AppConfig
from eth_validator_stats.config_sync import (
    KeySpec,
    SyncResult,
    append_upgrade_block,
    find_missing_keys,
    render_upgrade_block,
    schema_keys,
    sync_user_config,
)


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
        a: str | None = None  # included: optional scalar with default

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


def test_render_upgrade_block_header_includes_version_and_date():
    block = render_upgrade_block(
        missing=[KeySpec("beacon_auth_token", "", "str")],
        version="9.9.9",
        today=datetime.date(2030, 1, 2),
    )
    assert "# === Added by eth-validator-stats v9.9.9 on 2030-01-02 ===" in block


def test_render_upgrade_block_groups_root_and_alerts():
    block = render_upgrade_block(
        missing=[
            KeySpec("beacon_auth_token", "", "str"),
            KeySpec("alerts.daily_heartbeat", False, "bool"),
            KeySpec("alerts.heartbeat_url", "", "str"),
        ],
        version="0.5.1",
        today=datetime.date(2026, 5, 26),
    )
    assert "# beacon_auth_token:" in block
    assert "# alerts:" in block
    assert "#   daily_heartbeat:" in block
    assert "#   heartbeat_url:" in block
    # beacon_auth_token group comes before alerts group
    assert block.index("beacon_auth_token") < block.index("# alerts:")


def test_render_upgrade_block_omits_empty_groups():
    # Only an alerts.* key is missing; no root-level "# beacon_auth_token" line.
    block = render_upgrade_block(
        missing=[KeySpec("alerts.daily_heartbeat", False, "bool")],
        version="0.5.1",
        today=datetime.date(2026, 5, 26),
    )
    assert "beacon_auth_token" not in block


def test_render_upgrade_block_values_round_trip():
    # If we uncomment the body (strip leading "# "), the result must parse
    # as YAML and the values must match the defaults we passed in.
    block = render_upgrade_block(
        missing=[
            KeySpec("beacon_auth_token", "", "str"),
            KeySpec("alerts.daily_heartbeat", False, "bool"),
            KeySpec("alerts.daily_heartbeat_hour", 9, "int"),
            KeySpec("alerts.heartbeat_url", "", "str"),
        ],
        version="0.5.1",
        today=datetime.date(2026, 5, 26),
    )
    lines = block.splitlines()
    # The first bare-"#" line marks the end of the intro block; everything
    # after it is either a key line ("# key: value" / "#   key: value") or
    # another bare-"#" group separator. No prose. No bespoke parsing.
    body_start = lines.index("#")
    yaml_lines: list[str] = []
    for ln in lines[body_start + 1 :]:
        if ln == "#" or ln == "":
            continue
        assert ln.startswith("# "), f"unexpected body line: {ln!r}"
        yaml_lines.append(ln[2:])
    parsed = yaml.safe_load("\n".join(yaml_lines))
    assert parsed["beacon_auth_token"] == ""
    assert parsed["alerts"]["daily_heartbeat"] is False
    assert parsed["alerts"]["daily_heartbeat_hour"] == 9
    assert parsed["alerts"]["heartbeat_url"] == ""


def test_render_upgrade_block_long_url_value_renders_on_one_line():
    long = "https://raw.githubusercontent.com/Workharu/eth-validator-stats/main/assets/icon.png"
    block = render_upgrade_block(
        missing=[KeySpec("alerts.icon_url", long, "str")],
        version="0.5.1",
        today=datetime.date(2026, 5, 26),
    )
    # The line with icon_url contains the full URL with no internal newline.
    for line in block.splitlines():
        if "icon_url" in line:
            assert long in line
            break
    else:
        raise AssertionError("icon_url line not found in block")


def test_append_writable_file_appends_and_creates_bak(tmp_path):
    p = _write(tmp_path, "original: yes\n")
    ok = append_upgrade_block(p, "# appended block\n")
    assert ok is True
    text = p.read_text(encoding="utf-8")
    assert text.startswith("original: yes\n")
    assert "# appended block" in text
    bak = p.with_suffix(p.suffix + ".bak")
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == "original: yes\n"


def test_append_injects_leading_newline_if_missing(tmp_path):
    p = _write(tmp_path, "last_line: 1")  # no trailing newline
    append_upgrade_block(p, "# appended\n")
    text = p.read_text(encoding="utf-8")
    assert "last_line: 1\n# appended" in text


def test_append_no_double_blank_line_when_file_already_ends_in_newline(tmp_path):
    p = _write(tmp_path, "last_line: 1\n")
    append_upgrade_block(p, "# appended\n")
    text = p.read_text(encoding="utf-8")
    # Exactly one newline between "last_line: 1" and the appended block.
    assert "last_line: 1\n# appended" in text
    assert "last_line: 1\n\n# appended" not in text


def test_append_readonly_file_returns_false(tmp_path):
    p = _write(tmp_path, "data\n")
    os.chmod(p, stat_mod.S_IRUSR | stat_mod.S_IRGRP | stat_mod.S_IROTH)
    try:
        ok = append_upgrade_block(p, "# appended\n")
        assert ok is False
        assert p.read_text(encoding="utf-8") == "data\n"
        bak = p.with_suffix(p.suffix + ".bak")
        assert not bak.exists()
    finally:
        os.chmod(p, stat_mod.S_IRUSR | stat_mod.S_IWUSR)


def test_append_aborts_if_bak_copy_fails(tmp_path):
    p = _write(tmp_path, "data\n")
    with patch("eth_validator_stats.config_sync._safe_copy_bak", side_effect=OSError("disk full")):
        ok = append_upgrade_block(p, "# appended\n")
    assert ok is False
    assert p.read_text(encoding="utf-8") == "data\n"


def test_append_preserves_mode_bits_on_bak(tmp_path):
    p = _write(tmp_path, "data\n")
    os.chmod(p, 0o600)
    append_upgrade_block(p, "# appended\n")
    bak = p.with_suffix(p.suffix + ".bak")
    assert (bak.stat().st_mode & 0o777) == 0o600
    # Original's mode survives the append (defends against future refactor
    # that accidentally chmods the live file).
    assert (p.stat().st_mode & 0o777) == 0o600


def test_append_bak_is_overwritten_on_repeated_dirty_runs(tmp_path):
    # Spec mandates the .bak is a rolling snapshot — each sync overwrites
    # any prior .bak with the pre-append state of the original. Guards
    # against a future "skip if .bak exists" guard accidentally landing.
    p = _write(tmp_path, "first: 1\n")
    append_upgrade_block(p, "# first block\n")
    bak = p.with_suffix(p.suffix + ".bak")
    assert bak.read_text(encoding="utf-8") == "first: 1\n"

    # Second append simulates a subsequent upgrade producing more drift.
    append_upgrade_block(p, "# second block\n")
    # .bak now reflects post-first-append state, not the pristine original.
    assert "# first block" in bak.read_text(encoding="utf-8")


def test_append_aborts_if_dst_is_symlink(tmp_path):
    # CWE-61 guard: a pre-existing symlink at .bak must not redirect the
    # backup write. _safe_copy_bak uses O_NOFOLLOW.
    p = _write(tmp_path, "data\n")
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched", encoding="utf-8")
    bak = p.with_suffix(p.suffix + ".bak")
    bak.symlink_to(victim)
    ok = append_upgrade_block(p, "# appended\n")
    assert ok is False
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert p.read_text(encoding="utf-8") == "data\n"


def test_sync_user_config_appends_then_is_idempotent(tmp_path, monkeypatch):
    # Pre-heartbeat-era config snapshot.
    body = (
        "beacon_node_url: http://localhost:3500\n"
        "validators:\n"
        "  - index: 1\n"
        "    label: t\n"
        "alerts:\n"
        "  ntfy_topic: https://ntfy.sh/test\n"
        "  cooldown_minutes: 30\n"
        "  storm_threshold: 10\n"
    )
    p = _write(tmp_path, body)

    result1 = sync_user_config(p)
    assert result1.skipped_reason is None
    assert "alerts.heartbeat_url" in result1.appended_keys
    assert "alerts.daily_heartbeat" in result1.appended_keys

    after_first = p.read_text(encoding="utf-8")
    assert after_first.startswith(body)
    assert "# === Added by eth-validator-stats" in after_first

    # Idempotent: second run finds nothing missing, no further write.
    result2 = sync_user_config(p)
    assert result2.skipped_reason is None
    assert result2.appended_keys == []
    assert p.read_text(encoding="utf-8") == after_first


def test_sync_user_config_disabled_via_env(tmp_path, monkeypatch):
    p = _write(tmp_path, "beacon_node_url: http://localhost:3500\nvalidators: []\n")
    monkeypatch.setenv("ETH_VALIDATOR_STATS_NO_CONFIG_SYNC", "1")
    result = sync_user_config(p)
    assert result.skipped_reason == "disabled"
    assert result.appended_keys == []
    # No .bak either.
    assert not p.with_suffix(p.suffix + ".bak").exists()


def test_sync_user_config_no_config_returns_no_config(tmp_path):
    p = tmp_path / "does-not-exist.yml"
    result = sync_user_config(p)
    assert result.skipped_reason == "no_config"
    assert result.appended_keys == []


def test_sync_user_config_binary_garbage_does_not_raise(tmp_path):
    # Pins the documented "never raises" contract against the most obvious
    # corrupted-file case. read_text → UnicodeDecodeError → caught by the
    # broad except in sync_user_config → io_error.
    p = tmp_path / "config.yml"
    p.write_bytes(b"\xff\xfe binary garbage \x00\x01")
    result = sync_user_config(p)
    assert result.skipped_reason == "io_error"
    assert result.appended_keys == []


def test_sync_user_config_oversized_file_does_not_hang(tmp_path):
    # Defends against /dev/zero / runaway file pointed at by env var.
    # find_missing_keys has a 1 MiB guard.
    p = tmp_path / "config.yml"
    p.write_bytes(b"# " + b"x" * (2 << 20))  # 2 MiB of harmless filler
    result = sync_user_config(p)
    # Skipped because find_missing_keys returns [] → no drift → no append.
    # Whatever the reason, it must not raise and must not write a .bak.
    assert result.appended_keys == []
    assert not p.with_suffix(p.suffix + ".bak").exists()


def test_schema_keys_unwraps_optional_dataclass(tmp_path):
    # Latent crash: a future Optional[NestedDataclass] field would previously
    # try to call dataclasses.fields(Optional[...]) and raise TypeError.
    @dataclass
    class _Inner:
        x: int = 1

    @dataclass
    class _Outer:
        inner: _Inner | None = field(default_factory=_Inner)

    paths = [k.dotted_path for k in schema_keys(_Outer)]
    assert paths == ["inner.x"]


def test_dump_value_line_rejects_multi_line_default():
    # Future-contributor footgun: a multi-line default would corrupt the
    # appended block because only the first line would get the "# " prefix.
    from eth_validator_stats.config_sync import _dump_value_line

    with pytest.raises(ValueError, match="multiple lines"):
        _dump_value_line("anykey", "line1\nline2")


def test_sync_user_config_after_sync_yaml_still_parses(tmp_path):
    body = "beacon_node_url: http://localhost:3500\nvalidators: []\n"
    p = _write(tmp_path, body)
    sync_user_config(p)
    parsed = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert parsed["beacon_node_url"] == "http://localhost:3500"
    assert parsed["validators"] == []
    # appended block is all comments, so no new top-level keys.
    assert "beacon_auth_token" not in parsed
    assert "alerts" not in parsed


def test_sync_user_config_readonly_returns_not_writable(tmp_path):
    p = _write(tmp_path, "beacon_node_url: http://localhost:3500\nvalidators: []\n")
    os.chmod(p, stat_mod.S_IRUSR | stat_mod.S_IRGRP | stat_mod.S_IROTH)
    try:
        result = sync_user_config(p)
        assert result.skipped_reason == "not_writable"
        assert result.appended_keys == []
    finally:
        os.chmod(p, stat_mod.S_IRUSR | stat_mod.S_IWUSR)


def test_cli_helper_uses_resolved_path(tmp_path, monkeypatch):
    # Stale config in tmp_path; point the resolver at it via env var.
    body = "beacon_node_url: http://localhost:3500\nvalidators:\n  - pubkey: '0xabc'\n"
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))

    from eth_validator_stats.config_sync import load_config_with_sync

    cfg = load_config_with_sync()
    assert cfg.beacon_node_url == "http://localhost:3500"
    # Sync ran: appended block now in file.
    assert "# === Added by eth-validator-stats" in cfg_path.read_text(encoding="utf-8")


def test_cli_helper_respects_disable_env(tmp_path, monkeypatch):
    body = "beacon_node_url: http://localhost:3500\nvalidators:\n  - pubkey: '0xabc'\n"
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(body, encoding="utf-8")
    monkeypatch.setenv("ETH_VALIDATOR_STATS_CONFIG", str(cfg_path))
    monkeypatch.setenv("ETH_VALIDATOR_STATS_NO_CONFIG_SYNC", "1")

    from eth_validator_stats.config_sync import load_config_with_sync

    load_config_with_sync()
    # No append happened because sync was disabled.
    assert "# === Added by eth-validator-stats" not in cfg_path.read_text(encoding="utf-8")


def test_render_upgrade_block_includes_hint_for_known_keys():
    block = render_upgrade_block(
        missing=[
            KeySpec("beacon_auth_token", "", "str"),
            KeySpec("alerts.daily_heartbeat_hour", 9, "int"),
            KeySpec("alerts.heartbeat_url", "", "str"),  # no hint registered
        ],
        version="0.5.1",
        today=datetime.date(2026, 5, 26),
    )
    # beacon_auth_token has a hint — should render with trailing comment.
    assert "beacon_auth_token: ''  # optional bearer token" in block
    # daily_heartbeat_hour has a hint — should carry the unit annotation.
    assert "daily_heartbeat_hour: 9  # local time, 0-23" in block
    # heartbeat_url has no hint — line should end without a trailing comment.
    # (Spot-check by ensuring no extra "# " trails the bare key line.)
    assert "heartbeat_url: ''\n" in block or "heartbeat_url: ''" == block.splitlines()[-1].lstrip("# ").rstrip()


def test_render_upgrade_block_intro_drops_readme_pointer():
    block = render_upgrade_block(
        missing=[KeySpec("beacon_auth_token", "", "str")],
        version="0.5.1",
        today=datetime.date(2026, 5, 26),
    )
    # README pointer was misleading (most keys have no README entry) — removed.
    assert "README.md" not in block
    # Sentinel-disable mechanism is documented in the intro.
    assert "# config-sync: off" in block


def test_sync_user_config_respects_sentinel(tmp_path):
    body = (
        "beacon_node_url: http://localhost:3500\n"
        "validators: []\n"
        "# config-sync: off\n"
    )
    p = _write(tmp_path, body)
    result = sync_user_config(p)
    assert result.skipped_reason == "sentinel"
    assert result.appended_keys == []
    # File untouched; no .bak either.
    assert p.read_text(encoding="utf-8") == body
    assert not p.with_suffix(p.suffix + ".bak").exists()


def test_sync_user_config_sentinel_matches_after_lstrip(tmp_path):
    # User indented the sentinel — still recognized.
    body = (
        "beacon_node_url: http://localhost:3500\n"
        "validators: []\n"
        "    # config-sync: off\n"
    )
    p = _write(tmp_path, body)
    assert sync_user_config(p).skipped_reason == "sentinel"


def test_sync_user_config_intro_sentinel_mention_does_not_self_trigger(tmp_path):
    # The intro line embeds "    # config-sync: off" inside another comment
    # (with leading "#     "). It must not be parsed as the bare sentinel.
    body = "beacon_node_url: http://localhost:3500\nvalidators: []\n"
    p = _write(tmp_path, body)
    # First sync appends the block (which mentions the sentinel in its intro).
    r1 = sync_user_config(p)
    assert r1.skipped_reason is None
    assert r1.appended_keys  # non-empty

    # Second sync: must NOT detect the embedded mention as an active sentinel.
    # It should find no drift (all keys already in file as comments) → no-op.
    r2 = sync_user_config(p)
    assert r2.skipped_reason is None  # not "sentinel"
    assert r2.appended_keys == []
