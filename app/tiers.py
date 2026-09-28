"""Service tiers and their rate limits. This is the one place those numbers are defined."""

from collections.abc import Mapping
from dataclasses import dataclass

LIMIT_WINDOW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class Tier:
    name: str
    requests_per_minute: int
    tokens_per_minute: int

    def __post_init__(self) -> None:
        if self.requests_per_minute < 1 or self.tokens_per_minute < 1:
            raise ValueError(f"tier {self.name!r} needs positive limits")


FREE = Tier("free", requests_per_minute=5, tokens_per_minute=2_000)
PRO = Tier("pro", requests_per_minute=60, tokens_per_minute=40_000)
ENTERPRISE = Tier("enterprise", requests_per_minute=600, tokens_per_minute=400_000)

USER_TIERS: Mapping[str, Tier] = {
    "free_user": FREE,
    "pro_user": PRO,
    "enterprise_user": ENTERPRISE,
}


def tier_for(user_id: str) -> Tier:
    return USER_TIERS[user_id]
