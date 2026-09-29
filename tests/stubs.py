"""Test doubles shared by several test modules."""

from app.providers import Completion, CompletionRequest


class StubProvider:
    """Answers with a fixed reply or raises a fixed error, and records which models it was asked for."""

    def __init__(
        self,
        name: str,
        *,
        error: Exception | None = None,
        available: bool = True,
        reports_real_usage: bool = True,
    ) -> None:
        self.name = name
        self.error = error
        self.available = available
        self.reports_real_usage = reports_real_usage
        self.calls: list[str] = []

    async def complete(self, request: CompletionRequest) -> Completion:
        self.calls.append(request.model)
        if self.error is not None:
            raise self.error
        return Completion(content=f"{self.name} reply", finish_reason="stop", prompt_tokens=1, completion_tokens=1)

    async def is_available(self, model: str) -> bool:
        return self.available
