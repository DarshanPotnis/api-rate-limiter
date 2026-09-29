"""GET /status: a read-only view of the gateway's routing state, polled by the console.

It sits outside /v1 and needs no API key, like /health: it exposes no user data. It reads
the circuit breakers' in-memory state and never calls a model, so polling it is cheap and
cannot trip anything.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.budgets import TokenBucketBudget
from app.gateway.app import gateway
from app.gateway.dependencies import GatewayResources
from app.routing import BreakerState, CircuitBreakerFallback

router = APIRouter()


class ModelStatus(BaseModel):
    id: str
    provider: str
    breaker: BreakerState | None
    """None when the fallback strategy keeps no breakers."""
    consecutive_failures: int | None
    cooldown_remaining_seconds: float


class GatewayStatus(BaseModel):
    fallback_strategy: Literal["circuit_breaker", "sequential"]
    token_budget: Literal["token_bucket", "fixed_window"]
    models: list[ModelStatus]


async def get_gateway_resources() -> GatewayResources:
    resources: GatewayResources = gateway.state.resources
    return resources


@router.get("/status")
async def read_status(resources: GatewayResources = Depends(get_gateway_resources)) -> GatewayStatus:
    fallback = resources.fallback
    breakers = fallback if isinstance(fallback, CircuitBreakerFallback) else None
    models = []
    for target in resources.registry.targets():
        status = breakers.status(target.model) if breakers is not None else None
        models.append(
            ModelStatus(
                id=target.model,
                provider=target.provider.name,
                breaker=status.state if status is not None else None,
                consecutive_failures=status.consecutive_failures if status is not None else None,
                cooldown_remaining_seconds=status.cooldown_remaining_seconds if status is not None else 0.0,
            )
        )
    return GatewayStatus(
        fallback_strategy="circuit_breaker" if breakers is not None else "sequential",
        token_budget="token_bucket" if isinstance(resources.token_budget, TokenBucketBudget) else "fixed_window",
        models=models,
    )
