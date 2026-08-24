"""Tests for the interactive CLI behavior."""

from collections.abc import Callable
from io import StringIO
from typing import cast
from uuid import UUID

import httpx
import pytest
from openai import OpenAIError
from redis_agent_memory import errors
from rich.console import Console
from typer.testing import CliRunner

import trip_agent.cli as cli
from trip_agent.agent import (
    AgentReply,
    AssistantMemoryWarning,
    MemoryView,
    ProfileFact,
    ProfileSaveResult,
    TripAgent,
    TripAgentError,
)
from trip_agent.cli import (
    DEFAULT_MEMORY_QUERY,
    SessionState,
    app,
    handle_command,
    normalize_user_id,
    run_onboarding,
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

    def __init__(
        self,
        warning: bool = False,
        profile_exists: bool = False,
        profile_save_result: ProfileSaveResult | None = None,
        rewrite_error: TripAgentError | None = None,
        save_error: TripAgentError | None = None,
        profile_check_error: TripAgentError | None = None,
    ) -> None:
        self.warning = warning
        self.profile_exists = profile_exists
        self.memory_query: str | None = None
        self.messages: list[tuple[str, str]] = []
        self.profile_facts: list[ProfileFact] = []
        self.profile_save_result = profile_save_result
        self.rewrite_error = rewrite_error
        self.save_error = save_error
        self.profile_check_error = profile_check_error
        self.rewrite_calls = 0
        self.save_calls = 0
        self.user_id = "sam"
        self.profile_check_users: list[str] = []
        self.profile_check_hook: Callable[[], None] | None = None

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

    def save_profile(self, facts: tuple[ProfileFact, ...]) -> ProfileSaveResult:
        self.save_calls += 1
        if self.save_error is not None:
            raise self.save_error
        self.profile_facts.extend(facts)
        return self.profile_save_result or ProfileSaveResult(
            created_categories=tuple(fact.category for fact in facts),
            updated_categories=(),
            failed_categories=(),
        )

    def rewrite_profile(self, facts: tuple[ProfileFact, ...]) -> tuple[ProfileFact, ...]:
        self.rewrite_calls += 1
        if self.rewrite_error is not None:
            raise self.rewrite_error
        return tuple(
            ProfileFact(category=fact.category, text=f"Rewritten: {fact.text}") for fact in facts
        )

    def has_profile(self) -> bool:
        self.profile_check_users.append(self.user_id)
        if self.profile_check_hook is not None:
            self.profile_check_hook()
        if self.profile_check_error is not None:
            raise self.profile_check_error
        return self.profile_exists

    def set_user(self, user_id: str) -> None:
        self.user_id = user_id


def recording_console() -> tuple[Console, StringIO]:
    output = StringIO()
    return Console(file=output, force_terminal=False, color_system=None), output


def test_new_command_replaces_session_but_keeps_user() -> None:
    state = SessionState(session_id="old", user_id="sam")
    state.last_retrieved_memories = (MemoryView(memory_type="semantic", text="Vegetarian"),)
    console, output = recording_console()

    keep_running = handle_command("/new", state, cast(TripAgent, FakeAgent()), console)

    assert keep_running is True
    assert state.session_id != "old"
    assert state.user_id == "sam"
    assert state.last_retrieved_memories is None
    assert "fresh session" in output.getvalue().lower()
    assert state.session_id in output.getvalue()


def test_normalize_user_id_turns_display_name_into_owner_id() -> None:
    assert normalize_user_id("Maya Chen") == "maya-chen"


def test_onboarding_saves_non_empty_profile_answers_directly() -> None:
    agent = FakeAgent()
    console, output = recording_console()
    responses = iter(["food and museums", "vegetarian", "moderate", "relaxed"])

    run_onboarding(
        cast(TripAgent, agent),
        console,
        read_input=lambda: next(responses),
    )

    assert [fact.category for fact in agent.profile_facts] == [
        "preferences",
        "dietary",
        "budget",
        "origin",
    ]
    assert "long-term profile" in output.getvalue().lower()
    assert "/memories" in output.getvalue()
    assert "Trip agent: What city do you usually travel from?" in output.getvalue()
    assert [fact.text for fact in agent.profile_facts] == [
        "Rewritten: food and museums",
        "Rewritten: vegetarian",
        "Rewritten: moderate",
        "Rewritten: relaxed",
    ]


def test_onboarding_intro_explains_how_to_cancel_without_saving() -> None:
    agent = FakeAgent()
    console, output = recording_console()

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: "/cancel")

    text = output.getvalue().lower()
    assert "/cancel" in text
    assert "without saving" in text


