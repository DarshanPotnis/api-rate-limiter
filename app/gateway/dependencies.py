"""Gateway resources, created once per app lifetime, and the dependencies that hand them out."""

from dataclasses import dataclass, field

import redis.asyncio
from fastapi import Depends, Request

from app.budgets import TokenBudget
from app.gateway.auth import Caller, get_caller
from app.limiters import AsyncRateLimiter, AsyncSlidingLogLimiter
from app.routing import FallbackStrategy, ModelRegistry
from app.tiers import LIMIT_WINDOW_SECONDS, Tier


@dataclass
class GatewayResources:
    redis: redis.asyncio.Redis
    token_budget: TokenBudget
    registry: ModelRegistry
    fallback: FallbackStrategy
    started_at: int
    _request_limiters: dict[Tier, AsyncRateLimiter] = field(default_factory=dict, init=False)

    def request_limiter(self, tier: Tier) -> AsyncRateLimiter:
        limiter = self._request_limiters.get(tier)
        if limiter is None:
            limiter = AsyncSlidingLogLimiter(
                self.redis, limit=tier.requests_per_minute, window_seconds=LIMIT_WINDOW_SECONDS
            )
            self._request_limiters[tier] = limiter
        return limiter


async def get_resources(request: Request) -> GatewayResources:
    resources: GatewayResources = request.app.state.resources
    return resources


async def get_registry(resources: GatewayResources = Depends(get_resources)) -> ModelRegistry:
    return resources.registry


async def get_fallback(resources: GatewayResources = Depends(get_resources)) -> FallbackStrategy:
    return resources.fallback


async def get_token_budget(resources: GatewayResources = Depends(get_resources)) -> TokenBudget:
    return resources.token_budget


async def get_request_limiter(
    caller: Caller = Depends(get_caller), resources: GatewayResources = Depends(get_resources)
) -> AsyncRateLimiter:
    return resources.request_limiter(caller.tier)
