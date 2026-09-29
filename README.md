# API Rate Limiter & Gateway (Redis-Backed)

A production-style **API rate limiting gateway** built with **FastAPI and Redis**, featuring API key authentication, industry-standard rate-limit headers, and an interactive web dashboard to visualize request limits in real time.

It also serves an **OpenAI-compatible LLM gateway** at `/v1` that enforces per-tier request and token budgets, so existing OpenAI clients work against it by changing only the base URL and key.

---

## Features

- 🔐 **API Key Authentication**
- ⏱ **Redis-backed Rate Limiting**
- 📊 **Industry-Standard Headers**
  - `X-RateLimit-Limit`
  - `X-RateLimit-Remaining`
  - `X-RateLimit-Reset`
- 🖥 **Interactive Web Dashboard**
- ⏳ **Live Reset Countdown (Frontend)**
- 🎚 **Multiple API Tiers Simulation**
  - Free Tier
  - Pro Tier
  - Enterprise Tier
- 🤖 **OpenAI-compatible LLM Gateway** (`/v1/chat/completions`, `/v1/models`)
  - Per-tier requests-per-minute and tokens-per-minute limits
  - Token reservations settled against actual usage, in a continuously refilling token bucket
  - Prompt estimates calibrated per model from real token counts
  - OpenAI-style `x-ratelimit-*` headers and error format
  - A local model through Ollama, with an `auto` alias that falls back to the mock model
  - A circuit breaker that stops waiting on a backend that keeps failing
- ⚡ **FastAPI + Uvicorn**
- 🧱 Clean, modular backend architecture

---

## Why This Project Matters

APIs in real-world systems **must** protect against:
- Abuse
- Traffic spikes
- DDoS-style overuse
- Costly backend overload

This project demonstrates **how production systems enforce request limits**, similar to platforms like:
- Stripe
- GitHub
- AWS API Gateway
- Cloudflare

---

## 🏗 Tech Stack

| Layer        | Technology |
|-------------|------------|
| Backend     | FastAPI |
| Rate Limit  | Redis |
| Auth        | API Key Headers / Bearer tokens |
| LLM Providers | Ollama (native, GPU-accelerated) and a deterministic mock |
| Frontend   | HTML, CSS, Vanilla JS |
| Server     | Uvicorn |
| Language   | Python 3.10+ |

---

## 📂 Project Structure

```
api-rate-limiter/
│
├── app/
│   ├── main.py            # FastAPI app & routes; mounts the gateway at /v1
│   ├── auth.py            # API key validation
│   ├── tiers.py           # Tier limits, defined in one place
│   ├── config.py          # Settings from environment / .env
│   ├── redis_client.py    # Lazily created Redis client
│   ├── logging_setup.py   # Console logging for the app's own loggers
│   ├── limiters/
│   │   ├── base.py        # RateLimiter protocols & RateLimitDecision
│   │   ├── sliding_log.py # Sliding-log limiter (sync and asyncio)
│   │   └── scripts/
│   │       └── sliding_log.lua  # Atomic check-and-record in Redis
│   ├── budgets/
│   │   ├── base.py        # TokenBudget protocol, Reservation, BudgetState
│   │   ├── token_bucket.py # Continuously refilling budget (default)
│   │   ├── fixed_window.py # Per-minute budget (brute force)
│   │   └── scripts/       # Atomic reserve and settle Lua scripts
│   ├── estimates/
│   │   ├── calibrated.py  # Learned per-model prompt overhead (default)
│   │   ├── characters.py  # Characters / 4 (brute force)
│   │   └── scripts/       # Atomic learning Lua script
│   ├── providers/
│   │   ├── base.py        # Provider protocol
│   │   ├── mock.py        # Deterministic mock LLM
│   │   ├── ollama.py      # Ollama over HTTP, with real token counts
│   │   └── tokens.py      # Characters / 4 token estimate
│   ├── routing/
│   │   ├── registry.py    # Model names and aliases -> providers
│   │   ├── fallback.py    # FallbackStrategy protocol; sequential version (brute force)
│   │   └── circuit_breaker.py # Skips failing targets for a cooldown (default)
│   └── gateway/           # OpenAI-compatible /v1 app: schemas, errors, auth, headers
│
├── tests/                 # pytest suite (needs Redis)
│
├── static/
│   ├── index.html         # Dashboard UI
│   ├── styles.css         # Styling
│   └── app.js             # Frontend logic
│
├── docker-compose.yml     # Redis 7 with healthcheck
├── .env.example           # Configuration template
├── requirements.txt
├── requirements-dev.txt   # Test dependencies
├── pyproject.toml         # pytest and mypy configuration
├── README.md
└── .gitignore
```

