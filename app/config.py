"""Application settings, read from environment variables and an optional .env file."""

from functools import lru_cache

from pydantic import NonNegativeFloat, PositiveInt, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    default_max_tokens: PositiveInt = 256
    mock_latency_seconds: NonNegativeFloat = 0.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
