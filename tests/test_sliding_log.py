"""Tests for the Redis sliding-log rate limiter."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import redis

from app.limiters import SlidingLogLimiter

LIMIT = 5


@pytest.fixture
def limiter(redis_db: redis.Redis) -> SlidingLogLimiter:
    return SlidingLogLimiter(redis_db, limit=LIMIT, window_seconds=60)


def _wait_for_next_second() -> None:
    time.sleep(1 - time.time() % 1)


def test_burst_within_one_second_admits_exactly_limit(limiter: SlidingLogLimiter, key: str) -> None:
    _wait_for_next_second()

    results = [limiter.hit(key).allowed for _ in range(LIMIT * 2)]

    assert results == [True] * LIMIT + [False] * LIMIT


def test_concurrent_requests_admit_exactly_limit(limiter: SlidingLogLimiter, key: str) -> None:
    workers = 20
    start = threading.Barrier(workers)

    def send(_: int) -> bool:
        start.wait()
        return limiter.hit(key).allowed

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(send, range(workers)))

    assert results.count(True) == LIMIT


def test_remaining_counts_down_and_stays_at_zero(limiter: SlidingLogLimiter, key: str) -> None:
    remaining = [limiter.hit(key).remaining for _ in range(LIMIT + 2)]

    assert remaining == [4, 3, 2, 1, 0, 0, 0]


def test_keys_are_limited_independently(limiter: SlidingLogLimiter, key: str) -> None:
    for _ in range(LIMIT):
        limiter.hit(key)

    assert not limiter.hit(key).allowed
    assert limiter.hit(f"{key}:other").allowed


def test_reset_is_when_the_oldest_request_leaves_the_window(redis_db: redis.Redis, key: str) -> None:
    limiter = SlidingLogLimiter(redis_db, limit=2, window_seconds=1)

    first = limiter.hit(key)
    time.sleep(0.05)
    second = limiter.hit(key)
    denied = limiter.hit(key)

    assert not denied.allowed
    assert second.reset_at_ms == first.now_ms + 1000
    assert denied.reset_at_ms == first.now_ms + 1000
    assert denied.retry_after_seconds == 1


def test_requests_are_admitted_again_once_the_window_passes(redis_db: redis.Redis, key: str) -> None:
    limiter = SlidingLogLimiter(redis_db, limit=1, window_seconds=0.2)

    assert limiter.hit(key).allowed
    assert not limiter.hit(key).allowed
    time.sleep(0.25)
    assert limiter.hit(key).allowed


def test_denied_requests_do_not_use_up_the_window(redis_db: redis.Redis, key: str) -> None:
    limiter = SlidingLogLimiter(redis_db, limit=1, window_seconds=0.5)

    assert limiter.hit(key).allowed
    time.sleep(0.3)
    assert not limiter.hit(key).allowed
    # The admitted request has aged out; had the denied one been recorded, it would
    # still be in the window and block this request.
    time.sleep(0.3)
    assert limiter.hit(key).allowed


def test_idle_keys_expire_after_the_window(redis_db: redis.Redis, limiter: SlidingLogLimiter, key: str) -> None:
    limiter.hit(key)

    [stored] = redis_db.keys(f"*{key}*")
    assert 0 < redis_db.pttl(stored) <= 60_000


@pytest.mark.parametrize(("limit", "window_seconds"), [(0, 60), (5, 0), (5, 0.0004)])
def test_rejects_invalid_configuration(redis_db: redis.Redis, limit: int, window_seconds: float) -> None:
    with pytest.raises(ValueError):
        SlidingLogLimiter(redis_db, limit=limit, window_seconds=window_seconds)
