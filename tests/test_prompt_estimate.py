"""Tests for prompt-token estimates: the plain character estimate and the calibrated one."""

import pytest
import redis.asyncio

from app.estimates import CalibratedEstimator, CharacterEstimator
from app.providers import Message
from app.providers.tokens import estimate_prompt_tokens
from app.routing import Target
from tests.stubs import StubProvider

pytestmark = pytest.mark.anyio

HI = (Message("user", "hi"),)  # 2 characters: 1 token by characters / 4
PLANET = (Message("user", "Name one planet. One word."),)  # 26 characters: 7 tokens
MOCK = Target("mock", StubProvider("mock", reports_real_usage=False))


def _real(model: str) -> Target:
    """A target whose provider reports real token counts, like Ollama."""
    return Target(model, StubProvider("ollama"))


def _conversation(turns: int) -> tuple[Message, ...]:
    return tuple(
        Message("user" if i % 2 == 0 else "assistant", f"message number {i} in a long chat") for i in range(turns)
    )


@pytest.fixture
def estimator(async_redis_db: redis.asyncio.Redis) -> CalibratedEstimator:
    return CalibratedEstimator(async_redis_db, default_overhead=32, per_message_tokens=5)


async def test_mock_keeps_the_exact_character_estimate(estimator: CalibratedEstimator, key: str) -> None:
    await estimator.observe(_real(key), HI, reported_prompt_tokens=26)

    assert await estimator.estimate([MOCK], HI) == 1


async def test_a_new_model_starts_from_the_default_overhead(estimator: CalibratedEstimator, key: str) -> None:
    assert await estimator.estimate([_real(key)], HI) == 1 + 32


async def test_the_first_real_count_replaces_the_default(estimator: CalibratedEstimator, key: str) -> None:
    await estimator.observe(_real(key), HI, reported_prompt_tokens=26)

    assert await estimator.base_overhead(key) == 25
    assert await estimator.estimate([_real(key)], PLANET) == 7 + 25


async def test_a_higher_count_is_adopted_at_once(estimator: CalibratedEstimator, key: str) -> None:
    await estimator.observe(_real(key), HI, reported_prompt_tokens=26)
    await estimator.observe(_real(key), HI, reported_prompt_tokens=31)

    assert await estimator.base_overhead(key) == 30


async def test_a_lower_count_moves_the_overhead_only_a_tenth_of_the_way(
    estimator: CalibratedEstimator, key: str
) -> None:
    await estimator.observe(_real(key), HI, reported_prompt_tokens=31)
    await estimator.observe(_real(key), HI, reported_prompt_tokens=21)

    assert await estimator.base_overhead(key) == pytest.approx(30 - (30 - 20) * 0.1)


async def test_implausibly_low_counts_are_ignored_as_prompt_cache_artifacts(
    estimator: CalibratedEstimator, key: str
) -> None:
    await estimator.observe(_real(key), HI, reported_prompt_tokens=26)
    await estimator.observe(_real(key), HI, reported_prompt_tokens=5)  # only uncached tokens counted

    assert await estimator.base_overhead(key) == 25


async def test_each_extra_message_adds_the_per_message_allowance(estimator: CalibratedEstimator, key: str) -> None:
    await estimator.observe(_real(key), HI, reported_prompt_tokens=26)
    chat = _conversation(3)

    assert await estimator.estimate([_real(key)], chat) == estimate_prompt_tokens(chat) + 25 + 5 * 2


async def test_a_long_conversation_does_not_inflate_a_later_one_message_request(
    async_redis_db: redis.asyncio.Redis, key: str
) -> None:
    chat = _conversation(20)
    # What llama3.2 really reports: a base of 25 plus about 5 template tokens per extra message.
    chat_reported = estimate_prompt_tokens(chat) + 25 + 5 * 19
    estimates = {}
    for per_message_tokens in (0, 5):
        model = f"{key}-per-message-{per_message_tokens}"
        estimator = CalibratedEstimator(async_redis_db, default_overhead=32, per_message_tokens=per_message_tokens)
        await estimator.observe(_real(model), HI, reported_prompt_tokens=26)
        await estimator.observe(_real(model), chat, reported_prompt_tokens=chat_reported)
        estimates[per_message_tokens] = await estimator.estimate([_real(model)], HI)

    print(
        f"\none-message request after a 20-message chat (real: 26 tokens): "
        f"reserves {estimates[0]} without the per-message term, {estimates[5]} with it"
    )
    assert estimates[5] == 26
    assert estimates[0] >= 100


async def test_auto_reserves_for_its_most_expensive_model(estimator: CalibratedEstimator, key: str) -> None:
    await estimator.observe(_real(key), HI, reported_prompt_tokens=26)

    assert await estimator.estimate([_real(key), MOCK], HI) == 26


async def test_instances_share_what_they_learn(async_redis_db: redis.asyncio.Redis, key: str) -> None:
    first = CalibratedEstimator(async_redis_db, default_overhead=32, per_message_tokens=5)
    second = CalibratedEstimator(async_redis_db, default_overhead=32, per_message_tokens=5)

    await first.observe(_real(key), HI, reported_prompt_tokens=26)

    assert await second.estimate([_real(key)], HI) == 26


async def test_counts_from_estimating_providers_are_not_learned(estimator: CalibratedEstimator, key: str) -> None:
    await estimator.observe(Target(key, StubProvider("mock", reports_real_usage=False)), HI, reported_prompt_tokens=1)

    assert await estimator.base_overhead(key) == 32


async def test_the_character_estimator_ignores_overheads() -> None:
    estimator = CharacterEstimator()
    await estimator.observe(_real("any"), HI, reported_prompt_tokens=26)

    assert await estimator.estimate([_real("any")], PLANET) == 7


@pytest.mark.parametrize(
    ("default_overhead", "per_message_tokens"), [(-1, 5), (32, -1)], ids=["negative-default", "negative-per-message"]
)
def test_rejects_negative_settings(default_overhead: int, per_message_tokens: int) -> None:
    with pytest.raises(ValueError):
        CalibratedEstimator(
            redis.asyncio.Redis(), default_overhead=default_overhead, per_message_tokens=per_message_tokens
        )
