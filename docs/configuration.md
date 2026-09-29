# Configuration

The gateway reads its settings from environment variables, or from a `.env` file in the
directory it starts in (copy `.env.example` to begin). Invalid values stop it at startup
with a validation error.

## Gateway settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis used for rate limits, token budgets and learned estimates |
| `DEFAULT_MAX_TOKENS` | `256` | Completion tokens reserved when a request sends neither `max_tokens` nor `max_completion_tokens` |
| `MOCK_LATENCY_SECONDS` | `0` | Simulated response time of the mock model |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama listens |
| `OLLAMA_MODEL` | `llama3.2:3b` | The Ollama model the gateway serves |
| `OLLAMA_CONNECT_TIMEOUT_SECONDS` | `2` | Time allowed to connect to Ollama |
| `OLLAMA_READ_TIMEOUT_SECONDS` | `120` | Time allowed for the reply, covering a cold model load plus generation |
| `AUTO_FALLBACK` | `OLLAMA_MODEL,mock` | Comma-separated models the `auto` alias tries, in order |
| `LOG_LEVEL` | `INFO` | Level of the app's own logs (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `PROMPT_ESTIMATE` | `calibrated` | `calibrated`, or `characters` for the plain characters ÷ 4 |
| `PROMPT_OVERHEAD_DEFAULT` | `32` | Base prompt overhead assumed for a model until its first real count |
| `PER_MESSAGE_TOKENS` | `5` | Template tokens added for each message after the first |
| `TOKEN_BUDGET` | `token_bucket` | `token_bucket`, or `fixed_window` for per-minute windows |
| `FALLBACK_STRATEGY` | `circuit_breaker` | `circuit_breaker`, or `sequential` to try every model on every request |
| `BREAKER_FAILURE_THRESHOLD` | `3` | Consecutive failures (unavailable or timed out) that open a model's breaker |
| `BREAKER_COOLDOWN_SECONDS` | `30` | How long an open breaker skips the model before one trial request |

`PROMPT_ESTIMATE`, `TOKEN_BUDGET` and `FALLBACK_STRATEGY` switch each optimized piece back
to the simple version it replaced, for comparison. See [results.md](results.md).

## Tiers and API keys

Tier limits are defined once, in `app/tiers.py`. The keys are demo values.

| Tier | API key | Requests / min | Tokens / min |
|------|---------|----------------|--------------|
| Free | `free-tier-key` | 5 | 2,000 |
| Pro | `pro-tier-key` | 60 | 40,000 |
| Enterprise | `enterprise-key` | 600 | 400,000 |

## Docker Compose

`docker compose up` sets `REDIS_URL` to the Redis container and `OLLAMA_BASE_URL` to
`http://host.docker.internal:11434`, so a host Ollama is used when it runs. Other settings
can be added under the `gateway` service's `environment` in `docker-compose.yml`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `GATEWAY_PORT` | `8000` | Host port for the gateway, e.g. `GATEWAY_PORT=8080 docker compose up -d` |
| `REDIS_PORT` | `6379` | Host port for Redis |

## Tests

| Variable | Default | Meaning |
|----------|---------|---------|
| `TEST_REDIS_URL` | `redis://localhost:6379/15` | Redis database the tests use; they delete only the keys they create |
