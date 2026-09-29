"""Tests for the Ollama provider, against a fake Ollama."""

import json
import logging

import httpx
import pytest

from app.providers import CompletionRequest, Message, ProviderError, ProviderTimeout, ProviderUnavailable
from app.providers.ollama import create_ollama_client
from tests.fake_ollama import OLLAMA_MODEL, Handler, ollama_down, ollama_hangs, ollama_provider, ollama_up

pytestmark = pytest.mark.anyio


def _request(*messages: Message, max_tokens: int = 64) -> CompletionRequest:
    return CompletionRequest(model=OLLAMA_MODEL, messages=messages or (Message("user", "hi"),), max_tokens=max_tokens)


def _connect_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("connect timed out", request=request)


def _dropped_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.RemoteProtocolError("peer closed connection without sending a response", request=request)


async def test_sends_a_non_streaming_chat_with_max_tokens_as_num_predict() -> None:
    sent: list[tuple[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, json.loads(request.content)))
        return ollama_up()(request)

    await ollama_provider(handle).complete(
        _request(Message("developer", "Be brief."), Message("user", "hi"), max_tokens=7)
    )

    assert sent == [
        (
            "/api/chat",
            {
                "model": OLLAMA_MODEL,
                # Ollama's chat templates silently drop the OpenAI "developer" role.
                "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}],
                "stream": False,
                "options": {"num_predict": 7},
            },
        )
    ]


async def test_reports_ollamas_own_token_counts() -> None:
    completion = await ollama_provider(ollama_up(prompt_eval_count=37, eval_count=12)).complete(_request())

    assert completion.content == "Hello from Ollama"
    assert (completion.prompt_tokens, completion.completion_tokens) == (37, 12)
    assert completion.finish_reason == "stop"


async def test_a_reply_cut_off_at_num_predict_finishes_with_length() -> None:
    completion = await ollama_provider(ollama_up(done_reason="length")).complete(_request())

    assert completion.finish_reason == "length"


async def test_missing_counts_fall_back_to_the_estimate() -> None:
    provider = ollama_provider(ollama_up(content="12345678", prompt_eval_count=None, eval_count=None))

    completion = await provider.complete(_request(Message("user", "abcdefgh")))

    assert (completion.prompt_tokens, completion.completion_tokens) == (2, 2)


async def test_logs_the_prompt_estimate_next_to_ollamas_count(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.providers.ollama")

    await ollama_provider(ollama_up(prompt_eval_count=26)).complete(_request(Message("user", "hi")))

    [record] = [r for r in caplog.records if r.name == "app.providers.ollama"]
    assert (vars(record)["estimated_prompt_tokens"], vars(record)["reported_prompt_tokens"]) == (1, 26)
    assert "estimated=1" in record.getMessage()
    assert "reported=26" in record.getMessage()


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (ollama_down, ProviderUnavailable),
        (_connect_timeout, ProviderUnavailable),
        (ollama_hangs, ProviderTimeout),
        (lambda request: httpx.Response(404, json={"error": "model 'llama3.2:3b' not found"}), ProviderUnavailable),
        (lambda request: httpx.Response(500, json={"error": "out of memory"}), ProviderError),
        (lambda request: httpx.Response(200, text="not json"), ProviderError),
        (_dropped_connection, ProviderError),
    ],
    ids=["refused", "connect-timeout", "read-timeout", "not-pulled", "server-error", "bad-json", "dropped"],
)
async def test_failures_are_classified(handler: Handler, expected: type[ProviderError]) -> None:
    with pytest.raises(ProviderError) as failure:
        await ollama_provider(handler).complete(_request())

    assert type(failure.value) is expected


async def test_a_model_that_is_not_pulled_says_how_to_pull_it() -> None:
    provider = ollama_provider(lambda request: httpx.Response(404, json={"error": "model not found"}))

    with pytest.raises(ProviderUnavailable, match=f"ollama pull {OLLAMA_MODEL}"):
        await provider.complete(_request())


@pytest.mark.parametrize(
    ("handler", "model", "available"),
    [
        (ollama_up(), OLLAMA_MODEL, True),
        (ollama_up(models=("llama3.2:latest",)), "llama3.2", True),
        (ollama_up(models=("qwen3:4b",)), OLLAMA_MODEL, False),
        (ollama_down, OLLAMA_MODEL, False),
        (lambda request: httpx.Response(500), OLLAMA_MODEL, False),
    ],
    ids=["listed", "implicit-latest-tag", "not-pulled", "down", "server-error"],
)
async def test_availability_follows_what_ollama_reports(handler: Handler, model: str, available: bool) -> None:
    assert await ollama_provider(handler).is_available(model) is available


async def test_the_client_has_separate_connect_and_read_timeouts() -> None:
    async with create_ollama_client("http://localhost:11434", connect_timeout=2.0, read_timeout=120.0) as client:
        assert (client.timeout.connect, client.timeout.read) == (2.0, 120.0)
        assert client.base_url.host == "localhost"
