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

from collections.abc import Callable


def _label_part(label: str) -> str:
    return f" {label}" if label else ""


def build_missed_attestation(idx: int, label: str, *, last: int = 2) -> tuple[str, str]:
    return (f"validator {idx}{_label_part(label)}", f"❌ MISSED_ATTESTATIONS last={last}")


def build_offline(idx: int, label: str, *, status: str = "slashed") -> tuple[str, str]:
    return (f"validator {idx}{_label_part(label)}", f"❌ OFFLINE status={status}")


def build_withdrawal(idx: int, label: str, *, amount_eth: float = 0.001) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} withdrawal",
        f"💰 {amount_eth:.4f} ETH withdrawn",
    )


def build_proposing_soon(
    idx: int, label: str, *, slot: int = 12345, delay: str = "~6 min",
) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} proposing soon",
        f"🔜 slot {slot} ({delay} away)",
    )


def build_proposed(idx: int, label: str, *, slot: int = 12345) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} proposed slot {slot}",
        f"✓ block proposed at slot {slot}",
    )


def build_missed_proposal(idx: int, label: str, *, slot: int = 12345) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} missed proposal at slot {slot}",
        f"✗ missed block at slot {slot}",
    )


def build_blind() -> tuple[str, str]:
    return ("MONITOR BLIND", "🚫 beacon node unreachable: simulated")


def build_recovered() -> tuple[str, str]:
    return ("MONITOR RECOVERED", "✅ beacon node reachable again")


# --- lifecycle transitions ---------------------------------------------------

def build_activated(idx: int, label: str) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} ACTIVATED",
        "✅ now attesting (was pending_queued)",
    )


def build_exit_initiated(idx: int, label: str) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} EXIT INITIATED",
        "🚫 voluntary exit submitted; still attesting until exit epoch",
    )


def build_slashed(idx: int, label: str) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} SLASHED",
        "⚠️ status: active_ongoing -> active_slashed",
    )


def build_exited(idx: int, label: str) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} EXITED",
        "👋 exit complete (was active_exiting)",
    )


def build_withdrawal_ready(idx: int, label: str) -> tuple[str, str]:
    return (
        f"validator {idx}{_label_part(label)} WITHDRAWAL READY",
        "💰 funds claimable",
    )


# Scope tells cmd_simulate whether to resolve a validator before calling
# the builder. Validator-scoped builders take (idx, label, **kwargs);
# global-scoped builders take just **kwargs (currently none).
EVENTS: dict[str, tuple[Callable, str]] = {
    "missed-attestation": (build_missed_attestation, "validator"),
    "offline": (build_offline, "validator"),
    "withdrawal": (build_withdrawal, "validator"),
    "proposing-soon": (build_proposing_soon, "validator"),
    "proposed": (build_proposed, "validator"),
    "missed-proposal": (build_missed_proposal, "validator"),
    "blind": (build_blind, "global"),
    "recovered": (build_recovered, "global"),
    # Lifecycle transitions. All validator-scoped; the operator picks
    # which configured validator to demo the alert against.
    "activated": (build_activated, "validator"),
    "exit-initiated": (build_exit_initiated, "validator"),
    "slashed": (build_slashed, "validator"),
    "exited": (build_exited, "validator"),
    "withdrawal-ready": (build_withdrawal_ready, "validator"),
}
