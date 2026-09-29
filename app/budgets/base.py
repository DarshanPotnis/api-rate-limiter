"""Contract shared by every token-budget algorithm."""

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BudgetState:
    """A caller's token budget in the current window. Times are epoch ms on the Redis clock."""

    limit: int
    remaining: int
    reset_at_ms: int
    now_ms: int

    @property
    def reset_after_ms(self) -> int:
        return self.reset_at_ms - self.now_ms

    @property
    def retry_after_seconds(self) -> int:
        """Whole seconds until the window resets, rounded up."""
        return math.ceil(self.reset_after_ms / 1000)


@dataclass(frozen=True, slots=True)
class Reservation:
    """Outcome of reserving tokens. ``state`` is the budget right after the attempt."""

    key: str
    tokens: int
    allowed: bool
    window_start_ms: int
    state: BudgetState


class TokenBudget(Protocol):
    async def reserve(self, key: str, tokens: int, *, limit: int) -> Reservation:
        """Reserve ``tokens`` for ``key`` if they fit within ``limit`` for the current window."""
        ...

    async def settle(self, reservation: Reservation, actual_tokens: int) -> BudgetState:
        """Replace an admitted reservation with the tokens actually used."""
        ...

    async def release(self, reservation: Reservation) -> BudgetState:
        """Give back an admitted reservation whose request produced no usage."""
        ...