---

## 🔑 API Key Simulation

The system simulates **multiple API tiers**:

| Tier | API Key | Requests / min | Tokens / min |
|----|--------|----|----|
| Free | `free-tier-key` | 5 | 2,000 |
| Pro | `pro-tier-key` | 60 | 40,000 |
| Enterprise | `enterprise-key` | 600 | 400,000 |

Limits are defined once, in `app/tiers.py`. `/protected` takes the key in an `X-API-KEY` header; the LLM gateway takes the same keys as `Authorization: Bearer <key>`, which is how OpenAI clients send them.

❗ Any other key will return:
```
401 Unauthorized – Invalid API Key
```

---

## 🚀 Getting Started

### 1️⃣ Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/api-rate-limiter.git
cd api-rate-limiter
```

---

### 2️⃣ Create virtual environment

```bash
python -m venv venv
source venv/bin/activate   # macOS/Linux
```

---

### 3️⃣ Install dependencies

```bash
pip install -r requirements.txt
```

---

### 4️⃣ Start Redis

```bash
docker compose up -d --wait
```

If port 6379 is already taken, choose another host port and point the app at it:

```bash
REDIS_PORT=6380 docker compose up -d --wait
export REDIS_URL=redis://localhost:6380/0
```

---

### 🦙 Optional: run a local model with Ollama

Nothing requires Ollama: `mock` always works, and `auto` falls back to it. To serve a real model, run Ollama **natively**, not in Docker, because Docker on a Mac cannot use the GPU.

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2:3b                  # about 2 GB
curl http://localhost:11434/api/tags     # should list llama3.2:3b
```

`llama3.2:3b` is the default because it fits comfortably on an 8 GB Mac (roughly 2.5–3 GB in memory) and does not emit reasoning text that would use up `max_tokens`. On an 8 GB machine, keep the Docker VM small too: Redis needs far less than Colima's default, for example `colima start --memory 1`.

---

### ⚙️ Configuration