@pytest.mark.parametrize(
    "responses",
    [
        [" /cancel "],
        ["food and museums", "/cancel"],
        ["food and museums", "vegetarian", "/cancel"],
        ["food and museums", "vegetarian", "moderate", "/cancel"],
    ],
)
def test_onboarding_cancels_the_entire_attempt_for_an_exact_cancel_answer(
    responses: list[str],
) -> None:
    agent = FakeAgent()
    console, output = recording_console()
    answers = iter(responses)

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(answers))

    assert agent.rewrite_calls == 0
    assert agent.save_calls == 0
    assert agent.profile_facts == []
    assert "no profile changes were saved" in output.getvalue().lower()


@pytest.mark.parametrize("error_type", [EOFError, KeyboardInterrupt])
def test_onboarding_input_interruptions_cancel_after_answers_are_collected(
    error_type: type[EOFError] | type[KeyboardInterrupt],
) -> None:
    agent = FakeAgent()
    console, output = recording_console()
    answers = iter(["food and museums", "vegetarian"])

    def read_until_interrupted() -> str:
        try:
            return next(answers)
        except StopIteration:
            raise error_type from None

    run_onboarding(cast(TripAgent, agent), console, read_input=read_until_interrupted)

    assert agent.rewrite_calls == 0
    assert agent.save_calls == 0
    assert agent.profile_facts == []
    assert "no profile changes were saved" in output.getvalue().lower()


@pytest.mark.parametrize("answer", ["Please do not /cancel this trip", "/CANCEL"])
def test_onboarding_treats_cancel_text_as_an_answer_unless_it_is_exact(answer: str) -> None:
    agent = FakeAgent()
    console, _ = recording_console()
    responses = iter([answer, "", "", ""])

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(responses))

    assert agent.rewrite_calls == 1
    assert agent.save_calls == 1
    assert [fact.category for fact in agent.profile_facts] == ["preferences"]


def test_onboarding_with_only_blank_answers_does_not_rewrite_or_save() -> None:
    agent = FakeAgent()
    console, output = recording_console()
    responses = iter(["", "  ", "", "\t"])

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(responses))

    assert agent.rewrite_calls == 0
    assert agent.save_calls == 0
    assert "nothing was saved" in output.getvalue().lower()


def test_onboarding_does_not_save_when_rewrite_fails_and_invites_retry() -> None:
    agent = FakeAgent(rewrite_error=TripAgentError("I couldn't rewrite your profile answers."))
    console, output = recording_console()
    responses = iter(["food and museums", "", "", ""])

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(responses))

    assert agent.rewrite_calls == 1
    assert agent.save_calls == 0
    assert "couldn't rewrite" in output.getvalue().lower()
    assert "try /onboard again" in output.getvalue().lower()
    assert "saved 1" not in output.getvalue().lower()


def test_onboarding_renders_rewrite_errors_with_markup_as_plain_text() -> None:
    error_message = "I couldn't rewrite [/red] your profile answers."
    agent = FakeAgent(rewrite_error=TripAgentError(error_message))
    console, output = recording_console()
    responses = iter(["food and museums", "", "", ""])

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(responses))

    assert error_message in output.getvalue()
    assert "Try /onboard again" in output.getvalue()


def test_onboarding_reports_save_failure_without_claiming_success() -> None:
    agent = FakeAgent(save_error=TripAgentError("I couldn't save your profile."))
    console, output = recording_console()
    responses = iter(["food and museums", "", "", ""])

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(responses))

    assert agent.rewrite_calls == 1
    assert agent.save_calls == 1
    assert "couldn't save" in output.getvalue().lower()
    assert "try /onboard again" in output.getvalue().lower()
    assert "saved 1" not in output.getvalue().lower()


