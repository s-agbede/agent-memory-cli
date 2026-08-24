"""Environment configuration for the trip agent."""

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
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

    @field_validator(
        "openai_model",
        "redis_agent_memory_store_id",
        "trip_agent_user_id",
    )
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        """Trim and reject text configuration containing only whitespace."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("redis_agent_memory_endpoint")
    @classmethod
    def require_https_endpoint(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Require TLS for the Agent Memory service endpoint."""

        if value.scheme != "https":
            raise ValueError("must use https")
        return value

    @field_validator("openai_api_key", "redis_agent_memory_api_key")
    @classmethod
    def validate_non_blank_secret(cls, value: SecretStr) -> SecretStr:
        """Trim and reject blank credentials without exposing their values."""

        stripped = value.get_secret_value().strip()
        if not stripped:
            raise ValueError("must not be blank")
        return SecretStr(stripped)
