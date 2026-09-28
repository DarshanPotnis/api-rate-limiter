"""Tests for environment-driven settings and the lazily created Redis client."""

from collections.abc import Iterator

import pytest
import redis
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.redis_client import get_redis


@pytest.fixture
def fresh_caches() -> Iterator[None]:
    get_settings.cache_clear()
    get_redis.cache_clear()
    yield
    get_settings.cache_clear()
    get_redis.cache_clear()


def test_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6390/2")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "100")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "30")

    settings = Settings(_env_file=None)

    assert str(settings.redis_url) == "redis://cache.internal:6390/2"
    assert settings.rate_limit_requests == 100
    assert settings.rate_limit_window_seconds == 30


@pytest.mark.parametrize(
    ("name", "value"),
    [("RATE_LIMIT_REQUESTS", "0"), ("RATE_LIMIT_WINDOW_SECONDS", "-1"), ("REDIS_URL", "http://localhost:6379")],
)
def test_invalid_settings_fail_at_startup(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.usefixtures("fresh_caches")
def test_redis_client_does_not_connect_until_first_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens on port 1

    client = get_redis()

    assert client is get_redis()
    with pytest.raises(redis.ConnectionError):
        client.ping()
