"""The gateway's FastAPI app: POST /chat/completions and GET /models, mounted at /v1.

Per request: authenticate, size the request (prompt estimate + max tokens), apply the
tier's request limit, reserve tokens, call the provider, then settle the reservation
against actual usage. If the provider fails or the request is cancelled, the
reservation is released instead.
"""

import secrets
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import anyio
import redis.asyncio
from fastapi import Depends, FastAPI, Response

from app.budgets import FixedWindowTokenBudget, Reservation, TokenBudget
from app.config import get_settings
from app.gateway.auth import Caller, get_caller
from app.gateway.dependencies import (
    GatewayResources,
    get_providers,
    get_request_limiter,
    get_resources,
    get_token_budget,
)
from app.gateway.errors import OpenAIError, install_error_handlers
from app.gateway.headers import request_limit_headers, token_limit_headers
from app.gateway.schemas import (
    AssistantMessage,
    ChatCompletion,
    ChatCompletionRequest,
    Choice,
    Model,
    ModelList,
    Usage,
)
from app.limiters import AsyncRateLimiter
from app.providers import Completion, CompletionRequest, Message, MockProvider, Provider, ProviderError
from app.providers.tokens import estimate_prompt_tokens
from app.tiers import LIMIT_WINDOW_SECONDS


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    client = redis.asyncio.Redis.from_url(str(settings.redis_url), decode_responses=True)
    app.state.resources = GatewayResources(
        redis=client,
        token_budget=FixedWindowTokenBudget(client, window_seconds=LIMIT_WINDOW_SECONDS),
        providers={"mock": MockProvider(latency_seconds=settings.mock_latency_seconds)},
        started_at=int(time.time()),
    )
    try:
        yield
    finally:
        await client.aclose()


gateway = FastAPI(title="LLM Gateway", lifespan=lifespan)
install_error_handlers(gateway)


@gateway.get("/models", dependencies=[Depends(get_caller)])
async def list_models(
    providers: Mapping[str, Provider] = Depends(get_providers),
    resources: GatewayResources = Depends(get_resources),
) -> ModelList:
    return ModelList(data=[Model(id=name, created=resources.started_at, owned_by="llm-gateway") for name in providers])


@gateway.post("/chat/completions")
async def create_chat_completion(
    body: ChatCompletionRequest,
    response: Response,
    caller: Caller = Depends(get_caller),
    providers: Mapping[str, Provider] = Depends(get_providers),
    limiter: AsyncRateLimiter = Depends(get_request_limiter),
    budget: TokenBudget = Depends(get_token_budget),
) -> ChatCompletion:
    if body.stream:
        raise OpenAIError(400, "Streaming is not supported yet; send stream: false.", param="stream")
    provider = providers.get(body.model)
    if provider is None:
        raise OpenAIError(404, f"The model '{body.model}' does not exist.", code="model_not_found", param="model")

    request = CompletionRequest(
        model=body.model,
        messages=tuple(Message(role=m.role, content=m.content) for m in body.messages),
        max_tokens=_max_tokens(body),
    )
    tier = caller.tier
    cost = estimate_prompt_tokens(request.messages) + request.max_tokens
    if cost > tier.tokens_per_minute:
        raise OpenAIError(
            400,
            f"Request too large for the {tier.name} tier: it may use up to {cost} tokens (prompt estimate "
            f"plus max tokens), but the tier allows {tier.tokens_per_minute} tokens per minute. "
            "Shorten the messages or lower max_tokens.",
            code="request_too_large",
        )

    decision = await limiter.hit(f"{caller.user_id}:chat_completions")
    headers = request_limit_headers(decision)
    if not decision.allowed:
        raise OpenAIError(
            429,
            f"Rate limit reached for requests per minute on the {tier.name} tier: limit {decision.limit}. "
            f"Try again in {decision.retry_after_seconds}s.",
            error_type="requests",
            code="rate_limit_exceeded",
            headers={
                **headers,
                "x-ratelimit-limit-tokens": str(tier.tokens_per_minute),
                "retry-after": str(decision.retry_after_seconds),
            },
        )

    reservation = await budget.reserve(caller.user_id, cost, limit=tier.tokens_per_minute)
    if not reservation.allowed:
        state = reservation.state
        raise OpenAIError(
            429,
            f"Rate limit reached for tokens per minute on the {tier.name} tier: limit {state.limit}, "
            f"remaining {state.remaining}, requested {cost}. Try again in {state.retry_after_seconds}s.",
            error_type="tokens",
            code="rate_limit_exceeded",
            headers={**headers, **token_limit_headers(state), "retry-after": str(state.retry_after_seconds)},
        )

    completion = await _complete_or_release(provider, request, budget, reservation)
    # Shielded like the release: once the provider has answered, its real usage must be
    # recorded even if this request is being cancelled.
    with anyio.CancelScope(shield=True):
        state = await budget.settle(reservation, completion.total_tokens)

    response.headers.update({**headers, **token_limit_headers(state)})
    return ChatCompletion(
        id=f"chatcmpl-{secrets.token_hex(12)}",
        created=int(time.time()),
        model=request.model,
        choices=[
            Choice(
                index=0,
                message=AssistantMessage(content=completion.content),
                finish_reason=completion.finish_reason,
            )
        ],
        usage=Usage(
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
        ),
    )


def _max_tokens(body: ChatCompletionRequest) -> int:
    """max_completion_tokens (OpenAI's newer name) wins over max_tokens; the setting applies if neither is sent."""
    if body.max_completion_tokens is not None:
        return body.max_completion_tokens
    if body.max_tokens is not None:
        return body.max_tokens
    return get_settings().default_max_tokens


async def _complete_or_release(
    provider: Provider, request: CompletionRequest, budget: TokenBudget, reservation: Reservation
) -> Completion:
    """Call the provider. If it fails, or this request is cancelled, give the reserved tokens back."""
    completed = False
    try:
        completion = await provider.complete(request)
        completed = True
        return completion
    except ProviderError as exc:
        raise OpenAIError(
            502, "The model provider failed to complete the request.", error_type="server_error", code="provider_error"
        ) from exc
    finally:
        if not completed:
            # Shielded so the release still reaches Redis while the task is being cancelled.
            with anyio.CancelScope(shield=True):
                await budget.release(reservation)
