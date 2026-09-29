"""Application settings, read from environment variables and an optional .env file."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    Field,
    HttpUrl,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    RedisDsn,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    default_max_tokens: PositiveInt = 256
    mock_latency_seconds: NonNegativeFloat = 0.0

    ollama_base_url: HttpUrl = HttpUrl("http://localhost:11434")
    ollama_model: str = Field(default="llama3.2:3b", min_length=1)
    ollama_connect_timeout_seconds: PositiveFloat = 2.0
    ollama_read_timeout_seconds: PositiveFloat = 120.0
    auto_fallback: Annotated[tuple[str, ...], NoDecode] = ()

    # Each optimized piece keeps its brute-force predecessor selectable, for comparison.
    prompt_estimate: Literal["calibrated", "characters"] = "calibrated"
    prompt_overhead_default: NonNegativeInt = 32
    per_message_tokens: NonNegativeInt = 5
    token_budget: Literal["token_bucket", "fixed_window"] = "token_bucket"
    fallback_strategy: Literal["circuit_breaker", "sequential"] = "circuit_breaker"
    breaker_failure_threshold: PositiveInt = 3
    breaker_cooldown_seconds: PositiveFloat = 30.0

    @field_validator("auto_fallback", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(name.strip() for name in value.split(",") if name.strip())
        return value

    @property
    def auto_chain(self) -> tuple[str, ...]:
        """The models "auto" tries, in order: AUTO_FALLBACK if set, else the Ollama model and then mock."""
        return self.auto_fallback or (self.ollama_model, "mock")


@lru_cache
def get_settings() -> Settings:
    return Settings()
