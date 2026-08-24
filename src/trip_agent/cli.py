"""Interactive terminal interface for the trip recommendation agent."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import uuid4

import httpx
import typer
from openai import OpenAI, OpenAIError
from pydantic import ValidationError
from redis_agent_memory import AgentMemory, errors
from rich.console import Console
from rich.prompt import Prompt
from rich.text import Text

from trip_agent.agent import (
    AgentReply,
    AssistantMemoryWarning,
    MemoryView,
    ProfileFact,
    TripAgent,
    TripAgentError,
)
from trip_agent.config import Settings
from trip_agent.formatting import render_reply

DEFAULT_MEMORY_QUERY = "What travel preferences and plans are known about this traveler?"
ONBOARDING_PROMPTS = (
    ("preferences", "What kinds of trips and places do you enjoy?"),
    ("dietary", "What food or dietary needs should I remember?"),
    ("budget", "What budget works for you?"),
    ("origin", "What city do you usually travel from?"),
)

app = typer.Typer(
    help="Chat with a friendly, memory-aware trip adviser.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@dataclass(slots=True)
class SessionState:
    """Mutable CLI session identity for one process."""

    session_id: str
    user_id: str

    @classmethod
    def new(cls, user_id: str) -> "SessionState":
        """Create a fresh conversation for a stable traveler."""

        return cls(session_id=str(uuid4()), user_id=user_id)

    def reset(self) -> None:
        """Replace only the session identity."""

        self.session_id = str(uuid4())

    def switch_user(self, user_id: str) -> None:
        """Change travelers and start a fresh session for that traveler."""

        self.user_id = user_id
        self.reset()


def normalize_user_id(display_name: str) -> str:
    """Convert a display name into a Redis Agent Memory owner ID."""

    normalized = re.sub(r"[^a-z0-9]+", "-", display_name.casefold()).strip("-")[:64]
    if not normalized:
        raise ValueError("Use at least one letter or number for the traveler name.")
    return normalized


def prompt_for_user_id(console: Console, default: str) -> str:
    """Prompt until the traveler supplies a name usable as an owner ID."""

    while True:
        display_name = Prompt.ask("Traveler name", default=default)
        try:
            return normalize_user_id(display_name)
        except ValueError as error:
            console.print(f"[yellow]{error}[/yellow]")


def show_session_started(state: SessionState, console: Console) -> None:
    """Show the active session identity without treating it as an account credential."""

    console.print("[green]Fresh session started.[/green]")
    session = Text("Session ID: ")
    session.append(state.session_id, style="cyan")
    console.print(session)


def show_help(console: Console) -> None:
    """Show the supported interactive commands."""

    console.print("[bold]Commands[/bold]")
    console.print("  [cyan]/new[/cyan]                 Start a fresh conversation")
    console.print("  [cyan]/memories [query][/cyan]  View relevant long-term memories")
    console.print("  [cyan]/user <name>[/cyan]         Switch to another traveler")
    console.print("  [cyan]/onboard[/cyan]             Save travel preferences directly")
    console.print("  [cyan]/help[/cyan]                Show these commands")
    console.print("  [cyan]/exit[/cyan]                Leave the trip agent")


def show_memories(memories: Sequence[MemoryView], console: Console) -> None:
    """Render long-term memory search results."""

    if not memories:
        console.print(
            "[yellow]No matching memories yet. Automatic extraction is asynchronous, "
            "so newly learned details can take a short time to appear.[/yellow]"
        )
        return

    console.print("[bold magenta]Long-term memories[/bold magenta]")
    for memory in memories:
        line = Text("  ")
        line.append(memory.source, style="cyan")
        line.append("  ")
        line.append(memory.memory_type, style="dim")
        line.append("  ")
        line.append(memory.text)
        console.print(line)


def show_reply(reply: AgentReply, console: Console) -> None:
    """Render an answer with inline links and a source list."""

    rendered, sources = render_reply(reply.text, reply.citations)
    console.print("\n[bold green]Trip agent[/bold green]")
    console.print(rendered)
    if sources:
        console.print("\n[bold]Sources[/bold]")
        for source in sources:
            console.print("  • ", source, sep="")


def run_onboarding(
    agent: TripAgent,
    console: Console,
    read_input: Callable[[], str] | None = None,
) -> None:
    """Collect explicit travel preferences and save them directly to long-term memory."""

    console.print("[bold]Quick travel profile[/bold]")
    console.print(
        "Your answers are briefly rewritten into clear profile facts, then saved directly "
        "to long-term memory."
    )
    reader = read_input or _read_input
    facts: list[ProfileFact] = []
    for category, question in ONBOARDING_PROMPTS:
        console.print("[bold green]Trip agent[/bold green]: ", question, sep="")
        answer = reader().strip()
        if answer:
            facts.append(ProfileFact(category=category, text=answer))

    if not facts:
        console.print("[yellow]No profile preferences were saved.[/yellow]")
        return

    try:
        with console.status(
            "[bold cyan]Creating concise travel memories…[/bold cyan]", spinner="dots"
        ):
            rewritten_facts = agent.rewrite_profile(tuple(facts))
        result = agent.save_profile(rewritten_facts)
    except TripAgentError as error:
        console.print(f"[red]{error}[/red]")
        return

    console.print(
        f"[green]Saved {result.created_count} long-term profile "
        f"{'memory' if result.created_count == 1 else 'memories'}.[/green]"
    )
    if result.failed_count:
        console.print(
            f"[yellow]{result.failed_count} profile "
            f"{'memory was' if result.failed_count == 1 else 'memories were'} not saved. "
            "Try /onboard again.[/yellow]"
        )
    console.print("Next, run [cyan]/memories[/cyan] to inspect your profile.")


def handle_command(
    line: str,
    state: SessionState,
    agent: TripAgent,
    console: Console,
    read_input: Callable[[], str] | None = None,
) -> bool:
    """Handle one slash command and return whether the REPL should continue."""

    command, _, argument = line.partition(" ")
    if command == "/exit":
        console.print("[green]Safe travels![/green]")
        return False
    if command == "/new":
        state.reset()
        show_session_started(state, console)
        return True
    if command == "/memories":
        query = argument.strip() or DEFAULT_MEMORY_QUERY
        try:
            show_memories(agent.search_memories(query, limit=10), console)
        except TripAgentError as error:
            console.print(f"[red]{error}[/red]")
        return True
    if command == "/help":
        show_help(console)
        return True
    if command == "/user":
        try:
            user_id = normalize_user_id(argument)
        except ValueError as error:
            console.print(f"[yellow]{error}[/yellow]")
            return True
        state.switch_user(user_id)
        agent.set_user(user_id)
        traveler = Text("Active traveler: ")
        traveler.append(user_id, style="cyan")
        console.print(traveler)
        show_session_started(state, console)
        return True
    if command == "/onboard":
        run_onboarding(agent, console, read_input or _read_input)
        return True

    console.print("[yellow]I don't know that command yet. Try /help.[/yellow]")
    return True


def _read_input() -> str:
    return Prompt.ask("\n[bold cyan]You[/bold cyan]")


def run_repl(
    agent: TripAgent,
    state: SessionState,
    console: Console,
    read_input: Callable[[], str] = _read_input,
    offer_onboarding: bool = False,
) -> None:
    """Run the interactive chat loop."""

    console.print("[bold green]Hi! I'm your friendly trip-planning companion.[/bold green]")
    traveler = Text("Traveler: ")
    traveler.append(state.user_id, style="cyan")
    console.print(traveler)
    show_session_started(state, console)
    console.print("Type [cyan]/help[/cyan] to see the available commands.")

    if offer_onboarding:
        try:
            if not agent.has_profile():
                run_onboarding(agent, console, read_input)
        except TripAgentError as error:
            console.print(f"[yellow]{error} Run /onboard when you're ready.[/yellow]")

    while True:
        try:
            line = read_input().strip()
        except EOFError:
            console.print("\n[green]Safe travels![/green]")
            return
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled.[/yellow]")
            continue

        if not line:
            continue
        if line.startswith("/"):
            if not handle_command(line, state, agent, console, read_input):
                return
            continue

        try:
            with console.status("[bold cyan]Planning your trip…[/bold cyan]", spinner="dots"):
                reply = agent.reply(state.session_id, line)
            show_reply(reply, console)
            console.print(
                "[dim]Saved to session memory. Redis evaluates salient details for "
                "background promotion.[/dim]"
            )
        except AssistantMemoryWarning as warning:
            show_reply(warning.reply, console)
            console.print(f"[yellow]{warning}[/yellow]")
        except TripAgentError as error:
            console.print(f"[red]{error}[/red]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Request cancelled.[/yellow]")


def _missing_configuration(error: ValidationError) -> str:
    names = sorted({str(item["loc"][0]).upper() for item in error.errors()})
    return ", ".join(names)


@app.callback()
def main() -> None:
    """Start the interactive trip adviser."""

    console = Console()
    try:
        settings = Settings()
    except ValidationError as error:
        console.print(
            "[red]Missing or invalid configuration:[/red] "
            f"{_missing_configuration(error)}. See .env.example."
        )
        raise typer.Exit(code=2) from None

    try:
        with AgentMemory(
            str(settings.redis_agent_memory_endpoint),
            store_id=settings.redis_agent_memory_store_id,
            api_key=settings.redis_agent_memory_api_key.get_secret_value(),
        ) as memory:
            memory.health()
            openai = OpenAI(api_key=settings.openai_api_key.get_secret_value())
            user_id = prompt_for_user_id(console, settings.trip_agent_user_id)
            agent = TripAgent(
                memory=memory,
                openai=openai,
                model=settings.openai_model,
                user_id=user_id,
            )
            run_repl(
                agent,
                SessionState.new(user_id),
                console,
                offer_onboarding=True,
            )
    except (errors.AgentMemoryError, httpx.RequestError):
        console.print(
            "[red]Couldn't connect to Redis Agent Memory. Check the endpoint, store ID, "
            "and API key.[/red]"
        )
        raise typer.Exit(code=1) from None
    except OpenAIError:
        console.print("[red]Couldn't initialize OpenAI. Check OPENAI_API_KEY and try again.[/red]")
        raise typer.Exit(code=1) from None
