import time
from fastapi import HTTPException
from app.redis_client import redis_client

RATE_LIMIT = 5
WINDOW_SIZE = 60  # seconds


def rate_limit(user_id: str, endpoint: str):
    current_time = int(time.time())
    key = f"rate:{user_id}:{endpoint}"

    # Remove expired timestamps
    redis_client.zremrangebyscore(key, 0, current_time - WINDOW_SIZE)

    request_count = redis_client.zcard(key)

    if request_count >= RATE_LIMIT:
        oldest = redis_client.zrange(key, 0, 0, withscores=True)
        reset_time = int(oldest[0][1] + WINDOW_SIZE)
        retry_after = reset_time - current_time

        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(RATE_LIMIT),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_time),
            },
        )

    # Add current request
    redis_client.zadd(key, {current_time: current_time})
    redis_client.expire(key, WINDOW_SIZE)

    remaining = RATE_LIMIT - request_count - 1
    reset_time = current_time + WINDOW_SIZE

    return {
        "limit": RATE_LIMIT,
        "remaining": remaining,
        "reset": reset_time,
    }
