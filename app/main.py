from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import get_api_key
from app.config import get_settings
from app.gateway.app import gateway
from app.limiters import RateLimitDecision, RateLimiter, SlidingLogLimiter
from app.logging_setup import configure_logging
from app.redis_client import get_redis
from app.status import router as status_router
from app.tiers import LIMIT_WINDOW_SECONDS, Tier, tier_for

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

# The console loads only its own files and talks only to this gateway, so nothing else is
# allowed; this also keeps it working offline.
CONSOLE_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "img-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    # Starlette does not run the lifespans of mounted apps, so start the gateway's here.
    async with gateway.router.lifespan_context(gateway):
        yield


app = FastAPI(title="Real-Time API Rate Limiter & Gateway", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/v1", gateway)
app.include_router(status_router)


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


@app.get("/", response_class=FileResponse)
def console() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Content-Security-Policy": CONSOLE_CSP})


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
