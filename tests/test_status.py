"""Tests for GET /status, the read-only view of the gateway that the console polls."""

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

ENTERPRISE = {"Authorization": "Bearer enterprise-key"}


@pytest.fixture
def http(app_uses_test_redis: None) -> Iterator[TestClient]:
    with TestClient(app) as http:  # the lifespan builds the registry and the fallback strategy
        yield http


def _status(http: TestClient) -> dict[str, Any]:
    response = http.get("/status")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _model(body: dict[str, Any], model_id: str) -> dict[str, Any]:
    [model] = [m for m in body["models"] if m["id"] == model_id]
    return model


def _fail_ollama(http: TestClient, times: int) -> None:
    """Ask for the Ollama model, which the test settings point at a closed port."""
    body = {"model": get_settings().ollama_model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}
    for _ in range(times):
        assert http.post("/v1/chat/completions", headers=ENTERPRISE, json=body).status_code == 503


def test_every_configured_model_starts_closed(http: TestClient) -> None:
    body = _status(http)

    assert (body["fallback_strategy"], body["token_budget"]) == ("circuit_breaker", "token_bucket")
    assert [(m["id"], m["provider"], m["breaker"], m["cooldown_remaining_seconds"]) for m in body["models"]] == [
        ("mock", "mock", "closed", 0.0),
        (get_settings().ollama_model, "ollama", "closed", 0.0),
    ]


def test_a_model_opens_after_three_failures_and_reports_its_cooldown(http: TestClient) -> None:
    _fail_ollama(http, 3)

    body = _status(http)

    ollama = _model(body, get_settings().ollama_model)
    assert (ollama["breaker"], ollama["consecutive_failures"]) == ("open", 3)
    assert 0 < ollama["cooldown_remaining_seconds"] <= get_settings().breaker_cooldown_seconds
    assert _model(body, "mock")["breaker"] == "closed"


def test_a_model_is_half_open_once_its_cooldown_passes(
    app_uses_test_redis: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BREAKER_COOLDOWN_SECONDS", "0.2")
    get_settings.cache_clear()
    with TestClient(app) as http:
        _fail_ollama(http, 3)
        time.sleep(0.3)

        ollama = _model(_status(http), get_settings().ollama_model)

    assert (ollama["breaker"], ollama["cooldown_remaining_seconds"]) == ("half_open", 0.0)


def test_without_a_circuit_breaker_there_is_no_breaker_state(
    app_uses_test_redis: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FALLBACK_STRATEGY", "sequential")
    monkeypatch.setenv("TOKEN_BUDGET", "fixed_window")
    get_settings.cache_clear()
    with TestClient(app) as http:
        body = _status(http)

    assert (body["fallback_strategy"], body["token_budget"]) == ("sequential", "fixed_window")
    assert {(m["breaker"], m["consecutive_failures"]) for m in body["models"]} == {(None, None)}


def test_reading_the_status_changes_nothing(http: TestClient) -> None:
    _fail_ollama(http, 1)
    first = _status(http)

    for _ in range(3):
        _status(http)

    ollama_now = _model(_status(http), get_settings().ollama_model)
    ollama_then = _model(first, get_settings().ollama_model)
    assert (ollama_now["breaker"], ollama_now["consecutive_failures"]) == ("closed", 1)
    assert (ollama_then["breaker"], ollama_then["consecutive_failures"]) == ("closed", 1)


def test_the_status_is_read_only(http: TestClient) -> None:
    assert http.post("/status").status_code == 405
