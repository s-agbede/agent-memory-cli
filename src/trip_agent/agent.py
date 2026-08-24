"""Direct Redis Agent Memory and OpenAI turn coordination."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

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
