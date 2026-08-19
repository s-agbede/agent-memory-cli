"""Tests for direct Redis and OpenAI turn coordination."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from openai import OpenAI, OpenAIError
from redis_agent_memory import AgentMemory, models

from trip_agent.agent import AssistantMemoryWarning, TripAgent, TripAgentError


@dataclass(frozen=True, slots=True)
class Call:
    """A recorded fake-client call."""

    name: str
    kwargs: dict[str, object]


class FakeMemory:
    """Small stateful fake shaped like the Redis Agent Memory SDK."""

    def __init__(self, timeline: list[str], fail_add_number: int | None = None) -> None:
        self.timeline = timeline
        self.fail_add_number = fail_add_number
        self.add_count = 0
        self.calls: list[Call] = []

    def add_session_event(self, **kwargs: object) -> object:
        self.timeline.append("memory.add_session_event")
        self.calls.append(Call("add_session_event", kwargs))
        self.add_count += 1
        if self.add_count == self.fail_add_number:
            raise httpx.ConnectError("Redis is unavailable")
        return SimpleNamespace(event_id=f"event-{self.add_count}")

    def get_session_memory(self, **kwargs: object) -> object:
        self.timeline.append("memory.get_session_memory")
        self.calls.append(Call("get_session_memory", kwargs))
        return SimpleNamespace(
            summary=None,
            events=[
                SimpleNamespace(
                    role=models.MessageRole.USER,
                    content=[SimpleNamespace(text="Where should I eat?")],
                )
            ],
        )

    def search_long_term_memory(self, **kwargs: object) -> object:
        self.timeline.append("memory.search_long_term_memory")
        self.calls.append(Call("search_long_term_memory", kwargs))
        return SimpleNamespace(
            items=[SimpleNamespace(text="Sam is vegetarian.", memory_type="preference")]
        )


class FakeResponses:
    def __init__(
        self,
        timeline: list[str],
        text: str,
        error: OpenAIError | None = None,
    ) -> None:
        self.timeline = timeline
        self.text = text
        self.error = error
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.timeline.append("openai.responses.create")
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_text=self.text, output=[])


class FakeOpenAI:
    def __init__(
        self,
        timeline: list[str],
        text: str = "Kyoto has lovely vegetarian options.",
        error: OpenAIError | None = None,
    ) -> None:
        self.responses = FakeResponses(timeline, text, error)


def make_agent(
    memory: FakeMemory,
    openai: FakeOpenAI,
) -> TripAgent:
    return TripAgent(
        memory=cast(AgentMemory, memory),
        openai=cast(OpenAI, openai),
        model="gpt-5.6-luna",
        user_id="sam",
    )


def test_reply_stores_user_loads_context_calls_web_search_and_stores_assistant() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    openai = FakeOpenAI(timeline)
    agent = make_agent(memory, openai)

    reply = agent.reply(session_id="session-1", user_text="Where should I eat?")

    assert timeline == [
        "memory.add_session_event",
        "memory.get_session_memory",
        "memory.search_long_term_memory",
        "openai.responses.create",
        "memory.add_session_event",
    ]
    assert memory.calls[0].kwargs["session_id"] == "session-1"
    assert memory.calls[0].kwargs["actor_id"] == "sam"
    assert memory.calls[0].kwargs["role"] is models.MessageRole.USER
    request = cast(dict[str, object], memory.calls[2].kwargs["request"])
    assert request["filter_"] == {"owner_id": {"eq": "sam"}}
    assert request["limit"] == 5
    assert openai.responses.kwargs is not None
    assert openai.responses.kwargs["tools"] == [{"type": "web_search"}]
    assert str(openai.responses.kwargs["input"]).count("Where should I eat?") == 1
    assert reply.text == "Kyoto has lovely vegetarian options."
    assert memory.calls[-1].kwargs["role"] is models.MessageRole.ASSISTANT


def test_failed_user_event_prevents_openai_call() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline, fail_add_number=1)
    agent = make_agent(memory, FakeOpenAI(timeline))

    with pytest.raises(TripAgentError, match="save your message"):
        agent.reply(session_id="session-1", user_text="Help me plan")

    assert timeline == ["memory.add_session_event"]


def test_failed_assistant_event_preserves_generated_reply() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline, fail_add_number=2)
    agent = make_agent(memory, FakeOpenAI(timeline, text="Here is your plan."))

    with pytest.raises(AssistantMemoryWarning) as caught:
        agent.reply(session_id="session-1", user_text="Help me plan")

    assert caught.value.reply.text == "Here is your plan."
    assert timeline[-1] == "memory.add_session_event"


def test_openai_failure_does_not_store_assistant_event() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    agent = make_agent(memory, FakeOpenAI(timeline, error=OpenAIError("OpenAI is down")))

    with pytest.raises(TripAgentError, match="OpenAI"):
        agent.reply(session_id="session-1", user_text="Help me plan")

    assert [call.name for call in memory.calls] == [
        "add_session_event",
        "get_session_memory",
        "search_long_term_memory",
    ]
