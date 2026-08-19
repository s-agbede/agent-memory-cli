"""Tests for environment configuration."""

import pytest
from pydantic import ValidationError

from trip_agent.config import Settings

REQUIRED = {
    "OPENAI_API_KEY": "openai-secret",
    "REDIS_AGENT_MEMORY_ENDPOINT": "https://memory.example.com",
    "REDIS_AGENT_MEMORY_STORE_ID": "store-123",
    "REDIS_AGENT_MEMORY_API_KEY": "redis-secret",
}


def test_settings_load_required_values_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert settings.openai_model == "gpt-5.6-luna"
    assert settings.trip_agent_user_id == "traveler"
    assert str(settings.redis_agent_memory_endpoint) == "https://memory.example.com/"


def test_settings_require_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
