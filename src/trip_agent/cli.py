"""Interactive terminal interface for the trip recommendation agent."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import uuid4

import httpx
import typer
from openai import OpenAI
from pydantic import ValidationError
from redis_agent_memory import AgentMemory, errors
from rich.console import Console
from rich.prompt import Prompt

from trip_agent.agent import (
    AgentReply,
    AssistantMemoryWarning,
    MemoryView,
    TripAgent,
    TripAgentError,
)
from trip_agent.config import Settings
from trip_agent.formatting import render_reply

DEFAULT_MEMORY_QUERY = "What travel preferences and plans are known about this traveler?"

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


def show_help(console: Console) -> None:
    """Show the supported interactive commands."""

    console.print("[bold]Commands[/bold]")
    console.print("  [cyan]/new[/cyan]                 Start a fresh conversation")
    console.print("  [cyan]/memories [query][/cyan]  View relevant long-term memories")
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
        console.print(f"  [dim]{memory.memory_type}[/dim]  {memory.text}")


def show_reply(reply: AgentReply, console: Console) -> None:
    """Render an answer with inline links and a source list."""

    rendered, sources = render_reply(reply.text, reply.citations)
    console.print("\n[bold green]Trip agent[/bold green]")
    console.print(rendered)
    if sources:
        console.print("\n[bold]Sources[/bold]")
        for source in sources:
            console.print("  • ", source, sep="")


def handle_command(
    line: str,
    state: SessionState,
    agent: TripAgent,
    console: Console,
) -> bool:
    """Handle one slash command and return whether the REPL should continue."""

    command, _, argument = line.partition(" ")
    if command == "/exit":
        console.print("[green]Safe travels![/green]")
        return False
    if command == "/new":
        state.reset()
        console.print(
            "[green]Fresh session started.[/green] Your long-term memories are still here."
        )
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

    console.print("[yellow]I don't know that command yet. Try /help.[/yellow]")
    return True


def _read_input() -> str:
    return Prompt.ask("\n[bold cyan]You[/bold cyan]")


def run_repl(
    agent: TripAgent,
    state: SessionState,
    console: Console,
    read_input: Callable[[], str] = _read_input,
) -> None:
    """Run the interactive chat loop."""

    console.print("[bold green]Hi! I'm your friendly trip-planning companion.[/bold green]")
    console.print(f"Traveler: [cyan]{state.user_id}[/cyan]")
    console.print("Type [cyan]/help[/cyan] to see the available commands.")

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
            if not handle_command(line, state, agent, console):
                return
            continue

        try:
            show_reply(agent.reply(state.session_id, line), console)
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
            agent = TripAgent(
                memory=memory,
                openai=openai,
                model=settings.openai_model,
                user_id=settings.trip_agent_user_id,
            )
            run_repl(
                agent,
                SessionState.new(settings.trip_agent_user_id),
                console,
            )
    except (errors.AgentMemoryError, httpx.RequestError):
        console.print(
            "[red]Couldn't connect to Redis Agent Memory. Check the endpoint, store ID, "
            "and API key.[/red]"
        )
        raise typer.Exit(code=1) from None
