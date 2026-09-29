"""Per-caller token budgets over fixed windows aligned to the Redis clock.

This is the brute-force version: simple and exact within a window, but a caller can
spend a full budget at the end of one window and another at the start of the next.
"""

from pathlib import Path

import redis.asyncio

from app.budgets.base import BudgetState, Reservation

KEY_PREFIX = "tokenbudget:fixed_window"

_SCRIPTS = Path(__file__).parent / "scripts"
_RESERVE_SOURCE = (_SCRIPTS / "fixed_window_reserve.lua").read_text()
_SETTLE_SOURCE = (_SCRIPTS / "fixed_window_settle.lua").read_text()


class FixedWindowTokenBudget:
    """Reserve tokens before a request, then settle against the tokens it really used.

    Reserving and settling each run as one Lua script, so concurrent requests can never
    reserve past the limit, and a settlement only ever touches the window its
    reservation was made in.
    """

    def __init__(self, client: redis.asyncio.Redis, *, window_seconds: float) -> None:
        window_ms = round(window_seconds * 1000)
        if window_ms < 1:
            raise ValueError(f"window_seconds must be at least 0.001, got {window_seconds}")
        self._window_ms = window_ms
        self._reserve = client.register_script(_RESERVE_SOURCE)
        self._settle = client.register_script(_SETTLE_SOURCE)

    async def reserve(self, key: str, tokens: int, *, limit: int) -> Reservation:
        if tokens < 0:
            raise ValueError(f"tokens must not be negative, got {tokens}")
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        allowed, remaining, window_start_ms, reset_at_ms, now_ms = await self._reserve(
            keys=[f"{KEY_PREFIX}:{key}"],
            args=[limit, self._window_ms, tokens],
        )
        state = BudgetState(limit=limit, remaining=int(remaining), reset_at_ms=int(reset_at_ms), now_ms=int(now_ms))
        return Reservation(
            key=key,
            tokens=tokens,
            allowed=bool(allowed),
            settle_ref=int(window_start_ms),
            state=state,
            retry_after_ms=0 if allowed else state.reset_after_ms,
        )

    async def settle(self, reservation: Reservation, actual_tokens: int) -> BudgetState:
        if not reservation.allowed:
            raise ValueError("only an admitted reservation can be settled")
        if actual_tokens < 0:
            raise ValueError(f"actual_tokens must not be negative, got {actual_tokens}")
        limit = reservation.state.limit
        remaining, reset_at_ms, now_ms = await self._settle(
            keys=[f"{KEY_PREFIX}:{reservation.key}"],
            args=[limit, self._window_ms, reservation.settle_ref, actual_tokens - reservation.tokens],
        )
        return BudgetState(limit=limit, remaining=int(remaining), reset_at_ms=int(reset_at_ms), now_ms=int(now_ms))

    async def release(self, reservation: Reservation) -> BudgetState:
        return await self.settle(reservation, 0)
