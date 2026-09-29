"""Tests specific to the token-bucket budget. Shared behaviour is in test_token_budgets.py."""

import anyio
import pytest
import redis.asyncio

from app.budgets import TokenBucketBudget

pytestmark = pytest.mark.anyio

LIMIT = 100  # with a 60 s refill: one token every 600 ms


@pytest.fixture
def budget(async_redis_db: redis.asyncio.Redis) -> TokenBucketBudget:
    return TokenBucketBudget(async_redis_db, refill_seconds=60)


async def test_a_refused_request_can_retry_once_the_bucket_holds_enough_for_it(
    budget: TokenBucketBudget, key: str
) -> None:
    await budget.reserve(key, 60, limit=LIMIT)

    refused = await budget.reserve(key, 50, limit=LIMIT)  # 10 tokens short

    assert not refused.allowed
    assert 5_900 <= refused.retry_after_ms <= 6_000
    assert refused.retry_after_seconds == 6


async def test_reset_is_when_the_bucket_is_full_again(budget: TokenBucketBudget, key: str) -> None:
    reservation = await budget.reserve(key, 30, limit=LIMIT)

    assert 17_900 <= reservation.state.reset_after_ms <= 18_000


async def test_the_bucket_refills_continuously(async_redis_db: redis.asyncio.Redis, key: str) -> None:
    budget = TokenBucketBudget(async_redis_db, refill_seconds=1)  # 100 tokens a second
    await budget.reserve(key, LIMIT, limit=LIMIT)

    await anyio.sleep(0.3)
    refilled = await budget.reserve(key, 20, limit=LIMIT)

    assert refilled.allowed
    assert 5 <= refilled.state.remaining <= 20  # about 30 refilled, minus the 20 reserved


async def test_an_overcharge_leaves_a_debt_that_refill_pays_back(budget: TokenBucketBudget, key: str) -> None:
    reservation = await budget.reserve(key, 50, limit=LIMIT)

    state = await budget.settle(reservation, 150)  # 100 more than reserved: the balance goes to -50
    refused = await budget.reserve(key, 1, limit=LIMIT)

    assert state.remaining == 0
    assert not refused.allowed
    assert 30 <= refused.retry_after_seconds <= 31  # 51 tokens at one per 600 ms


async def test_a_refund_never_fills_the_bucket_past_capacity(budget: TokenBucketBudget, key: str) -> None:
    reservation = await budget.reserve(key, 10, limit=LIMIT)

    state = await budget.settle(reservation, 0)

    assert state.remaining == LIMIT
    assert (await budget.reserve(key, LIMIT, limit=LIMIT)).allowed
    assert not (await budget.reserve(key, 1, limit=LIMIT)).allowed


async def test_an_idle_bucket_is_forgotten_once_it_would_be_full(
    budget: TokenBucketBudget, async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    await budget.reserve(key, 30, limit=LIMIT)

    [stored] = await async_redis_db.keys(f"*{key}*")
    assert 17_900 <= await async_redis_db.pttl(stored) <= 18_001


def test_rejects_an_instant_refill() -> None:
    with pytest.raises(ValueError):
        TokenBucketBudget(redis.asyncio.Redis(), refill_seconds=0)
