"""The gateway's FastAPI app: POST /chat/completions and GET /models, mounted at /v1.

Per request: authenticate, resolve the model to its targets, size the request (prompt
estimate + max tokens), apply the tier's request limit, reserve tokens, try the targets
in order, then settle the reservation against the usage of whichever model answered.
If none answers or the request is cancelled, the reservation is released instead.
"""

import math
import secrets
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import anyio
import redis.asyncio
from fastapi import Depends, FastAPI, Response

from app.budgets import FixedWindowTokenBudget, Reservation, TokenBucketBudget, TokenBudget
from app.config import Settings, get_settings
from app.estimates import CalibratedEstimator, CharacterEstimator, PromptEstimator
from app.gateway.auth import Caller, get_caller
from app.gateway.dependencies import (
    GatewayResources,
    get_estimator,
    get_fallback,
    get_registry,
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
from app.providers import CompletionRequest, Message, MockProvider, ProviderTimeout, ProviderUnavailable
from app.providers.ollama import OllamaProvider, create_ollama_client
from app.routing import (
    AllTargetsFailed,
    Answer,
    Attempt,
    CircuitBreakerFallback,
    CircuitOpen,
    FallbackStrategy,
    ModelRegistry,
    SequentialFallback,
    Target,
)
from app.tiers import LIMIT_WINDOW_SECONDS

AUTO_ALIAS = "auto"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    async with (
        redis.asyncio.Redis.from_url(str(settings.redis_url), decode_responses=True) as redis_client,
        create_ollama_client(
            str(settings.ollama_base_url),
            connect_timeout=settings.ollama_connect_timeout_seconds,
            read_timeout=settings.ollama_read_timeout_seconds,
        ) as ollama_client,
    ):
        registry = ModelRegistry(
            models={
                "mock": MockProvider(latency_seconds=settings.mock_latency_seconds),
                settings.ollama_model: OllamaProvider(ollama_client),
            },
            aliases={AUTO_ALIAS: settings.auto_chain},
        )
        app.state.resources = GatewayResources(
            redis=redis_client,
            token_budget=_token_budget(settings, redis_client),
            estimator=_estimator(settings, redis_client),
            registry=registry,
            fallback=_fallback(settings),
            started_at=int(time.time()),
        )
        yield


def _token_budget(settings: Settings, client: redis.asyncio.Redis) -> TokenBudget:
    if settings.token_budget == "fixed_window":
        return FixedWindowTokenBudget(client, window_seconds=LIMIT_WINDOW_SECONDS)
    return TokenBucketBudget(client, refill_seconds=LIMIT_WINDOW_SECONDS)


def _estimator(settings: Settings, client: redis.asyncio.Redis) -> PromptEstimator:
    if settings.prompt_estimate == "characters":
        return CharacterEstimator()
    return CalibratedEstimator(
        client, default_overhead=settings.prompt_overhead_default, per_message_tokens=settings.per_message_tokens
    )


def _fallback(settings: Settings) -> FallbackStrategy:
    if settings.fallback_strategy == "sequential":
        return SequentialFallback()
    return CircuitBreakerFallback(
        failure_threshold=settings.breaker_failure_threshold, cooldown_seconds=settings.breaker_cooldown_seconds
    )


gateway = FastAPI(title="LLM Gateway", lifespan=lifespan)
install_error_handlers(gateway)


@gateway.get("/models", dependencies=[Depends(get_caller)])
async def list_models(
    registry: ModelRegistry = Depends(get_registry),
    resources: GatewayResources = Depends(get_resources),
) -> ModelList:
    listed = await registry.available_models()
    return ModelList(data=[Model(id=m.id, created=resources.started_at, owned_by=m.owned_by) for m in listed])


@gateway.post("/chat/completions")
async def create_chat_completion(
    body: ChatCompletionRequest,
    response: Response,
    caller: Caller = Depends(get_caller),
    registry: ModelRegistry = Depends(get_registry),
    fallback: FallbackStrategy = Depends(get_fallback),
    estimator: PromptEstimator = Depends(get_estimator),
    limiter: AsyncRateLimiter = Depends(get_request_limiter),
    budget: TokenBudget = Depends(get_token_budget),
) -> ChatCompletion:
    if body.stream:
        raise OpenAIError(400, "Streaming is not supported yet; send stream: false.", param="stream")
    targets = registry.resolve(body.model)
    if targets is None:
        raise OpenAIError(404, f"The model '{body.model}' does not exist.", code="model_not_found", param="model")

    request = CompletionRequest(
        model=body.model,
        messages=tuple(Message(role=m.role, content=m.content) for m in body.messages),
        max_tokens=_max_tokens(body),
    )
    tier = caller.tier
    cost = await estimator.estimate(targets, request.messages) + request.max_tokens
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
            f"remaining {state.remaining}, requested {cost}. Try again in {reservation.retry_after_seconds}s.",
            error_type="tokens",
            code="rate_limit_exceeded",
            headers={**headers, **token_limit_headers(state), "retry-after": str(reservation.retry_after_seconds)},
        )

    answer = await _answer_or_release(
        fallback, targets, request, budget, reservation, alias=registry.is_alias(body.model)
    )
    completion = answer.completion
    # Shielded like the release: once a model has answered, its real usage must be
    # recorded even if this request is being cancelled.
    with anyio.CancelScope(shield=True):
        state = await budget.settle(reservation, completion.total_tokens)
    await estimator.observe(answer.target, request.messages, completion.prompt_tokens)

    response.headers.update(
        {**headers, **token_limit_headers(state), "x-gateway-provider": answer.target.provider.name}
    )
    return ChatCompletion(
        id=f"chatcmpl-{secrets.token_hex(12)}",
        created=int(time.time()),
        model=answer.target.model,
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


async def _answer_or_release(
    fallback: FallbackStrategy,
    targets: Sequence[Target],
    request: CompletionRequest,
    budget: TokenBudget,
    reservation: Reservation,
    *,
    alias: bool,
) -> Answer:
    """Route the request. If no target answers, or this request is cancelled, give the reserved tokens back."""
    answered = False
    try:
        answer = await fallback.complete(targets, request)
        answered = True
        return answer
    except AllTargetsFailed as exc:
        raise _routing_error(request.model, exc.attempts, alias=alias) from exc
    finally:
        if not answered:
            # Shielded so the release still reaches Redis while the task is being cancelled.
            with anyio.CancelScope(shield=True):
                await budget.release(reservation)


def _routing_error(model: str, attempts: Sequence[Attempt], *, alias: bool) -> OpenAIError:
    """503 for a model that is down or whose breaker is open, 504 for one that timed out,
    502 when it answered badly or when every model behind an alias failed."""
    headers: dict[str, str] = {}
    open_circuits = [a.error for a in attempts if isinstance(a.error, CircuitOpen)]
    # OpenAI SDKs retry 5xx twice by default. A model that timed out will probably time out
    # again, and an open breaker would only answer the retry with the same 503, so tell them
    # not to; Retry-After says when the earliest breaker lets a trial through.
    if open_circuits or any(isinstance(a.error, ProviderTimeout) for a in attempts):
        headers["x-should-retry"] = "false"
    if open_circuits:
        headers["retry-after"] = str(max(1, math.ceil(min(e.retry_after_seconds for e in open_circuits))))
    if alias:
        tried = "; ".join(f"{a.target.model} ({a.target.provider.name}): {a.error}" for a in attempts)
        return OpenAIError(
            502,
            f"Every model behind '{model}' failed. {tried}",
            error_type="server_error",
            code="all_providers_failed",
            headers=headers,
        )
    [attempt] = attempts
    if isinstance(attempt.error, ProviderTimeout):
        return OpenAIError(
            504,
            f"The model '{model}' did not answer in time.",
            error_type="server_error",
            code="provider_timeout",
            headers=headers,
        )
    if isinstance(attempt.error, ProviderUnavailable):
        return OpenAIError(
            503,
            f"The model '{model}' is unavailable: {attempt.error}",
            error_type="server_error",
            code="model_unavailable",
            headers=headers,
        )
    return OpenAIError(
        502, "The model provider failed to complete the request.", error_type="server_error", code="provider_error"
    )
