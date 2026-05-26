from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeySpec:
    """A single config leaf key derived from the dataclass schema."""

    dotted_path: str  # e.g. "alerts.heartbeat_url"
    default: object
    type_name: str  # e.g. "str", "int", "bool"


@dataclass(frozen=True)
class SyncResult:
    """Outcome of a sync_user_config() call. Returned for testing/logging."""

    # None on success or no-drift. Otherwise one of:
    #   "no_config"   load_config-resolved path doesn't exist
    #   "not_writable"  os.access W_OK returned False
    #   "disabled"  ETH_VALIDATOR_STATS_NO_CONFIG_SYNC=1
    #   "io_error"  shutil/open raised
    skipped_reason: str | None
    appended_keys: list[str]  # dotted paths actually written to disk
