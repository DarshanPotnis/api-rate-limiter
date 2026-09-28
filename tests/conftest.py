"""Shared fixtures.

Tests run against the Redis database at TEST_REDIS_URL (database 15 by default),
and each test deletes only the keys it created, so the app's data is never touched.
"""

import os
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import cast

import pytest
import redis
import redis.asyncio

from app.auth import VALID_API_KEYS
from app.config import get_settings
from app.main import limiter_for_tier
from app.redis_client import get_redis

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture(scope="session")
def redis_db() -> Iterator[redis.Redis]:
    client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except redis.ConnectionError as exc:
        pytest.exit(
            f"Redis is not reachable at {TEST_REDIS_URL} ({exc}). "
            "Start it with `docker compose up -d` or set TEST_REDIS_URL.",
            returncode=1,
        )
    yield client
    client.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def async_redis_db(redis_db: redis.Redis) -> AsyncIterator[redis.asyncio.Redis]:
    """An asyncio client for the test database (``redis_db`` has already checked it is reachable)."""
    client = redis.asyncio.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def token_window_has_room(redis_db: redis.Redis) -> None:
    """Wait for the next minute if the current one ends within two seconds.

    Fixed-window tests expect all their requests to land in one window; this keeps a
    test from straddling a minute boundary on the Redis clock.
    """
    seconds, microseconds = cast(tuple[int, int], redis_db.time())
    left_ms = 60_000 - (seconds * 1000 + microseconds // 1000) % 60_000
    if left_ms < 2_000:
        time.sleep(left_ms / 1000 + 0.05)


@pytest.fixture
def key(redis_db: redis.Redis) -> Iterator[str]:
    """A rate-limit key unique to this test; every Redis key containing it is removed afterwards."""
    key = f"test-{uuid.uuid4().hex}"
    yield key
    created = list(redis_db.scan_iter(match=f"*{key}*"))
    if created:
        redis_db.delete(*created)


def _clear_app_caches() -> None:
    for cached in (get_settings, get_redis, limiter_for_tier):
        cached.cache_clear()


def _delete_demo_user_keys(client: redis.Redis) -> None:
    for user_id in VALID_API_KEYS.values():
        created = list(client.scan_iter(match=f"*{user_id}*"))
        if created:
            client.delete(*created)


@pytest.fixture
def app_uses_test_redis(redis_db: redis.Redis, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the real app, with no dependency overrides, at the test database.

    Requests then run as the built-in demo users, whose keys are removed before and after.
    """
    monkeypatch.setenv("REDIS_URL", TEST_REDIS_URL)
    _clear_app_caches()
    _delete_demo_user_keys(redis_db)
    yield
    _delete_demo_user_keys(redis_db)
    _clear_app_caches()
