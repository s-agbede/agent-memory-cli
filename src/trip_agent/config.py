"""Environment configuration for the trip agent."""

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr
    openai_model: str = "gpt-5.6-luna"
    redis_agent_memory_endpoint: AnyHttpUrl
    redis_agent_memory_store_id: str = Field(min_length=1)
    redis_agent_memory_api_key: SecretStr
    trip_agent_user_id: str = Field(default="traveler", min_length=1)
