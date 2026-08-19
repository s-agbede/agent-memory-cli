"""Tests for the interactive CLI behavior."""

from io import StringIO
from typing import cast

import pytest
from rich.console import Console

from trip_agent.agent import AgentReply, AssistantMemoryWarning, MemoryView, TripAgent
from trip_agent.cli import (
    DEFAULT_MEMORY_QUERY,
    SessionState,
    handle_command,
    run_repl,
)
from trip_agent.formatting import Citation


class FakeAgent:
    """Small fake for observable CLI behavior."""

    def __init__(self, warning: bool = False) -> None:
        self.warning = warning
        self.memory_query: str | None = None
        self.messages: list[tuple[str, str]] = []

    def reply(self, session_id: str, user_text: str) -> AgentReply:
        self.messages.append((session_id, user_text))
        reply = AgentReply(
            text="Try Kyoto's Nishiki Market.",
            citations=(
                Citation(
                    title="Nishiki Market guide",
                    url="https://example.com/nishiki",
                    start_index=4,
                    end_index=10,
                ),
            ),
        )
        if self.warning:
            raise AssistantMemoryWarning(reply)
        return reply

    def search_memories(self, query: str, limit: int = 10) -> tuple[MemoryView, ...]:
        self.memory_query = query
        assert limit == 10
        return (MemoryView(memory_type="preference", text="The traveler is vegetarian."),)


def recording_console() -> tuple[Console, StringIO]:
    output = StringIO()
    return Console(file=output, force_terminal=False, color_system=None), output


def test_new_command_replaces_session_but_keeps_user() -> None:
    state = SessionState(session_id="old", user_id="sam")
    console, output = recording_console()

    keep_running = handle_command("/new", state, cast(TripAgent, FakeAgent()), console)

    assert keep_running is True
    assert state.session_id != "old"
    assert state.user_id == "sam"
    assert "fresh session" in output.getvalue().lower()


@pytest.mark.parametrize(
    ("command", "expected_query"),
    [
        ("/memories", DEFAULT_MEMORY_QUERY),
        ("/memories food preferences", "food preferences"),
    ],
)
def test_memories_command_uses_owner_scoped_agent_search(
    command: str,
    expected_query: str,
) -> None:
    state = SessionState(session_id="session", user_id="sam")
    agent = FakeAgent()
    console, output = recording_console()

    keep_running = handle_command(command, state, cast(TripAgent, agent), console)

    assert keep_running is True
    assert agent.memory_query == expected_query
    assert "vegetarian" in output.getvalue()
    assert "preference" in output.getvalue()


def test_help_unknown_and_exit_commands() -> None:
    state = SessionState(session_id="session", user_id="sam")
    agent = cast(TripAgent, FakeAgent())
    console, output = recording_console()

    assert handle_command("/help", state, agent, console) is True
    assert handle_command("/surprise", state, agent, console) is True
    assert handle_command("/exit", state, agent, console) is False

    text = output.getvalue()
    assert "/memories" in text
    assert "don't know that command" in text


def test_repl_ignores_empty_input_and_renders_reply_and_sources() -> None:
    state = SessionState(session_id="session", user_id="sam")
    agent = FakeAgent()
    console, output = recording_console()
    responses = iter(["", "Where should I go?", "/exit"])

    run_repl(
        cast(TripAgent, agent),
        state,
        console,
        read_input=lambda: next(responses),
    )

    assert agent.messages == [("session", "Where should I go?")]
    text = output.getvalue()
    assert "Nishiki Market" in text
    assert "Sources" in text
    assert "Nishiki Market guide" in text


def test_repl_displays_answer_before_assistant_memory_warning() -> None:
    state = SessionState(session_id="session", user_id="sam")
    console, output = recording_console()
    responses = iter(["Plan my trip", "/exit"])

    run_repl(
        cast(TripAgent, FakeAgent(warning=True)),
        state,
        console,
        read_input=lambda: next(responses),
    )

    text = output.getvalue()
    assert text.index("Nishiki Market") < text.index("could not be saved")
