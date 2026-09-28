"""Exact rolling-window rate limiting backed by a Redis sorted set."""

import secrets
from pathlib import Path

import redis

from app.limiters.base import RateLimitDecision

KEY_PREFIX = "ratelimit:sliding_log"

_SCRIPT_SOURCE = (Path(__file__).parent / "scripts" / "sliding_log.lua").read_text()


class SlidingLogLimiter:
    """Admits at most ``limit`` requests per key in any rolling ``window_seconds``.

    Every admitted request is kept as one sorted-set member, so counts are exact at
    the cost of O(limit) memory per key. The prune/count/insert sequence runs as one
    Lua script, so concurrent callers can never observe the same count.
    """

    def __init__(self, client: redis.Redis, *, limit: int, window_seconds: float) -> None:
        window_ms = round(window_seconds * 1000)
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        if window_ms < 1:
            raise ValueError(f"window_seconds must be at least 0.001, got {window_seconds}")
        self._limit = limit
        self._window_ms = window_ms
        self._script = client.register_script(_SCRIPT_SOURCE)

    def hit(self, key: str) -> RateLimitDecision:
        allowed, remaining, reset_at_ms, now_ms = self._script(
            keys=[f"{KEY_PREFIX}:{key}"],
            args=[self._limit, self._window_ms, secrets.token_hex(8)],
        )
        return RateLimitDecision(
            allowed=bool(allowed),
            limit=self._limit,
            remaining=int(remaining),
            reset_at_ms=int(reset_at_ms),
            now_ms=int(now_ms),
        )
