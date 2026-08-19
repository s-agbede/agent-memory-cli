"""Tests for the interactive CLI behavior."""

from io import StringIO
from typing import cast

import httpx
import pytest
from openai import OpenAIError
from rich.console import Console
from typer.testing import CliRunner

import trip_agent.cli as cli
from trip_agent.agent import AgentReply, AssistantMemoryWarning, MemoryView, TripAgent
from trip_agent.cli import (
    DEFAULT_MEMORY_QUERY,
    SessionState,
    app,
    handle_command,
    run_repl,
    show_memories,
)
from trip_agent.formatting import Citation

VALID_ENV = {
    "OPENAI_API_KEY": "openai-secret",
    "REDIS_AGENT_MEMORY_ENDPOINT": "https://memory.example.com",
    "REDIS_AGENT_MEMORY_STORE_ID": "store-123",
    "REDIS_AGENT_MEMORY_API_KEY": "redis-secret",
    "TRIP_AGENT_USER_ID": "sam",
}


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


def test_show_memories_treats_rich_markup_as_plain_data() -> None:
    console, output = recording_console()

    show_memories(
        [MemoryView(memory_type="[/dim]", text="Preference: [/red]")],
        console,
    )

    assert "[/dim]" in output.getvalue()
    assert "[/red]" in output.getvalue()


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


def test_repl_treats_markup_in_user_id_as_plain_data() -> None:
    state = SessionState(session_id="session", user_id="[/cyan]")
    console, output = recording_console()

    run_repl(
        cast(TripAgent, FakeAgent()),
        state,
        console,
        read_input=lambda: "/exit",
    )

    assert "[/cyan]" in output.getvalue()


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


class FakeMemoryContext:
    """Context-manager fake for the CLI composition boundary."""

    def __init__(self, health_error: Exception | None = None) -> None:
        self.health_error = health_error
        self.health_called = False

    def __enter__(self) -> "FakeMemoryContext":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def health(self) -> object:
        self.health_called = True
        if self.health_error is not None:
            raise self.health_error
        return object()


def test_cli_entrypoint_composes_clients_and_starts_repl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = FakeMemoryContext()
    started: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "AgentMemory", lambda *args, **kwargs: memory)
    monkeypatch.setattr(cli, "OpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        cli,
        "run_repl",
        lambda agent, state, console: started.append((state.user_id, state.session_id)),
    )

    result = CliRunner().invoke(app, env=VALID_ENV)

    assert result.exit_code == 0
    assert memory.health_called is True
    assert started and started[0][0] == "sam"


def test_cli_entrypoint_reports_invalid_configuration() -> None:
    invalid_env = {name: " " for name in VALID_ENV}

    result = CliRunner().invoke(app, env=invalid_env)

    assert result.exit_code == 2
    assert "Missing or invalid configuration" in result.stdout
    assert "OPENAI_API_KEY" in result.stdout
    assert "REDIS_AGENT_MEMORY_API_KEY" in result.stdout


def test_cli_entrypoint_reports_redis_health_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = FakeMemoryContext(httpx.ConnectError("offline"))
    monkeypatch.setattr(cli, "AgentMemory", lambda *args, **kwargs: memory)

    result = CliRunner().invoke(app, env=VALID_ENV)

    assert result.exit_code == 1
    assert "Couldn't connect to Redis Agent Memory" in result.stdout


def test_cli_entrypoint_reports_openai_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = FakeMemoryContext()
    monkeypatch.setattr(cli, "AgentMemory", lambda *args, **kwargs: memory)

    def fail_openai(*args: object, **kwargs: object) -> object:
        raise OpenAIError("Missing credentials")

    monkeypatch.setattr(cli, "OpenAI", fail_openai)

    result = CliRunner().invoke(app, env=VALID_ENV)

    assert result.exit_code == 1
    assert "Couldn't initialize OpenAI" in result.stdout
    assert not isinstance(result.exception, OpenAIError)
