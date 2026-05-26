from __future__ import annotations

from eth_validator_stats.config_sync import KeySpec, SyncResult


def test_dataclasses_importable():
    ks = KeySpec(dotted_path="x.y", default=0, type_name="int")
    assert ks.dotted_path == "x.y"
    sr = SyncResult(skipped_reason=None, appended_keys=[])
    assert sr.appended_keys == []
