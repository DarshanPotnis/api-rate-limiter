"""A narrated tour of the gateway, driven by the official openai SDK.

Start the stack, then run the demo from the repository root:

    docker compose up -d --wait
    python scripts/demo.py                # or --base-url http://host:port/v1

It works with or without Ollama. Every step reports what actually happened, so a second
run within a minute, while the limits are still spent, says so instead of repeating a
script. For recordings, --pace adds a pause before each step.
"""

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import openai
from openai.types.chat import ChatCompletionMessageParam

FREE, PRO, ENTERPRISE = "free-tier-key", "pro-tier-key", "enterprise-key"
OLLAMA_MODEL = "llama3.2:3b"

STEP_TITLES = (
    "1. A normal completion",
    "2. The free tier hits its request limit",
    "3. A long document hits the token budget",
    '4. "auto" falls back when Ollama is unavailable',
    "5. The circuit breaker fails fast",
)

# About 48,000 characters: roughly 12,000 tokens at four characters per token.
DOCUMENT_TOKENS = 12_000
DOCUMENT = (
    "Rate limits protect a shared model from any single caller. They cap how many requests "
    "arrive each minute and how many tokens those requests may spend, because a single long "
    "prompt can cost more than a hundred short ones. " * 250
)[: DOCUMENT_TOKENS * 4]


@dataclass(frozen=True)
class Gateway:
    """Where the demo sends requests. Tests pass a Starlette TestClient to run in-process."""

    base_url: str
    http_client: Any = None

    def client(self, api_key: str, *, max_retries: int = 0) -> openai.OpenAI:
        return openai.OpenAI(
            base_url=self.base_url, api_key=api_key, max_retries=max_retries, http_client=self.http_client
        )


@dataclass(frozen=True)
class CompletionResult:
    reply: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    headers: dict[str, str]


@dataclass(frozen=True)
class RequestLimitResult:
    admitted: int
    error_type: str | None
    retry_after: str | None


@dataclass(frozen=True)
class TokenBudgetResult:
    admitted: int
    used_tokens: int | None
    remaining_tokens: str | None
    error_type: str | None
    retry_after: str | None


@dataclass(frozen=True)
class FallbackResult:
    model: str
    provider: str


@dataclass(frozen=True)
class BreakerAttempt:
    status: int
    milliseconds: float
    retry_after: str | None
    should_retry: str | None


@dataclass(frozen=True)
class BreakerResult:
    ollama_answered: bool
    attempts: tuple[BreakerAttempt, ...]
    opened: bool
    default_retries_milliseconds: float | None


@dataclass(frozen=True)
class DemoRun:
    completion: CompletionResult
    request_limit: RequestLimitResult
    token_budget: TokenBudgetResult
    fallback: FallbackResult
    breaker: BreakerResult


def _ask(text: str) -> list[ChatCompletionMessageParam]:
    return [{"role": "user", "content": text}]


def normal_completion(gateway: Gateway) -> CompletionResult:
    raw = gateway.client(ENTERPRISE).chat.completions.with_raw_response.create(
        model="mock", messages=_ask("Summarize rate limiting in one line."), max_tokens=32
    )
    completion = raw.parse()
    usage = completion.usage
    assert usage is not None
    return CompletionResult(
        reply=completion.choices[0].message.content or "",
        model=completion.model,
        provider=raw.headers["x-gateway-provider"],
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        headers={name: value for name, value in raw.headers.items() if name.startswith("x-ratelimit-")},
    )


def request_limit(gateway: Gateway, *, max_requests: int = 10) -> RequestLimitResult:
    client = gateway.client(FREE)
    for admitted in range(max_requests):
        try:
            client.chat.completions.create(model="mock", messages=_ask("Hello"), max_tokens=8)
        except openai.RateLimitError as exc:
            return RequestLimitResult(admitted, exc.type, exc.response.headers.get("retry-after"))
    return RequestLimitResult(max_requests, None, None)


def token_budget(gateway: Gateway) -> TokenBudgetResult:
    """Send the long document twice; the mock echoes it, so each request costs about 24,000 tokens."""
    client = gateway.client(PRO)
    used: int | None = None
    remaining: str | None = None
    for admitted in range(2):
        try:
            raw = client.chat.completions.with_raw_response.create(
                model="mock", messages=_ask(DOCUMENT), max_tokens=DOCUMENT_TOKENS
            )
        except openai.RateLimitError as exc:
            return TokenBudgetResult(admitted, used, remaining, exc.type, exc.response.headers.get("retry-after"))
        usage = raw.parse().usage
        used = usage.total_tokens if usage is not None else None
        remaining = raw.headers.get("x-ratelimit-remaining-tokens")
    return TokenBudgetResult(2, used, remaining, None, None)


def fallback(gateway: Gateway) -> FallbackResult:
    raw = gateway.client(ENTERPRISE).chat.completions.with_raw_response.create(
        model="auto", messages=_ask("Hello"), max_tokens=8
    )
    return FallbackResult(model=raw.parse().model, provider=raw.headers["x-gateway-provider"])


