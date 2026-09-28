# API Rate Limiter & Gateway (Redis-Backed)

A production-style **API rate limiting gateway** built with **FastAPI and Redis**, featuring API key authentication, industry-standard rate-limit headers, and an interactive web dashboard to visualize request limits in real time.

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
| Auth        | API Key Headers |
| Frontend   | HTML, CSS, Vanilla JS |
| Server     | Uvicorn |
| Language   | Python 3.10+ |

---

## 📂 Project Structure

```
api-rate-limiter/
│
├── app/
│   ├── main.py            # FastAPI app & routes
│   ├── auth.py            # API key validation
│   ├── config.py          # Settings from environment / .env
│   ├── redis_client.py    # Lazily created Redis client
│   └── limiters/
│       ├── base.py        # RateLimiter protocol & RateLimitDecision
│       ├── sliding_log.py # Sliding-log limiter
│       └── scripts/
│           └── sliding_log.lua  # Atomic check-and-record in Redis
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
├── pyproject.toml         # pytest configuration
├── README.md
└── .gitignore
```

---

## 🔑 API Key Simulation

The system simulates **multiple API tiers**:

| Tier | API Key |
|----|--------|
| Free | `free-tier-key` |
| Pro | `pro-tier-key` |
| Enterprise | `enterprise-key` |

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
| `RATE_LIMIT_REQUESTS` | `5` | Requests allowed per window, per API key |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Length of the sliding window |

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

- Requests are tracked **per API key** with a **sliding log**: one sorted-set entry per admitted request, scored by its time in milliseconds
- The check and the insert run as a single **Lua script**, so concurrent requests cannot both slip under the limit
- The script uses the **Redis server clock**, so every app instance agrees on the window
- Denied requests are not recorded; idle keys expire on their own
- `X-RateLimit-Reset` is when the oldest request in the window ages out, freeing a slot
- Exceeding the limit returns `429 Too Many Requests` with a `Retry-After` header

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

- Per-tier rate limits
- JWT authentication
- Persistent user management
- Cloud deployment (AWS / GCP)
- Metrics dashboard (Prometheus + Grafana)

---

## 👤 Author

**Darshan Potnis**  
Backend / Data / Systems Enthusiast  

---
