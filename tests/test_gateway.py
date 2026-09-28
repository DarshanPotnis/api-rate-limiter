"""Tests for the OpenAI-compatible gateway at /v1."""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from functools import partial
from typing import cast

import anyio
import httpx
import pytest
import redis.asyncio

from app.budgets.fixed_window import KEY_PREFIX
from app.gateway.app import gateway
from app.gateway.auth import Caller, get_caller
from app.gateway.dependencies import get_providers
from app.main import app
from app.providers import Completion, CompletionRequest, MockProvider, Provider, ProviderError
from app.tiers import Tier

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("token_window_has_room")]

TEST_TIER = Tier("test", requests_per_minute=100, tokens_per_minute=100)


@pytest.fixture
async def client(app_uses_test_redis: None) -> AsyncIterator[httpx.AsyncClient]:
    """A client for the whole app, with its lifespan (and so the gateway's Redis client) running."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


@pytest.fixture(autouse=True)
def clear_overrides() -> Iterator[None]:
    yield
    gateway.dependency_overrides.clear()


@pytest.fixture
def as_caller(key: str) -> Callable[[Tier], None]:
    """Authenticate every request as a user unique to this test, on the given tier."""

    def use(tier: Tier) -> None:
        caller = Caller(user_id=key, tier=tier)
        gateway.dependency_overrides[get_caller] = lambda: caller

    return use


def use_provider(provider: Provider) -> None:
    gateway.dependency_overrides[get_providers] = lambda: {"mock": provider}


async def tokens_used(client: redis.asyncio.Redis, user_id: str) -> int:
    used = await cast(Awaitable[str | None], client.hget(f"{KEY_PREFIX}:{user_id}", "used"))
    return int(used or 0)


def chat(content: str = "hi", **fields: object) -> dict[str, object]:
    return {"model": "mock", "messages": [{"role": "user", "content": content}], **fields}


def bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def completions(
    client: httpx.AsyncClient, body: Mapping[str, object], headers: Mapping[str, str] | None = None
) -> httpx.Response:
    return await client.post("/v1/chat/completions", json=body, headers=headers)


@pytest.mark.parametrize(
    ("api_key", "requests_per_minute", "tokens_per_minute"),
    [("free-tier-key", 5, 2_000), ("pro-tier-key", 60, 40_000), ("enterprise-key", 600, 400_000)],
)
async def test_each_tier_gets_its_own_request_and_token_limits(
    client: httpx.AsyncClient, api_key: str, requests_per_minute: int, tokens_per_minute: int
) -> None:
    response = await completions(client, chat(max_tokens=16), headers=bearer(api_key))

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit-requests"] == str(requests_per_minute)
    assert response.headers["x-ratelimit-remaining-requests"] == str(requests_per_minute - 1)
    assert response.headers["x-ratelimit-limit-tokens"] == str(tokens_per_minute)


async def test_free_tier_hits_its_request_limit_while_pro_does_not(client: httpx.AsyncClient) -> None:
    free = [await completions(client, chat(max_tokens=16), headers=bearer("free-tier-key")) for _ in range(6)]
    pro = [await completions(client, chat(max_tokens=16), headers=bearer("pro-tier-key")) for _ in range(6)]

    assert [r.status_code for r in free] == [200] * 5 + [429]
    assert [r.status_code for r in pro] == [200] * 6
    limited = free[-1]
    assert limited.json()["error"]["type"] == "requests"
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert limited.headers["x-ratelimit-remaining-requests"] == "0"
    assert 1 <= int(limited.headers["retry-after"]) <= 60


async def test_successful_response_has_the_openai_shape(
    client: httpx.AsyncClient, as_caller: Callable[[Tier], None]
) -> None:
    as_caller(TEST_TIER)

    response = await completions(client, chat("Hello there", max_tokens=16))

    body = response.json()
    assert response.status_code == 200
    assert body["id"].startswith("chatcmpl-")
    assert body["object"] == "chat.completion"
    assert body["model"] == "mock"
    assert body["choices"] == [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Mock reply to: Hello there"},
            "finish_reason": "stop",
            "logprobs": None,
        }
    ]
    assert body["usage"] == {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}
    for name in ("requests", "tokens"):
        for header in ("limit", "remaining", "reset"):
            assert f"x-ratelimit-{header}-{name}" in response.headers


async def test_an_exhausted_token_budget_returns_429(
    client: httpx.AsyncClient, as_caller: Callable[[Tier], None]
) -> None:
    as_caller(TEST_TIER)
    body = chat("x" * 200, max_tokens=2)  # reserves 50 + 2 = 52 tokens and uses all of them

    first = await completions(client, body)
    second = await completions(client, body)

    assert first.status_code == 200
    assert first.headers["x-ratelimit-remaining-tokens"] == "48"
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "tokens"
    assert second.json()["error"]["code"] == "rate_limit_exceeded"
    assert second.headers["x-ratelimit-remaining-tokens"] == "48"
    assert 1 <= int(second.headers["retry-after"]) <= 60


async def test_settling_frees_the_tokens_a_request_did_not_use(
    client: httpx.AsyncClient, as_caller: Callable[[Tier], None]
) -> None:
    as_caller(TEST_TIER)
    body = chat("hi", max_tokens=80)  # reserves 1 + 80 = 81 tokens but uses 1 + 5

    first = await completions(client, body)
    second = await completions(client, body)

    assert first.headers["x-ratelimit-remaining-tokens"] == "94"
    assert second.status_code == 200  # without settling, 81 + 81 would exceed 100


async def test_a_provider_failure_releases_the_reservation(
    client: httpx.AsyncClient,
    as_caller: Callable[[Tier], None],
    async_redis_db: redis.asyncio.Redis,
    key: str,
) -> None:
    class FailingProvider:
        async def complete(self, request: CompletionRequest) -> Completion:
            raise ProviderError("backend unavailable")

    as_caller(TEST_TIER)
    use_provider(FailingProvider())

    response = await completions(client, chat(max_tokens=50))

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "server_error"
    assert await tokens_used(async_redis_db, key) == 0


async def test_a_cancelled_request_releases_the_reservation(
    client: httpx.AsyncClient,
    as_caller: Callable[[Tier], None],
    async_redis_db: redis.asyncio.Redis,
    key: str,
) -> None:
    started = anyio.Event()

    class SlowProvider:
        async def complete(self, request: CompletionRequest) -> Completion:
            started.set()
            await anyio.sleep(30)
            raise AssertionError("the request should have been cancelled")

    as_caller(TEST_TIER)
    use_provider(SlowProvider())

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(partial(completions, client, chat(max_tokens=50)))
        await started.wait()
        assert await tokens_used(async_redis_db, key) == 51
        # Close the gateway's idle Redis connections so the release has to open a new one
        # first: a real wait that the cancellation would interrupt if it were not shielded.
        await gateway.state.resources.redis.connection_pool.disconnect()
        tasks.cancel_scope.cancel()

    assert await tokens_used(async_redis_db, key) == 0


async def test_a_request_larger_than_the_whole_budget_returns_400_without_using_quota(
    client: httpx.AsyncClient, as_caller: Callable[[Tier], None]
) -> None:
    as_caller(TEST_TIER)

    oversized = await completions(client, chat(max_tokens=100))  # 1 + 100 > 100 tokens per minute
    follow_up = await completions(client, chat(max_tokens=10))

    assert oversized.status_code == 400
    assert oversized.json()["error"]["type"] == "invalid_request_error"
    assert oversized.json()["error"]["code"] == "request_too_large"
    assert follow_up.headers["x-ratelimit-remaining-requests"] == "99"
    assert follow_up.headers["x-ratelimit-remaining-tokens"] == "94"


async def test_concurrent_requests_never_push_usage_past_the_limit(
    client: httpx.AsyncClient,
    as_caller: Callable[[Tier], None],
    async_redis_db: redis.asyncio.Redis,
    key: str,
) -> None:
    as_caller(TEST_TIER)
    use_provider(MockProvider(latency_seconds=0.05))
    body = chat("x" * 32, max_tokens=2)  # reserves and uses exactly 8 + 2 = 10 tokens
    statuses: list[int] = []

    async def send() -> None:
        statuses.append((await completions(client, body)).status_code)

    async with anyio.create_task_group() as tasks:
        for _ in range(20):
            tasks.start_soon(send)

    assert sorted(statuses) == [200] * 10 + [429] * 10
    assert await tokens_used(async_redis_db, key) == 100


@pytest.mark.parametrize(
    ("fields", "status_code"),
    [
        ({}, 400),  # 1 + the 256-token default exceeds 200
        ({"max_tokens": 150}, 200),
        ({"max_tokens": 150, "max_completion_tokens": 250}, 400),
        ({"max_tokens": 250, "max_completion_tokens": 150}, 200),
    ],
)
async def test_max_completion_tokens_wins_and_256_is_the_default(
    client: httpx.AsyncClient, as_caller: Callable[[Tier], None], fields: dict[str, int], status_code: int
) -> None:
    as_caller(Tier("test", requests_per_minute=100, tokens_per_minute=200))

    response = await completions(client, {**chat(), **fields})

    assert response.status_code == status_code


async def test_an_unknown_model_returns_404(client: httpx.AsyncClient, as_caller: Callable[[Tier], None]) -> None:
    as_caller(TEST_TIER)

    response = await completions(client, {**chat(), "model": "gpt-4o"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"
    assert response.json()["error"]["param"] == "model"


async def test_models_lists_the_mock_model(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/models", headers=bearer("free-tier-key"))

    assert response.status_code == 200
    assert response.json()["object"] == "list"
    assert [(m["id"], m["object"]) for m in response.json()["data"]] == [("mock", "model")]


async def test_streaming_is_rejected(client: httpx.AsyncClient, as_caller: Callable[[Tier], None]) -> None:
    as_caller(TEST_TIER)

    response = await completions(client, chat(stream=True))

    assert response.status_code == 400
    assert response.json()["error"]["param"] == "stream"


async def test_an_invalid_body_returns_an_openai_style_400(
    client: httpx.AsyncClient, as_caller: Callable[[Tier], None]
) -> None:
    as_caller(TEST_TIER)

    response = await completions(client, {"model": "mock"})

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert response.json()["error"]["param"] == "messages"


@pytest.mark.parametrize("headers", [{}, bearer("not-a-real-key")])
async def test_a_missing_or_wrong_api_key_returns_401(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    response = await completions(client, chat(), headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


async def test_unknown_gateway_paths_use_the_openai_error_format(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "invalid_request_error"
