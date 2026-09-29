"""Tests for the Redis sliding-log rate limiter."""

import time
from collections.abc import Awaitable
from typing import cast

import anyio
import pytest
import redis.asyncio

from app.limiters import AsyncSlidingLogLimiter

pytestmark = pytest.mark.anyio

LIMIT = 5


@pytest.fixture
def limiter(async_redis_db: redis.asyncio.Redis) -> AsyncSlidingLogLimiter:
    return AsyncSlidingLogLimiter(async_redis_db, limit=LIMIT, window_seconds=60)


async def _wait_for_next_second() -> None:
    await anyio.sleep(1 - time.time() % 1)


async def test_burst_within_one_second_admits_exactly_limit(limiter: AsyncSlidingLogLimiter, key: str) -> None:
    await _wait_for_next_second()

    results = [(await limiter.hit(key)).allowed for _ in range(LIMIT * 2)]

    assert results == [True] * LIMIT + [False] * LIMIT


async def test_concurrent_requests_admit_exactly_limit(limiter: AsyncSlidingLogLimiter, key: str) -> None:
    results: list[bool] = []

    async def send() -> None:
        results.append((await limiter.hit(key)).allowed)

    async with anyio.create_task_group() as tasks:
        for _ in range(20):
            tasks.start_soon(send)

    assert results.count(True) == LIMIT


async def test_remaining_counts_down_and_stays_at_zero(limiter: AsyncSlidingLogLimiter, key: str) -> None:
    remaining = [(await limiter.hit(key)).remaining for _ in range(LIMIT + 2)]

    assert remaining == [4, 3, 2, 1, 0, 0, 0]


async def test_keys_are_limited_independently(limiter: AsyncSlidingLogLimiter, key: str) -> None:
    for _ in range(LIMIT):
        await limiter.hit(key)

    assert not (await limiter.hit(key)).allowed
    assert (await limiter.hit(f"{key}:other")).allowed


async def test_reset_is_when_the_oldest_request_leaves_the_window(
    async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    limiter = AsyncSlidingLogLimiter(async_redis_db, limit=2, window_seconds=1)

    first = await limiter.hit(key)
    await anyio.sleep(0.05)
    second = await limiter.hit(key)
    denied = await limiter.hit(key)

    assert not denied.allowed
    assert second.reset_at_ms == first.now_ms + 1000
    assert denied.reset_at_ms == first.now_ms + 1000
    assert denied.retry_after_seconds == 1


async def test_requests_are_admitted_again_once_the_window_passes(
    async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    limiter = AsyncSlidingLogLimiter(async_redis_db, limit=1, window_seconds=0.2)

    assert (await limiter.hit(key)).allowed
    assert not (await limiter.hit(key)).allowed
    await anyio.sleep(0.25)
    assert (await limiter.hit(key)).allowed


async def test_denied_requests_do_not_use_up_the_window(async_redis_db: redis.asyncio.Redis, key: str) -> None:
    limiter = AsyncSlidingLogLimiter(async_redis_db, limit=1, window_seconds=0.5)

    assert (await limiter.hit(key)).allowed
    await anyio.sleep(0.3)
    assert not (await limiter.hit(key)).allowed
    # The admitted request has aged out; had the denied one been recorded, it would
    # still be in the window and block this request.
    await anyio.sleep(0.3)
    assert (await limiter.hit(key)).allowed


async def test_idle_keys_expire_after_the_window(
    async_redis_db: redis.asyncio.Redis, limiter: AsyncSlidingLogLimiter, key: str
) -> None:
    await limiter.hit(key)

    [stored] = await cast(Awaitable[list[str]], async_redis_db.keys(f"*{key}*"))
    assert 0 < await cast(Awaitable[int], async_redis_db.pttl(stored)) <= 60_000


@pytest.mark.parametrize(("limit", "window_seconds"), [(0, 60), (5, 0), (5, 0.0004)])
def test_rejects_invalid_configuration(limit: int, window_seconds: float) -> None:
    with pytest.raises(ValueError):
        AsyncSlidingLogLimiter(redis.asyncio.Redis(), limit=limit, window_seconds=window_seconds)
