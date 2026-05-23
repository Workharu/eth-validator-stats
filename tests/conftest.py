from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eth_validator_stats.beacon import ValidatorInfo
from eth_validator_stats.onboarding.portscan import Found


class FakePrompts:
    """Records prompts and replays scripted answers in order."""

    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.prompted: list[str] = []
        self.written: list[str] = []

    def read_line(self, prompt: str) -> str:
        self.prompted.append(prompt)
        if not self.answers:
            raise AssertionError(f"FakePrompts ran out of answers; last prompt was: {prompt!r}")
        return self.answers.pop(0)

    def write(self, text: str) -> None:
        self.written.append(text)


@dataclass
class FakePortscanResult:
    found: list[Found] = field(default_factory=list)

    async def __call__(self, host: str, **_: Any) -> list[Found]:
        return self.found


class FakeBeaconClient:
    """Stand-in for BeaconClient with scriptable validator results."""

    def __init__(self, validators: list[ValidatorInfo], *, raise_on_call: Exception | None = None):
        self._validators = validators
        self._raise = raise_on_call
        self.calls: list[list[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get_validators(self, ids: list[str]) -> list[ValidatorInfo]:
        self.calls.append(list(ids))
        if self._raise:
            raise self._raise
        return list(self._validators)


class FakeNotifier:
    def __init__(self, *, raise_on_send: Exception | None = None):
        self._raise = raise_on_send
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, body: str) -> None:
        if self._raise:
            raise self._raise
        self.sent.append((title, body))
