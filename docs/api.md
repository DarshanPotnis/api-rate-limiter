# API reference

## OpenAI-compatible gateway (`/v1`)

The gateway speaks the OpenAI chat completions API, so an OpenAI client works by changing
only its base URL and key. Interactive docs are at `/v1/docs`. Authenticate with
`Authorization: Bearer <key>`, which is how OpenAI clients send keys (see
[configuration.md](configuration.md#tiers-and-api-keys) for the keys).

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/v1/chat/completions` | `model`, `messages`, and `max_tokens` or `max_completion_tokens` (the latter wins). Non-streaming only. |
| `GET` | `/v1/models` | `mock`, `auto`, and the Ollama model while Ollama reports it as pulled |

### Models

| Model | Answered by |
|-------|-------------|
| `mock` | A deterministic stand-in that replies `Mock reply to: <your last message>` |
| `llama3.2:3b` (`OLLAMA_MODEL`) | Ollama |
| `auto` | The first model in `AUTO_FALLBACK` that answers, skipping any whose circuit breaker is open: Ollama, then mock by default |

The response's `model` field and an `x-gateway-provider` header (`ollama` or `mock`) always
name what actually answered.

### Example

```bash
curl -i http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer free-tier-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "mock", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 64}'
```

### How a request is limited

1. **Size it**: the prompt estimate plus `max_completion_tokens`, else `max_tokens`, else `DEFAULT_MAX_TOKENS`. For a model that reports real token counts (Ollama), the estimate is characters ÷ 4 + the model's learned base overhead + `PER_MESSAGE_TOKENS` for each message after the first; `auto` reserves for its most expensive model. Mock keeps the exact characters ÷ 4.
2. **Reject what can never fit**: if that is more than the tier's tokens per minute, return `400 request_too_large` without using any quota.
3. **Request limit**: the tier's requests per minute, enforced by a sliding log in a Lua script. Over it: `429` with `type: requests`.
4. **Reserve tokens**: the caller's token bucket holds up to the tier's tokens per minute and refills continuously, computed in Lua from the Redis clock. The reservation is taken only if the balance covers it. Otherwise: `429` with `type: tokens` and `Retry-After` set to when the balance will cover this request.
5. **Route it**: a named model is tried alone; `auto` tries its models in order until one answers. If none answers, or the request is cancelled, the whole reservation is released, in a cancel scope shielded so the release completes even while the request is being cancelled.
6. **Settle and learn**: the reservation is replaced by the usage of the model that answered (for Ollama, its own `prompt_eval_count` and `eval_count`). Unused tokens are refunded, never past a full bucket; extra tokens are charged and can leave a debt that refill pays back. Ollama's prompt count then updates that model's learned overhead.

### Rate-limit headers

Successful responses carry OpenAI-style headers. As with OpenAI, reset values are durations,
not timestamps:

```http
x-ratelimit-limit-requests: 60
x-ratelimit-remaining-requests: 59
x-ratelimit-reset-requests: 1m0s
x-ratelimit-limit-tokens: 40000
x-ratelimit-remaining-tokens: 39965
x-ratelimit-reset-tokens: 53ms
x-gateway-provider: ollama
```

Remaining tokens are measured after settlement, and `x-ratelimit-reset-tokens` is the time
until the bucket is full again. Every `429` includes `Retry-After` in seconds. A
request-limit `429` leaves out the remaining and reset token headers, because the token
budget is not checked for that request.

### Errors

Errors use OpenAI's shape, `{"error": {"message", "type", "param", "code"}}`, so OpenAI SDKs
raise their usual exceptions.

| Status | `type` | `code` | When |
|--------|--------|--------|------|
| 400 | `invalid_request_error` | (`param` names the field) | Invalid body, or `stream: true` |
| 400 | `invalid_request_error` | `request_too_large` | The request could never fit the tier's token budget |
| 401 | `invalid_request_error` | `invalid_api_key` | Missing or unknown key |
| 404 | `invalid_request_error` | `model_not_found` | A model name the gateway does not serve |
| 429 | `requests` or `tokens` | `rate_limit_exceeded` | Request or token limit reached |
| 500 | `server_error` | | An unexpected error inside the gateway |
| 502 | `server_error` | `provider_error` | The named model answered with an error or an unreadable reply |
| 502 | `server_error` | `all_providers_failed` | Every model behind `auto` failed or was skipped; the message lists each attempt |
| 503 | `server_error` | `model_unavailable` | The named model is down, not pulled in Ollama, or its circuit breaker is open |
| 504 | `server_error` | `provider_timeout` | The named model did not answer within the read timeout |

A `503` means the backend is temporarily unavailable, which is true of a stopped Ollama;
`502` is kept for a backend that answered with something broken, so clients and operators
can tell the two apart.

**No retries after a timeout or into an open breaker.** OpenAI SDKs retry 5xx responses
twice by default. With a 120-second read timeout, retrying a hung model could hold a client
for six minutes, so any response caused by a timeout carries `x-should-retry: false`, which
the SDKs obey. So does any response involving an open breaker, which the SDKs would
otherwise answer by waiting out `Retry-After` and retrying into the same `503`.
`Retry-After` still says when the breaker lets its next trial request through.

**Why oversized requests get 400, not 429.** OpenAI answers a request that exceeds the
entire per-minute token limit with `429`. This gateway deliberately returns `400` instead:
a `429` tells the client to retry later, and the OpenAI SDKs retry `429`s automatically, but
this request can never succeed however long the client waits.

## Console and status

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/` | The gateway console: a playground, live limits, bursts, model health and a request log |
| `GET` | `/status` | Read-only routing state, polled by the console; no API key needed |
| `GET` | `/health` | `{"status": "ok"}` |

The console is plain HTML, CSS and JavaScript served by the gateway, with no build step and
no external resources. It is sent with a Content-Security-Policy that allows only its own
origin, so it works offline, and it writes everything from the server with `textContent`.
It calls the `/v1` API with the demo keys, reads its limits from the `x-ratelimit-*`
headers, and refreshes model health from `/status` every two seconds.

`GET /status` reports the fallback strategy, the token-budget kind, and each configured
model's circuit breaker. It reads in-memory state only and never calls a model, so polling
it is cheap and cannot trip anything.

```json
{
  "fallback_strategy": "circuit_breaker",
  "token_budget": "token_bucket",
  "models": [
    {"id": "mock", "provider": "mock", "breaker": "closed", "consecutive_failures": 0, "cooldown_remaining_seconds": 0.0},
    {"id": "llama3.2:3b", "provider": "ollama", "breaker": "open", "consecutive_failures": 3, "cooldown_remaining_seconds": 21.4}
  ]
}
```

`breaker` is `closed`, `open` (skipped until the cooldown ends) or `half_open` (the cooldown
has passed and the next request is the trial). With `FALLBACK_STRATEGY=sequential` there are
no breakers, and `breaker` and `consecutive_failures` are `null`.

The v1 rate-limited endpoint `/protected`, its `X-API-KEY` header and the original
dashboard were retired when the console replaced them; the `v1-original` tag keeps that
version.
