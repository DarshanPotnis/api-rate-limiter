"""A deterministic stand-in for a real LLM, for local development and tests."""

import anyio

from app.providers.base import Completion, CompletionRequest
from app.providers.tokens import CHARS_PER_TOKEN, estimate_prompt_tokens, estimate_tokens


class MockProvider:
    """Replies ``Mock reply to: <last user message>``, cut to ``max_tokens``, after a fixed delay.

    Usage is counted with the gateway's own estimate, so the reported prompt tokens always
    match what the gateway reserved for the prompt.
    """

    name = "mock"
    reports_real_usage = False

    def __init__(self, *, latency_seconds: float = 0.0) -> None:
        if latency_seconds < 0:
            raise ValueError(f"latency_seconds must not be negative, got {latency_seconds}")
        self._latency_seconds = latency_seconds

    async def complete(self, request: CompletionRequest) -> Completion:
        await anyio.sleep(self._latency_seconds)
        last_user_message = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        reply = f"Mock reply to: {last_user_message}"
        max_chars = request.max_tokens * CHARS_PER_TOKEN
        content = reply[:max_chars]
        return Completion(
            content=content,
            finish_reason="length" if len(reply) > max_chars else "stop",
            prompt_tokens=estimate_prompt_tokens(request.messages),
            completion_tokens=estimate_tokens(content),
        )

    async def is_available(self, model: str) -> bool:
        return True
