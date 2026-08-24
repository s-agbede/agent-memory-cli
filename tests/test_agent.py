"""Tests for direct Redis and OpenAI turn coordination."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from openai import OpenAI, OpenAIError
from redis_agent_memory import AgentMemory, models

from trip_agent.agent import (
    AssistantMemoryWarning,
    MemoryView,
    ProfileFact,
    ProfileSaveResult,
    TripAgent,
    TripAgentError,
)


@dataclass(frozen=True, slots=True)
class Call:
    """A recorded fake-client call."""

    name: str
    kwargs: dict[str, object]


class FakeMemory:
    """Small stateful fake shaped like the Redis Agent Memory SDK."""

    def __init__(
        self,
        timeline: list[str],
        fail_add_number: int | None = None,
        fail_profile_write: bool = False,
        profile_errors: int = 0,
        trip_plans: list[object] | None = None,
        profile_exists: bool = False,
    ) -> None:
        self.timeline = timeline
        self.fail_add_number = fail_add_number
        self.fail_profile_write = fail_profile_write
        self.profile_errors = profile_errors
        self.trip_plans = trip_plans or []
        self.profile_exists = profile_exists
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
        request = cast(dict[str, object], kwargs["request"])
        filter_ = cast(dict[str, object], request.get("filter_", {}))
        namespace = cast(dict[str, object], filter_.get("namespace", {}))
        if namespace.get("eq") == "trip-plans":
            return SimpleNamespace(items=self.trip_plans)
        if namespace.get("eq") == "profile":
            return SimpleNamespace(
                items=[SimpleNamespace(text="The traveler is vegetarian.")]
                if self.profile_exists
                else []
            )
        return SimpleNamespace(
            items=[SimpleNamespace(text="Sam is vegetarian.", memory_type="preference")]
        )

    def bulk_create_long_term_memories(self, **kwargs: object) -> object:
        self.timeline.append("memory.bulk_create_long_term_memories")
        self.calls.append(Call("bulk_create_long_term_memories", kwargs))
        if self.fail_profile_write:
            raise httpx.ConnectError("Redis is unavailable")
        records = cast(list[dict[str, object]], kwargs["memories"])
        return SimpleNamespace(
            created=[str(record["id"]) for record in records[self.profile_errors :]],
            errors=[SimpleNamespace() for _ in range(self.profile_errors)],
        )


class FakeResponses:
    def __init__(
        self,
        timeline: list[str],
        text: str,
        error: OpenAIError | None = None,
        texts: list[str] | None = None,
    ) -> None:
        self.timeline = timeline
        self.text = text
        self.error = error
        self.texts = texts or []
        self.kwargs: dict[str, object] | None = None
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.timeline.append("openai.responses.create")
        self.kwargs = kwargs
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        text = self.texts.pop(0) if self.texts else self.text
        return SimpleNamespace(output_text=text, output=[])


class FakeOpenAI:
    def __init__(
        self,
        timeline: list[str],
        text: str = "Kyoto has lovely vegetarian options.",
        error: OpenAIError | None = None,
        texts: list[str] | None = None,
    ) -> None:
        self.responses = FakeResponses(timeline, text, error, texts)


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
    assert request == {
        "text": "Where should I eat?",
        "filter_": {"owner_id": {"eq": "sam"}},
        "limit": 5,
        "similarity_threshold": 0.7,
    }
    assert openai.responses.kwargs is not None
    assert openai.responses.kwargs["tools"] == [{"type": "web_search"}]
    assert str(openai.responses.kwargs["input"]).count("Where should I eat?") == 1
    assert reply.text == "Kyoto has lovely vegetarian options."
    assert reply.memories == (MemoryView(memory_type="preference", text="Sam is vegetarian."),)
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


def test_reply_flags_a_conflicting_dated_trip_before_generating_an_itinerary() -> None:
    timeline: list[str] = []
    memory = FakeMemory(
        timeline,
        trip_plans=[
            SimpleNamespace(text="[trip-plan] destination=Asia | start=2027-05-01 | end=2027-05-31")
        ],
    )
    openai = FakeOpenAI(
        timeline,
        text=(
            '{"is_trip_plan":true,"destination":"Nigeria",'
            '"start_date":"2027-05-01","end_date":"2027-05-31"}'
        ),
    )
    agent = make_agent(memory, openai)

    reply = agent.reply(
        session_id="session-1",
        user_text="Plan me a trip to Nigeria for the entire month of May 2027.",
    )

    assert "overlaps" in reply.text
    assert "Asia" in reply.text
    assert "Nigeria" in reply.text
    assert len(openai.responses.calls) == 1
    assert "tools" not in openai.responses.calls[0]
    assert "memory.bulk_create_long_term_memories" not in timeline
    trip_plan_request = next(
        cast(dict[str, object], call.kwargs["request"])
        for call in memory.calls
        if call.name == "search_long_term_memory"
        and cast(dict[str, object], call.kwargs["request"])["filter_"]
        == {
            "owner_id": {"eq": "sam"},
            "namespace": {"eq": "trip-plans"},
        }
    )
    assert trip_plan_request == {
        "filter_": {
            "owner_id": {"eq": "sam"},
            "namespace": {"eq": "trip-plans"},
        },
        "limit": 100,
    }


def test_reply_saves_a_non_conflicting_dated_trip_for_future_checks() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    openai = FakeOpenAI(
        timeline,
        texts=[
            (
                '{"is_trip_plan":true,"destination":"Asia",'
                '"start_date":"2027-05-01","end_date":"2027-05-31"}'
            ),
            "Here is your Asia itinerary.",
        ],
    )
    agent = make_agent(memory, openai)

    reply = agent.reply(
        session_id="session-1",
        user_text="Plan me a trip to Asia for the month of May 2027.",
    )

    records = cast(list[dict[str, object]], memory.calls[-2].kwargs["memories"])
    assert reply.text == "Here is your Asia itinerary."
    assert records[0]["namespace"] == "trip-plans"
    assert records[0]["text"] == "[trip-plan] destination=Asia | start=2027-05-01 | end=2027-05-31"


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


def test_search_memories_uses_owner_scoped_semantic_request() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    agent = make_agent(memory, FakeOpenAI(timeline))

    rows = agent.search_memories("vegetarian city break", limit=5)

    request = cast(dict[str, object], memory.calls[0].kwargs["request"])
    assert request == {
        "text": "vegetarian city break",
        "filter_": {"owner_id": {"eq": "sam"}},
        "limit": 5,
        "similarity_threshold": 0.7,
    }
    assert [(row.memory_type, row.text) for row in rows] == [("preference", "Sam is vegetarian.")]


def test_has_profile_checks_only_direct_profile_records_for_the_active_owner() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline, profile_exists=True)
    agent = make_agent(memory, FakeOpenAI(timeline))

    assert agent.has_profile() is True

    request = cast(dict[str, object], memory.calls[0].kwargs["request"])
    assert request == {
        "filter_": {
            "owner_id": {"eq": "sam"},
            "namespace": {"eq": "profile"},
        },
        "limit": 1,
    }


def test_search_memories_labels_direct_profile_records() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    memory.search_long_term_memory = lambda **kwargs: SimpleNamespace(
        items=[
            SimpleNamespace(text="Sam is vegetarian.", memory_type="semantic", namespace="profile")
        ]
    )
    agent = make_agent(memory, FakeOpenAI(timeline))

    rows = agent.search_memories("food preferences")

    assert [(row.source, row.text) for row in rows] == [("direct", "Sam is vegetarian.")]


def test_search_memories_preserves_kind_and_derives_provenance_independently() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    memory.search_long_term_memory = lambda **kwargs: SimpleNamespace(
        items=[
            SimpleNamespace(
                text="Vegetarian",
                memory_type="semantic",
                topics=["direct", "dietary"],
            ),
            SimpleNamespace(text="Future trip", memory_type="episodic", namespace="trip-plans"),
            SimpleNamespace(text="Rail travel", memory_type="message", topics=[]),
            SimpleNamespace(text="Custom", memory_type="custom"),
            SimpleNamespace(text="Untyped", memory_type=""),
            SimpleNamespace(text="Missing type"),
        ]
    )
    agent = make_agent(memory, FakeOpenAI(timeline))

    rows = agent.search_memories("travel details")

    assert [(row.memory_type, row.source, row.text) for row in rows] == [
        ("semantic", "direct", "Vegetarian"),
        ("episodic", "direct", "Future trip"),
        ("message", "learned", "Rail travel"),
        ("custom", "learned", "Custom"),
        ("memory", "learned", "Untyped"),
        ("memory", "learned", "Missing type"),
    ]


def test_save_profile_writes_owner_scoped_semantic_records() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    agent = make_agent(memory, FakeOpenAI(timeline))

    result = agent.save_profile(
        (
            ProfileFact(category="dietary", text="Vegetarian"),
            ProfileFact(category="budget", text="Moderate budget"),
        )
    )

    assert timeline == ["memory.bulk_create_long_term_memories"]
    records = cast(list[dict[str, object]], memory.calls[0].kwargs["memories"])
    assert result == ProfileSaveResult(created_count=2, failed_count=0)
    assert [record["text"] for record in records] == ["Vegetarian", "Moderate budget"]
    assert all(record["owner_id"] == "sam" for record in records)
    assert all(record["memory_type"] == "semantic" for record in records)
    assert all(record["namespace"] == "profile" for record in records)
    assert [record["topics"] for record in records] == [
        ["direct", "dietary"],
        ["direct", "budget"],
    ]


def test_rewrite_profile_turns_answers_into_concise_category_preserving_facts() -> None:
    timeline: list[str] = []
    rewritten = (
        '{"preferences":"The traveler prefers quiet coastal places with nature.",'
        '"dietary":"The traveler has no strict dietary needs and enjoys chicken."}'
    )
    openai = FakeOpenAI(timeline, text=rewritten)
    agent = make_agent(FakeMemory(timeline), openai)

    facts = agent.rewrite_profile(
        (
            ProfileFact(category="preferences", text="I like coastal and quiet places with nature"),
            ProfileFact(category="dietary", text="No strict needs, but I love chicken"),
        )
    )

    assert facts == (
        ProfileFact(
            category="preferences", text="The traveler prefers quiet coastal places with nature."
        ),
        ProfileFact(
            category="dietary", text="The traveler has no strict dietary needs and enjoys chicken."
        ),
    )
    assert timeline == ["openai.responses.create"]
    assert openai.responses.kwargs is not None
    assert "tools" not in openai.responses.kwargs


def test_rewrite_profile_rejects_invalid_model_output() -> None:
    timeline: list[str] = []
    agent = make_agent(FakeMemory(timeline), FakeOpenAI(timeline, text="not JSON"))

    with pytest.raises(TripAgentError, match="rewrite your profile answers"):
        agent.rewrite_profile((ProfileFact(category="budget", text="Moderate"),))


def test_save_profile_reports_redis_failure() -> None:
    timeline: list[str] = []
    agent = make_agent(FakeMemory(timeline, fail_profile_write=True), FakeOpenAI(timeline))

    with pytest.raises(TripAgentError, match="long-term travel profile"):
        agent.save_profile((ProfileFact(category="dietary", text="Vegetarian"),))


def test_save_profile_reports_partial_bulk_failure() -> None:
    timeline: list[str] = []
    agent = make_agent(FakeMemory(timeline, profile_errors=1), FakeOpenAI(timeline))

    result = agent.save_profile(
        (
            ProfileFact(category="dietary", text="Vegetarian"),
            ProfileFact(category="budget", text="Moderate budget"),
        )
    )

    assert result == ProfileSaveResult(created_count=1, failed_count=1)
