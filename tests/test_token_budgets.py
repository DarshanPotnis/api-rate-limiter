"""Behaviour every token budget must share, and how the two compare across a window boundary."""

from collections.abc import Awaitable
from typing import cast

import anyio
import pytest
import redis.asyncio

from app.budgets import FixedWindowTokenBudget, TokenBucketBudget, TokenBudget

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("token_window_has_room")]

LIMIT = 100


@pytest.fixture(params=["fixed_window", "token_bucket"])
def budget(request: pytest.FixtureRequest, async_redis_db: redis.asyncio.Redis) -> TokenBudget:
    if request.param == "fixed_window":
        return FixedWindowTokenBudget(async_redis_db, window_seconds=60)
    return TokenBucketBudget(async_redis_db, refill_seconds=60)


async def test_reservations_within_the_limit_are_admitted(budget: TokenBudget, key: str) -> None:
    first = await budget.reserve(key, 60, limit=LIMIT)
    second = await budget.reserve(key, 40, limit=LIMIT)

    assert first.allowed and second.allowed
    assert (first.state.remaining, second.state.remaining) == (40, 0)
    assert first.retry_after_seconds == 0


async def test_a_reservation_that_does_not_fit_is_refused_and_not_recorded(budget: TokenBudget, key: str) -> None:
    await budget.reserve(key, 60, limit=LIMIT)

    refused = await budget.reserve(key, 50, limit=LIMIT)

    assert not refused.allowed
    assert refused.state.remaining == 40
    assert 1 <= refused.retry_after_seconds <= 60
    assert (await budget.reserve(key, 40, limit=LIMIT)).allowed


async def test_settling_below_the_reservation_refunds_the_difference(budget: TokenBudget, key: str) -> None:
    reservation = await budget.reserve(key, 90, limit=LIMIT)

    state = await budget.settle(reservation, 30)

    assert state.remaining == 70
    assert (await budget.reserve(key, 70, limit=LIMIT)).allowed


async def test_settling_above_the_reservation_charges_the_difference(budget: TokenBudget, key: str) -> None:
    reservation = await budget.reserve(key, 50, limit=LIMIT)

    state = await budget.settle(reservation, 80)

    assert state.remaining == 20
    assert not (await budget.reserve(key, 21, limit=LIMIT)).allowed


async def test_release_returns_the_whole_reservation(budget: TokenBudget, key: str) -> None:
    reservation = await budget.reserve(key, LIMIT, limit=LIMIT)

    state = await budget.release(reservation)

    assert state.remaining == LIMIT
    assert (await budget.reserve(key, LIMIT, limit=LIMIT)).allowed


async def test_a_zero_token_reservation_reads_the_balance_without_changing_it(budget: TokenBudget, key: str) -> None:
    await budget.reserve(key, 30, limit=LIMIT)

    first = await budget.reserve(key, 0, limit=LIMIT)
    second = await budget.reserve(key, 0, limit=LIMIT)

    assert (first.state.remaining, second.state.remaining) == (70, 70)


async def test_concurrent_reservations_never_exceed_the_limit(budget: TokenBudget, key: str) -> None:
    admitted: list[bool] = []

    async def reserve() -> None:
        admitted.append((await budget.reserve(key, 10, limit=LIMIT)).allowed)

    async with anyio.create_task_group() as tasks:
        for _ in range(20):
            tasks.start_soon(reserve)

    assert admitted.count(True) == 10
    assert (await budget.reserve(key, 1, limit=LIMIT)).state.remaining == 0


async def test_only_admitted_reservations_can_be_settled(budget: TokenBudget, key: str) -> None:
    refused = await budget.reserve(key, LIMIT + 1, limit=LIMIT)

    with pytest.raises(ValueError):
        await budget.settle(refused, 0)


async def _redis_ms(client: redis.asyncio.Redis) -> int:
    seconds, microseconds = await cast(Awaitable[tuple[int, int]], client.time())
    return seconds * 1000 + microseconds // 1000


async def _burst(budget: TokenBudget, caller: str, cost: int) -> int:
    """Reserve ``cost`` tokens at a time until refused; return the tokens admitted."""
    admitted = 0
    while (await budget.reserve(caller, cost, limit=LIMIT)).allowed:
        admitted += cost
    return admitted


async def test_a_burst_across_a_boundary_gets_2x_from_a_fixed_window_but_about_1x_from_a_bucket(
    async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    # One-second windows and a one-second refill, so the test never waits for a real minute.
    budgets: dict[str, TokenBudget] = {
        "fixed window": FixedWindowTokenBudget(async_redis_db, window_seconds=1),
        "token bucket": TokenBucketBudget(async_redis_db, refill_seconds=1),
    }
    admitted: dict[str, int] = {}
    spans: dict[str, int] = {}
    for name, budget in budgets.items():
        caller = f"{key}-{name.replace(' ', '-')}"
        until_boundary = 1000 - (await _redis_ms(async_redis_db)) % 1000
        await anyio.sleep(((until_boundary - 100) % 1000) / 1000)  # start 100 ms before a boundary
        started = await _redis_ms(async_redis_db)
        before = await _burst(budget, caller, cost=10)
        await anyio.sleep((1000 - (await _redis_ms(async_redis_db)) % 1000 + 20) / 1000)  # 20 ms after it
        after = await _burst(budget, caller, cost=10)
        span = await _redis_ms(async_redis_db) - started
        admitted[name] = before + after
        spans[name] = span
        print(
            f"\n{name}: {before} + {after} = {before + after} tokens admitted within {span} ms across a "
            f"boundary, {(before + after) / LIMIT:.1f}x the limit of {LIMIT}"
        )

    assert admitted["fixed window"] >= 1.9 * LIMIT
    # A bucket can only add what it refilled during the burst (LIMIT tokens a second here),
    # plus one request's worth of rounding, however long a slow machine takes.
    assert admitted["token bucket"] <= LIMIT + spans["token bucket"] * LIMIT / 1000 + 10
