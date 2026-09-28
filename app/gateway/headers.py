"""OpenAI-style x-ratelimit-{limit,remaining,reset}-{requests,tokens} response headers."""

from app.budgets import BudgetState
from app.limiters import RateLimitDecision


def format_duration(milliseconds: int) -> str:
    """Format a wait the way OpenAI's reset headers do: 250ms, 1s, 17.525s, 6m0s."""
    if milliseconds < 1000:
        return f"{milliseconds}ms"
    minutes, rest_ms = divmod(milliseconds, 60_000)
    seconds = f"{rest_ms / 1000:.3f}".rstrip("0").rstrip(".") + "s"
    return f"{minutes}m{seconds}" if minutes else seconds


def request_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "x-ratelimit-limit-requests": str(decision.limit),
        "x-ratelimit-remaining-requests": str(decision.remaining),
        "x-ratelimit-reset-requests": format_duration(decision.reset_at_ms - decision.now_ms),
    }


def token_limit_headers(state: BudgetState) -> dict[str, str]:
    return {
        "x-ratelimit-limit-tokens": str(state.limit),
        "x-ratelimit-remaining-tokens": str(state.remaining),
        "x-ratelimit-reset-tokens": format_duration(state.reset_after_ms),
    }