def test_onboarding_renders_save_errors_with_markup_as_plain_text() -> None:
    error_message = "I couldn't save [/red] your profile."
    agent = FakeAgent(save_error=TripAgentError(error_message))
    console, output = recording_console()
    responses = iter(["food and museums", "", "", ""])

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(responses))

    assert error_message in output.getvalue()
    assert "Try /onboard again" in output.getvalue()


def test_onboarding_reports_update_only_result_as_saved_and_updated() -> None:
    agent = FakeAgent(
        profile_save_result=ProfileSaveResult(
            created_categories=(),
            updated_categories=("dietary",),
            failed_categories=(),
        )
    )
    console, output = recording_console()
    responses = iter(["", "vegetarian", "", ""])

    run_onboarding(
        cast(TripAgent, agent),
        console,
        read_input=lambda: next(responses),
    )

    text = output.getvalue()
    assert "Saved 1 long-term profile memory" in text
    assert "1 updated: dietary" in text
    assert "Saved 0" not in text


def test_onboarding_reports_created_and_updated_categories_in_submitted_order() -> None:
    agent = FakeAgent(
        profile_save_result=ProfileSaveResult(
            created_categories=("origin", "preferences"),
            updated_categories=("budget", "dietary"),
            failed_categories=(),
        )
    )
    console, output = recording_console()
    responses = iter(["museums", "vegetarian", "moderate", "London"])

    run_onboarding(
        cast(TripAgent, agent),
        console,
        read_input=lambda: next(responses),
    )

    text = output.getvalue()
    created_start = text.index("2 created")
    updated_start = text.index("2 updated")
    assert "Saved 4 long-term profile memories" in text
    assert text.index("preferences", created_start) < text.index("origin", created_start)
    assert text.index("dietary", updated_start) < text.index("budget", updated_start)


def test_onboarding_reports_partial_success_and_failed_categories_truthfully() -> None:
    agent = FakeAgent(
        profile_save_result=ProfileSaveResult(
            created_categories=("preferences",),
            updated_categories=(),
            failed_categories=("dietary",),
        )
    )
    console, output = recording_console()
    responses = iter(["museums", "vegetarian", "", ""])

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(responses))

    text = output.getvalue()
    assert "Saved 1 long-term profile memory" in text
    assert "1 created: preferences" in text
    assert "1 profile memory was not saved: dietary" in text
    assert "Try /onboard again" in text
    assert "No profile changes were saved" not in text
    assert "Saved 0" not in text


def test_onboarding_reports_an_all_failed_result_without_a_success_claim() -> None:
    agent = FakeAgent(
        profile_save_result=ProfileSaveResult(
            created_categories=(),
            updated_categories=(),
            failed_categories=("dietary", "preferences"),
        )
    )
    console, output = recording_console()
    responses = iter(["museums", "vegetarian", "", ""])

    run_onboarding(cast(TripAgent, agent), console, read_input=lambda: next(responses))

    text = output.getvalue().lower()
    assert "saved 0" not in text
    assert "no profile changes were saved" in text
    assert text.rfind("preferences") < text.rfind("dietary")
    assert "try /onboard again" in text


def test_manual_onboard_command_uses_the_cancellable_onboarding_flow() -> None:
    state = SessionState(session_id="session", user_id="sam")
    agent = FakeAgent()
    console, output = recording_console()
    responses = iter(["/cancel"])

    keep_running = handle_command(
        "/onboard",
        state,
        cast(TripAgent, agent),
        console,
        read_input=lambda: next(responses),
    )

    assert keep_running is True
    assert agent.rewrite_calls == 0
    assert agent.save_calls == 0
    assert "no profile changes were saved" in output.getvalue().lower()


def test_repl_explains_background_promotion_after_a_chat_turn() -> None:
    state = SessionState(session_id="session", user_id="sam")
    console, output = recording_console()
    responses = iter(["Plan my trip", "/exit"])

    run_repl(
        cast(TripAgent, FakeAgent()),
        state,
        console,
        read_input=lambda: next(responses),
    )

    assert "saved to session memory" in output.getvalue().lower()
    assert "background" in output.getvalue().lower()
    assert "promotion" in output.getvalue().lower()


