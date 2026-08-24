"""Tests for direct Redis and OpenAI turn coordination."""

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import NAMESPACE_URL, uuid5

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
        profile_items: list[object] | None = None,
        fail_profile_update_ids: set[str] | None = None,
        fail_profile_lookup: bool = False,
        unaccounted_profile_categories: set[str] | None = None,
        profile_error_categories: set[str] | None = None,
    ) -> None:
        self.timeline = timeline
        self.fail_add_number = fail_add_number
        self.fail_profile_write = fail_profile_write
        self.profile_errors = profile_errors
        self.trip_plans = trip_plans or []
        self.profile_exists = profile_exists
        self.profile_items = profile_items
        self.fail_profile_update_ids = fail_profile_update_ids or set()
        self.fail_profile_lookup = fail_profile_lookup
        self.unaccounted_profile_categories = unaccounted_profile_categories or set()
        self.profile_error_categories = profile_error_categories
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
            if self.fail_profile_lookup:
                raise httpx.ConnectError("Redis is unavailable")
            if self.profile_items is not None:
                return SimpleNamespace(items=self.profile_items)
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
        error_records = (
            [
                record
                for record in records
                if self.profile_error_categories.intersection(cast(list[str], record["topics"]))
            ]
            if self.profile_error_categories is not None
            else records[: self.profile_errors]
        )
        error_ids = {str(record["id"]) for record in error_records}
        created_records = [
            record
            for record in records
            if str(record["id"]) not in error_ids
            if not self.unaccounted_profile_categories.intersection(
                cast(list[str], record["topics"])
            )
        ]
        return SimpleNamespace(
            created=[str(record["id"]) for record in created_records],
            errors=[
                SimpleNamespace(id=str(record["id"]), error="write failed")
                for record in error_records
            ],
        )

    def update_long_term_memory(self, **kwargs: object) -> object:
        self.timeline.append("memory.update_long_term_memory")
        self.calls.append(Call("update_long_term_memory", kwargs))
        if kwargs["memory_id"] in self.fail_profile_update_ids:
            raise httpx.ConnectError("Redis is unavailable")
        return SimpleNamespace()


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


def test_save_profile_empty_input_returns_empty_categories_without_redis_call() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    agent = make_agent(memory, FakeOpenAI(timeline))

    result = agent.save_profile(())

    assert result == ProfileSaveResult(
        created_categories=(), updated_categories=(), failed_categories=()
    )
    assert memory.calls == []


def test_save_profile_looks_up_the_owner_profile_once_with_filters_only() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    agent = make_agent(memory, FakeOpenAI(timeline))

    result = agent.save_profile(
        (
            ProfileFact(category="dietary", text="Vegetarian"),
            ProfileFact(category="budget", text="Moderate budget"),
        )
    )

    assert timeline == [
        "memory.search_long_term_memory",
        "memory.bulk_create_long_term_memories",
    ]
    assert memory.calls[0].kwargs == {
        "request": {
            "filter_": {
                "owner_id": {"eq": "sam"},
                "namespace": {"eq": "profile"},
            }
        }
    }
    records = cast(list[dict[str, object]], memory.calls[1].kwargs["memories"])
    assert result == ProfileSaveResult(
        created_categories=("dietary", "budget"),
        updated_categories=(),
        failed_categories=(),
    )
    assert [record["text"] for record in records] == ["Vegetarian", "Moderate budget"]
    assert all(record["owner_id"] == "sam" for record in records)
    assert all(record["memory_type"] == "semantic" for record in records)
    assert all(record["namespace"] == "profile" for record in records)
    assert [record["topics"] for record in records] == [
        ["direct", "dietary"],
        ["direct", "budget"],
    ]


def test_save_profile_creates_missing_categories_with_deterministic_ids_in_one_bulk_call() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline)
    agent = make_agent(memory, FakeOpenAI(timeline))

    result = agent.save_profile(
        (
            ProfileFact(category="origin", text="London"),
            ProfileFact(category="preferences", text="Quiet places"),
        )
    )

    bulk_calls = [call for call in memory.calls if call.name == "bulk_create_long_term_memories"]
    assert len(bulk_calls) == 1
    records = cast(list[dict[str, object]], bulk_calls[0].kwargs["memories"])
    assert [record["id"] for record in records] == [
        str(uuid5(NAMESPACE_URL, "profile:sam:origin")),
        str(uuid5(NAMESPACE_URL, "profile:sam:preferences")),
    ]
    assert result.created_categories == ("origin", "preferences")


