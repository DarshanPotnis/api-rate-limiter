"""Prompt estimates calibrated per model from the token counts providers report.

Estimate = characters / 4 + the model's learned base overhead + ``per_message_tokens``
for each message after the first. The base is the part of a chat template that every
request pays once; the per-message part is fixed and subtracted before learning, so a
long conversation cannot inflate the base for later short requests.
"""

import logging
import math
from collections.abc import Awaitable, Sequence
from pathlib import Path
from typing import cast

import redis.asyncio

from app.providers import Message
from app.providers.tokens import estimate_prompt_tokens
from app.routing import Target

log = logging.getLogger(__name__)

KEY_PREFIX = "promptoverhead"
STEP_DOWN = 0.1
FORGET_AFTER_MS = 30 * 24 * 60 * 60 * 1000

_LEARN_SOURCE = (Path(__file__).parent / "scripts" / "prompt_overhead_learn.lua").read_text()


class CalibratedEstimator:
    def __init__(self, client: redis.asyncio.Redis, *, default_overhead: int, per_message_tokens: int) -> None:
        if default_overhead < 0:
            raise ValueError(f"default_overhead must not be negative, got {default_overhead}")
        if per_message_tokens < 0:
            raise ValueError(f"per_message_tokens must not be negative, got {per_message_tokens}")
        self._client = client
        self._default_overhead = default_overhead
        self._per_message_tokens = per_message_tokens
        self._learn = client.register_script(_LEARN_SOURCE)

    async def estimate(self, targets: Sequence[Target], messages: Sequence[Message]) -> int:
        content = estimate_prompt_tokens(messages)
        calibrated = [target.model for target in targets if target.provider.reports_real_usage]
        if not calibrated:
            return content  # every candidate reports our own estimate, so it is exact
        base = max([await self.base_overhead(model) for model in calibrated])
        return content + math.ceil(base) + self._per_message_allowance(messages)

    async def observe(self, target: Target, messages: Sequence[Message], reported_prompt_tokens: int) -> None:
        if not target.provider.reports_real_usage:
            return
        observed = reported_prompt_tokens - estimate_prompt_tokens(messages) - self._per_message_allowance(messages)
        learned, overhead = await self._learn(
            keys=[f"{KEY_PREFIX}:{target.model}"],
            args=[observed, self._default_overhead, STEP_DOWN, FORGET_AFTER_MS],
        )
        log.info(
            "prompt overhead for %s: observed=%d %s, base overhead now %s",
            target.model,
            observed,
            "learned" if learned else "ignored as a likely prompt-cache count",
            overhead,
        )

    async def base_overhead(self, model: str) -> float:
        # redis-py types commands as sync-or-async; on an asyncio client this is awaitable.
        stored = await cast(Awaitable[str | None], self._client.hget(f"{KEY_PREFIX}:{model}", "overhead"))
        return self._default_overhead if stored is None else float(stored)

    def _per_message_allowance(self, messages: Sequence[Message]) -> int:
        return self._per_message_tokens * max(len(messages) - 1, 0)