def test_first_run_starts_onboarding_without_a_confirmation_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SessionState(session_id="session", user_id="sam")
    console, output = recording_console()
    onboarding_started: list[bool] = []
    monkeypatch.setattr(
        cli,
        "run_onboarding",
        lambda agent, console, read_input=None: onboarding_started.append(True),
    )

    run_repl(
        cast(TripAgent, FakeAgent()),
        state,
        console,
        read_input=lambda: "/exit",
        offer_onboarding=True,
    )

    text = output.getvalue()
    assert onboarding_started == [True]
    assert "Save a travel profile" not in text
    assert "long-term memories are still available" not in text


def test_startup_returning_traveler_shows_profile_available_message_and_skips_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = SessionState(session_id="session", user_id="sam")
    console, output = recording_console()
    onboarding_started: list[bool] = []
    monkeypatch.setattr(
        cli,
        "run_onboarding",
        lambda agent, console, read_input=None: onboarding_started.append(True),
    )

    run_repl(
        cast(TripAgent, FakeAgent(profile_exists=True)),
        state,
        console,
        read_input=lambda: "/exit",
        offer_onboarding=True,
    )

    assert onboarding_started == []
    assert "profile is available" in output.getvalue().lower()


def test_onboarding_skips_blank_answers() -> None:
    agent = FakeAgent()
    console, _ = recording_console()
    responses = iter(["food and museums", "", "", "relaxed"])

    run_onboarding(
        cast(TripAgent, agent),
        console,
        read_input=lambda: next(responses),
    )

    assert [fact.category for fact in agent.profile_facts] == ["preferences", "origin"]


@pytest.mark.parametrize("command", ["/user", "/user !!!"])
def test_user_command_with_missing_or_invalid_name_leaves_the_active_traveler_unchanged(
    command: str,
) -> None:
    state = SessionState(session_id="old", user_id="sam")
    receipt = (MemoryView(memory_type="semantic", text="Vegetarian"),)
    state.last_retrieved_memories = receipt
    agent = FakeAgent()
    console, output = recording_console()

    keep_running = handle_command(command, state, cast(TripAgent, agent), console)

    assert keep_running is True
    assert state.user_id == "sam"
    assert state.session_id == "old"
    assert state.last_retrieved_memories == receipt
    assert agent.user_id == "sam"
    assert agent.profile_check_users == []
    assert "letter or number" in output.getvalue().lower()


def test_user_command_switches_to_new_owner_before_checking_their_profile() -> None:
    state = SessionState(session_id="old", user_id="sam")
    state.last_retrieved_memories = (MemoryView(memory_type="semantic", text="Vegetarian"),)
    agent = FakeAgent(profile_exists=True)
    console, output = recording_console()

    def assert_switch_timeline_at_profile_check() -> None:
        assert state.user_id == "alex"
        assert state.session_id != "old"
        assert state.last_retrieved_memories is None
        assert agent.user_id == "alex"
        text = output.getvalue()
        assert "Active traveler: alex" in text
        assert f"Session ID: {state.session_id}" in text

    agent.profile_check_hook = assert_switch_timeline_at_profile_check

    keep_running = handle_command("/user Alex", state, cast(TripAgent, agent), console)

    assert keep_running is True
    assert state.user_id == "alex"
    assert state.session_id != "old"
    assert state.last_retrieved_memories is None
    assert agent.user_id == "alex"
    assert agent.profile_check_users == ["alex"]
    UUID(state.session_id)
    text = output.getvalue()
    assert "Active traveler: alex" in text
    assert f"Session ID: {state.session_id}" in text
    assert "profile is available" in text.lower()


def test_user_command_refreshes_the_session_for_the_same_normalized_owner() -> None:
    state = SessionState(session_id="old", user_id="sam")
    state.last_retrieved_memories = (MemoryView(memory_type="semantic", text="Vegetarian"),)
    agent = FakeAgent(profile_exists=True)
    console, _ = recording_console()

    handle_command("/user SAM", state, cast(TripAgent, agent), console)

    assert state.user_id == "sam"
    assert state.session_id != "old"
    assert state.last_retrieved_memories is None
    assert agent.user_id == "sam"
    assert agent.profile_check_users == ["sam"]


