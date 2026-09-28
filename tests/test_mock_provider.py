"""Tests for the token estimate and the mock LLM provider."""

import anyio
import pytest

from app.providers import CompletionRequest, Message, MockProvider
from app.providers.tokens import estimate_prompt_tokens, estimate_tokens


def _request(content: str = "Hello there", *, max_tokens: int = 100) -> CompletionRequest:
    return CompletionRequest(
        model="mock",
        messages=(Message(role="system", content="Be brief."), Message(role="user", content=content)),
        max_tokens=max_tokens,
    )


@pytest.mark.parametrize(("text", "tokens"), [("", 0), ("abcd", 1), ("abcde", 2), ("x" * 400, 100)])
def test_estimate_is_characters_divided_by_four_rounded_up(text: str, tokens: int) -> None:
    assert estimate_tokens(text) == tokens


def test_prompt_estimate_counts_all_message_contents_together() -> None:
    # "Be brief." (9) + "Hello there" (11) = 20 characters.
    assert estimate_prompt_tokens(_request().messages) == 5


@pytest.mark.anyio
async def test_replies_deterministically_to_the_last_user_message() -> None:
    provider = MockProvider()

    first = await provider.complete(_request())
    second = await provider.complete(_request())

    assert first == second
    assert first.content == "Mock reply to: Hello there"
    assert first.finish_reason == "stop"


@pytest.mark.anyio
async def test_reports_usage_with_the_same_estimate() -> None:
    completion = await MockProvider().complete(_request())

    # 20 prompt characters -> 5 tokens; the 26-character reply -> 7 tokens.
    assert (completion.prompt_tokens, completion.completion_tokens, completion.total_tokens) == (5, 7, 12)


@pytest.mark.anyio
async def test_cuts_the_reply_at_max_tokens() -> None:
    completion = await MockProvider().complete(_request(max_tokens=2))

    assert completion.content == "Mock rep"
    assert completion.finish_reason == "length"
    assert completion.completion_tokens == 2


@pytest.mark.anyio
async def test_waits_for_the_configured_latency() -> None:
    started = anyio.current_time()

    await MockProvider(latency_seconds=0.2).complete(_request())

    assert anyio.current_time() - started >= 0.2


def test_rejects_negative_latency() -> None:
    with pytest.raises(ValueError):
        MockProvider(latency_seconds=-0.1)
