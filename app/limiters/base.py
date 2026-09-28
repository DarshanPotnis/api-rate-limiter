"""Contract shared by every rate-limiting algorithm."""

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of one rate-limit check. Times are epoch milliseconds from the Redis clock."""

    allowed: bool
    limit: int
    remaining: int
    reset_at_ms: int
    now_ms: int

    @property
    def reset_at_seconds(self) -> int:
        """``reset_at_ms`` as epoch seconds, rounded up so clients never retry early."""
        return math.ceil(self.reset_at_ms / 1000)

    @property
    def retry_after_seconds(self) -> int:
        """Whole seconds from this decision until ``reset_at_ms``, rounded up."""
        return math.ceil((self.reset_at_ms - self.now_ms) / 1000)


class RateLimiter(Protocol):
    def hit(self, key: str) -> RateLimitDecision:
        """Record an attempt for ``key`` and decide whether it is admitted."""
        ...