def test_user_command_onboards_a_new_owner_with_the_injected_reader() -> None:
    state = SessionState(session_id="old", user_id="sam")
    agent = FakeAgent()
    console, output = recording_console()
    responses = iter(["museums", "vegetarian", "moderate", "London"])

    handle_command(
        "/user Alex",
        state,
        cast(TripAgent, agent),
        console,
        read_input=lambda: next(responses),
    )

    assert state.user_id == "alex"
    assert agent.user_id == "alex"
    assert agent.profile_check_users == ["alex"]
    assert [fact.category for fact in agent.profile_facts] == [
        "preferences",
        "dietary",
        "budget",
        "origin",
    ]
    assert "quick travel profile" in output.getvalue().lower()


def test_user_command_can_cancel_automatic_onboarding_for_a_new_owner() -> None:
    state = SessionState(session_id="old", user_id="sam")
    agent = FakeAgent()
    console, output = recording_console()

    handle_command(
        "/user Alex",
        state,
        cast(TripAgent, agent),
        console,
        read_input=lambda: "/cancel",
    )

    assert state.user_id == "alex"
    assert agent.user_id == "alex"
    assert agent.rewrite_calls == 0
    assert agent.save_calls == 0
    assert "no profile changes were saved" in output.getvalue().lower()


def test_user_profile_check_failure_keeps_the_selected_owner_and_allows_manual_onboarding() -> None:
    state = SessionState(session_id="old", user_id="sam")
    state.last_retrieved_memories = (MemoryView(memory_type="semantic", text="Vegetarian"),)
    error = TripAgentError("Profile service said [/red] unavailable.")
    agent = FakeAgent(profile_check_error=error)
    console, output = recording_console()

    keep_running = handle_command("/user Alex", state, cast(TripAgent, agent), console)

    assert keep_running is True
    assert state.user_id == "alex"
    assert state.session_id != "old"
    assert state.last_retrieved_memories is None
    assert agent.user_id == "alex"
    assert agent.profile_check_users == ["alex"]
    assert agent.rewrite_calls == 0
    assert agent.save_calls == 0
    text = output.getvalue()
    assert "couldn't check" in text.lower()
    assert "/onboard" in text
    assert "[/red]" in text

    responses = iter(["museums", "vegetarian", "moderate", "London"])
    keep_running = handle_command(
        "/onboard",
        state,
        cast(TripAgent, agent),
        console,
        read_input=lambda: next(responses),
    )

    assert keep_running is True
    assert agent.user_id == "alex"
    assert agent.rewrite_calls == 1
    assert agent.save_calls == 1


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


def test_show_memories_labels_direct_profile_records() -> None:
    console, output = recording_console()

    show_memories(
        [MemoryView(memory_type="semantic", text="Vegetarian", source="direct")],
        console,
    )

    assert "direct" in output.getvalue().lower()


def test_show_memories_renders_friendly_kinds_separately_from_provenance() -> None:
    console, output = recording_console()

    show_memories(
        [
            MemoryView(memory_type="semantic", text="Vegetarian", source="direct"),
            MemoryView(memory_type="episodic", text="Paris trip", source="direct"),
            MemoryView(memory_type="message", text="Take the train", source="learned"),
            MemoryView(memory_type="custom[/dim]", text="Custom: [/red]", source="learned"),
        ],
        console,
    )

    text = output.getvalue().lower()
    assert "semantic fact" in text
    assert "episodic event" in text
    assert "retained message" in text
    assert "custom[/dim]" in text
    assert text.count("direct") == 2
    assert text.count("learned") == 2
    assert "[/red]" in text


