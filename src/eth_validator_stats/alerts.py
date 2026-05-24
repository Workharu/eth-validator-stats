from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class AlertsConfig:
    ntfy_topic: str = ""
    cooldown_minutes: int = 30
    storm_threshold: int = 10
    request_timeout_s: float = 5.0
    missed_attestations_threshold: int = 2
    withdrawal_threshold_gwei: int = 1_000_000  # 0.001 ETH — skim noise below this
    proposal_lookahead_epochs: int = 1


class Notifier(Protocol):
    def send(self, title: str, body: str) -> None: ...


class NullNotifier:
    def send(self, title: str, body: str) -> None:  # noqa: ARG002
        return


class NtfyNotifier:
    def __init__(self, topic_url: str, *, timeout: float = 5.0, transport: httpx.BaseTransport | None = None) -> None:
        self.topic_url = topic_url
        self.timeout = timeout
        self._transport = transport

    def send(self, title: str, body: str) -> None:
        if not self.topic_url:
            return
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as c:
                r = c.post(
                    self.topic_url,
                    content=body.encode("utf-8"),
                    headers={"Title": title},
                )
                r.raise_for_status()
        except Exception as e:
            sys.stderr.write(f"warning: ntfy notify failed: {e}\n")


def make_notifier(cfg: AlertsConfig) -> Notifier:
    if cfg.ntfy_topic:
        return NtfyNotifier(cfg.ntfy_topic, timeout=cfg.request_timeout_s)
    return NullNotifier()


BLIND_KEY = "blind_alerted_until_ts"


def process_blind(state: dict, error_message: str, notifier: Notifier, cfg: AlertsConfig, now: int) -> None:
    """Beacon node unreachable: send MONITOR BLIND, deduped by cooldown."""
    until = int(state.get(BLIND_KEY, 0))
    if now < until:
        return
    notifier.send("MONITOR BLIND", f"beacon node unreachable: {error_message}")
    state[BLIND_KEY] = now + cfg.cooldown_minutes * 60


def clear_blind_if_recovered(state: dict, notifier: Notifier) -> None:
    """Beacon node reachable again after a previous BLIND: send recovery message."""
    if int(state.get(BLIND_KEY, 0)) > 0:
        notifier.send("MONITOR RECOVERED", "beacon node reachable again")
        state[BLIND_KEY] = 0


def process_validator_alerts(
    state: dict,
    configured_indices: set[int],
    current_alerts: list[tuple[int, str, str]],
    notifier: Notifier,
    cfg: AlertsConfig,
    now: int,
) -> tuple[list[tuple[int, str, str]], list[tuple[int, str]]]:
    """Apply dedup, storm grouping, recovery messaging. Mutates state. Returns (notified_alerts, recoveries)."""
    cooldown_s = cfg.cooldown_minutes * 60
    vstate = state.setdefault("validators", {})

    new_alerts: list[tuple[int, str, str]] = []
    alerting_now: set[int] = set()
    for idx, label, rule in current_alerts:
        alerting_now.add(idx)
        record = vstate.setdefault(str(idx), {})
        prev_rule = record.get("alerted_rule")
        until = int(record.get("alerted_until_ts", 0))
        if prev_rule != rule or now >= until:
            new_alerts.append((idx, label, rule))
            record["alerted_rule"] = rule
            record["alerted_until_ts"] = now + cooldown_s

    recoveries: list[tuple[int, str]] = []
    for idx_str, record in vstate.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if idx not in configured_indices:
            continue
        prev_rule = record.get("alerted_rule")
        if prev_rule and idx not in alerting_now:
            recoveries.append((idx, record.get("label", "")))
            record["alerted_rule"] = None
            record["alerted_until_ts"] = 0

    if len(new_alerts) > cfg.storm_threshold:
        sample = ", ".join(
            f"{idx}({label})" if label else str(idx)
            for idx, label, _ in new_alerts[:5]
        )
        notifier.send(
            "VALIDATOR STORM",
            f"{len(new_alerts)} new validator alerts. Sample: {sample}",
        )
    else:
        for idx, label, rule in new_alerts:
            label_part = f" {label}" if label else ""
            notifier.send(f"validator {idx}{label_part}", rule)

    for idx, label in recoveries:
        label_part = f" {label}" if label else ""
        notifier.send(f"validator {idx}{label_part} RECOVERED", "back to active_ongoing")

    return new_alerts, recoveries
