"""Shared fixtures.

Tests run against the Redis database at TEST_REDIS_URL (database 15 by default),
and each test deletes only the keys it created, so the app's data is never touched.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
import redis

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
def key(redis_db: redis.Redis) -> Iterator[str]:
    """A rate-limit key unique to this test; every Redis key containing it is removed afterwards."""
    key = f"test-{uuid.uuid4().hex}"
    yield key
    created = list(redis_db.scan_iter(match=f"*{key}*"))
    if created:
        redis_db.delete(*created)
