"""Regression tests for the Redis sliding-log rate limiter."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

LIMIT = 5  # the limit the current implementation hard-codes

Hit = Callable[[str], bool]


def _wait_for_next_second() -> None:
    time.sleep(1 - time.time() % 1)


def test_burst_within_one_second_admits_exactly_limit(hit: Hit, key: str) -> None:
    _wait_for_next_second()

    results = [hit(key) for _ in range(LIMIT * 2)]

    assert results == [True] * LIMIT + [False] * LIMIT


def test_concurrent_requests_admit_exactly_limit(hit: Hit, key: str) -> None:
    workers = 20
    start = threading.Barrier(workers)

    def send(_: int) -> bool:
        start.wait()
        return hit(key)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(send, range(workers)))

    assert results.count(True) == LIMIT
