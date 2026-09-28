"""Exact rolling-window rate limiting backed by a Redis sorted set."""

import secrets
from pathlib import Path

import redis
import redis.asyncio

from app.limiters.base import RateLimitDecision

KEY_PREFIX = "ratelimit:sliding_log"

_SCRIPT_SOURCE = (Path(__file__).parent / "scripts" / "sliding_log.lua").read_text()


class _SlidingLog:
    """Settings and reply parsing shared by the sync and asyncio limiters."""

    def __init__(self, *, limit: int, window_seconds: float) -> None:
        window_ms = round(window_seconds * 1000)
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        if window_ms < 1:
            raise ValueError(f"window_seconds must be at least 0.001, got {window_seconds}")
        self._limit = limit
        self._window_ms = window_ms

    def _keys(self, key: str) -> list[str]:
        return [f"{KEY_PREFIX}:{key}"]

    def _args(self) -> list[int | str]:
        return [self._limit, self._window_ms, secrets.token_hex(8)]

    def _decision(self, reply: list[int]) -> RateLimitDecision:
        allowed, remaining, reset_at_ms, now_ms = reply
        return RateLimitDecision(
            allowed=bool(allowed),
            limit=self._limit,
            remaining=int(remaining),
            reset_at_ms=int(reset_at_ms),
            now_ms=int(now_ms),
        )


class SlidingLogLimiter(_SlidingLog):
    """Admits at most ``limit`` requests per key in any rolling ``window_seconds``.

    Every admitted request is kept as one sorted-set member, so counts are exact at
    the cost of O(limit) memory per key. The prune/count/insert sequence runs as one
    Lua script, so concurrent callers can never observe the same count.
    """

    def __init__(self, client: redis.Redis, *, limit: int, window_seconds: float) -> None:
        super().__init__(limit=limit, window_seconds=window_seconds)
        self._script = client.register_script(_SCRIPT_SOURCE)

    def hit(self, key: str) -> RateLimitDecision:
        return self._decision(self._script(keys=self._keys(key), args=self._args()))


class AsyncSlidingLogLimiter(_SlidingLog):
    """The asyncio counterpart of ``SlidingLogLimiter``: same script, same keys, same behaviour."""

    def __init__(self, client: redis.asyncio.Redis, *, limit: int, window_seconds: float) -> None:
        super().__init__(limit=limit, window_seconds=window_seconds)
        self._script = client.register_script(_SCRIPT_SOURCE)

    async def hit(self, key: str) -> RateLimitDecision:
        return self._decision(await self._script(keys=self._keys(key), args=self._args()))