def breaker(gateway: Gateway, *, max_attempts: int = 6) -> BreakerResult:
    """Ask for the Ollama model directly until the gateway stops trying it."""
    client = gateway.client(ENTERPRISE)
    attempts: list[BreakerAttempt] = []
    for _ in range(max_attempts):
        started = time.perf_counter()
        try:
            client.chat.completions.create(model=OLLAMA_MODEL, messages=_ask("Hello"), max_tokens=8)
        except openai.APIStatusError as exc:
            headers = exc.response.headers
            attempts.append(
                BreakerAttempt(
                    status=exc.status_code,
                    milliseconds=(time.perf_counter() - started) * 1000,
                    retry_after=headers.get("retry-after"),
                    should_retry=headers.get("x-should-retry"),
                )
            )
            if headers.get("x-should-retry") == "false" and headers.get("retry-after") is not None:
                break
        else:
            return BreakerResult(
                ollama_answered=True, attempts=tuple(attempts), opened=False, default_retries_milliseconds=None
            )
    opened = bool(attempts) and attempts[-1].retry_after is not None
    default_retries_milliseconds = None
    if opened:
        # The same call with the SDK's default of two retries: x-should-retry stops it retrying.
        started = time.perf_counter()
        try:
            gateway.client(ENTERPRISE, max_retries=2).chat.completions.create(
                model=OLLAMA_MODEL, messages=_ask("Hello"), max_tokens=8
            )
        except openai.APIStatusError:
            default_retries_milliseconds = (time.perf_counter() - started) * 1000
    return BreakerResult(False, tuple(attempts), opened, default_retries_milliseconds)


class _Narrator:
    def __init__(self, pace: float) -> None:
        self._pace = pace
        self._color = sys.stdout.isatty()

    def step(self, title: str) -> None:
        time.sleep(self._pace)
        print(f"\n\033[1m{title}\033[0m" if self._color else f"\n{title}")

    def line(self, text: str) -> None:
        print(f"   {text}")


def run(gateway: Gateway, *, pace: float = 0.0) -> DemoRun:
    say = _Narrator(pace)

    say.step(STEP_TITLES[0])
    completion = normal_completion(gateway)
    say.line(f'enterprise key, model "mock" -> "{completion.reply}" (answered by {completion.provider})')
    say.line(f"usage: {completion.prompt_tokens} prompt + {completion.completion_tokens} completion tokens")
    for resource in ("requests", "tokens"):
        limit, remaining, reset = (
            completion.headers[f"x-ratelimit-{kind}-{resource}"] for kind in ("limit", "remaining", "reset")
        )
        say.line(f"{resource:<9} limit {limit:>7}   remaining {remaining:>7}   reset in {reset}")

    say.step(STEP_TITLES[1])
    limited = request_limit(gateway)
    if limited.error_type is None:
        say.line(f"all {limited.admitted} requests got through; the limit was not reached")
    else:
        got_through = f"requests 1-{limited.admitted} got through" if limited.admitted else "no request got through"
        refusal = f'429 (type "{limited.error_type}"), Retry-After {limited.retry_after}s'
        say.line(f"{got_through}; the next was refused: {refusal}")
        if limited.admitted == 0:
            say.line("the free tier is still spent from an earlier run; it refills within a minute")

    say.step(STEP_TITLES[2])
    say.line(f"pro key (40,000 tokens a minute): sending a document of about {DOCUMENT_TOKENS:,} tokens,")
    say.line("which the mock echoes back, so each request costs about 24,000")
    budget = token_budget(gateway)
    if budget.admitted:
        say.line(f"first request: used {budget.used_tokens:,} tokens, {int(budget.remaining_tokens or 0):,} left")
    else:
        say.line("the pro tier's budget is still spent from an earlier run")
    if budget.error_type is not None:
        say.line(
            f'next request: refused, 429 (type "{budget.error_type}"), Retry-After {budget.retry_after}s: '
            "how long the bucket needs to refill enough for it"
        )

    say.step(STEP_TITLES[3])
    answered = fallback(gateway)
    if answered.provider == "ollama":
        say.line(f'Ollama is running, so "auto" was answered by {answered.model}. Stop Ollama to see the fallback.')
    else:
        header = f"x-gateway-provider: {answered.provider}"
        say.line(f'Ollama did not answer, so "auto" was answered by {answered.model} ({header})')

    say.step(STEP_TITLES[4])
    tripped = breaker(gateway)
    if tripped.ollama_answered:
        say.line(f"{OLLAMA_MODEL} answered, so its breaker stays closed. Stop Ollama to see it open.")
    else:
        say.line(f"asking for {OLLAMA_MODEL} directly while Ollama is down:")
        for attempt in tripped.attempts:
            detail = ""
            if attempt.retry_after is not None:
                detail = f", Retry-After {attempt.retry_after}s, x-should-retry: {attempt.should_retry} (breaker open)"
            say.line(f"{attempt.status} in {attempt.milliseconds:.0f} ms{detail}")
        if tripped.default_retries_milliseconds is not None:
            elapsed = f"{tripped.default_retries_milliseconds:.0f} ms"
            say.line(
                f"the same call with the SDK's default retries returned in {elapsed} instead of waiting and retrying"
            )

    return DemoRun(completion, limited, budget, answered, tripped)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--base-url", default="http://localhost:8000/v1", help="the gateway's /v1 URL")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds to pause before each step")
    args = parser.parse_args(argv)

    gateway = Gateway(base_url=args.base_url)
    try:
        gateway.client(ENTERPRISE).models.list()
    except openai.APIConnectionError:
        print(
            f"Cannot reach the gateway at {args.base_url}. Start it with: docker compose up -d --wait",
            file=sys.stderr,
        )
        return 1
    print(f"LLM gateway demo against {args.base_url}, using the official openai SDK")
    run(gateway, pace=args.pace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
