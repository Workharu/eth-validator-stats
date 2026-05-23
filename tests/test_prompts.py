from __future__ import annotations

import io

import pytest

from eth_validator_stats.onboarding.prompts import (
    confirm,
    parse_validator_input,
    prompt,
    prompt_validator,
)


class FakeIO:
    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.output = io.StringIO()

    def read_line(self, _prompt: str) -> str:
        self.output.write(_prompt)
        return self.answers.pop(0)

    def write(self, text: str) -> None:
        self.output.write(text)


def test_parse_validator_input_accepts_lowercase_pubkey():
    kind, value = parse_validator_input("0xabc123")
    assert kind == "pubkey"
    assert value == "0xabc123"


def test_parse_validator_input_normalizes_uppercase_pubkey():
    kind, value = parse_validator_input("0XABC123")
    assert kind == "pubkey"
    assert value == "0xabc123"  # lowercase normalization


def test_parse_validator_input_accepts_decimal_index():
    kind, value = parse_validator_input("12345")
    assert kind == "index"
    assert value == 12345


def test_parse_validator_input_rejects_junk():
    with pytest.raises(ValueError):
        parse_validator_input("not-a-thing")


def test_prompt_returns_default_on_empty_input():
    io_fake = FakeIO([""])
    result = prompt(io_fake, "Host?", default="localhost")
    assert result == "localhost"


def test_prompt_returns_user_value_when_provided():
    io_fake = FakeIO(["custom-host"])
    assert prompt(io_fake, "Host?", default="localhost") == "custom-host"


def test_confirm_default_yes_on_blank():
    io_fake = FakeIO([""])
    assert confirm(io_fake, "OK?", default=True) is True


def test_confirm_default_no_on_blank():
    io_fake = FakeIO([""])
    assert confirm(io_fake, "OK?", default=False) is False


def test_confirm_parses_y_n():
    io_fake = FakeIO(["y"])
    assert confirm(io_fake, "OK?", default=False) is True
    io_fake = FakeIO(["n"])
    assert confirm(io_fake, "OK?", default=True) is False


def test_prompt_validator_reprompts_on_junk_then_accepts():
    io_fake = FakeIO(["not-valid", "12345"])
    kind, value = prompt_validator(io_fake)
    assert kind == "index" and value == 12345
