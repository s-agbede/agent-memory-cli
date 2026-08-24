"""Direct Redis Agent Memory and OpenAI turn coordination."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from openai import OpenAI, OpenAIError
from redis_agent_memory import AgentMemory, errors, models
from redis_agent_memory.models.creatememoryrecord import CreateMemoryRecordTypedDict

from trip_agent.formatting import Citation, build_model_input, extract_citations
from trip_agent.prompt import SYSTEM_PROMPT


@dataclass(frozen=True, slots=True)
class AgentReply:
    """A generated answer and its web citations."""

    text: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class MemoryView:
    """A display-ready long-term memory."""

    memory_type: str
    text: str
    source: str = "learned"


@dataclass(frozen=True, slots=True)
class ProfileFact:
    """One explicit traveler preference collected during onboarding."""

    category: str
    text: str


@dataclass(frozen=True, slots=True)
class ProfileSaveResult:
    """Outcome of a bulk direct-memory onboarding write."""

    created_count: int
    failed_count: int


@dataclass(frozen=True, slots=True)
class TripPlan:
    """A dated future trip used for deterministic conflict detection."""

    destination: str
    start_date: date
    end_date: date

    def overlaps(self, other: "TripPlan") -> bool:
        """Return whether this trip shares one or more calendar days with another."""

        return self.start_date <= other.end_date and other.start_date <= self.end_date

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
            memories = self.memory.search_long_term_memory(
                request=self._memory_request(user_text, limit=5)
            )
        except (errors.AgentMemoryError, httpx.RequestError) as error:
            raise TripAgentError("I couldn't load your Redis Agent Memory context.") from error

        proposed_plan = (
            self._extract_trip_plan(user_text) if _may_describe_dated_trip(user_text) else None
        )
        if proposed_plan is not None:
            conflicts = tuple(plan for plan in self._trip_plans() if plan.overlaps(proposed_plan))
            if conflicts:
                reply = AgentReply(
                    text=_conflict_message(proposed_plan, conflicts[0]),
                    citations=(),
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
        except (errors.AgentMemoryError, httpx.RequestError) as error:
            raise TripAgentError("I couldn't search your Redis Agent Memory data.") from error

        return tuple(
            MemoryView(
                memory_type=item.memory_type or "memory",
                text=item.text,
                source="direct" if getattr(item, "namespace", None) == "profile" else "learned",
            )
            for item in result.items
        )

    def rewrite_profile(self, facts: Sequence[ProfileFact]) -> tuple[ProfileFact, ...]:
        """Turn explicit profile answers into concise, durable memory statements."""

        if not facts:
            return ()

        categories = [fact.category for fact in facts]
        try:
            response = self.openai.responses.create(
                model=self.model,
                instructions=(
                    "Rewrite each explicit travel-profile answer into one brief, standalone "
                    "fact for long-term memory. Preserve meaning, uncertainty, and every "
                    "qualification. Do not infer, add, omit, advise, or make any preference "
                    "stronger. Use third person (for example, 'The traveler prefers...'). "
                    "Return only a JSON object whose keys are exactly the supplied categories "
                    "and whose values are the rewritten facts."
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
                ProfileFact(category=fact.category, text=_profile_text(output[fact.category]))
                for fact in facts
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise TripAgentError("I couldn't rewrite your profile answers.") from error
        return rewritten

    def save_profile(self, facts: Sequence[ProfileFact]) -> ProfileSaveResult:
        """Save explicit onboarding preferences directly to long-term memory."""

        if not facts:
            return ProfileSaveResult(created_count=0, failed_count=0)

        records: list[CreateMemoryRecordTypedDict] = [
            {
                "id": str(uuid4()),
                "text": fact.text,
                "owner_id": self.user_id,
                "memory_type": "semantic",
                "namespace": "profile",
                "topics": ["direct", fact.category],
            }
            for fact in facts
        ]
        try:
            result = self.memory.bulk_create_long_term_memories(memories=records)
        except (errors.AgentMemoryError, httpx.RequestError) as error:
            raise TripAgentError("I couldn't save your long-term travel profile.") from error
        return ProfileSaveResult(
            created_count=len(result.created),
            failed_count=len(result.errors or ()),
        )

    def _memory_request(self, text: str, limit: int) -> MemoryRequest:
        request = {
            "text": text,
            "filter_": {"owner_id": {"eq": self.user_id}},
            "limit": limit,
        }
        return cast(MemoryRequest, request)

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
        except (errors.AgentMemoryError, httpx.RequestError) as error:
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
            "memory_type": "semantic",
            "namespace": "trip-plans",
            "topics": ["direct", "trip-plan"],
        }
        try:
            self.memory.bulk_create_long_term_memories(memories=[record])
        except (errors.AgentMemoryError, httpx.RequestError) as error:
            raise TripAgentError("I couldn't save your future trip plan.") from error

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
        except (errors.AgentMemoryError, httpx.RequestError) as error:
            raise TripAgentError(failure_message) from error


def _profile_text(value: object) -> str:
    """Validate one rewritten profile fact from the model response."""

    if not isinstance(value, str) or not (text := value.strip()):
        raise ValueError("A profile fact must be non-empty text.")
    return text


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
