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
│   ├── rate_limiter.py    # Redis rate limiting logic
│   └── redis_client.py    # Redis connection
│
├── static/
│   ├── index.html         # Dashboard UI
│   ├── styles.css         # Styling
│   └── app.js             # Frontend logic
│
├── requirements.txt
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

Make sure Redis is running locally:

```bash
redis-server
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

- Requests are tracked **per API key**
- Redis stores counters with expiration
- Limits reset automatically using TTL
- Exceeding limit returns:
```
429 Too Many Requests
```

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
