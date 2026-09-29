"""A fallback strategy that stops calling a backend while it is known to be down.

Breaker state lives in this process's memory. That is deliberate: it is soft state that
only saves time, so losing it on a restart costs a few failed attempts to re-learn;
each process pays at most ``failure_threshold`` failures per cooldown on its own; and
routing never waits on Redis, so the breaker keeps working when Redis does not.
"""

import dataclasses
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.providers import CompletionRequest, ProviderError, ProviderTimeout, ProviderUnavailable
from app.routing.fallback import AllTargetsFailed, Answer, Attempt
from app.routing.registry import Target

log = logging.getLogger(__name__)


class CircuitOpen(ProviderUnavailable):
    """The target was not tried, because its breaker is open."""

    def __init__(self, model: str, retry_after_seconds: float) -> None:
        super().__init__(f"{model} is failing, so the gateway is not trying it for {retry_after_seconds:.0f}s")
        self.retry_after_seconds = retry_after_seconds


BreakerState = Literal["closed", "open", "half_open"]


@dataclass(frozen=True, slots=True)
class BreakerStatus:
    """A read-only snapshot of one target's breaker."""

    state: BreakerState
    consecutive_failures: int
    cooldown_remaining_seconds: float


@dataclass
class _Breaker:
    failures: int = 0
    open_until: float | None = None  # None while closed
    trial_in_flight: bool = False


class CircuitBreakerFallback:
    """Tries targets in order, skipping any whose breaker is open.

    A breaker opens after ``failure_threshold`` consecutive unavailable or timed-out
    failures and stays open for ``cooldown_seconds``. Then one trial request is let
    through while others keep skipping the target: success closes the breaker, failure
    opens it for another cooldown. A broken reply (a plain ``ProviderError``) shows the
    backend is up, so it resets the count instead.
    """

    def __init__(
        self, *, failure_threshold: int, cooldown_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be at least 1, got {failure_threshold}")
        if cooldown_seconds <= 0:
            raise ValueError(f"cooldown_seconds must be positive, got {cooldown_seconds}")
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._breakers: dict[str, _Breaker] = {}

    async def complete(self, targets: Sequence[Target], request: CompletionRequest) -> Answer:
        attempts: list[Attempt] = []
        for target in targets:
            breaker = self._breakers.setdefault(target.model, _Breaker())
            skipped = self._skip(target.model, breaker)
            if skipped is not None:
                attempts.append(Attempt(target, skipped))
                continue
            trial = breaker.open_until is not None  # open, cooldown over: this request is the trial
            breaker.trial_in_flight = trial
            try:
                completion = await target.provider.complete(dataclasses.replace(request, model=target.model))
            except (ProviderUnavailable, ProviderTimeout) as exc:
                self._failed(target.model, breaker, trial=trial)
                attempts.append(Attempt(target, exc))
                continue
            except ProviderError as exc:
                self._reachable(target.model, breaker)
                attempts.append(Attempt(target, exc))
                continue
            finally:
                # Also runs when the trial is cancelled, so the next request can be the trial.
                breaker.trial_in_flight = False
            self._reachable(target.model, breaker)
            return Answer(target, completion)
        raise AllTargetsFailed(attempts)

    def status(self, model: str) -> BreakerStatus:
        """The breaker's state for ``model``, without changing it. Half-open means the
        cooldown has passed and the next request, or the one in flight, is the trial."""
        breaker = self._breakers.get(model)
        if breaker is None:
            return BreakerStatus("closed", 0, 0.0)
        if breaker.open_until is None:
            return BreakerStatus("closed", breaker.failures, 0.0)
        remaining = breaker.open_until - self._clock()
        if remaining > 0:
            return BreakerStatus("open", breaker.failures, remaining)
        return BreakerStatus("half_open", breaker.failures, 0.0)

    def _skip(self, model: str, breaker: _Breaker) -> CircuitOpen | None:
        if breaker.open_until is None:
            return None
        remaining = breaker.open_until - self._clock()
        if remaining > 0 or breaker.trial_in_flight:
            return CircuitOpen(model, max(remaining, 0.0))
        return None

    def _failed(self, model: str, breaker: _Breaker, *, trial: bool) -> None:
        breaker.failures += 1
        if trial or breaker.failures >= self._threshold:
            breaker.open_until = self._clock() + self._cooldown
            log.warning(
                "opening the circuit for %s for %.0fs after %d consecutive failures",
                model,
                self._cooldown,
                breaker.failures,
            )

    def _reachable(self, model: str, breaker: _Breaker) -> None:
        if breaker.open_until is not None:
            log.info("closing the circuit for %s", model)
        breaker.failures = 0
        breaker.open_until = None
