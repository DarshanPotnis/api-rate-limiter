"""Rough token counting: one token per four characters, rounded up.

Good enough to size a reservation before a request is sent. Real providers report the
actual counts afterwards, and the gateway settles against those.
"""

import math
from collections.abc import Iterable

from app.providers.base import Message

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def estimate_prompt_tokens(messages: Iterable[Message]) -> int:
    return estimate_tokens("".join(message.content for message in messages))
