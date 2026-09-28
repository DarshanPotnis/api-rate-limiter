"""The official openai SDK, unmodified, used as the client to prove wire compatibility."""

from collections.abc import Iterator

import openai
import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.usefixtures("token_window_has_room")

MESSAGES = [{"role": "user", "content": "Hello there"}]


@pytest.fixture
def http(app_uses_test_redis: None) -> Iterator[TestClient]:
    with TestClient(app) as http:  # entering runs the lifespan, which opens the gateway's Redis client
        yield http


def sdk(http: TestClient, api_key: str = "free-tier-key") -> openai.OpenAI:
    return openai.OpenAI(base_url="http://testserver/v1", api_key=api_key, http_client=http, max_retries=0)


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
    assert [model.id for model in sdk(http).models.list()] == ["mock"]


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
