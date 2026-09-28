"""Ollama as a provider: a local model server, run natively so it can use the Mac's GPU."""

import logging

import httpx
from pydantic import BaseModel, ValidationError

from app.providers.base import (
    Completion,
    CompletionRequest,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    Role,
)
from app.providers.tokens import estimate_prompt_tokens, estimate_tokens

log = logging.getLogger(__name__)

# Ollama's chat templates know system, user and assistant, and silently drop "developer".
_OLLAMA_ROLES: dict[Role, str] = {"system": "system", "developer": "system", "user": "user", "assistant": "assistant"}

_TAGS_TIMEOUT_SECONDS = 2.0


def create_ollama_client(base_url: str, *, connect_timeout: float, read_timeout: float) -> httpx.AsyncClient:
    """An HTTP client for Ollama with separate timeouts.

    Connecting should be near-instant on localhost. Reading covers everything before a
    non-streaming reply arrives: waiting in Ollama's queue, loading a cold model into
    memory (several seconds for a 3B model), and generating every token.
    """
    return httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(read_timeout, connect=connect_timeout))


class _Message(BaseModel):
    content: str


class _ChatResponse(BaseModel):
    message: _Message
    done_reason: str | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None


class _Tag(BaseModel):
    name: str


class _Tags(BaseModel):
    models: list[_Tag]


def _with_tag(model: str) -> str:
    """Ollama treats a model name without a tag as ``:latest``."""
    return model if ":" in model else f"{model}:latest"


class OllamaProvider:
    name = "ollama"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def complete(self, request: CompletionRequest) -> Completion:
        payload = {
            "model": request.model,
            "messages": [{"role": _OLLAMA_ROLES[m.role], "content": m.content} for m in request.messages],
            "stream": False,
            "options": {"num_predict": request.max_tokens},
        }
        try:
            response = await self._client.post("/api/chat", json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderUnavailable(f"Could not connect to Ollama: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"Ollama did not answer in time ({type(exc).__name__}).") from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"The connection to Ollama failed: {exc}") from exc

        if response.status_code == 404:
            raise ProviderUnavailable(
                f"Ollama does not have the model '{request.model}'. Pull it with `ollama pull {request.model}`."
            )
        if response.is_error:
            raise ProviderError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            body = _ChatResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise ProviderError("Ollama returned a response the gateway could not read.") from exc

        estimated = estimate_prompt_tokens(request.messages)
        log.info(
            "prompt tokens for %s: estimated=%d reported=%s",
            request.model,
            estimated,
            body.prompt_eval_count,
            extra={"estimated_prompt_tokens": estimated, "reported_prompt_tokens": body.prompt_eval_count},
        )
        content = body.message.content
        return Completion(
            content=content,
            finish_reason="length" if body.done_reason == "length" else "stop",
            prompt_tokens=estimated if body.prompt_eval_count is None else body.prompt_eval_count,
            completion_tokens=estimate_tokens(content) if body.eval_count is None else body.eval_count,
        )

    async def is_available(self, model: str) -> bool:
        try:
            response = await self._client.get("/api/tags", timeout=_TAGS_TIMEOUT_SECONDS)
            response.raise_for_status()
            tags = _Tags.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError):
            return False
        return _with_tag(model) in {_with_tag(tag.name) for tag in tags.models}
