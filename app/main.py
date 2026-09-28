from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path

from app.auth import get_api_key
from app.gateway.app import gateway
from app.limiters import RateLimitDecision, RateLimiter, SlidingLogLimiter
from app.redis_client import get_redis
from app.tiers import LIMIT_WINDOW_SECONDS, Tier, tier_for

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Starlette does not run the lifespans of mounted apps, so start the gateway's here.
    async with gateway.router.lifespan_context(gateway):
        yield


app = FastAPI(title="Real-Time API Rate Limiter & Gateway", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/v1", gateway)


@lru_cache
def limiter_for_tier(tier: Tier) -> RateLimiter:
    return SlidingLogLimiter(get_redis(), limit=tier.requests_per_minute, window_seconds=LIMIT_WINDOW_SECONDS)


def get_limiter(user_id: str = Depends(get_api_key)) -> RateLimiter:
    return limiter_for_tier(tier_for(user_id))


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
