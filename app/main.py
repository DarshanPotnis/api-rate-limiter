from functools import lru_cache

from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path

from app.auth import get_api_key
from app.config import get_settings
from app.limiters import RateLimitDecision, RateLimiter, SlidingLogLimiter
from app.redis_client import get_redis

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Real-Time API Rate Limiter & Gateway")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@lru_cache
def get_limiter() -> RateLimiter:
    settings = get_settings()
    return SlidingLogLimiter(
        get_redis(),
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(decision.reset_at_seconds),
    }


@app.get("/", response_class=HTMLResponse)
def home():
    return (STATIC_DIR / "index.html").read_text()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/protected")
def protected_route(
    response: Response,
    user_id: str = Depends(get_api_key),
    limiter: RateLimiter = Depends(get_limiter),
) -> dict[str, str]:
    decision = limiter.hit(f"{user_id}:protected")
    headers = rate_limit_headers(decision)

    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={**headers, "Retry-After": str(decision.retry_after_seconds)},
        )

    response.headers.update(headers)

    return {
        "message": "You accessed a protected resource",
        "user": user_id,
    }
