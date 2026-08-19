# Trip Recommendation Agent CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a warm interactive trip-recommendation CLI that directly demonstrates Redis Cloud Agent Memory session and long-term memory calls and OpenAI Responses API web search.

**Architecture:** A small synchronous `TripAgent` coordinates the official Redis and OpenAI SDK clients directly. Focused pure helpers normalize SDK responses, build model context, and render citations; the Typer/Rich CLI owns the prompt loop and slash commands.

**Tech Stack:** Python 3.12, uv, redis-agent-memory 0.2.1, OpenAI Python SDK, Pydantic Settings, Typer, Rich, pytest, Ruff, mypy.

---

## File Map

- `pyproject.toml` — package metadata, pinned Redis SDK, runtime and development dependencies, entry point, and tool configuration.
- `.gitignore` — Python, uv, test, type-check, and local-secret exclusions.
- `.env.example` — documented placeholder configuration.
- `src/trip_agent/__init__.py` — package version.
- `src/trip_agent/config.py` — typed environment settings.
- `src/trip_agent/prompt.py` — warm trip-adviser instructions.
- `src/trip_agent/formatting.py` — Redis response normalization, OpenAI input construction, and citation extraction/rendering.
- `src/trip_agent/agent.py` — direct Redis/OpenAI conversational turn coordinator.
- `src/trip_agent/cli.py` — Typer entry point, client composition, prompt loop, commands, and user-facing errors.
- `tests/test_config.py` — configuration behavior.
- `tests/test_formatting.py` — context and citation behavior.
- `tests/test_agent.py` — turn sequencing and failure behavior using lightweight fakes.
- `tests/test_cli.py` — session and slash-command behavior.
- `tests/test_integration.py` — opt-in Redis health smoke test.
- `README.md` — setup, security note, command reference, and video demo flow.

### Task 1: Project scaffold and typed settings

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/trip_agent/__init__.py`
- Create: `src/trip_agent/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Create the non-behavioral project scaffold**

Create `pyproject.toml` with this configuration:

```toml
[project]
name = "trip-agent-cli"
version = "0.1.0"
description = "A friendly trip recommendation CLI powered by Redis Agent Memory"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
  "openai>=1.99.0",
  "pydantic-settings>=2.10.1",
  "redis-agent-memory==0.2.1",
  "rich>=14.1.0",
  "typer>=0.16.0",
]

[project.scripts]
trip-agent = "trip_agent.cli:app"

[dependency-groups]
dev = [
  "mypy>=1.17.1",
  "pytest>=8.4.1",
  "pytest-cov>=6.2.1",
  "ruff>=0.12.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/trip_agent"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["trip_agent"]
```

Create the package version:

```python
"""Trip recommendation agent CLI."""

__version__ = "0.1.0"
```

Create `.gitignore` with `.env`, `.venv`, Python caches, build outputs, coverage files, `.pytest_cache`, `.mypy_cache`, and `.ruff_cache`. Create `.env.example` with the six configuration names from the design and placeholder values for required secrets.

- [ ] **Step 2: Resolve dependencies**

Run: `uv sync --all-groups`

Expected: dependencies resolve and `uv.lock` is created; inspect installed Redis and OpenAI SDK signatures before completing implementation steps below.

- [ ] **Step 3: Write failing settings tests**

```python
from pydantic import ValidationError
import pytest

from trip_agent.config import Settings


REQUIRED = {
    "OPENAI_API_KEY": "openai-secret",
    "REDIS_AGENT_MEMORY_ENDPOINT": "https://memory.example.com",
    "REDIS_AGENT_MEMORY_STORE_ID": "store-123",
    "REDIS_AGENT_MEMORY_API_KEY": "redis-secret",
}


def test_settings_load_required_values_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert settings.openai_model == "gpt-5.6-luna"
    assert settings.trip_agent_user_id == "traveler"
    assert str(settings.redis_agent_memory_endpoint) == "https://memory.example.com/"


def test_settings_require_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 4: Run the settings tests to verify RED**

Run: `uv run pytest tests/test_config.py -v`

Expected: FAIL because `trip_agent.config` does not exist.

- [ ] **Step 5: Implement the settings model**

```python
"""Environment configuration for the trip agent."""

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr
    openai_model: str = "gpt-5.6-luna"
    redis_agent_memory_endpoint: AnyHttpUrl
    redis_agent_memory_store_id: str = Field(min_length=1)
    redis_agent_memory_api_key: SecretStr
    trip_agent_user_id: str = Field(default="traveler", min_length=1)
