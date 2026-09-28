"""Gateway authentication: API keys sent as bearer tokens, the way the OpenAI SDK sends them."""

from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import VALID_API_KEYS
from app.gateway.errors import OpenAIError
from app.tiers import Tier, tier_for

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Caller:
    user_id: str
    tier: Tier


async def get_caller(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Caller:
    if credentials is None:
        raise OpenAIError(
            401, "You didn't provide an API key. Send it as 'Authorization: Bearer <key>'.", code="invalid_api_key"
        )
    user_id = VALID_API_KEYS.get(credentials.credentials)
    if user_id is None:
        raise OpenAIError(401, "Incorrect API key provided.", code="invalid_api_key")
    return Caller(user_id=user_id, tier=tier_for(user_id))
