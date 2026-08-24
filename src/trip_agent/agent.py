"""Direct Redis Agent Memory and OpenAI turn coordination."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast
from uuid import NAMESPACE_URL, uuid5

import httpx
from openai import OpenAI, OpenAIError
from redis_agent_memory import AgentMemory, errors, models
from redis_agent_memory.models.creatememoryrecord import CreateMemoryRecordTypedDict

from trip_agent.formatting import Citation, build_model_input, extract_citations
from trip_agent.prompt import SYSTEM_PROMPT


@dataclass(frozen=True, slots=True)
class MemoryView:
    """A display-ready long-term memory."""

    memory_type: str
    text: str
    source: str = "learned"


@dataclass(frozen=True, slots=True)
class AgentReply:
    """A generated answer, citations, and long-term memories retrieved for it."""

    text: str
    citations: tuple[Citation, ...]
    memories: tuple[MemoryView, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileFact:
    """One explicit traveler preference collected during onboarding."""

    category: str
    text: str


@dataclass(frozen=True, slots=True)
class ProfileSaveResult:
    """Profile categories created, updated, or not saved."""

    created_categories: tuple[str, ...]
    updated_categories: tuple[str, ...]
    failed_categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _MemoryContext:
    """Combined Redis records supplied to one model turn."""

    items: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class TripPlan:
    """A dated future trip used for deterministic conflict detection."""

    destination: str
    start_date: date
    end_date: date

    def overlaps(self, other: "TripPlan") -> bool:
        """Return whether this trip shares one or more calendar days with another."""

        return self.start_date <= other.end_date and other.start_date <= self.end_date

    def matches(self, other: "TripPlan") -> bool:
        """Return whether this plan has the deterministic direct-memory identity of another."""

        return (
            self.destination.casefold() == other.destination.casefold()
            and self.start_date == other.start_date
            and self.end_date == other.end_date
        )

    def memory_text(self) -> str:
        """Serialize a plan into the canonical long-term-memory representation."""

        return (
            f"[trip-plan] destination={self.destination} | start={self.start_date.isoformat()} "
            f"| end={self.end_date.isoformat()}"
        )


class TripAgentError(RuntimeError):
    """A conversational turn failed before an answer was generated."""


class AssistantMemoryWarning(RuntimeError):
    """An answer was generated but could not be stored in session memory."""

    def __init__(self, reply: AgentReply) -> None:
        super().__init__("The answer was generated but could not be saved to session memory.")
        self.reply = reply


MemoryRequest = models.SearchLongTermMemoryRequestContentTypedDict
MEMORY_EXCEPTIONS = (errors.AgentMemoryError, errors.NoResponseError, httpx.RequestError)
MEMORY_SIMILARITY_THRESHOLD = 0.7
PROFILE_CATEGORIES = ("preferences", "dietary", "budget", "origin")
PROFILE_FACT_PREFIXES = {
    "preferences": "The traveler prefers ",
    "dietary": "The traveler's food and dietary preferences are: ",
    "budget": "The traveler's typical trip budget is ",
    "origin": "The traveler's usual departure city is ",
}


class TripAgent:
    """Generate trip advice using Redis context and OpenAI web search."""

    def __init__(self, memory: AgentMemory, openai: OpenAI, model: str, user_id: str) -> None:
        self.memory = memory
        self.openai = openai
        self.model = model
        self.user_id = user_id

    def set_user(self, user_id: str) -> None:
        """Use the existing SDK clients for a different active traveler."""

        self.user_id = user_id

    def reply(self, session_id: str, user_text: str) -> AgentReply:
        """Generate and store one conversational turn."""

        self._add_event(
            session_id=session_id,
            actor_id=self.user_id,
            role=models.MessageRole.USER,
            text=user_text,
            failure_message="I couldn't save your message to Redis Agent Memory.",
        )

        try:
            session = self.memory.get_session_memory(session_id=session_id)
            profile = self.memory.search_long_term_memory(request=self._profile_request(limit=100))
            recalled = self.memory.search_long_term_memory(
                request=self._memory_request(user_text, limit=5)
            )
            memories = _merge_memory_results(profile, recalled)
        except MEMORY_EXCEPTIONS as error:
            raise TripAgentError("I couldn't load your Redis Agent Memory context.") from error

        proposed_plan = (
            self._extract_trip_plan(user_text) if _may_describe_dated_trip(user_text) else None
        )
        if proposed_plan is not None:
            existing_plans = self._trip_plans()
            matches = tuple(plan for plan in existing_plans if plan.matches(proposed_plan))
            conflicts = tuple(
                plan
                for plan in existing_plans
                if not plan.matches(proposed_plan) and plan.overlaps(proposed_plan)
            )
            if conflicts:
                reply = AgentReply(
                    text=_conflict_message(proposed_plan, conflicts[0]),
                    citations=(),
                    memories=_memory_views(memories),
                )
                try:
                    self._add_event(
                        session_id=session_id,
                        actor_id="trip-agent",
                        role=models.MessageRole.ASSISTANT,
                        text=reply.text,
                        failure_message="I couldn't save the answer to Redis Agent Memory.",
                    )
                except TripAgentError as error:
                    raise AssistantMemoryWarning(reply) from error
                return reply
            if not matches:
                self._save_trip_plan(proposed_plan)

        try:
            response = self.openai.responses.create(
                model=self.model,
                instructions=SYSTEM_PROMPT,
                tools=[{"type": "web_search"}],
                input=build_model_input(session, memories),
            )
        except OpenAIError as error:
            raise TripAgentError("I couldn't get a response from OpenAI.") from error

        if not response.output_text.strip():
            raise TripAgentError("OpenAI returned an empty response.")

        reply = AgentReply(
            text=response.output_text,
            citations=extract_citations(response),
            memories=_memory_views(memories),
        )
        try:
            self._add_event(
                session_id=session_id,
                actor_id="trip-agent",
                role=models.MessageRole.ASSISTANT,
                text=reply.text,
                failure_message="I couldn't save the answer to Redis Agent Memory.",
            )
        except TripAgentError as error:
            raise AssistantMemoryWarning(reply) from error
        return reply

    def search_memories(self, query: str, limit: int = 10) -> tuple[MemoryView, ...]:
        """Search this traveler's long-term memories for display."""

        try:
            result = self.memory.search_long_term_memory(
                request=self._memory_request(query, limit=limit)
            )
        except MEMORY_EXCEPTIONS as error:
            raise TripAgentError("I couldn't search your Redis Agent Memory data.") from error

        return _memory_views(result)

    def browse_memories(self, limit: int = 100) -> tuple[MemoryView, ...]:
        """Browse this traveler's long-term memories without semantic ranking."""

        request = cast(
            MemoryRequest,
            {
                "filter_": {"owner_id": {"eq": self.user_id}},
                "limit": limit,
            },
        )
        try:
            result = self.memory.search_long_term_memory(request=request)
        except MEMORY_EXCEPTIONS as error:
            raise TripAgentError("I couldn't browse your Redis Agent Memory data.") from error

        return _memory_views(result)

    def has_profile(self) -> bool:
        """Return whether this traveler has at least one direct onboarding record."""

        try:
            result = self.memory.search_long_term_memory(request=self._profile_request(limit=1))
        except MEMORY_EXCEPTIONS as error:
            raise TripAgentError("I couldn't check your saved travel profile.") from error
        return bool(result.items)

    def rewrite_profile(self, facts: Sequence[ProfileFact]) -> tuple[ProfileFact, ...]:
        """Turn explicit profile answers into concise, durable memory statements."""

        if not facts:
            return ()

        categories = [fact.category for fact in facts]
        try:
            response = self.openai.responses.create(
                model=self.model,
                instructions=(
                    "Normalize each explicit travel-profile answer into one concise value phrase. "
                    "Preserve meaning, uncertainty, and every qualification. Do not infer, add, "
                    "omit, advise, or make any preference stronger. For origin, preserve the "
                    "traveler's usual departure place, not their residence, birthplace, or "
                    "nationality. Do not include 'The traveler' or a category label in a value. "
                    "Return only a JSON object whose keys are exactly the supplied categories "
                    "and whose values are the normalized value phrases."
                ),
                input=json.dumps(
                    {"answers": [{"category": fact.category, "text": fact.text} for fact in facts]}
                ),
            )
        except OpenAIError as error:
            raise TripAgentError("I couldn't rewrite your profile answers.") from error

        try:
            output = json.loads(response.output_text)
            if not isinstance(output, dict) or set(output) != set(categories):
                raise ValueError("The response did not contain the expected categories.")
            rewritten = tuple(
                ProfileFact(
                    category=fact.category,
                    text=_canonical_profile_text(fact.category, output[fact.category]),
                )
                for fact in facts
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise TripAgentError("I couldn't rewrite your profile answers.") from error
        return rewritten

    def save_profile(self, facts: Sequence[ProfileFact]) -> ProfileSaveResult:
        """Create missing profile categories and update existing categories in place."""

        if not facts:
            return ProfileSaveResult(
                created_categories=(), updated_categories=(), failed_categories=()
            )
        _validate_profile_facts(facts)

        try:
            profile = self.memory.search_long_term_memory(request=self._profile_request(limit=100))
        except MEMORY_EXCEPTIONS as error:
            raise TripAgentError("I couldn't load your long-term travel profile.") from error

        existing_by_category = _latest_profile_record_ids(profile.items)
        updated_categories: list[str] = []
        failed_categories: list[str] = []
        missing_facts: list[ProfileFact] = []
        for fact in facts:
            existing_id = existing_by_category.get(fact.category)
            if existing_id is None:
                missing_facts.append(fact)
                continue
            try:
                self.memory.update_long_term_memory(
                    memory_id=existing_id,
                    text=fact.text,
                    memory_type="semantic",
                    topics=["direct", fact.category],
                    namespace="profile",
                    owner_id=self.user_id,
                )
            except MEMORY_EXCEPTIONS:
                failed_categories.append(fact.category)
            else:
                if self._profile_write_is_readable(existing_id, fact):
                    updated_categories.append(fact.category)
                else:
                    failed_categories.append(fact.category)

        records: list[CreateMemoryRecordTypedDict] = [
            {
                "id": str(uuid5(NAMESPACE_URL, f"profile:{self.user_id}:{fact.category}")),
                "text": fact.text,
                "owner_id": self.user_id,
                "memory_type": "semantic",
                "namespace": "profile",
                "topics": ["direct", fact.category],
            }
            for fact in missing_facts
        ]
        created_categories: list[str] = []
        if records:
            try:
                result = self.memory.bulk_create_long_term_memories(memories=records)
            except MEMORY_EXCEPTIONS:
                failed_categories.extend(fact.category for fact in missing_facts)
            else:
                created_ids = set(result.created)
                error_ids = {error.id for error in result.errors or ()}
                for fact, record in zip(missing_facts, records, strict=True):
                    memory_id = record["id"]
                    if (
                        memory_id in created_ids
                        and memory_id not in error_ids
                        and self._profile_write_is_readable(memory_id, fact)
                    ):
                        created_categories.append(fact.category)
                    else:
                        failed_categories.append(fact.category)

        created = set(created_categories)
        updated = set(updated_categories)
        failed = set(failed_categories)
        return ProfileSaveResult(
            created_categories=tuple(fact.category for fact in facts if fact.category in created),
            updated_categories=tuple(fact.category for fact in facts if fact.category in updated),
            failed_categories=tuple(fact.category for fact in facts if fact.category in failed),
        )

    def _memory_request(self, text: str, limit: int) -> MemoryRequest:
        request = {
            "text": text,
            "filter_": {"owner_id": {"eq": self.user_id}},
            "limit": limit,
            "similarity_threshold": MEMORY_SIMILARITY_THRESHOLD,
        }
        return cast(MemoryRequest, request)

    def _profile_request(self, limit: int) -> MemoryRequest:
        request = {
            "filter_": {
                "owner_id": {"eq": self.user_id},
                "namespace": {"eq": "profile"},
            },
            "limit": limit,
        }
        return cast(MemoryRequest, request)

    def _profile_write_is_readable(self, memory_id: str, fact: ProfileFact) -> bool:
        """Confirm one acknowledged profile write through an exact Redis read."""

        try:
            record = self.memory.get_long_term_memory(memory_id=memory_id)
        except MEMORY_EXCEPTIONS:
            return False
        return _matches_profile_fact(record, memory_id, self.user_id, fact)

    def _extract_trip_plan(self, user_text: str) -> TripPlan | None:
        """Extract one concrete future plan, when the traveler supplies an exact date range."""

        try:
            response = self.openai.responses.create(
                model=self.model,
                instructions=(
                    "Identify whether the message proposes a future trip with a destination and "
                    "an unambiguous date range. Today is "
                    f"{date.today().isoformat()}. Resolve relative dates using that date. Return "
                    "only JSON. For a dated trip, return exactly: "
                    '{"is_trip_plan":true,"destination":"...","start_date":"YYYY-MM-DD",'
                    '"end_date":"YYYY-MM-DD"}. For anything else or an ambiguous range, return '
                    '{"is_trip_plan":false}. Do not invent missing dates or destinations.'
                ),
                input=user_text,
            )
        except OpenAIError as error:
            raise TripAgentError("I couldn't check your trip dates for conflicts.") from error

        try:
            output = json.loads(response.output_text)
            if not isinstance(output, dict) or not isinstance(output.get("is_trip_plan"), bool):
                raise ValueError("Missing trip-plan indicator.")
            if not output["is_trip_plan"]:
                return None
            destination = output.get("destination")
            start = output.get("start_date")
            end = output.get("end_date")
            if not isinstance(destination, str) or not destination.strip():
                raise ValueError("Missing trip destination.")
            if not isinstance(start, str) or not start.strip():
                raise ValueError("Missing trip start date.")
            if not isinstance(end, str) or not end.strip():
                raise ValueError("Missing trip end date.")
            plan = TripPlan(
                destination=destination.strip(),
                start_date=date.fromisoformat(start),
                end_date=date.fromisoformat(end),
            )
            if plan.end_date < plan.start_date:
                raise ValueError("Trip ends before it starts.")
            return plan
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise TripAgentError("I couldn't check your trip dates for conflicts.") from error

    def _trip_plans(self) -> tuple[TripPlan, ...]:
        """Load this traveler's canonical future-trip records."""

        request = cast(
            MemoryRequest,
            {
                "filter_": {
                    "owner_id": {"eq": self.user_id},
                    "namespace": {"eq": "trip-plans"},
                },
                "limit": 100,
            },
        )
        try:
            result = self.memory.search_long_term_memory(request=request)
        except MEMORY_EXCEPTIONS as error:
            raise TripAgentError("I couldn't check your saved trip plans.") from error
        return tuple(
            plan
            for item in result.items
            if (plan := _trip_plan_from_memory(getattr(item, "text", ""))) is not None
        )

    def _save_trip_plan(self, plan: TripPlan) -> None:
        """Persist a non-conflicting future plan for later overlap checks."""

        record: CreateMemoryRecordTypedDict = {
            "id": str(
                uuid5(
                    NAMESPACE_URL,
                    f"{self.user_id}:{plan.destination.casefold()}:{plan.start_date}:{plan.end_date}",
                )
            ),
            "text": plan.memory_text(),
            "owner_id": self.user_id,
            "memory_type": "episodic",
            "namespace": "trip-plans",
            "topics": ["direct", "trip-plan"],
        }
        try:
            result = self.memory.bulk_create_long_term_memories(memories=[record])
        except MEMORY_EXCEPTIONS as error:
            raise TripAgentError("I couldn't save your future trip plan.") from error
        requested_id = record["id"]
        has_matching_error = any(error.id == requested_id for error in result.errors or ())
        if requested_id not in result.created or has_matching_error:
            raise TripAgentError("I couldn't save your future trip plan.")

    def _add_event(
        self,
        session_id: str,
        actor_id: str,
        role: models.MessageRole,
        text: str,
        failure_message: str,
    ) -> None:
        try:
            self.memory.add_session_event(
                session_id=session_id,
                actor_id=actor_id,
                role=role,
                content=[models.Text(text=text)],
                created_at=datetime.now(UTC),
            )
        except MEMORY_EXCEPTIONS as error:
            raise TripAgentError(failure_message) from error


def _profile_text(value: object) -> str:
    """Validate one rewritten profile fact from the model response."""

    if not isinstance(value, str) or not (text := value.strip()):
        raise ValueError("A profile fact must be non-empty text.")
    return text


def _canonical_profile_text(category: str, value: object) -> str:
    """Wrap a normalized value in wording that preserves its category semantics."""

    text = _profile_text(value).removesuffix(".")
    prefix = PROFILE_FACT_PREFIXES.get(category)
    if prefix is None:
        raise ValueError("Unsupported profile category.")
    return f"{prefix}{text}."


def _matches_profile_fact(
    record: object,
    memory_id: str,
    owner_id: str,
    fact: ProfileFact,
) -> bool:
    """Return whether an exact Redis read matches the submitted profile fact."""

    topics = getattr(record, "topics", None)
    valid_topics = (
        isinstance(topics, Sequence)
        and not isinstance(topics, (str, bytes))
        and len(topics) == 2
        and all(isinstance(topic, str) for topic in topics)
        and set(topics) == {"direct", fact.category}
    )
    return (
        getattr(record, "id", None) == memory_id
        and getattr(record, "text", None) == fact.text
        and getattr(record, "owner_id", None) == owner_id
        and getattr(record, "namespace", None) == "profile"
        and getattr(record, "memory_type", None) == "semantic"
        and valid_topics
    )


def _merge_memory_results(*results: object) -> _MemoryContext:
    """Combine Redis results in priority order and remove repeated record IDs."""

    items: list[object] = []
    seen_ids: set[str] = set()
    for result in results:
        for item in getattr(result, "items", ()):
            memory_id = getattr(item, "id", None)
            if isinstance(memory_id, str) and memory_id:
                if memory_id in seen_ids:
                    continue
                seen_ids.add(memory_id)
            items.append(item)
    return _MemoryContext(items=tuple(items))


def _profile_category(item: object) -> str | None:
    """Return the first canonical profile category tagged on a memory record."""

    topics = getattr(item, "topics", None)
    if not isinstance(topics, Sequence) or isinstance(topics, (str, bytes)):
        return None
    return next((category for category in PROFILE_CATEGORIES if category in topics), None)


def _validate_profile_facts(facts: Sequence[ProfileFact]) -> None:
    """Reject unsupported or repeated profile categories before Redis access."""

    seen: set[str] = set()
    for fact in facts:
        if fact.category not in PROFILE_CATEGORIES:
            raise TripAgentError(f"Unsupported profile category: {fact.category}.")
        if fact.category in seen:
            raise TripAgentError(f"Duplicate profile category: {fact.category}.")
        seen.add(fact.category)


def _latest_profile_record_ids(items: Sequence[object]) -> dict[str, str]:
    """Select the most recently updated record ID for each supported category."""

    selected: dict[str, tuple[datetime, str]] = {}
    for item in items:
        category = _profile_category(item)
        memory_id = getattr(item, "id", None)
        if category is None or not isinstance(memory_id, str):
            continue
        updated_at = _profile_updated_at(item)
        current = selected.get(category)
        if current is None or updated_at > current[0]:
            selected[category] = (updated_at, memory_id)
    return {category: memory_id for category, (_, memory_id) in selected.items()}


def _profile_updated_at(item: object) -> datetime:
    """Normalize timestamps, falling back safely for missing or malformed legacy data."""

    value = getattr(item, "updated_at", None)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _memory_views(result: object) -> tuple[MemoryView, ...]:
    """Convert a Redis memory-search result into display-ready provenance records."""

    items = getattr(result, "items", ())
    return tuple(
        MemoryView(
            memory_type=_memory_kind(getattr(item, "memory_type", None)),
            text=getattr(item, "text", ""),
            source=_memory_source(item),
        )
        for item in items
    )


def _memory_source(item: object) -> str:
    """Classify direct records without inferring their Redis memory type."""

    topics = getattr(item, "topics", ())
    if isinstance(topics, Sequence) and not isinstance(topics, str) and "direct" in topics:
        return "direct"
    namespace = getattr(item, "namespace", None)
    if isinstance(namespace, str) and namespace.casefold() in {"profile", "trip-plans"}:
        return "direct"
    return "learned"


def _memory_kind(memory_type: object) -> str:
    """Keep Redis-provided memory kinds, with a safe label for untyped records."""

    return memory_type if isinstance(memory_type, str) and memory_type else "memory"


def _trip_plan_from_memory(text: object) -> TripPlan | None:
    """Parse only trip-plan records written by this application."""

    if not isinstance(text, str) or not text.startswith("[trip-plan] destination="):
        return None
    try:
        destination, start, end = text.removeprefix("[trip-plan] destination=").split(" | ")
        return TripPlan(
            destination=destination,
            start_date=date.fromisoformat(start.removeprefix("start=")),
            end_date=date.fromisoformat(end.removeprefix("end=")),
        )
    except ValueError:
        return None


def _may_describe_dated_trip(text: str) -> bool:
    """Avoid a date-extraction model call when a turn contains no date-like language."""

    return bool(
        re.search(
            r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
            r"dec(?:ember)?|20\d{2}|next\s+(?:week|month|year)|this\s+(?:week|month|year))\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _conflict_message(proposed: TripPlan, existing: TripPlan) -> str:
    """Explain one deterministic dated-trip conflict to the traveler."""

    return (
        f"Your proposed trip to {proposed.destination} from {proposed.start_date:%b %-d, %Y} "
        f"to {proposed.end_date:%b %-d, %Y} overlaps your existing trip to "
        f"{existing.destination} from {existing.start_date:%b %-d, %Y} to "
        f"{existing.end_date:%b %-d, %Y}. Would you like to change the dates or replace "
        "the existing trip plan?"
    )
