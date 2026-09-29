"""End-to-end checks against a real Ollama. They skip themselves when Ollama is not running
with the model pulled, so the suite still passes without it."""

import logging
import math
import os
import time
from collections.abc import Iterator
from functools import partial

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.gateway.app import gateway
from app.gateway.auth import Caller, get_caller
from app.main import app
from app.tiers import Tier

# Read at import, before the autouse no_real_ollama fixture points the app elsewhere.
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
PRO = {"Authorization": "Bearer pro-tier-key"}
# Refills 2 tokens a second, so refill during a request barely moves the balance.
SLOW_TIER = Tier("integration", requests_per_minute=100, tokens_per_minute=120)


def _ollama_has_model() -> bool:
    try:
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    names = {model["name"] for model in response.json().get("models", [])}
    return OLLAMA_MODEL in names or f"{OLLAMA_MODEL}:latest" in names


pytestmark = [
    pytest.mark.skipif(
        not _ollama_has_model(), reason=f"Ollama is not running at {OLLAMA_URL} with {OLLAMA_MODEL} pulled"
    ),
    pytest.mark.usefixtures("token_window_has_room"),
]


@pytest.fixture
def http(app_uses_test_redis: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OLLAMA_BASE_URL", OLLAMA_URL)  # opt back in to the real Ollama
    monkeypatch.setenv("OLLAMA_MODEL", OLLAMA_MODEL)
    get_settings.cache_clear()
    with TestClient(app) as http:
        yield http
    gateway.dependency_overrides.clear()


def _chat(model: str, max_tokens: int) -> dict[str, object]:
    return {"model": model, "messages": [{"role": "user", "content": "Reply with one word: pong"}], "max_tokens": max_tokens}


def test_a_real_completion_is_settled_with_ollamas_own_counts(
    http: TestClient, caplog: pytest.LogCaptureFixture, key: str
) -> None:
    caplog.set_level(logging.INFO, logger="app.providers.ollama")
    gateway.dependency_overrides[get_caller] = lambda: Caller(user_id=key, tier=SLOW_TIER)
    budget = gateway.state.resources.token_budget
    assert http.portal is not None

    def tokens_left() -> int:  # a zero-token reservation reads the balance without changing it
        return http.portal.call(partial(budget.reserve, key, 0, limit=SLOW_TIER.tokens_per_minute)).state.remaining

    before = tokens_left()
    started = time.monotonic()
    response = http.post("/v1/chat/completions", json=_chat(OLLAMA_MODEL, max_tokens=8))
    refill_allowance = math.ceil((time.monotonic() - started) * SLOW_TIER.tokens_per_minute / 60) + 1
    used = before - tokens_left()

    assert response.status_code == 200, response.text
    usage = response.json()["usage"]
    assert response.headers["x-gateway-provider"] == "ollama"
    assert response.json()["model"] == OLLAMA_MODEL
    assert usage["prompt_tokens"] > 0
    assert 0 < usage["completion_tokens"] <= 8
    # The bucket lost what Ollama reported, less whatever refilled during the request.
    assert usage["total_tokens"] - refill_allowance <= used <= usage["total_tokens"]
    [record] = [r for r in caplog.records if r.name == "app.providers.ollama"]
    assert vars(record)["reported_prompt_tokens"] == usage["prompt_tokens"]


def test_auto_is_answered_by_the_real_ollama(http: TestClient) -> None:
    response = http.post("/v1/chat/completions", headers=PRO, json=_chat("auto", max_tokens=4))

    assert response.status_code == 200, response.text
    assert response.headers["x-gateway-provider"] == "ollama"
    assert response.json()["model"] == OLLAMA_MODEL


def test_models_lists_the_real_ollama_model(http: TestClient) -> None:
    response = http.get("/v1/models", headers=PRO)

    assert [model["id"] for model in response.json()["data"]] == ["mock", OLLAMA_MODEL, "auto"]
