from functools import lru_cache

import redis

from app.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    """Process-wide Redis client. redis-py opens connections on the first command, not here."""
    return redis.Redis.from_url(str(get_settings().redis_url), decode_responses=True)
