"""Tests for the fixed-window token budget."""

import anyio
import pytest
import redis.asyncio

from app.budgets import FixedWindowTokenBudget

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("token_window_has_room")]

LIMIT = 100


@pytest.fixture
def budget(async_redis_db: redis.asyncio.Redis) -> FixedWindowTokenBudget:
    return FixedWindowTokenBudget(async_redis_db, window_seconds=60)


async def test_reservations_within_the_limit_are_admitted(budget: FixedWindowTokenBudget, key: str) -> None:
    first = await budget.reserve(key, 60, limit=LIMIT)
    second = await budget.reserve(key, 40, limit=LIMIT)

    assert first.allowed and second.allowed
    assert (first.state.remaining, second.state.remaining) == (40, 0)
    assert second.state.reset_at_ms % 60_000 == 0
    assert 0 < second.state.reset_at_ms - second.state.now_ms <= 60_000


async def test_a_reservation_that_does_not_fit_is_refused_and_not_recorded(
    budget: FixedWindowTokenBudget, key: str
) -> None:
    await budget.reserve(key, 60, limit=LIMIT)

    refused = await budget.reserve(key, 50, limit=LIMIT)

    assert not refused.allowed
    assert refused.state.remaining == 40
    assert 1 <= refused.state.retry_after_seconds <= 60
    assert (await budget.reserve(key, 40, limit=LIMIT)).allowed


async def test_settling_below_the_reservation_refunds_the_difference(
    budget: FixedWindowTokenBudget, key: str
) -> None:
    reservation = await budget.reserve(key, 90, limit=LIMIT)

    state = await budget.settle(reservation, 30)

    assert state.remaining == 70
    assert (await budget.reserve(key, 70, limit=LIMIT)).allowed


async def test_settling_above_the_reservation_charges_the_difference(
    budget: FixedWindowTokenBudget, key: str
) -> None:
    reservation = await budget.reserve(key, 50, limit=LIMIT)

    state = await budget.settle(reservation, 80)

    assert state.remaining == 20
    assert not (await budget.reserve(key, 21, limit=LIMIT)).allowed


async def test_release_returns_the_whole_reservation(budget: FixedWindowTokenBudget, key: str) -> None:
    reservation = await budget.reserve(key, LIMIT, limit=LIMIT)

    state = await budget.release(reservation)

    assert state.remaining == LIMIT
    assert (await budget.reserve(key, LIMIT, limit=LIMIT)).allowed


async def test_settling_after_the_window_ends_leaves_the_new_window_alone(
    async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    budget = FixedWindowTokenBudget(async_redis_db, window_seconds=0.5)
    old = await budget.reserve(key, 80, limit=LIMIT)
    await anyio.sleep((old.state.reset_at_ms - old.state.now_ms) / 1000 + 0.02)
    await budget.reserve(key, 30, limit=LIMIT)

    state = await budget.settle(old, 0)

    assert state.remaining == 70


async def test_concurrent_reservations_never_exceed_the_limit(budget: FixedWindowTokenBudget, key: str) -> None:
    admitted: list[bool] = []

    async def reserve() -> None:
        admitted.append((await budget.reserve(key, 10, limit=LIMIT)).allowed)

    async with anyio.create_task_group() as tasks:
        for _ in range(20):
            tasks.start_soon(reserve)

    assert admitted.count(True) == 10
    refused = await budget.reserve(key, 1, limit=LIMIT)
    assert refused.state.remaining == 0


async def test_idle_budgets_expire_when_their_window_ends(
    budget: FixedWindowTokenBudget, async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    reservation = await budget.reserve(key, 10, limit=LIMIT)

    [stored] = await async_redis_db.keys(f"*{key}*")
    ttl_ms = await async_redis_db.pttl(stored)
    assert 0 < ttl_ms <= reservation.state.reset_at_ms - reservation.state.now_ms


async def test_only_admitted_reservations_can_be_settled(budget: FixedWindowTokenBudget, key: str) -> None:
    refused = await budget.reserve(key, LIMIT + 1, limit=LIMIT)

    with pytest.raises(ValueError):
        await budget.settle(refused, 0)


def test_rejects_an_empty_window() -> None:
    with pytest.raises(ValueError):
        FixedWindowTokenBudget(redis.asyncio.Redis(), window_seconds=0)
