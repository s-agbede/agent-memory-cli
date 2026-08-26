# Trip Agent CLI

[![CI](https://github.com/s-agbede/agent-memory-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/s-agbede/agent-memory-cli/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A small, friendly trip recommendation agent that demonstrates the
[Redis Agent Memory Python SDK](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/)
directly from an interactive CLI. Redis Agent Memory supplies session and long-term context;
the OpenAI Responses API generates the answer and can use its built-in web search for current
recommendations.

## Quickstart

Requires Python 3.12+, [`uv`](https://docs.astral.sh/uv/), an OpenAI API key, and a Redis Cloud
Agent Memory service. See [Prerequisites](#prerequisites) for how to provision the last one.

```bash
git clone https://github.com/s-agbede/agent-memory-cli.git
cd agent-memory-cli
uv sync --all-groups --locked
cp .env.example .env      # fill in your OpenAI and Agent Memory credentials
uv run trip-agent
```

## What the demo shows

- Direct profile onboarding briefly rewrites explicit preferences into clear facts, then writes them to long-term memory immediately.
- Each normal user and assistant turn is stored as a Redis Agent Memory session event.
- Session history and an optional Redis-generated summary reconstruct the current conversation.
- Every reply loads the owner's direct profile as baseline context, then adds relevant semantic
  and episodic recall from long-term memory.
- Dated future trip plans are checked against saved plans before an overlapping itinerary is generated.
- OpenAI's built-in `web_search` tool finds current travel information.
- Web citations appear as inline terminal links and in a clickable source list.
- `/memories` and `/why` make direct and automatically learned Redis memories visible during the demo.

The implementation directly uses the Redis Agent Memory SDK—calling
`AgentMemory.add_session_event()`, `get_session_memory()`, and `search_long_term_memory()`—with
Redis Cloud Agent Memory. It adds no adapter, MCP integration, custom search client, or local
Docker stack. The OpenAI Responses API uses its built-in `web_search` tool for current information.

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/)
- An OpenAI API key
- A Redis Cloud database with an Agent Memory service

Create the Agent Memory service by following the
[Redis Cloud setup guide](https://redis.io/docs/latest/operate/rc/context-engine/agent-memory/create-service/).
From its Configuration tab, copy the HTTPS endpoint and Store ID. Save the Agent Memory API key
when Redis displays it; the key is shown only once.

## Install and configure

Install the locked dependencies:

```bash
uv sync --all-groups --locked
```

Copy the example configuration:

```bash
cp .env.example .env
```

Set these values in `.env`:

```dotenv
OPENAI_API_KEY=replace-with-your-openai-api-key
OPENAI_MODEL=gpt-5.5
REDIS_AGENT_MEMORY_ENDPOINT=https://replace-with-your-agent-memory-endpoint
REDIS_AGENT_MEMORY_STORE_ID=replace-with-your-store-id
REDIS_AGENT_MEMORY_API_KEY=replace-with-your-agent-memory-api-key
TRIP_AGENT_USER_ID=traveler
```

`OPENAI_MODEL` and `TRIP_AGENT_USER_ID` are optional. The default model is `gpt-5.5`; the default
traveler ID supplies the startup prompt's default value. The entered
traveler name is normalized into an Agent Memory `owner_id` (for example, `Maya Chen` becomes
`maya-chen`). It is a demo scoping key, not authentication, authorization, account creation, or
a secure identity. Enter the same traveler name after restarting when you want its long-term
memory to carry across runs.

Do not commit `.env`. It is already ignored by Git.

## Run the agent

```bash
uv run trip-agent
```

The CLI checks the Redis Agent Memory service before opening the chat. Once connected, type a
normal message or one of these commands:

| Command | Behavior |
| --- | --- |
| `/new` | Start a fresh session while retaining the traveler's long-term memories. |
| `/memories` | Browse the active owner's saved long-term memories without semantic filtering. |
| `/memories food preferences` | Semantically search the active owner's memories using a custom query. |
| `/why` | Show the long-term memories retrieved for the most recent answer. |
| `/user Maya` | Normalize Maya as the active `owner_id`, start a fresh session, clear `/why`'s prior receipt, check Maya's direct profile, then welcome a returning owner or automatically begin onboarding for a new one. |
| `/onboard` | Update the active traveler's explicit profile preferences directly in long-term memory. |
| `/help` | Show the command reference. |
| `/exit` | Close the client and leave the agent. |

## Direct writes and automatic learning

Use direct long-term-memory writes for explicit, trusted facts you already have, such as an
onboarding profile, imported preferences, or business reference data. Use session events for
normal conversation and let Redis Agent Memory identify durable information in the background.

Extraction and session summarization are asynchronous and eventually consistent, so a fact
mentioned in chat will not appear in `/memories` immediately. Direct profile writes are the
exception: `/onboard` reads each record back and verifies it, so confirmed facts are queryable
right away.

`/memories` and `/why` display two independent dimensions for every returned record:

- **Provenance:** `direct` for records deliberately written by the app, or `learned` for records
  Redis promoted from session history.
- **Kind:** `semantic fact`, `episodic event`, `retained message`, or a service-defined custom
  type shown exactly as Redis returns it. Direct profile facts are semantic; dated trip plans are
  episodic. The app shows Redis-promoted kinds rather than guessing them.

The normal reply path first uses an owner-and-namespace filter-only request to load the direct
profile, then performs owner-scoped semantic search with a relevance threshold for learned and
episodic context. The two results are merged with the profile first and duplicate Redis record IDs
removed. This makes facts such as the usual departure city available even when the current message
is not semantically similar to the profile wording. `/why` shows this merged context.

`/memories <query>` uses only the relevance-thresholded semantic search. Bare `/memories` uses an
owner-scoped filter-only browse so all direct onboarding facts are visible immediately. Direct
profile checks and dated-trip-plan checks also use owner-scoped filters only, then the app applies
the deterministic profile or date-overlap rule in code.

Retrieved memory is reference context, not executable instruction. Keep authorization, security,
and hard safety rules in application code and system instructions rather than relying on memory
retrieval to enforce them.

## Optional `trip_preference` memory type

The app works with Redis Agent Memory's built-in extraction. It also handles custom memory types
without code changes: retrieval filters on `owner_id` only, so custom-typed records are returned
by `/memories` and `/why` and labelled with the type name exactly as Redis reports it.

One limit is worth knowing before you configure one. The app renders each record's `text` field.
It does not read `MemoryRecord.attributes`, where Redis puts a custom type's structured fields, so
those values are retrieved but not displayed. A custom type is therefore visible as a labelled
record rather than as a structured card.

To try it, configure the optional custom memory type from Redis's travel-planning quickstart:

- Name: `trip_preference`
- Description: `Structured requirements for a planned trip`
- Destinations: `list[str]`
- Travel period: `str`
- Dietary requirements: `list[str]`
- Food preferences: `list[str]`

A suitable extraction instruction is:

```text
Extract trip requirements only when the user states a destination or travel plan.
Preserve explicit dietary requirements and food preferences.
```

Custom types are configured on the Redis Agent Memory service, not in this application.

## Privacy and safety

Configure Redis Agent Memory's sensitive-data exclusions for passwords, access tokens, recovery
codes, payment-card information, and booking confirmation codes. Semantic exclusions guide the
extraction model but are advisory rather than a guarantee. Session content still reaches the
configured model provider.

Do not enter real secrets, payment details, recovery codes, or booking confirmation codes in
this demo.

## Development checks

Run the unit suite and quality checks:

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

The normal suite never calls Redis or OpenAI. The application startup check and the opt-in
integration test each call `health()` plus read-only `list_sessions(limit=1, include_all=True)`.
The SDK requires this explicit all-sessions scope when no owner filter is supplied. These checks
create no events or memories and do not trigger promotion test data. To run that real Redis check
without calling OpenAI:

```bash
RUN_REDIS_INTEGRATION=1 uv run pytest tests/test_integration.py -v
```

## Project structure

```text
src/trip_agent/
  cli.py          Typer CLI, the interactive loop, and slash commands
  agent.py        Redis Agent Memory + OpenAI Responses calls
  config.py       Pydantic Settings loaded from .env
  prompt.py       System instructions and context assembly
  formatting.py   Rich rendering, including citation links
tests/            Unit tests (stubbed) plus an opt-in Redis integration test
docs/             Design specs and implementation plans
```

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and
project conventions, and [SECURITY.md](SECURITY.md) for how to report a vulnerability and how
credentials are handled.

## License

Released under the [MIT License](LICENSE).

## References

- [Redis Agent Memory overview](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/)
- [Redis Agent Memory developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/)
- [Redis Agent Memory Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/)
- [OpenAI Responses API web search](https://developers.openai.com/api/docs/guides/tools-web-search)