```

- [ ] **Step 6: Verify GREEN and commit**

Run: `uv run pytest tests/test_config.py -v && uv run ruff check . && uv run mypy src`

Expected: all commands pass.

```bash
git add pyproject.toml uv.lock .gitignore .env.example src/trip_agent/__init__.py src/trip_agent/config.py tests/test_config.py
git commit -m "chore: scaffold trip agent configuration"
```

### Task 2: Redis context and web citation formatting

**Files:**
- Create: `src/trip_agent/formatting.py`
- Create: `tests/test_formatting.py`

- [ ] **Step 1: Write failing tests for model input and citations**

Use `SimpleNamespace` objects shaped like the installed SDK responses:

```python
from types import SimpleNamespace

from rich.text import Text

from trip_agent.formatting import build_model_input, extract_citations, render_reply


def test_build_model_input_includes_summary_memories_and_events_once() -> None:
    session = SimpleNamespace(
        summary=SimpleNamespace(text="Planning a spring trip to Kyoto."),
        events=[
            SimpleNamespace(role="user", content=[SimpleNamespace(text="I avoid meat.")]),
            SimpleNamespace(role="assistant", content=[SimpleNamespace(text="I'll remember that.")]),
        ],
    )
    memories = SimpleNamespace(
        items=[SimpleNamespace(text="The traveler is vegetarian.", memory_type="preference")]
    )

    model_input = build_model_input(session, memories)

    assert model_input[0]["role"] == "developer"
    assert "Planning a spring trip" in str(model_input[0]["content"])
    assert "traveler is vegetarian" in str(model_input[0]["content"])
    assert [item["role"] for item in model_input[1:]] == ["user", "assistant"]
    assert str(model_input).count("I avoid meat.") == 1


def test_extract_and_render_clickable_unique_citations() -> None:
    annotation = SimpleNamespace(
        type="url_citation",
        start_index=6,
        end_index=11,
        title="Kyoto guide",
        url="https://example.com/kyoto",
    )
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="Visit Kyoto", annotations=[annotation, annotation])],
            )
        ],
        output_text="Visit Kyoto",
    )

    citations = extract_citations(response)
    rendered, sources = render_reply(response.output_text, citations)

    assert len(citations) == 1
    assert isinstance(rendered, Text)
    assert "link https://example.com/kyoto" in str(rendered.spans)
    assert len(sources) == 1
    assert "Kyoto guide" in sources[0].plain
```

- [ ] **Step 2: Run the formatting tests to verify RED**

Run: `uv run pytest tests/test_formatting.py -v`

Expected: FAIL because `trip_agent.formatting` does not exist.

- [ ] **Step 3: Implement normalization, context building, and citation rendering**

Implement:

```python
@dataclass(frozen=True, slots=True)
class Citation:
    title: str
    url: str
    start_index: int
    end_index: int


