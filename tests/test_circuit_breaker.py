"""Tests for the circuit-breaker fallback strategy, driven by a fake clock."""

import anyio
import pytest

from app.providers import (
    Completion,
    CompletionRequest,
    Message,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.routing import AllTargetsFailed, Answer, CircuitBreakerFallback, CircuitOpen, Target
from tests.stubs import StubProvider

pytestmark = pytest.mark.anyio

REQUEST = CompletionRequest(model="auto", messages=(Message("user", "hi"),), max_tokens=8)
THRESHOLD = 3
COOLDOWN = 30.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> CircuitBreakerFallback:
    return CircuitBreakerFallback(failure_threshold=THRESHOLD, cooldown_seconds=COOLDOWN, clock=clock)


async def _fail(breaker: CircuitBreakerFallback, targets: list[Target]) -> AllTargetsFailed:
    with pytest.raises(AllTargetsFailed) as failed:
        await breaker.complete(targets, REQUEST)
    return failed.value


async def _open(breaker: CircuitBreakerFallback, target: Target) -> None:
    for _ in range(THRESHOLD):
        await _fail(breaker, [target])


@pytest.mark.parametrize("error", [ProviderUnavailable("down"), ProviderTimeout("hung")], ids=["down", "timed-out"])
async def test_a_target_opens_after_consecutive_failures(breaker: CircuitBreakerFallback, error: Exception) -> None:
    local = StubProvider("local", error=error)
    target = Target("local", local)

    await _open(breaker, target)
    skipped = await _fail(breaker, [target])

    assert len(local.calls) == THRESHOLD
    assert isinstance(skipped.attempts[0].error, CircuitOpen)


async def test_a_success_resets_the_count(breaker: CircuitBreakerFallback) -> None:
    local = StubProvider("local", error=ProviderUnavailable("down"))
    target = Target("local", local)
    for _ in range(THRESHOLD - 1):
        await _fail(breaker, [target])
    local.error = None
    await breaker.complete([target], REQUEST)
    local.error = ProviderUnavailable("down")

    for _ in range(THRESHOLD - 1):
        await _fail(breaker, [target])
    await _fail(breaker, [target])

    assert len(local.calls) == 2 * THRESHOLD  # every request still reached the provider


async def test_broken_replies_do_not_open_the_breaker(breaker: CircuitBreakerFallback) -> None:
    local = StubProvider("local", error=ProviderError("unreadable reply"))
    target = Target("local", local)

    for _ in range(THRESHOLD * 2):
        await _fail(breaker, [target])

    assert len(local.calls) == THRESHOLD * 2


async def test_an_open_target_reports_the_remaining_cooldown(breaker: CircuitBreakerFallback, clock: FakeClock) -> None:
    target = Target("local", StubProvider("local", error=ProviderUnavailable("down")))
    await _open(breaker, target)

    clock.now += 10
    skipped = await _fail(breaker, [target])

    error = skipped.attempts[0].error
    assert isinstance(error, CircuitOpen)
    assert error.retry_after_seconds == pytest.approx(COOLDOWN - 10)


async def test_auto_skips_an_open_target(breaker: CircuitBreakerFallback) -> None:
    local = StubProvider("local", error=ProviderUnavailable("down"))
    mock = StubProvider("mock")
    targets = [Target("local", local), Target("mock", mock)]
    for _ in range(THRESHOLD):
        await breaker.complete(targets, REQUEST)

    answer = await breaker.complete(targets, REQUEST)

    assert answer.target.model == "mock"
    assert len(local.calls) == THRESHOLD
    assert len(mock.calls) == THRESHOLD + 1


async def test_after_the_cooldown_one_trial_goes_through_and_a_failure_reopens(
    breaker: CircuitBreakerFallback, clock: FakeClock
) -> None:
    local = StubProvider("local", error=ProviderUnavailable("down"))
    target = Target("local", local)
    await _open(breaker, target)

    clock.now += COOLDOWN
    await _fail(breaker, [target])  # the trial reaches the provider and fails
    reopened = await _fail(breaker, [target])

    assert len(local.calls) == THRESHOLD + 1
    error = reopened.attempts[0].error
    assert isinstance(error, CircuitOpen)
    assert error.retry_after_seconds == pytest.approx(COOLDOWN)


async def test_a_successful_trial_closes_the_breaker(breaker: CircuitBreakerFallback, clock: FakeClock) -> None:
    local = StubProvider("local", error=ProviderUnavailable("down"))
    target = Target("local", local)
    await _open(breaker, target)

    clock.now += COOLDOWN
    local.error = None
    await breaker.complete([target], REQUEST)
    await breaker.complete([target], REQUEST)

    assert len(local.calls) == THRESHOLD + 2


class GatedProvider:
    """Waits for ``release`` before answering, so a test can hold a request in flight."""

    name = "gated"
    reports_real_usage = True

    def __init__(self) -> None:
        self.started = anyio.Event()
        self.release = anyio.Event()
        self.calls = 0
        self.error: Exception | None = ProviderUnavailable("down")

    async def complete(self, request: CompletionRequest) -> Completion:
        self.calls += 1
        if self.error is not None:
            raise self.error
        self.started.set()
        await self.release.wait()
        return Completion(content="gated reply", finish_reason="stop", prompt_tokens=1, completion_tokens=1)

    async def is_available(self, model: str) -> bool:
        return True


async def test_only_one_trial_is_let_through_at_a_time(breaker: CircuitBreakerFallback, clock: FakeClock) -> None:
    gated = GatedProvider()
    target = Target("local", gated)
    await _open(breaker, target)
    clock.now += COOLDOWN
    gated.error = None
    answers: list[Answer] = []

    async def trial() -> None:
        answers.append(await breaker.complete([target], REQUEST))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(trial)
        await gated.started.wait()
        concurrent = await _fail(breaker, [target])  # skipped while the trial is in flight
        gated.release.set()

    assert isinstance(concurrent.attempts[0].error, CircuitOpen)
    assert gated.calls == THRESHOLD + 1
    assert len(answers) == 1


async def test_a_cancelled_trial_lets_the_next_request_try(breaker: CircuitBreakerFallback, clock: FakeClock) -> None:
    gated = GatedProvider()
    target = Target("local", gated)
    await _open(breaker, target)
    clock.now += COOLDOWN
    gated.error = None

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(breaker.complete, [target], REQUEST)
        await gated.started.wait()
        tasks.cancel_scope.cancel()

    gated.release.set()
    await breaker.complete([target], REQUEST)

    assert gated.calls == THRESHOLD + 2


async def test_breakers_are_kept_per_target(breaker: CircuitBreakerFallback) -> None:
    await _open(breaker, Target("local", StubProvider("local", error=ProviderUnavailable("down"))))
    other = StubProvider("other")

    await breaker.complete([Target("other", other)], REQUEST)

    assert other.calls == ["other"]


async def test_unexpected_errors_propagate_and_do_not_count(breaker: CircuitBreakerFallback) -> None:
    local = StubProvider("local", error=RuntimeError("bug"))
    target = Target("local", local)

    for _ in range(THRESHOLD + 1):
        with pytest.raises(RuntimeError):
            await breaker.complete([target], REQUEST)

    assert len(local.calls) == THRESHOLD + 1


@pytest.mark.parametrize(("threshold", "cooldown"), [(0, 30.0), (3, 0.0)], ids=["no-threshold", "no-cooldown"])
def test_rejects_invalid_settings(threshold: int, cooldown: float) -> None:
    with pytest.raises(ValueError):
        CircuitBreakerFallback(failure_threshold=threshold, cooldown_seconds=cooldown)