Settings come from environment variables or a `.env` file (see `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis used by the app |
| `DEFAULT_MAX_TOKENS` | `256` | Completion tokens reserved when a gateway request sends neither `max_tokens` nor `max_completion_tokens` |
| `MOCK_LATENCY_SECONDS` | `0` | Simulated response time of the mock model |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama listens |
| `OLLAMA_MODEL` | `llama3.2:3b` | The Ollama model the gateway serves |
| `OLLAMA_CONNECT_TIMEOUT_SECONDS` | `2` | Time allowed to connect to Ollama |
| `OLLAMA_READ_TIMEOUT_SECONDS` | `120` | Time allowed for the reply, covering a cold model load plus generation |
| `AUTO_FALLBACK` | `OLLAMA_MODEL,mock` | Comma-separated models the `auto` alias tries, in order |
| `LOG_LEVEL` | `INFO` | Level of the app's own logs |
| `PROMPT_ESTIMATE` | `calibrated` | `calibrated`, or `characters` for the plain characters ÷ 4 |
| `PROMPT_OVERHEAD_DEFAULT` | `32` | Base prompt overhead assumed for a model until its first real count |
| `PER_MESSAGE_TOKENS` | `5` | Template tokens added for each message after the first |
| `TOKEN_BUDGET` | `token_bucket` | `token_bucket`, or `fixed_window` for per-minute windows |
| `FALLBACK_STRATEGY` | `circuit_breaker` | `circuit_breaker`, or `sequential` to try every model on every request |
| `BREAKER_FAILURE_THRESHOLD` | `3` | Consecutive failures (unavailable or timed out) that open a model's breaker |
| `BREAKER_COOLDOWN_SECONDS` | `30` | How long an open breaker skips the model before one trial request |

```bash
cp .env.example .env
```

---

### 5️⃣ Run the application

```bash
uvicorn app.main:app --reload
```

App runs at:

```
http://127.0.0.1:8000
```

---

## 🖥 Dashboard

Open your browser:

```
http://127.0.0.1:8000
```

You can:
- Select API tier
- Enter API key
- Call protected endpoint
- See remaining requests
- Watch live reset countdown

---

## 🔐 Protected API Endpoint

### Endpoint
```
GET /protected
```

### Headers Required
```http
X-API-KEY: free-tier-key
```

### Example (curl)

```bash
curl -i http://127.0.0.1:8000/protected   -H "X-API-KEY: free-tier-key"
```

### Example Response Headers

```http
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 2
X-RateLimit-Reset: 1700000000
```

---

## 📈 Rate Limiting Logic

- Requests are tracked **per API key**, against **the key's tier limit**, with a **sliding log**: one sorted-set entry per admitted request, scored by its time in milliseconds
- The check and the insert run as a single **Lua script**, so concurrent requests cannot both slip under the limit
- The script uses the **Redis server clock**, so every app instance agrees on the window
- Denied requests are not recorded; idle keys expire on their own
- `X-RateLimit-Reset` is when the oldest request in the window ages out, freeing a slot
- Exceeding the limit returns `429 Too Many Requests` with a `Retry-After` header

---

## 🤖 LLM Gateway (OpenAI-compatible)

The gateway at `/v1` speaks the OpenAI chat completions API. Interactive docs are at `/v1/docs`. It serves three model names:

| Model | Answered by |
|-------|-------------|
| `mock` | A deterministic stand-in that replies `Mock reply to: <your last message>` |
| `llama3.2:3b` (`OLLAMA_MODEL`) | Ollama |
| `auto` | The first model in `AUTO_FALLBACK` that answers: Ollama, then mock by default |

The response's `model` field and an `x-gateway-provider` header (`ollama` or `mock`) always name what actually answered.

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/v1/chat/completions` | `model`, `messages`, and `max_tokens` or `max_completion_tokens`. Non-streaming only. |
| `GET` | `/v1/models` | `mock`, `auto`, and the Ollama model only while Ollama reports it as pulled |

### Example (openai SDK)

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="free-tier-key")
reply = client.chat.completions.create(
    model="mock",
    messages=[{"role": "user", "content": "Hello"}],
    max_tokens=64,
)
print(reply.choices[0].message.content)  # Mock reply to: Hello
print(reply.usage)
```

### Example (curl)

```bash
curl -i http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer free-tier-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "mock", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 64}'
```

### How a request is limited

1. **Size it**: the prompt estimate plus `max_completion_tokens`, else `max_tokens`, else `DEFAULT_MAX_TOKENS`. For a model that reports real token counts (Ollama), the estimate is characters ÷ 4 + the model's learned base overhead + `PER_MESSAGE_TOKENS` for each message after the first; `auto` reserves for its most expensive model. Mock keeps the exact characters ÷ 4.
2. **Reject what can never fit**: if that is more than the tier's tokens per minute, return `400 request_too_large` without using any quota.
3. **Request limit**: the tier's requests per minute, enforced by the same sliding-log Lua script as `/protected`. Over it: `429` with `type: requests`.
4. **Reserve tokens**: the caller's token bucket holds up to the tier's tokens per minute and refills continuously, computed in Lua from the Redis clock. The reservation is taken only if the balance covers it. Otherwise: `429` with `type: tokens` and `Retry-After` set to when the balance will cover this request.
5. **Route it**: a named model is tried alone; `auto` tries its models in order until one answers, skipping any whose circuit breaker is open. If none answers, or the request is cancelled, the whole reservation is released. The release runs in a shielded cancel scope so it completes even while the request is being cancelled.
6. **Settle and learn**: the reservation is replaced by the usage of the model that answered. For Ollama that is its own `prompt_eval_count` and `eval_count`. Unused tokens are refunded (never past a full bucket); extra tokens are charged, and can leave a debt that refill pays back. Ollama's prompt count then updates that model's learned overhead.

The gateway talks to Redis through `redis.asyncio`, so rate limiting never blocks the event loop while other requests wait on the model.

### Rate-limit headers

Successful responses carry OpenAI-style headers. As with OpenAI, reset values are durations, not timestamps:

```http
x-ratelimit-limit-requests: 60
x-ratelimit-remaining-requests: 59
x-ratelimit-reset-requests: 1m0s
x-ratelimit-limit-tokens: 40000
x-ratelimit-remaining-tokens: 39965
x-ratelimit-reset-tokens: 53ms
```

Remaining tokens are measured after settlement, and `x-ratelimit-reset-tokens` is the time until the bucket is full again. Every `429` includes `Retry-After` in seconds. A request-limit `429` leaves out the remaining and reset token headers, because the token budget is not checked for that request.

### Errors

Errors use OpenAI's shape, `{"error": {"message", "type", "param", "code"}}`, so OpenAI SDKs raise their usual exceptions.

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

A `503` means the backend is temporarily unavailable, which is true of a stopped Ollama; `502` is kept for a backend that answered with something broken, so clients and operators can tell the two apart.

**No retries after a timeout or into an open breaker.** OpenAI SDKs retry 5xx responses twice by default. With a 120-second read timeout, retrying a hung model could hold a client for six minutes, so any response caused by a timeout carries `x-should-retry: false`, which the SDKs obey. So does any response involving an open breaker: the SDKs would otherwise wait out `Retry-After` and retry into the same `503`. `Retry-After` still says when the breaker lets its next trial request through.

**Why oversized requests get 400, not 429.** OpenAI answers a request that exceeds the entire per-minute token limit with `429`. This gateway deliberately returns `400` instead. A `429` tells the client to retry later, and the OpenAI SDKs retry `429`s automatically, but this request can never succeed however long the client waits. A `400` fails immediately and tells the client to shorten the messages or lower `max_tokens`.

### Simple versions and their replacements

Each optimized piece sits behind the same interface as the simple version it replaced, and a setting switches back for comparison. Measured numbers, from the tests:

| Piece | Simple version | Optimized version (default) |
|-------|----------------|-----------------------------|
| Token budget (`TOKEN_BUDGET`) | Fixed minute windows: a burst straddling a boundary gets **2.0×** the limit (200 tokens against 100 within about 120 ms) | Token bucket: the same burst gets **1.1×** (110 tokens) |
| Prompt estimate (`PROMPT_ESTIMATE`) | Characters ÷ 4: `Name one planet. One word.` is estimated at **7** tokens; `llama3.2:3b` counts **32** (4.6× too low) | Calibrated: **39** before any real count (it errs high), **32** after learning from one different prompt |
| Fallback (`FALLBACK_STRATEGY`) | Sequential: with Ollama hung, every `auto` request waits the read timeout (**~310 ms** each at a 0.3 s timeout) | Circuit breaker: after 3 failures, `auto` requests skip Ollama (**7 ms**) until a trial after the cooldown |

The calibrated estimate learns only a per-model **base** overhead. The per-message part is a fixed `PER_MESSAGE_TOKENS` (5 for `llama3.2:3b`'s template) and is subtracted before learning, so a long conversation does not inflate later short requests: after a 20-message chat, a one-message request still reserves 26 tokens (its real cost), where learning the whole overhead would reserve 121. The learning rules err toward reserving too much: the first real count replaces the default, a higher count is adopted at once, a lower one moves the base only a tenth of the way down, and a count under half the base is ignored as a prompt-cache artifact.

Circuit-breaker state is kept in memory, per process. It is soft state that only saves time: losing it on a restart costs a few failed attempts to re-learn, each process pays at most `BREAKER_FAILURE_THRESHOLD` failures per cooldown, and routing never waits on Redis. Rate limits and token budgets, which are fairness guarantees, are the parts shared through Redis.

### Known limits

- A full bucket still allows a burst of one minute's budget at once; the bucket only removes the second burst a window boundary used to allow.
- The estimate is still characters ÷ 4 for the messages themselves; only the template overhead is learned. Prompts that tokenize very differently from English prose (code, other languages) can still be under-reserved, and settlement charges the difference.
- One outlier count can raise a model's learned base, which then comes down by a tenth of the gap per request. That errs toward reserving too much, which only costs headroom near the limit.
- With Ollama hung, one trial request per cooldown still waits the full read timeout.
- A request refused for tokens still counts against requests per minute.
- If the process dies between reserving and settling, the reservation stays counted until the bucket refills.

---

## ✅ Running Tests

The tests need Redis and use database 15 by default, deleting only the keys they create.

```bash
pip install -r requirements-dev.txt
docker compose up -d --wait
pytest
```

Point them elsewhere with `TEST_REDIS_URL`, e.g. `TEST_REDIS_URL=redis://localhost:6380/15 pytest`.

Unit tests fake Ollama with `httpx.MockTransport`, and an autouse fixture points the app at a closed port, so no test reaches a real Ollama by accident. The integration tests in `tests/test_ollama_integration.py` run against the real Ollama at `OLLAMA_BASE_URL` when it is running with `OLLAMA_MODEL` pulled, and skip themselves otherwise.

---

## 🧪 Health Check

```
GET /health
```

Response:
```json
{
  "status": "ok"
}
```

---

## 🧠 Learning Outcomes

This project demonstrates:
- Backend system design
- Rate limiting strategies
- Secure API authentication
- Redis usage patterns
- Frontend–backend integration
- Production-grade HTTP standards

---

## 🌟 Possible Enhancements

- Tokenizer-based prompt estimates for the message content
- Streaming chat completions
- JWT authentication
- Persistent user management
- Cloud deployment (AWS / GCP)
- Metrics dashboard (Prometheus + Grafana)

---

## 👤 Author

**Darshan Potnis**  
Backend / Data / Systems Enthusiast  

---
