"""Contract for estimating a request's prompt tokens before it is sent."""

from collections.abc import Sequence
from typing import Protocol

from app.providers import Message
from app.routing import Target


class PromptEstimator(Protocol):
    async def estimate(self, targets: Sequence[Target], messages: Sequence[Message]) -> int:
        """Prompt tokens to reserve for ``messages`` if any of ``targets`` may answer them."""
        ...

    async def observe(self, target: Target, messages: Sequence[Message], reported_prompt_tokens: int) -> None:
        """Learn from the prompt size ``target`` reported after answering ``messages``."""
        ...
