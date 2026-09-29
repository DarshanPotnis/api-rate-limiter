"""Contract shared by every token-budget algorithm."""

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BudgetState:
    """A caller's token budget right now. Times are epoch ms on the Redis clock.

    ``reset_at_ms`` is when the budget is back to its full limit.
    """

    limit: int
    remaining: int
    reset_at_ms: int
    now_ms: int

    @property
    def reset_after_ms(self) -> int:
        return self.reset_at_ms - self.now_ms


@dataclass(frozen=True, slots=True)
class Reservation:
    """Outcome of reserving tokens. ``state`` is the budget right after the attempt."""

    key: str
    tokens: int
    allowed: bool
    settle_ref: int
    """Whatever the budget needs to settle this reservation later, such as its window."""
    state: BudgetState
    retry_after_ms: int
    """For a refused reservation, how long until the same request would fit; 0 if admitted."""

    @property
    def retry_after_seconds(self) -> int:
        return math.ceil(self.retry_after_ms / 1000)


class TokenBudget(Protocol):
    async def reserve(self, key: str, tokens: int, *, limit: int) -> Reservation:
        """Reserve ``tokens`` for ``key`` if they fit within ``limit``. Zero tokens reads the balance."""
        ...

    async def settle(self, reservation: Reservation, actual_tokens: int) -> BudgetState:
        """Replace an admitted reservation with the tokens actually used."""
        ...

    async def release(self, reservation: Reservation) -> BudgetState:
        """Give back an admitted reservation whose request produced no usage."""
        ...
