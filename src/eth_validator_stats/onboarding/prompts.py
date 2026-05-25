from __future__ import annotations

import re
import sys
from typing import Protocol

_PUBKEY_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")
_INDEX_RE = re.compile(r"^\d+$")


class IOLike(Protocol):
    def read_line(self, prompt: str) -> str: ...
    def write(self, text: str) -> None: ...


class StdIO:
    """Default IO that reads from stdin and writes to stdout."""

    def read_line(self, prompt: str) -> str:
        return input(prompt)

    def write(self, text: str) -> None:
        sys.stdout.write(text)


def prompt(io: IOLike, message: str, *, default: str | None = None) -> str:
    # Only show "[default]" when there's actually something to show.
    # An empty-string default is a sentinel for "Enter is OK, no displayed
    # value" — printing "[]" would just be confusing.
    suffix = f" [{default}]" if default else ""
    raw = io.read_line(f"{message}{suffix}: ").strip()
    if not raw and default is not None:
        return default
    return raw


def confirm(io: IOLike, message: str, *, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = io.read_line(f"{message} {suffix}: ").strip().lower()
    if not raw:
        return default
    if raw in ("y", "yes"):
        return True
    if raw in ("n", "no"):
        return False
    # On junk, fall back to default rather than re-loop
    return default


def parse_validator_input(raw: str) -> tuple[str, str | int]:
    """Parse a pubkey or index string. Normalizes pubkeys to lowercase.
    Returns ('pubkey', '0x...') or ('index', int). Raises ValueError on junk.
    """
    raw = raw.strip()
    if _PUBKEY_RE.match(raw):
        return ("pubkey", raw.lower())
    if _INDEX_RE.match(raw):
        return ("index", int(raw))
    raise ValueError(f"not a pubkey (0x...) or index (digits): {raw!r}")


def prompt_validator(io: IOLike) -> tuple[str, str | int]:
    """Prompt for a validator pubkey or index, re-prompting on bad input."""
    while True:
        raw = io.read_line("Validator pubkey (0x...) or index (number): ").strip()
        try:
            return parse_validator_input(raw)
        except ValueError as e:
            io.write(f"  {e}. Try again.\n")
