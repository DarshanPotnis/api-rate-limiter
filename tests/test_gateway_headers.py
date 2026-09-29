"""Tests for the OpenAI-style duration format used in x-ratelimit-reset-* headers."""

import pytest

from app.gateway.headers import format_duration


@pytest.mark.parametrize(
    ("milliseconds", "formatted"),
    [
        (0, "0ms"),
        (250, "250ms"),
        (1_000, "1s"),
        (1_500, "1.5s"),
        (17_525, "17.525s"),
        (60_000, "1m0s"),
        (90_500, "1m30.5s"),
        (360_000, "6m0s"),
    ],
)
def test_durations_are_formatted_like_openai(milliseconds: int, formatted: str) -> None:
    assert format_duration(milliseconds) == formatted
