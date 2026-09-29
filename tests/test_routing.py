"""Tests for the model registry and the brute-force fallback strategy."""

from collections.abc import Mapping, Sequence

import pytest

from app.providers import Completion, CompletionRequest, Message, Provider, ProviderError, ProviderUnavailable
from app.routing import AllTargetsFailed, ListedModel, ModelRegistry, SequentialFallback, Target

REQUEST = CompletionRequest(model="auto", messages=(Message("user", "hi"),), max_tokens=8)


class StubProvider:
    """Answers with a fixed reply or raises a fixed error, and records which models it was asked for."""

    def __init__(self, name: str, *, error: Exception | None = None, available: bool = True) -> None:
        self.name = name
        self.error = error
        self.available = available
        self.calls: list[str] = []

    async def complete(self, request: CompletionRequest) -> Completion:
        self.calls.append(request.model)
        if self.error is not None:
            raise self.error
        return Completion(content=f"{self.name} reply", finish_reason="stop", prompt_tokens=1, completion_tokens=1)

    async def is_available(self, model: str) -> bool:
        return self.available


def _registry(local: StubProvider, mock: StubProvider) -> ModelRegistry:
    return ModelRegistry(models={"mock": mock, "local": local}, aliases={"auto": ["local", "mock"]})


def test_a_specific_model_resolves_to_only_itself() -> None:
    local, mock = StubProvider("local"), StubProvider("mock")

    assert _registry(local, mock).resolve("local") == (Target("local", local),)


def test_an_alias_resolves_to_its_chain_in_order() -> None:
    local, mock = StubProvider("local"), StubProvider("mock")
    registry = _registry(local, mock)

    assert registry.resolve("auto") == (Target("local", local), Target("mock", mock))
    assert registry.is_alias("auto")
    assert not registry.is_alias("mock")


def test_an_unknown_name_resolves_to_nothing() -> None:
    assert _registry(StubProvider("local"), StubProvider("mock")).resolve("gpt-4o") is None


@pytest.mark.parametrize(
    "aliases",
    [{"auto": ["missing"]}, {"mock": ["mock"]}, {"auto": []}],
    ids=["unknown-member", "clashes-with-a-model", "empty-chain"],
)
def test_rejects_an_inconsistent_configuration(aliases: Mapping[str, Sequence[str]]) -> None:
    models: Mapping[str, Provider] = {"mock": StubProvider("mock")}

    with pytest.raises(ValueError):
        ModelRegistry(models=models, aliases=aliases)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("local_available", "expected"),
    [
        (True, [ListedModel("mock", "mock"), ListedModel("local", "local"), ListedModel("auto", "gateway")]),
        (False, [ListedModel("mock", "mock"), ListedModel("auto", "gateway")]),
    ],
)
async def test_listing_shows_only_available_models(local_available: bool, expected: list[ListedModel]) -> None:
    registry = _registry(StubProvider("local", available=local_available), StubProvider("mock"))

    assert await registry.available_models() == expected


@pytest.mark.anyio
async def test_an_alias_is_listed_only_while_one_of_its_models_is_available() -> None:
    registry = _registry(StubProvider("local", available=False), StubProvider("mock", available=False))

    assert await registry.available_models() == []


@pytest.mark.anyio
async def test_the_first_target_that_answers_wins() -> None:
    local, mock = StubProvider("local"), StubProvider("mock")

    answer = await SequentialFallback().complete(_registry(local, mock).resolve("auto") or (), REQUEST)

    assert answer.target.model == "local"
    assert answer.completion.content == "local reply"
    assert mock.calls == []


@pytest.mark.anyio
async def test_a_failing_target_falls_through_to_the_next_one() -> None:
    local, mock = StubProvider("local", error=ProviderUnavailable("down")), StubProvider("mock")

    answer = await SequentialFallback().complete(_registry(local, mock).resolve("auto") or (), REQUEST)

    assert answer.target.model == "mock"
    assert (local.calls, mock.calls) == (["local"], ["mock"])


@pytest.mark.anyio
async def test_every_target_failing_reports_each_attempt_in_order() -> None:
    local = StubProvider("local", error=ProviderUnavailable("down"))
    mock = StubProvider("mock", error=ProviderError("broken"))

    with pytest.raises(AllTargetsFailed) as failed:
        await SequentialFallback().complete(_registry(local, mock).resolve("auto") or (), REQUEST)

    assert [(a.target.model, type(a.error)) for a in failed.value.attempts] == [
        ("local", ProviderUnavailable),
        ("mock", ProviderError),
    ]


@pytest.mark.anyio
async def test_unexpected_errors_are_not_hidden_by_falling_back() -> None:
    local, mock = StubProvider("local", error=RuntimeError("bug")), StubProvider("mock")

    with pytest.raises(RuntimeError):
        await SequentialFallback().complete(_registry(local, mock).resolve("auto") or (), REQUEST)

    assert mock.calls == []


@pytest.mark.anyio
async def test_every_request_walks_the_chain_from_the_top() -> None:
    local, mock = StubProvider("local", error=ProviderUnavailable("down")), StubProvider("mock")
    targets = _registry(local, mock).resolve("auto") or ()
    fallback = SequentialFallback()

    await fallback.complete(targets, REQUEST)
    await fallback.complete(targets, REQUEST)

    assert local.calls == ["local", "local"]
