"""Builders for `eth-validator-stats simulate <event>`.

Each builder returns the exact (title, body) tuple the production alert
path would produce for that event. Validator-scoped builders take (idx,
label) and follow the `label_part = f" {label}" if label else ""`
convention from alerts.py so labelless validators don't get a trailing
space in the title.

These functions do no I/O. The CLI layer (cli.cmd_simulate) is responsible
for resolving the validator, building the notifier, and pushing the result.
"""
from __future__ import annotations

from typing import Callable


def _label_part(label: str) -> str:
    return f" {label}" if label else ""


def build_missed_attestation(idx: int, label: str, *, last: int = 2) -> tuple[str, str]:
    return (f"validator {idx}{_label_part(label)}", f"MISSED_ATTESTATIONS last={last}")


def build_offline(idx: int, label: str, *, status: str = "slashed") -> tuple[str, str]:
    return (f"validator {idx}{_label_part(label)}", f"OFFLINE status={status}")


def build_withdrawal(idx: int, label: str, *, amount_eth: float = 0.001) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} withdrawal",
        f"{amount_eth:.4f} ETH withdrawn",
    )


def build_proposing_soon(
    idx: int, label: str, *, slot: int = 12345, delay: str = "~6 min",
) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} proposing soon",
        f"slot {slot} ({delay} away)",
    )


def build_proposed(idx: int, label: str, *, slot: int = 12345) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} proposed slot {slot}",
        f"✓ block landed at slot {slot}",
    )


def build_missed_proposal(idx: int, label: str, *, slot: int = 12345) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} missed proposal at slot {slot}",
        f"✗ no block produced at slot {slot}",
    )
