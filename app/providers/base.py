"""Contract between the gateway and the LLM backends it forwards requests to."""

from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "developer", "user", "assistant"]
FinishReason = Literal["stop", "length"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    model: str
    messages: tuple[Message, ...]
    max_tokens: int


@dataclass(frozen=True, slots=True)
class Completion:
    content: str
    finish_reason: FinishReason
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ProviderError(Exception):
    """The backend could not produce a completion. Used as-is when it answered with something broken."""


class ProviderUnavailable(ProviderError):
    """The backend cannot be reached, or does not have the requested model."""


class ProviderTimeout(ProviderError):
    """The backend accepted the request but did not answer in time."""


class Provider(Protocol):
    name: str
    """Short backend name reported to clients, such as "mock" or "ollama"."""

    reports_real_usage: bool
    """Whether usage comes from the model's own tokenizer, rather than from our estimate."""

    async def complete(self, request: CompletionRequest) -> Completion:
        """Generate one reply of at most ``request.max_tokens`` tokens. Raises ``ProviderError``."""
        ...

    async def is_available(self, model: str) -> bool:
        """Whether ``model`` can be served right now. Never raises."""
        ...
