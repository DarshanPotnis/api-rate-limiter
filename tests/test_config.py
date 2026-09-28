"""Tests for environment-driven settings and the lazily created Redis client."""

from collections.abc import Iterator
from pathlib import Path

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


@pytest.fixture
def no_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run from an empty directory so a developer's .env cannot leak into the test."""
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("no_dotenv")
def test_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://cache.internal:6390/2")
    monkeypatch.setenv("DEFAULT_MAX_TOKENS", "512")
    monkeypatch.setenv("MOCK_LATENCY_SECONDS", "0.25")

    settings = Settings()

    assert str(settings.redis_url) == "redis://cache.internal:6390/2"
    assert settings.default_max_tokens == 512
    assert settings.mock_latency_seconds == 0.25


@pytest.mark.parametrize(
    ("name", "value"),
    [("REDIS_URL", "http://localhost:6379"), ("DEFAULT_MAX_TOKENS", "0"), ("MOCK_LATENCY_SECONDS", "-1")],
)
@pytest.mark.usefixtures("no_dotenv")
def test_invalid_settings_fail_at_startup(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.usefixtures("fresh_caches")
def test_redis_client_does_not_connect_until_first_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens on port 1

    client = get_redis()

    assert client is get_redis()
    with pytest.raises(redis.ConnectionError):
        client.ping()