def test_why_command_shows_memories_retrieved_for_the_last_answer() -> None:
    state = SessionState(session_id="session", user_id="sam")
    state.last_retrieved_memories = (
        MemoryView(memory_type="semantic", text="The traveler is vegetarian.", source="direct"),
        MemoryView(memory_type="preference", text="The traveler prefers rail travel."),
    )
    console, output = recording_console()

    keep_running = handle_command("/why", state, cast(TripAgent, FakeAgent()), console)

    assert keep_running is True
    assert "retrieved for your last answer" in output.getvalue().lower()
    assert "vegetarian" in output.getvalue().lower()
    assert "rail travel" in output.getvalue().lower()


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

    def __init__(
        self,
        health_error: Exception | None = None,
        list_sessions_error: Exception | None = None,
    ) -> None:
        self.health_error = health_error
        self.list_sessions_error = list_sessions_error
        self.health_called = False
        self.list_sessions_limits: list[int | None] = []
        self.list_sessions_include_all: list[bool | None] = []
        self.timeline: list[str] = []

    def __enter__(self) -> "FakeMemoryContext":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def health(self) -> object:
        self.health_called = True
        self.timeline.append("health")
        if self.health_error is not None:
            raise self.health_error
        return object()

    def list_sessions(
        self,
        *,
        limit: int | None = None,
        include_all: bool | None = None,
    ) -> object:
        self.list_sessions_limits.append(limit)
        self.list_sessions_include_all.append(include_all)
        self.timeline.append("list_sessions")
        if self.list_sessions_error is not None:
            raise self.list_sessions_error
        return object()


def test_cli_entrypoint_composes_clients_and_starts_repl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = FakeMemoryContext()
    started: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "AgentMemory", lambda *args, **kwargs: memory)
    monkeypatch.setattr(
        cli,
        "OpenAI",
        lambda *args, **kwargs: memory.timeline.append("openai") or object(),
    )
    monkeypatch.setattr(
        cli,
        "prompt_for_user_id",
        lambda console, default: memory.timeline.append("prompt") or "maya",
    )
    monkeypatch.setattr(
        cli,
        "run_repl",
        lambda agent, state, console, **kwargs: (
            memory.timeline.append("repl"),
            started.append((state.user_id, state.session_id)),
        ),
    )

    result = CliRunner().invoke(app, env=VALID_ENV)

    assert result.exit_code == 0
    assert memory.health_called is True
    assert memory.list_sessions_limits == [1]
    assert memory.list_sessions_include_all == [True]
    assert memory.timeline == ["health", "list_sessions", "openai", "prompt", "repl"]
    assert started and started[0][0] == "maya"


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
    monkeypatch.setattr(
        cli,
        "OpenAI",
        lambda *args, **kwargs: memory.timeline.append("openai") or object(),
    )
    monkeypatch.setattr(
        cli,
        "prompt_for_user_id",
        lambda console, default: memory.timeline.append("prompt") or "maya",
    )
    monkeypatch.setattr(
        cli,
        "run_repl",
        lambda *args, **kwargs: memory.timeline.append("repl"),
    )

    result = CliRunner().invoke(app, env=VALID_ENV)

    assert result.exit_code == 1
    assert "Couldn't connect to Redis Agent Memory" in result.stdout
    assert memory.list_sessions_limits == []
    assert memory.list_sessions_include_all == []
    assert memory.timeline == ["health"]


@pytest.mark.parametrize(
    "list_sessions_error",
    [
        errors.AgentMemoryError(
            "invalid store",
            httpx.Response(
                404,
                request=httpx.Request("GET", "https://memory.example.com"),
            ),
            "invalid store",
        ),
        errors.NoResponseError(),
        httpx.ConnectError("offline"),
    ],
    ids=["agent-memory-error", "no-response", "request-error"],
)
def test_cli_entrypoint_reports_store_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    list_sessions_error: Exception,
) -> None:
    memory = FakeMemoryContext(list_sessions_error=list_sessions_error)
    monkeypatch.setattr(cli, "AgentMemory", lambda *args, **kwargs: memory)
    monkeypatch.setattr(
        cli,
        "OpenAI",
        lambda *args, **kwargs: memory.timeline.append("openai") or object(),
    )
    monkeypatch.setattr(
        cli,
        "prompt_for_user_id",
        lambda console, default: memory.timeline.append("prompt") or "maya",
    )
    monkeypatch.setattr(
        cli,
        "run_repl",
        lambda *args, **kwargs: memory.timeline.append("repl"),
    )

    result = CliRunner().invoke(app, env=VALID_ENV)

    assert result.exit_code == 1
    assert "Couldn't validate Redis Agent Memory Store ID" in result.stdout
    assert memory.list_sessions_limits == [1]
    assert memory.list_sessions_include_all == [True]
    assert memory.timeline == ["health", "list_sessions"]


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
