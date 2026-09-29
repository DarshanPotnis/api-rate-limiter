"""Tests for how /protected reports rate-limit decisions over HTTP."""

import math
from collections.abc import Iterator
from typing import cast

import pytest
import redis
from fastapi.testclient import TestClient

from app.auth import get_api_key
from app.limiters import SlidingLogLimiter
from app.main import app, get_limiter

LIMIT = 2
WINDOW_SECONDS = 60


def _redis_now_ms(client: redis.Redis) -> int:
    """Redis server time in ms, truncated the way the limiter's Lua script truncates it."""
    seconds, microseconds = cast(tuple[int, int], client.time())
    return seconds * 1000 + microseconds // 1000


@pytest.fixture
def client(redis_db: redis.Redis, key: str) -> Iterator[TestClient]:
    """A client authenticated as a user unique to this test, limited in the test database."""
    limiter = SlidingLogLimiter(redis_db, limit=LIMIT, window_seconds=WINDOW_SECONDS)
    app.dependency_overrides[get_limiter] = lambda: limiter
    app.dependency_overrides[get_api_key] = lambda: key
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_admitted_request_reports_rate_limit_headers(client: TestClient, key: str, redis_db: redis.Redis) -> None:
    # The reset time comes from the Redis clock, which can run ahead of this machine's clock
    # (Redis runs in a VM), so bound it by Redis time read around the request.
    before_ms = _redis_now_ms(redis_db)
    response = client.get("/protected")
    after_ms = _redis_now_ms(redis_db)

    assert response.status_code == 200
    assert response.json() == {"message": "You accessed a protected resource", "user": key}
    assert response.headers["X-RateLimit-Limit"] == str(LIMIT)
    assert response.headers["X-RateLimit-Remaining"] == str(LIMIT - 1)
    window_ms = WINDOW_SECONDS * 1000
    reset = int(response.headers["X-RateLimit-Reset"])
    assert math.ceil((before_ms + window_ms) / 1000) <= reset <= math.ceil((after_ms + window_ms) / 1000)
    assert "Retry-After" not in response.headers


def test_request_over_the_limit_gets_429_with_retry_after(client: TestClient) -> None:
    for _ in range(LIMIT):
        assert client.get("/protected").status_code == 200

    response = client.get("/protected")

    assert response.status_code == 429
    assert response.json() == {"detail": "Rate limit exceeded"}
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert 1 <= int(response.headers["Retry-After"]) <= WINDOW_SECONDS


def test_unknown_api_key_is_rejected() -> None:
    response = TestClient(app).get("/protected", headers={"X-API-KEY": "not-a-real-key"})

    assert response.status_code == 401


@pytest.mark.usefixtures("app_uses_test_redis")
@pytest.mark.parametrize(
    ("api_key", "limit"), [("free-tier-key", 5), ("pro-tier-key", 60), ("enterprise-key", 600)]
)
def test_each_tier_reports_its_own_request_limit(api_key: str, limit: int) -> None:
    response = TestClient(app).get("/protected", headers={"X-API-KEY": api_key})

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == str(limit)


@pytest.mark.usefixtures("app_uses_test_redis")
def test_free_tier_is_limited_while_pro_is_not() -> None:
    client = TestClient(app)

    free = [client.get("/protected", headers={"X-API-KEY": "free-tier-key"}).status_code for _ in range(6)]
    pro = [client.get("/protected", headers={"X-API-KEY": "pro-tier-key"}).status_code for _ in range(6)]

    assert free == [200] * 5 + [429]
    assert pro == [200] * 6
