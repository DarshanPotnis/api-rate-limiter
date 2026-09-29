"""Tests specific to the fixed-window token budget. Shared behaviour is in test_token_budgets.py."""

import anyio
import pytest
import redis.asyncio

from app.budgets import FixedWindowTokenBudget

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("token_window_has_room")]

LIMIT = 100


@pytest.fixture
def budget(async_redis_db: redis.asyncio.Redis) -> FixedWindowTokenBudget:
    return FixedWindowTokenBudget(async_redis_db, window_seconds=60)


async def test_the_budget_resets_when_the_minute_ends(budget: FixedWindowTokenBudget, key: str) -> None:
    reservation = await budget.reserve(key, 60, limit=LIMIT)

    assert reservation.state.reset_at_ms % 60_000 == 0
    assert 0 < reservation.state.reset_at_ms - reservation.state.now_ms <= 60_000


async def test_a_refused_request_can_retry_when_the_minute_ends(budget: FixedWindowTokenBudget, key: str) -> None:
    await budget.reserve(key, 60, limit=LIMIT)

    refused = await budget.reserve(key, 50, limit=LIMIT)

    assert refused.retry_after_ms == refused.state.reset_at_ms - refused.state.now_ms


async def test_settling_after_the_window_ends_leaves_the_new_window_alone(
    async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    budget = FixedWindowTokenBudget(async_redis_db, window_seconds=0.5)
    old = await budget.reserve(key, 80, limit=LIMIT)
    await anyio.sleep((old.state.reset_at_ms - old.state.now_ms) / 1000 + 0.02)
    await budget.reserve(key, 30, limit=LIMIT)

    state = await budget.settle(old, 0)

    assert state.remaining == 70


async def test_idle_budgets_expire_when_their_window_ends(
    budget: FixedWindowTokenBudget, async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    reservation = await budget.reserve(key, 10, limit=LIMIT)

    [stored] = await async_redis_db.keys(f"*{key}*")
    ttl_ms = await async_redis_db.pttl(stored)
    assert 0 < ttl_ms <= reservation.state.reset_at_ms - reservation.state.now_ms


def test_rejects_an_empty_window() -> None:
    with pytest.raises(ValueError):
        FixedWindowTokenBudget(redis.asyncio.Redis(), window_seconds=0)
