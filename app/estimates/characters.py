"""The brute-force estimate: characters / 4, the same for every model, learning nothing."""

from collections.abc import Sequence

from app.providers import Message
from app.providers.tokens import estimate_prompt_tokens
from app.routing import Target


class CharacterEstimator:
    async def estimate(self, targets: Sequence[Target], messages: Sequence[Message]) -> int:
        return estimate_prompt_tokens(messages)

    async def observe(self, target: Target, messages: Sequence[Message], reported_prompt_tokens: int) -> None:
        return None
