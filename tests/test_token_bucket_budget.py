"""Tests specific to the token-bucket budget. Shared behaviour is in test_token_budgets.py."""

import math
from collections.abc import Awaitable
from typing import cast

import anyio
import pytest
import redis.asyncio

from app.budgets import TokenBucketBudget

pytestmark = pytest.mark.anyio

LIMIT = 100  # with a 60 s refill: one token every 600 ms

# Timing assertions use the Redis-clock timestamps the budget returns, so a slow machine
# changes the expected value and the actual one together.


@pytest.fixture
def budget(async_redis_db: redis.asyncio.Redis) -> TokenBucketBudget:
    return TokenBucketBudget(async_redis_db, refill_seconds=60)


async def test_a_refused_request_can_retry_once_the_bucket_holds_enough_for_it(
    budget: TokenBucketBudget, key: str
) -> None:
    first = await budget.reserve(key, 60, limit=LIMIT)

    refused = await budget.reserve(key, 50, limit=LIMIT)  # 10 tokens short, less what refilled since

    elapsed_ms = refused.state.now_ms - first.state.now_ms
    assert not refused.allowed
    assert abs(refused.retry_after_ms - (6_000 - elapsed_ms)) <= 1
    assert refused.retry_after_seconds == math.ceil(refused.retry_after_ms / 1000)


async def test_reset_is_when_the_bucket_is_full_again(budget: TokenBucketBudget, key: str) -> None:
    reservation = await budget.reserve(key, 30, limit=LIMIT)

    assert abs(reservation.state.reset_after_ms - 18_000) <= 1  # 30 tokens at one per 600 ms


async def test_the_bucket_refills_continuously(async_redis_db: redis.asyncio.Redis, key: str) -> None:
    budget = TokenBucketBudget(async_redis_db, refill_seconds=1)  # 100 tokens a second
    drained = await budget.reserve(key, LIMIT, limit=LIMIT)

    await anyio.sleep(0.3)
    refilled = await budget.reserve(key, 20, limit=LIMIT)

    elapsed_ms = refilled.state.now_ms - drained.state.now_ms
    expected = min(LIMIT, elapsed_ms * LIMIT / 1000) - 20
    assert refilled.allowed
    assert abs(refilled.state.remaining - math.floor(expected)) <= 1


async def test_an_overcharge_leaves_a_debt_that_refill_pays_back(budget: TokenBucketBudget, key: str) -> None:
    reservation = await budget.reserve(key, 50, limit=LIMIT)

    state = await budget.settle(reservation, 150)  # 100 more than reserved: the balance goes to -50
    refused = await budget.reserve(key, 1, limit=LIMIT)

    # 51 tokens short at one per 600 ms, less whatever refilled since the reservation.
    elapsed_ms = refused.state.now_ms - reservation.state.now_ms
    assert state.remaining == 0
    assert not refused.allowed
    assert abs(refused.retry_after_ms - (30_600 - elapsed_ms)) <= 1


async def test_a_refund_never_fills_the_bucket_past_capacity(budget: TokenBucketBudget, key: str) -> None:
    reservation = await budget.reserve(key, 10, limit=LIMIT)

    state = await budget.settle(reservation, 0)

    assert state.remaining == LIMIT
    assert (await budget.reserve(key, LIMIT, limit=LIMIT)).allowed
    assert not (await budget.reserve(key, 1, limit=LIMIT)).allowed


async def test_an_idle_bucket_is_forgotten_once_it_would_be_full(
    budget: TokenBucketBudget, async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    reservation = await budget.reserve(key, 30, limit=LIMIT)

    [stored] = await async_redis_db.keys(f"*{key}*")
    ttl_ms = await async_redis_db.pttl(stored)
    seconds, microseconds = await cast(Awaitable[tuple[int, int]], async_redis_db.time())
    since_ms = seconds * 1000 + microseconds // 1000 - reservation.state.now_ms
    assert 18_000 - since_ms - 1 <= ttl_ms <= 18_001  # full again 18 s after the reservation


def test_rejects_an_instant_refill() -> None:
    with pytest.raises(ValueError):
        TokenBucketBudget(redis.asyncio.Redis(), refill_seconds=0)
