# Development

## Run without Docker

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d --wait redis        # just Redis
uvicorn app.main:app --reload            # http://localhost:8000
```

## Ollama (optional)

Nothing requires Ollama: `mock` always answers and `auto` falls back to it. To serve a real
model, run Ollama **natively** on the host, not in Docker, because Docker on a Mac cannot use
the GPU.

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2:3b                  # about 2 GB
curl http://localhost:11434/api/tags     # should list llama3.2:3b
```

`llama3.2:3b` is the default because it fits comfortably on an 8 GB Mac (roughly 2.5–3 GB in
memory) and does not emit reasoning text that would use up `max_tokens`. On an 8 GB machine,
keep the Docker VM small too: Redis needs far less than Colima's default, for example
`colima start --memory 1`.

### Reaching the host's Ollama from the gateway container

`docker-compose.yml` points the gateway at `http://host.docker.internal:11434` and maps that
name to the host with `host-gateway`.

- **macOS (Colima or Docker Desktop):** works as is. On Colima, `host.docker.internal`
  resolves to `192.168.5.2`, which reaches an Ollama listening only on `127.0.0.1`; this was
  checked from inside the gateway container, and `auto` was answered by `llama3.2:3b`.
- **Linux:** Ollama listens only on `127.0.0.1` by default, and a container reaches the host
  through the Docker bridge, not loopback. Make Ollama listen on all interfaces, for example
  with `sudo systemctl edit ollama` and:

  ```ini
  [Service]
  Environment="OLLAMA_HOST=0.0.0.0"
  ```

  then `sudo systemctl restart ollama`. Firewall port 11434 from other machines, since
  Ollama has no authentication.

## Tests and checks

The tests need Redis (`docker compose up -d --wait redis`) and use database 15, deleting only
the keys they create.

```bash
pytest                      # unit and gateway tests, plus Ollama integration tests if Ollama runs
ruff check . && ruff format --check .
mypy app tests scripts
```

- Unit tests fake Ollama with `httpx.MockTransport`, and an autouse fixture points the app at
  a closed port, so no test reaches a real Ollama by accident.
- `tests/test_ollama_integration.py` runs against the real Ollama at `OLLAMA_BASE_URL` when it
  is running with `OLLAMA_MODEL` pulled, and skips itself otherwise.
- `tests/test_demo.py` runs `scripts/demo.py` in-process, so the demo cannot drift from the
  gateway.
- `tests/test_console.py` holds the console to its rules: served with a same-origin
  Content-Security-Policy, local assets only, labelled fields, and no `innerHTML` (or other
  HTML-from-strings sinks) in its scripts.
- Timing-sensitive tests compare against the Redis-clock timestamps the code returns, or
  against the other side of a comparison, rather than fixed millisecond bounds, so a slow
  CI runner does not fail them.

CI (`.github/workflows/ci.yml`) runs ruff, mypy and pytest against a Redis service
container on every push and pull request, and a second job runs `docker compose up` and
the demo against the stack.

## Record the demo GIF

`docs/demo.tape` records `scripts/demo.py` into `docs/demo.gif` with
[VHS](https://github.com/charmbracelet/vhs). From the repository root:

```bash
brew install vhs                                     # also installs ttyd and ffmpeg
docker compose down && docker compose up -d --wait   # fresh limits and breaker state
brew services stop ollama                            # optional: shows the fallback and the breaker
vhs docs/demo.tape                                   # writes docs/demo.gif
brew services start ollama                           # if you stopped it
```

The tape activates `.venv`, so the openai SDK must be installed there
(`pip install -r requirements-dev.txt`).

## Troubleshooting

- **`/v1/models` briefly omits the Ollama model.** The listing asks Ollama's `/api/tags`
  with a 2-second timeout and leaves the model out if that call fails. Ollama occasionally
  answers `/api/tags` with a `500` (2 of 72 calls in one session); the next call lists it
  again. Requests to the model are not affected.
- **Port already in use.** `GATEWAY_PORT=8080 docker compose up -d` or
  `REDIS_PORT=6380 docker compose up -d`.
