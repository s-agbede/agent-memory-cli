"""Opt-in integration checks for Redis Cloud Agent Memory."""

import os

import pytest
from redis_agent_memory import AgentMemory

from trip_agent.config import Settings


@pytest.mark.skipif(
    os.getenv("RUN_REDIS_INTEGRATION") != "1",
    reason="set RUN_REDIS_INTEGRATION=1 to call Redis Agent Memory",
)
def test_redis_agent_memory_health() -> None:
    """Verify configured credentials can reach Agent Memory without calling OpenAI."""

    settings = Settings()
    with AgentMemory(
        str(settings.redis_agent_memory_endpoint),
        store_id=settings.redis_agent_memory_store_id,
        api_key=settings.redis_agent_memory_api_key.get_secret_value(),
    ) as memory:
        assert memory.health() is not None
