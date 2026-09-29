"""Request and response bodies for the subset of the OpenAI API the gateway supports."""

from typing import Literal

from pydantic import BaseModel, Field, PositiveInt

from app.providers.base import FinishReason, Role


class ChatMessage(BaseModel):
    role: Role
    content: str


class ChatCompletionRequest(BaseModel):
    """Other OpenAI fields, such as temperature, are accepted and ignored."""

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    max_tokens: PositiveInt | None = None
    max_completion_tokens: PositiveInt | None = None
    stream: bool = False


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class Choice(BaseModel):
    index: int
    message: AssistantMessage
    finish_reason: FinishReason
    logprobs: None = None


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletion(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


class Model(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[Model]
