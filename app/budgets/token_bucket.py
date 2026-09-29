"""Per-caller token budgets as token buckets refilled continuously on the Redis clock.

A full bucket allows a burst of up to one minute's budget; after that, callers are held
to the refill rate. Unlike fixed windows, there is no boundary at which a second full
budget becomes available.
"""

from pathlib import Path

import redis.asyncio

from app.budgets.base import BudgetState, Reservation

KEY_PREFIX = "tokenbudget:token_bucket"

_SCRIPTS = Path(__file__).parent / "scripts"
_RESERVE_SOURCE = (_SCRIPTS / "token_bucket_reserve.lua").read_text()
_SETTLE_SOURCE = (_SCRIPTS / "token_bucket_settle.lua").read_text()


class TokenBucketBudget:
    """Capacity is the ``limit`` passed per call; an empty bucket refills in ``refill_seconds``."""

    def __init__(self, client: redis.asyncio.Redis, *, refill_seconds: float) -> None:
        refill_ms = round(refill_seconds * 1000)
        if refill_ms < 1:
            raise ValueError(f"refill_seconds must be at least 0.001, got {refill_seconds}")
        self._refill_ms = refill_ms
        self._reserve = client.register_script(_RESERVE_SOURCE)
        self._settle = client.register_script(_SETTLE_SOURCE)

    async def reserve(self, key: str, tokens: int, *, limit: int) -> Reservation:
        if tokens < 0:
            raise ValueError(f"tokens must not be negative, got {tokens}")
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        allowed, remaining, full_at_ms, now_ms, retry_after_ms = await self._reserve(
            keys=[f"{KEY_PREFIX}:{key}"], args=[limit, self._refill_ms, tokens]
        )
        return Reservation(
            key=key,
            tokens=tokens,
            allowed=bool(allowed),
            settle_ref=0,  # a bucket has no windows, so settling needs nothing extra
            state=BudgetState(limit=limit, remaining=int(remaining), reset_at_ms=int(full_at_ms), now_ms=int(now_ms)),
            retry_after_ms=int(retry_after_ms),
        )

    async def settle(self, reservation: Reservation, actual_tokens: int) -> BudgetState:
        if not reservation.allowed:
            raise ValueError("only an admitted reservation can be settled")
        if actual_tokens < 0:
            raise ValueError(f"actual_tokens must not be negative, got {actual_tokens}")
        limit = reservation.state.limit
        remaining, full_at_ms, now_ms = await self._settle(
            keys=[f"{KEY_PREFIX}:{reservation.key}"],
            args=[limit, self._refill_ms, actual_tokens - reservation.tokens],
        )
        return BudgetState(limit=limit, remaining=int(remaining), reset_at_ms=int(full_at_ms), now_ms=int(now_ms))

    async def release(self, reservation: Reservation) -> BudgetState:
        return await self.settle(reservation, 0)
