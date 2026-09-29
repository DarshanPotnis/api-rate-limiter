"""The demo script, run in-process against the app, so CI notices when it drifts from the gateway."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway.app import gateway as gateway_app
from app.gateway.dependencies import get_registry
from app.main import app
from app.providers import MockProvider, Provider
from app.routing import ModelRegistry
from scripts import demo
from tests.fake_ollama import OLLAMA_MODEL, ollama_provider, ollama_up

RATE_LIMIT_HEADERS = {
    f"x-ratelimit-{kind}-{resource}" for kind in ("limit", "remaining", "reset") for resource in ("requests", "tokens")
}


@pytest.fixture
def http(app_uses_test_redis: None) -> Iterator[TestClient]:
    with TestClient(app) as http:  # runs the lifespan, as the real server does
        yield http
    gateway_app.dependency_overrides.clear()


@pytest.fixture
def gateway(http: TestClient) -> demo.Gateway:
    return demo.Gateway(base_url="http://testserver/v1", http_client=http)


def test_with_ollama_off_each_step_shows_its_limit(
    gateway: demo.Gateway, http: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    chat_requests: list[str] = []

    def record(request: httpx.Request) -> None:
        if request.url.path == "/v1/chat/completions":
            chat_requests.append(request.url.path)

    http.event_hooks = {"request": [record], "response": []}

    result = demo.run(gateway)

    completion = result.completion
    assert (completion.model, completion.provider) == ("mock", "mock")
    assert set(completion.headers) == RATE_LIMIT_HEADERS

    limit = result.request_limit
    assert (limit.admitted, limit.error_type) == (5, "requests")
    assert limit.retry_after is not None

    budget = result.token_budget
    assert (budget.admitted, budget.used_tokens, budget.error_type) == (1, 24_000, "tokens")
    assert budget.retry_after is not None and 1 <= int(budget.retry_after) <= 60

    assert (result.fallback.provider, result.fallback.model) == ("mock", "mock")

    breaker = result.breaker
    assert not breaker.ollama_answered
    # Step 4's "auto" request already counted one failure, so the third attempt finds it open.
    assert [attempt.status for attempt in breaker.attempts] == [503, 503, 503]
    assert breaker.opened
    assert breaker.attempts[-1].should_retry == "false"
    assert breaker.attempts[-1].retry_after is not None
    # 1 + 6 + 2 + 1 + 3 attempts, plus one call with the SDK's default retries that was not retried.
    assert len(chat_requests) == 14

    narration = capsys.readouterr().out
    for heading in demo.STEP_TITLES:
        assert heading in narration


def test_with_ollama_up_the_demo_says_ollama_answered(
    gateway: demo.Gateway, capsys: pytest.CaptureFixture[str]
) -> None:
    models: dict[str, Provider] = {"mock": MockProvider(), OLLAMA_MODEL: ollama_provider(ollama_up())}
    registry = ModelRegistry(models=models, aliases={"auto": [OLLAMA_MODEL, "mock"]})
    gateway_app.dependency_overrides[get_registry] = lambda: registry

    result = demo.run(gateway)

    assert (result.fallback.provider, result.fallback.model) == ("ollama", OLLAMA_MODEL)
    assert result.breaker.ollama_answered
    assert not result.breaker.opened
    assert "Stop Ollama" in capsys.readouterr().out


def test_a_second_run_within_a_minute_reports_the_spent_limits(gateway: demo.Gateway) -> None:
    demo.run(gateway)

    again = demo.run(gateway)

    assert again.request_limit.admitted == 0
    assert again.token_budget.admitted == 0
    assert again.breaker.attempts[0].should_retry == "false"  # still open from the first run


def test_it_explains_how_to_start_the_stack_when_the_gateway_is_down(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = demo.main(["--base-url", "http://127.0.0.1:9/v1"])  # nothing listens on port 9

    assert exit_code == 1
    assert "docker compose up" in capsys.readouterr().err