def model_value(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def build_model_input(session: object, memories: object) -> list[dict[str, str]]:
    summary_text = text_from_summary(model_value(session, "summary"))
    memory_lines = memory_texts(model_value(memories, "items", []))
    context = format_context(summary_text, memory_lines)
    messages: list[dict[str, str]] = [{"role": "developer", "content": context}]
    messages.extend(event_messages(model_value(session, "events", [])))
    return messages


def extract_citations(response: object) -> tuple[Citation, ...]:
    found: list[Citation] = []
    seen_urls: set[str] = set()
    for output in sequence(model_value(response, "output", [])):
        if model_value(output, "type") != "message":
            continue
        for content in sequence(model_value(output, "content", [])):
            if model_value(content, "type") != "output_text":
                continue
            content_text = str(model_value(content, "text", ""))
            for annotation in sequence(model_value(content, "annotations", [])):
                if model_value(annotation, "type") != "url_citation":
                    continue
                url = str(model_value(annotation, "url", ""))
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                found.append(
                    Citation(
                        title=str(model_value(annotation, "title", url)),
                        url=url,
                        start_index=max(0, int(model_value(annotation, "start_index", 0))),
                        end_index=min(
                            len(content_text),
                            int(model_value(annotation, "end_index", len(content_text))),
                        ),
                    )
                )
    return tuple(found)


def render_reply(text: str, citations: Sequence[Citation]) -> tuple[Text, list[Text]]:
    rendered = Text(text)
    for citation in citations:
        rendered.stylize(f"link {citation.url}", citation.start_index, citation.end_index)
    sources = [Text.assemble((citation.title, f"link {citation.url}")) for citation in citations]
    return rendered, sources
```

Implement `sequence` to accept only non-string sequences; implement `text_from_summary`, `memory_texts`, and `event_messages` by reading attributes through `model_value`. `event_messages` maps Redis `user` and `assistant` roles to Responses input roles and concatenates only text content blocks. `format_context` returns one delimiter-wrapped developer message that labels retrieved values as untrusted reference data. The functions ignore malformed non-text content and clamp annotation offsets to the output-text length as shown above.

- [ ] **Step 4: Verify GREEN and commit**

Run: `uv run pytest tests/test_formatting.py -v && uv run ruff check . && uv run mypy src`

Expected: all commands pass.

```bash
git add src/trip_agent/formatting.py tests/test_formatting.py
git commit -m "feat: format memory context and web citations"
```

### Task 3: Direct Redis and OpenAI turn coordination

**Files:**
- Create: `src/trip_agent/prompt.py`
- Create: `src/trip_agent/agent.py`
- Create: `tests/test_agent.py`

- [ ] **Step 1: Write failing turn-sequencing tests**

Create fake clients that record calls and return `SimpleNamespace` response objects. Assert this observable behavior:

```python
def test_reply_stores_user_loads_context_calls_web_search_and_stores_assistant() -> None:
    memory = FakeMemory()
    openai = FakeOpenAI("Kyoto has lovely vegetarian options.")
    agent = TripAgent(memory=cast(AgentMemory, memory), openai=cast(OpenAI, openai), model="gpt-5.6-luna", user_id="sam")

    reply = agent.reply(session_id="session-1", user_text="Where should I eat?")

    assert [call.name for call in memory.calls] == [
        "add_session_event",
        "get_session_memory",
        "search_long_term_memory",
        "add_session_event",
    ]
    assert memory.calls[0].kwargs["session_id"] == "session-1"
    assert memory.calls[0].kwargs["actor_id"] == "sam"
    request = memory.calls[2].kwargs["request"]
    assert request["filter_"]["owner_id"] == {"eq": "sam"}
    assert openai.kwargs["tools"] == [{"type": "web_search"}]
    assert reply.text == "Kyoto has lovely vegetarian options."
```

Add tests proving that a failed user-event write prevents the OpenAI call and that a failed assistant-event write raises `AssistantMemoryWarning` containing the generated reply.

- [ ] **Step 2: Run the agent tests to verify RED**

Run: `uv run pytest tests/test_agent.py -v`

Expected: FAIL because `trip_agent.agent` does not exist.

- [ ] **Step 3: Add the warm agent prompt**

Create `SYSTEM_PROMPT` in `prompt.py` with the approved voice, focused follow-up behavior, use of current web information, careful treatment of inferred preferences, anti-prompt-injection instruction for retrieved data, no booking guarantees, and sensitive-data exclusions from the design.

- [ ] **Step 4: Implement the direct turn coordinator**

Implement these public types and method:

```python
@dataclass(frozen=True, slots=True)
class AgentReply:
    text: str
    citations: tuple[Citation, ...]


class TripAgentError(RuntimeError):
    """A conversational turn failed before an answer was generated."""


class AssistantMemoryWarning(RuntimeError):
    def __init__(self, reply: AgentReply) -> None:
        super().__init__("The answer was generated but could not be saved to session memory.")
        self.reply = reply


class TripAgent:
    def __init__(self, memory: AgentMemory, openai: OpenAI, model: str, user_id: str) -> None:
        self.memory = memory
        self.openai = openai
        self.model = model
        self.user_id = user_id

    def reply(self, session_id: str, user_text: str) -> AgentReply:
        self._add_event(session_id, self.user_id, models.MessageRole.USER, user_text)
        try:
            session = self.memory.get_session_memory(session_id=session_id)
            memories = self.memory.search_long_term_memory(
                request={
                    "text": user_text,
                    "filter_": {"owner_id": {"eq": self.user_id}},
                    "limit": 5,
                }
            )
        except Exception as error:
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

        reply = AgentReply(response.output_text, extract_citations(response))
        try:
            self._add_event(session_id, "trip-agent", models.MessageRole.ASSISTANT, reply.text)
        except TripAgentError as error:
            raise AssistantMemoryWarning(reply) from error
        return reply
```

`_add_event` must call `add_session_event` with `models.Text(text=text)` and `datetime.now(timezone.utc)`. Catch `(redis_agent_memory.errors.AgentMemoryError, httpx.RequestError)` around Redis calls and `openai.OpenAIError` around the Responses call, wrapping them in the user-facing error types shown above. Tests simulate Redis network failures with `httpx.ConnectError` so they exercise the same boundary.

- [ ] **Step 5: Verify GREEN and commit**

Run: `uv run pytest tests/test_agent.py -v && uv run pytest && uv run ruff check . && uv run mypy src`

Expected: all commands pass.

```bash
git add src/trip_agent/prompt.py src/trip_agent/agent.py tests/test_agent.py
git commit -m "feat: add memory-aware trip agent turns"
```

### Task 4: Interactive CLI and slash commands

**Files:**
- Create: `src/trip_agent/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing command and REPL tests**

Test `SessionState.new()`, `/new`, `/memories` with and without a query, `/help`, unknown commands, empty input, `/exit`, and an `AssistantMemoryWarning` that still renders the reply. Use an in-memory Rich `Console(file=StringIO(), force_terminal=False)` and a fake agent.

Representative test:

```python
def test_new_command_replaces_session_but_keeps_user() -> None:
    state = SessionState(session_id="old", user_id="sam")
    console, output = recording_console()

    keep_running = handle_command("/new", state, FakeAgent(), console)

    assert keep_running is True
    assert state.session_id != "old"
    assert state.user_id == "sam"
    assert "fresh session" in output.getvalue().lower()


def test_memories_uses_default_query() -> None:
    state = SessionState(session_id="session", user_id="sam")
    agent = FakeAgent()
    console, output = recording_console()

    handle_command("/memories", state, agent, console)

    assert agent.memory_query == "What travel preferences and plans are known about this traveler?"
    assert "vegetarian" in output.getvalue()
```

- [ ] **Step 2: Run CLI tests to verify RED**

Run: `uv run pytest tests/test_cli.py -v`

Expected: FAIL because `trip_agent.cli` does not exist.

- [ ] **Step 3: Implement state, command dispatch, and prompt loop**

Implement:

```python
@dataclass(slots=True)
class SessionState:
    session_id: str
    user_id: str

    @classmethod
    def new(cls, user_id: str) -> "SessionState":
        return cls(session_id=str(uuid4()), user_id=user_id)

    def reset(self) -> None:
        self.session_id = str(uuid4())


def handle_command(line: str, state: SessionState, agent: TripAgent, console: Console) -> bool:
    command, _, argument = line.partition(" ")
    if command == "/exit":
        return False
    if command == "/new":
        state.reset()
        console.print("[green]Fresh session started.[/green] Your long-term memories are still here.")
        return True
    if command == "/memories":
        show_memories(agent.search_memories(argument.strip() or DEFAULT_MEMORY_QUERY), console)
        return True
    if command == "/help":
        show_help(console)
        return True
    console.print("[yellow]I don't know that command yet. Try /help.[/yellow]")
    return True
```

Add `TripAgent.search_memories(query, limit=10)` as a direct owner-filtered SDK call returning a normalized tuple of memory rows. Keep output rendering in the CLI. Implement `run_repl` with an injectable `read_input` callable so tests avoid patching terminal internals. Ignore blank lines; handle EOF and keyboard interrupts without tracebacks.

- [ ] **Step 4: Compose real clients in the Typer entry point**

Create `app = typer.Typer(invoke_without_command=True, no_args_is_help=False)` and a callback that:

1. loads `Settings`;
2. opens `AgentMemory(str(endpoint), store_id=..., api_key=...)` as a context manager;
3. calls `health()`;
4. creates `OpenAI(api_key=...)`;
5. creates `TripAgent` and starts `run_repl`;
6. converts validation, Redis SDK, HTTPX, and OpenAI startup failures into concise Rich messages and a non-zero Typer exit.

- [ ] **Step 5: Verify GREEN and commit**

Run: `uv run pytest tests/test_cli.py -v && uv run pytest && uv run ruff check . && uv run mypy src`

Expected: all commands pass.

```bash
git add src/trip_agent/cli.py src/trip_agent/agent.py tests/test_cli.py tests/test_agent.py
git commit -m "feat: add interactive trip agent CLI"
```

### Task 5: Documentation and opt-in integration verification

**Files:**
- Create: `README.md`
- Create: `tests/test_integration.py`
- Modify: `.env.example`

- [ ] **Step 1: Write the skipped-by-default integration test**

```python
import os

import pytest
from redis_agent_memory import AgentMemory

from trip_agent.config import Settings


@pytest.mark.skipif(
    os.getenv("RUN_REDIS_INTEGRATION") != "1",
    reason="set RUN_REDIS_INTEGRATION=1 to call Redis Agent Memory",
)
def test_redis_agent_memory_health() -> None:
    settings = Settings()
    with AgentMemory(
        str(settings.redis_agent_memory_endpoint),
        store_id=settings.redis_agent_memory_store_id,
        api_key=settings.redis_agent_memory_api_key.get_secret_value(),
    ) as memory:
        assert memory.health() is not None
```

- [ ] **Step 2: Run the test and confirm its safe default**

Run: `uv run pytest tests/test_integration.py -v`

Expected: one skipped test and no external requests.

- [ ] **Step 3: Write the README**

Document exactly:

- the Redis Cloud Agent Memory and Python/OpenAI prerequisites;
- `uv sync --all-groups` setup;
- `.env.example` copy and every configuration variable;
- `uv run trip-agent` startup;
- `/new`, `/memories [query]`, `/help`, and `/exit`;
- the five-step demo flow from the design;
- asynchronous extraction behavior;
- optional `trip_preference` fields: destinations, travel period, dietary requirements, and food preferences;
- clickable OpenAI web citations;
- the advisory nature of semantic exclusions and the instruction not to enter secrets, payment data, or booking codes;
- unit checks and the opt-in `RUN_REDIS_INTEGRATION=1 uv run pytest tests/test_integration.py -v` command.

- [ ] **Step 4: Run full verification**

Run:

```bash
uv run pytest --cov=trip_agent --cov-report=term-missing
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run trip-agent --help
```

Expected: tests pass with only the integration test skipped, formatting/lint/type checks pass, and the CLI help command exits successfully.

- [ ] **Step 5: Commit the completed application**

```bash
git add README.md .env.example tests/test_integration.py
git commit -m "docs: add trip agent setup and demo guide"
```

### Task 6: Final spec audit

**Files:**
- Modify only files requiring corrections discovered by the audit.

- [ ] **Step 1: Audit acceptance criteria**

Confirm the repository contains no MCP dependency/code, Docker file, custom REST client, memory adapter, or agent framework. Confirm all four slash commands, owner scoping, session replacement, built-in `web_search`, citation links, friendly prompt, and safe error behavior have corresponding passing tests.

- [ ] **Step 2: Run final verification from a clean process**

Run:

```bash
uv sync --all-groups --locked
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src
git status --short
```

Expected: dependency sync succeeds, all normal tests pass with the integration test skipped, quality checks pass, and Git shows only intentional plan tracking or no changes.

- [ ] **Step 3: Commit any audit corrections**

If the audit required changes, add only those files and commit with `fix: complete trip agent acceptance criteria`. If no correction was needed, do not create an empty commit.
