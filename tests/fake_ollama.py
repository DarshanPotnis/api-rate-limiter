"""A fake Ollama for tests, built on httpx.MockTransport: no network and no extra dependency.

Response bodies mirror what Ollama 0.33 returns from /api/chat and /api/tags.
"""

from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import anyio
import httpx

from app.providers.ollama import OllamaProvider

SyncHandler = Callable[[httpx.Request], httpx.Response]
Handler = SyncHandler | Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]

OLLAMA_MODEL = "llama3.2:3b"


def ollama_provider(handler: Handler) -> OllamaProvider:
    return OllamaProvider(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.test"))


def ollama_up(
    *,
    content: str = "Hello from Ollama",
    prompt_eval_count: int | None = 37,
    eval_count: int | None = 12,
    done_reason: str = "stop",
    models: Sequence[str] = (OLLAMA_MODEL,),
) -> Handler:
    """An Ollama that answers every chat with ``content`` and reports the given token counts."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": name, "model": name} for name in models]})
        body: dict[str, object] = {
            "model": OLLAMA_MODEL,
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": done_reason,
        }
        if prompt_eval_count is not None:
            body["prompt_eval_count"] = prompt_eval_count
        if eval_count is not None:
            body["eval_count"] = eval_count
        return httpx.Response(200, json=body)

    return handle


def ollama_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[Errno 61] Connection refused", request=request)


def ollama_hangs(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def ollama_hangs_for(seconds: float, calls: list[str]) -> Handler:
    """An Ollama that holds each chat for ``seconds`` and then times out, the way a hung server
    runs into the gateway's read timeout. Records each call in ``calls``."""

    async def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        await anyio.sleep(seconds)
        raise httpx.ReadTimeout("timed out", request=request)

    return handle


def counted(handler: SyncHandler, calls: list[str]) -> SyncHandler:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)

    return handle
