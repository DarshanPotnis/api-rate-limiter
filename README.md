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
  - Token reservations settled against actual usage
  - OpenAI-style `x-ratelimit-*` headers and error format
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
| LLM Provider | Mock provider (Ollama next) |
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
│   ├── limiters/
│   │   ├── base.py        # RateLimiter protocols & RateLimitDecision
│   │   ├── sliding_log.py # Sliding-log limiter (sync and asyncio)
│   │   └── scripts/
│   │       └── sliding_log.lua  # Atomic check-and-record in Redis
│   ├── budgets/
│   │   ├── base.py        # TokenBudget protocol, Reservation, BudgetState
│   │   ├── fixed_window.py # Per-minute token budget
│   │   └── scripts/       # Atomic reserve and settle Lua scripts
│   ├── providers/
│   │   ├── base.py        # Provider protocol
│   │   ├── mock.py        # Deterministic mock LLM
│   │   └── tokens.py      # Characters / 4 token estimate
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

### ⚙️ Configuration

Settings come from environment variables or a `.env` file (see `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis used by the app |
| `DEFAULT_MAX_TOKENS` | `256` | Completion tokens reserved when a gateway request sends neither `max_tokens` nor `max_completion_tokens` |
| `MOCK_LATENCY_SECONDS` | `0` | Simulated response time of the mock model |

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

The gateway at `/v1` speaks the OpenAI chat completions API. It currently serves one model, `mock`, a deterministic stand-in that answers `Mock reply to: <your last message>`; Ollama will plug into the same provider interface. Interactive docs are at `/v1/docs`.

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/v1/chat/completions` | `model`, `messages`, and `max_tokens` or `max_completion_tokens`. Non-streaming only. |
| `GET` | `/v1/models` | Lists the available models |

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

1. **Size it**: the prompt estimate (characters in all messages ÷ 4, rounded up) plus `max_completion_tokens`, else `max_tokens`, else `DEFAULT_MAX_TOKENS`.
2. **Reject what can never fit**: if that is more than the tier's tokens per minute, return `400 request_too_large` without using any quota.
3. **Request limit**: the tier's requests per minute, enforced by the same sliding-log Lua script as `/protected`. Over it: `429` with `type: requests`.
4. **Reserve tokens**: a Lua script adds the reservation to the caller's count for the current minute (on the Redis clock) only if it fits. Otherwise: `429` with `type: tokens` and `Retry-After` until the minute ends.
5. **Call the provider**: if it fails (`502`) or the request is cancelled, the whole reservation is released. The release runs in a shielded cancel scope so it completes even while the request is being cancelled.
6. **Settle**: the reservation is replaced by the actual usage, in the minute it was made in. Unused tokens are refunded; extra tokens are charged.

The gateway talks to Redis through `redis.asyncio`, so rate limiting never blocks the event loop while other requests wait on the model.

### Rate-limit headers

Successful responses carry OpenAI-style headers. As with OpenAI, reset values are durations, not timestamps:

```http
x-ratelimit-limit-requests: 60
x-ratelimit-remaining-requests: 59
x-ratelimit-reset-requests: 1m0s
x-ratelimit-limit-tokens: 40000
x-ratelimit-remaining-tokens: 39986
x-ratelimit-reset-tokens: 33.023s
```

Remaining tokens are measured after settlement. Every `429` includes `Retry-After` in seconds. A request-limit `429` leaves out the remaining and reset token headers, because the token budget is not checked for that request.

### Errors

Errors use OpenAI's shape, `{"error": {"message", "type", "param", "code"}}`, so OpenAI SDKs raise their usual exceptions.

| Status | `type` | `code` | When |
|--------|--------|--------|------|
| 400 | `invalid_request_error` | (`param` names the field) | Invalid body, or `stream: true` |
| 400 | `invalid_request_error` | `request_too_large` | The request could never fit the tier's token budget |
| 401 | `invalid_request_error` | `invalid_api_key` | Missing or unknown key |
| 404 | `invalid_request_error` | `model_not_found` | Any model other than `mock` |
| 429 | `requests` or `tokens` | `rate_limit_exceeded` | Request or token limit reached |
| 502 | `server_error` | `provider_error` | The model provider failed |

**Why oversized requests get 400, not 429.** OpenAI answers a request that exceeds the entire per-minute token limit with `429`. This gateway deliberately returns `400` instead. A `429` tells the client to retry later, and the OpenAI SDKs retry `429`s automatically, but this request can never succeed however long the client waits. A `400` fails immediately and tells the client to shorten the messages or lower `max_tokens`.

### Known limits of the current token budget

This is the simple, fixed-window version; a smoother algorithm can replace it behind the same `TokenBudget` interface.

- A caller can spend a full budget just before a minute ends and another just after, briefly using up to twice the limit.
- The characters ÷ 4 estimate is rough. If a provider reports more tokens than were reserved, settlement charges the difference, which can take usage past the limit after the request was admitted.
- A request refused for tokens still counts against requests per minute.
- If the process dies between reserving and settling, the reservation stays counted until the minute ends.

---

## ✅ Running Tests

The tests need Redis and use database 15 by default, deleting only the keys they create.

```bash
pip install -r requirements-dev.txt
docker compose up -d --wait
pytest
```

Point them elsewhere with `TEST_REDIS_URL`, e.g. `TEST_REDIS_URL=redis://localhost:6380/15 pytest`.

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

- Ollama provider for the LLM gateway
- Streaming chat completions
- Smoother token budget without the minute-boundary burst
- JWT authentication
- Persistent user management
- Cloud deployment (AWS / GCP)
- Metrics dashboard (Prometheus + Grafana)

---

## 👤 Author

**Darshan Potnis**  
Backend / Data / Systems Enthusiast  

---
