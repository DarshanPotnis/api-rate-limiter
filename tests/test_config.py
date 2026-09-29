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


@pytest.mark.usefixtures("no_dotenv")
def test_ollama_defaults_and_the_auto_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    settings = Settings()

    assert str(settings.ollama_base_url) == "http://localhost:11434/"
    assert (settings.ollama_connect_timeout_seconds, settings.ollama_read_timeout_seconds) == (2.0, 120.0)
    assert settings.auto_chain == ("llama3.2:3b", "mock")


@pytest.mark.usefixtures("no_dotenv")
def test_the_optimized_implementations_are_the_defaults() -> None:
    settings = Settings()

    assert (settings.prompt_estimate, settings.token_budget, settings.fallback_strategy) == (
        "calibrated",
        "token_bucket",
        "circuit_breaker",
    )
    assert (settings.prompt_overhead_default, settings.per_message_tokens) == (32, 5)
    assert (settings.breaker_failure_threshold, settings.breaker_cooldown_seconds) == (3, 30.0)


@pytest.mark.usefixtures("no_dotenv")
def test_auto_fallback_is_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_FALLBACK", "qwen3:4b, mock")

    assert Settings().auto_chain == ("qwen3:4b", "mock")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("REDIS_URL", "http://localhost:6379"),
        ("DEFAULT_MAX_TOKENS", "0"),
        ("MOCK_LATENCY_SECONDS", "-1"),
        ("OLLAMA_BASE_URL", "not a url"),
        ("OLLAMA_MODEL", ""),
        ("OLLAMA_READ_TIMEOUT_SECONDS", "0"),
        ("LOG_LEVEL", "LOUD"),
        ("PROMPT_ESTIMATE", "psychic"),
        ("PROMPT_OVERHEAD_DEFAULT", "-1"),
        ("PER_MESSAGE_TOKENS", "-1"),
        ("TOKEN_BUDGET", "leaky_bucket"),
        ("FALLBACK_STRATEGY", "random"),
        ("BREAKER_FAILURE_THRESHOLD", "0"),
        ("BREAKER_COOLDOWN_SECONDS", "0"),
    ],
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