def test_save_profile_updates_an_existing_category_using_its_legacy_random_id() -> None:
    timeline: list[str] = []
    memory = FakeMemory(
        timeline,
        profile_items=[
            SimpleNamespace(
                id="random-legacy-id",
                topics=["direct", "dietary"],
                updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        ],
    )
    agent = make_agent(memory, FakeOpenAI(timeline))

    result = agent.save_profile((ProfileFact(category="dietary", text="No shellfish"),))

    assert result == ProfileSaveResult(
        created_categories=(),
        updated_categories=("dietary",),
        failed_categories=(),
    )
    assert [call.name for call in memory.calls] == [
        "search_long_term_memory",
        "update_long_term_memory",
    ]
    assert memory.calls[1].kwargs == {
        "memory_id": "random-legacy-id",
        "text": "No shellfish",
        "memory_type": "semantic",
        "topics": ["direct", "dietary"],
        "namespace": "profile",
        "owner_id": "sam",
    }


def test_save_profile_updates_the_newest_duplicate_category_record() -> None:
    timeline: list[str] = []
    memory = FakeMemory(
        timeline,
        profile_items=[
            SimpleNamespace(
                id="newest-id",
                topics=["direct", "preferences"],
                updated_at=datetime(2026, 5, 1, tzinfo=UTC),
            ),
            SimpleNamespace(
                id="older-id",
                topics=["direct", "preferences"],
                updated_at=datetime(2025, 5, 1, tzinfo=UTC),
            ),
        ],
    )
    agent = make_agent(memory, FakeOpenAI(timeline))

    agent.save_profile((ProfileFact(category="preferences", text="Quiet places"),))

    update_calls = [call for call in memory.calls if call.name == "update_long_term_memory"]
    assert len(update_calls) == 1
    assert update_calls[0].kwargs["memory_id"] == "newest-id"


def test_save_profile_uses_stable_first_match_for_missing_or_malformed_timestamps() -> None:
    timeline: list[str] = []
    memory = FakeMemory(
        timeline,
        profile_items=[
            SimpleNamespace(id="dietary-first", topics=["direct", "dietary"]),
            SimpleNamespace(
                id="dietary-second", topics=["direct", "dietary"], updated_at="not-a-date"
            ),
            SimpleNamespace(
                id="budget-first", topics=["direct", "budget"], updated_at="also-not-a-date"
            ),
            SimpleNamespace(id="budget-second", topics=["direct", "budget"]),
        ],
    )
    agent = make_agent(memory, FakeOpenAI(timeline))

    result = agent.save_profile(
        (
            ProfileFact(category="dietary", text="Vegetarian"),
            ProfileFact(category="budget", text="Moderate"),
        )
    )

    update_calls = [call for call in memory.calls if call.name == "update_long_term_memory"]
    assert [call.kwargs["memory_id"] for call in update_calls] == [
        "dietary-first",
        "budget-first",
    ]
    assert result.updated_categories == ("dietary", "budget")


def test_save_profile_reports_mixed_update_and_bulk_failures_in_fact_order() -> None:
    timeline: list[str] = []
    memory = FakeMemory(
        timeline,
        profile_error_categories={"origin"},
        fail_profile_update_ids={"preferences-id"},
        profile_items=[
            SimpleNamespace(
                id="preferences-id",
                topics=["direct", "preferences"],
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            SimpleNamespace(
                id="dietary-id",
                topics=["direct", "dietary"],
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ],
    )
    agent = make_agent(memory, FakeOpenAI(timeline))

    result = agent.save_profile(
        (
            ProfileFact(category="budget", text="Moderate"),
            ProfileFact(category="preferences", text="Quiet places"),
            ProfileFact(category="origin", text="London"),
            ProfileFact(category="dietary", text="Vegetarian"),
        )
    )

    assert result == ProfileSaveResult(
        created_categories=("budget",),
        updated_categories=("dietary",),
        failed_categories=("preferences", "origin"),
    )
    assert [call.name for call in memory.calls].count("bulk_create_long_term_memories") == 1


def test_save_profile_counts_unaccounted_bulk_creates_as_failed() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline, unaccounted_profile_categories={"budget"})
    agent = make_agent(memory, FakeOpenAI(timeline))

    result = agent.save_profile(
        (
            ProfileFact(category="dietary", text="Vegetarian"),
            ProfileFact(category="budget", text="Moderate"),
        )
    )

    assert result == ProfileSaveResult(
        created_categories=("dietary",),
        updated_categories=(),
        failed_categories=("budget",),
    )


def test_save_profile_does_not_touch_existing_omitted_categories() -> None:
    timeline: list[str] = []
    memory = FakeMemory(
        timeline,
        profile_items=[
            SimpleNamespace(
                id="dietary-id",
                topics=["direct", "dietary"],
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            SimpleNamespace(
                id="budget-id",
                topics=["direct", "budget"],
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ],
    )
    agent = make_agent(memory, FakeOpenAI(timeline))

    agent.save_profile((ProfileFact(category="dietary", text="Vegetarian"),))

    update_calls = [call for call in memory.calls if call.name == "update_long_term_memory"]
    assert [call.kwargs["memory_id"] for call in update_calls] == ["dietary-id"]
    assert "memory.bulk_create_long_term_memories" not in timeline


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


def test_save_profile_reports_lookup_failure_before_any_write() -> None:
    timeline: list[str] = []
    memory = FakeMemory(timeline, fail_profile_lookup=True)
    agent = make_agent(memory, FakeOpenAI(timeline))

    with pytest.raises(TripAgentError, match="load your long-term travel profile"):
        agent.save_profile((ProfileFact(category="dietary", text="Vegetarian"),))

    assert timeline == ["memory.search_long_term_memory"]


def test_save_profile_reports_partial_bulk_failure() -> None:
    timeline: list[str] = []
    agent = make_agent(FakeMemory(timeline, profile_errors=1), FakeOpenAI(timeline))

    result = agent.save_profile(
        (
            ProfileFact(category="dietary", text="Vegetarian"),
            ProfileFact(category="budget", text="Moderate budget"),
        )
    )

    assert result == ProfileSaveResult(
        created_categories=("budget",),
        updated_categories=(),
        failed_categories=("dietary",),
    )
