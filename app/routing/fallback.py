"""How the gateway walks a list of targets until one of them answers."""

import dataclasses
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.providers import Completion, CompletionRequest, ProviderError
from app.routing.registry import Target

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Answer:
    target: Target
    completion: Completion


@dataclass(frozen=True, slots=True)
class Attempt:
    target: Target
    error: ProviderError


class AllTargetsFailed(Exception):
    def __init__(self, attempts: Sequence[Attempt]) -> None:
        super().__init__("; ".join(f"{a.target.model}: {a.error}" for a in attempts))
        self.attempts = tuple(attempts)


class FallbackStrategy(Protocol):
    async def complete(self, targets: Sequence[Target], request: CompletionRequest) -> Answer:
        """Answer from the first target that succeeds, or raise ``AllTargetsFailed``.

        Only ``ProviderError`` moves on to the next target; any other exception is a bug
        and propagates unchanged.
        """
        ...


class SequentialFallback:
    """Brute force: try every target in order on every request, remembering nothing.

    A backend that is down is retried on each request. A circuit breaker can replace
    this class behind ``FallbackStrategy`` by skipping targets that failed recently.
    """

    async def complete(self, targets: Sequence[Target], request: CompletionRequest) -> Answer:
        attempts: list[Attempt] = []
        for target in targets:
            try:
                completion = await target.provider.complete(dataclasses.replace(request, model=target.model))
            except ProviderError as exc:
                log.warning("%s (%s) failed: %s", target.model, target.provider.name, exc)
                attempts.append(Attempt(target, exc))
                continue
            return Answer(target, completion)
        raise AllTargetsFailed(attempts)
