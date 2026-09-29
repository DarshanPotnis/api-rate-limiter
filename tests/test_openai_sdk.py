"""The official openai SDK, unmodified, used as the client to prove wire compatibility."""

from collections.abc import Iterator
from typing import Any, cast

import openai
import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionMessageParam

from app.gateway.app import gateway
from app.gateway.dependencies import get_registry
from app.main import app
from app.providers import MockProvider
from app.routing import ModelRegistry
from tests.fake_ollama import OLLAMA_MODEL, ollama_hangs, ollama_provider

pytestmark = pytest.mark.usefixtures("token_window_has_room")

MESSAGES: list[ChatCompletionMessageParam] = [{"role": "user", "content": "Hello there"}]


@pytest.fixture
def http(app_uses_test_redis: None) -> Iterator[TestClient]:
    with TestClient(app) as http:  # entering runs the lifespan, which opens the gateway's Redis client
        yield http


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    gateway.dependency_overrides.clear()


def sdk(http: TestClient, api_key: str = "free-tier-key") -> openai.OpenAI:
    # openai 3.x annotates http_client as an httpx2 client but also accepts a classic
    # httpx.Client such as Starlette's TestClient, which is what routes calls in-process.
    http_client = cast(Any, http)
    return openai.OpenAI(base_url="http://testserver/v1", api_key=api_key, http_client=http_client, max_retries=0)


def test_a_chat_completion_round_trips(http: TestClient) -> None:
    completion = sdk(http).chat.completions.create(model="mock", messages=MESSAGES, max_tokens=16)

    assert completion.object == "chat.completion"
    assert completion.choices[0].message.content == "Mock reply to: Hello there"
    assert completion.choices[0].finish_reason == "stop"
    assert completion.usage is not None
    assert completion.usage.total_tokens == completion.usage.prompt_tokens + completion.usage.completion_tokens


def test_max_completion_tokens_is_accepted(http: TestClient) -> None:
    completion = sdk(http).chat.completions.create(model="mock", messages=MESSAGES, max_completion_tokens=2)

    assert completion.choices[0].finish_reason == "length"


def test_rate_limit_headers_are_visible_on_the_raw_response(http: TestClient) -> None:
    raw = sdk(http).chat.completions.with_raw_response.create(model="mock", messages=MESSAGES, max_tokens=16)

    assert raw.headers["x-ratelimit-limit-requests"] == "5"
    assert raw.headers["x-ratelimit-limit-tokens"] == "2000"
    assert raw.parse().choices[0].message.role == "assistant"


def test_models_are_listed(http: TestClient) -> None:
    assert [model.id for model in sdk(http).models.list()] == ["mock", "auto"]


def test_a_rate_limit_raises_rate_limit_error(http: TestClient) -> None:
    client = sdk(http)
    for _ in range(5):
        client.chat.completions.create(model="mock", messages=MESSAGES, max_tokens=16)

    with pytest.raises(openai.RateLimitError) as limited:
        client.chat.completions.create(model="mock", messages=MESSAGES, max_tokens=16)

    assert limited.value.code == "rate_limit_exceeded"
    assert int(limited.value.response.headers["retry-after"]) >= 1


def test_a_request_larger_than_the_budget_raises_bad_request_error(http: TestClient) -> None:
    with pytest.raises(openai.BadRequestError) as too_large:
        sdk(http).chat.completions.create(model="mock", messages=MESSAGES, max_tokens=5_000)

    assert too_large.value.code == "request_too_large"


def test_an_unknown_model_raises_not_found_error(http: TestClient) -> None:
    with pytest.raises(openai.NotFoundError) as missing:
        sdk(http).chat.completions.create(model="gpt-4o", messages=MESSAGES)

    assert missing.value.code == "model_not_found"


def test_a_wrong_key_raises_authentication_error(http: TestClient) -> None:
    with pytest.raises(openai.AuthenticationError) as rejected:
        sdk(http, api_key="not-a-real-key").models.list()

    assert rejected.value.code == "invalid_api_key"


def test_a_timed_out_model_is_not_retried_by_the_sdk(http: TestClient) -> None:
    registry = ModelRegistry(models={"mock": MockProvider(), OLLAMA_MODEL: ollama_provider(ollama_hangs)}, aliases={})
    gateway.dependency_overrides[get_registry] = lambda: registry
    received: list[str] = []
    http.event_hooks = {"request": [lambda request: received.append(request.url.path)], "response": []}
    client = openai.OpenAI(base_url="http://testserver/v1", api_key="free-tier-key", http_client=cast(Any, http))
    assert client.max_retries == 2  # the SDK default, which would otherwise retry a 504 twice

    with pytest.raises(openai.InternalServerError) as failed:
        client.chat.completions.create(model=OLLAMA_MODEL, messages=MESSAGES, max_tokens=16)

    assert failed.value.status_code == 504
    assert failed.value.code == "provider_timeout"
    assert received == ["/v1/chat/completions"]
