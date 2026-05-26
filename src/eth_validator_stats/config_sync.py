from __future__ import annotations

import dataclasses
import logging
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


_SCALAR_TYPES = (str, int, float, bool)


def _resolve_type(field_type: Any) -> tuple[type | None, bool]:
    """Return (concrete_scalar_type_or_None, is_nested_dataclass).

    Handles Optional[T] by unwrapping to T. Anything that's neither a
    scalar nor a dataclass returns (None, False) and gets skipped.
    """
    if dataclasses.is_dataclass(field_type):
        return None, True

    origin = typing.get_origin(field_type)
    if origin is typing.Union:
        args = [a for a in typing.get_args(field_type) if a is not type(None)]
        if len(args) == 1:
            return _resolve_type(args[0])
        return None, False

    if origin in (list, dict, tuple, set, frozenset):
        return None, False

    if isinstance(field_type, type) and issubclass(field_type, _SCALAR_TYPES):
        return field_type, False

    return None, False


def _has_default(f: dataclasses.Field) -> tuple[bool, Any]:
    if f.default is not dataclasses.MISSING:
        return True, f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return True, f.default_factory()
    return False, None


def schema_keys(cls: type, _prefix: str = "") -> list[KeySpec]:
    """Walk a dataclass tree depth-first, returning scalar leaf keys.

    Rules:
      - Required fields (no default) excluded.
      - list/dict/tuple/set fields excluded.
      - Nested @dataclass fields recursed into; path is dotted with parent name.
      - Optional[T] unwraps to T.
    """
    # Build a localns so that get_type_hints can resolve locally-defined
    # dataclasses (e.g. those defined inside test functions) whose names
    # are not present in any module's global namespace.
    localns: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            factory = f.default_factory  # type: ignore[misc]
            if dataclasses.is_dataclass(factory):
                localns[factory.__name__] = factory
        if isinstance(f.default, type) and dataclasses.is_dataclass(f.default):
            localns[f.default.__name__] = f.default
    hints = typing.get_type_hints(cls, localns=localns)
    out: list[KeySpec] = []
    for f in dataclasses.fields(cls):
        ftype = hints.get(f.name, f.type)
        scalar, is_nested = _resolve_type(ftype)
        has_default, default = _has_default(f)

        if is_nested:
            # Always recurse into nested dataclasses, even if the parent
            # field has no default — we're not auto-populating the
            # parent, just discovering its leaves.
            sub_cls = ftype
            out.extend(schema_keys(sub_cls, _prefix=f"{_prefix}{f.name}."))
            continue

        if not has_default or scalar is None:
            continue

        out.append(
            KeySpec(
                dotted_path=f"{_prefix}{f.name}",
                default=default,
                type_name=scalar.__name__,
            )
        )
    return out


def find_missing_keys(config_path: Path, keys: list[KeySpec]) -> list[KeySpec]:
    """Return the subset of `keys` whose leaf name is not present anywhere
    in the raw text of `config_path` (parsed values, comments, anywhere).

    Leaf-name match is intentional: it gives us free idempotency against
    blocks we previously appended, and treats commented-out values as
    "user already knows about this".

    If the file is unreadable, return an empty list — sync becomes a no-op.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []

    out: list[KeySpec] = []
    for k in keys:
        leaf = k.dotted_path.rsplit(".", 1)[-1]
        if leaf not in text:
            out.append(k)
    return out
