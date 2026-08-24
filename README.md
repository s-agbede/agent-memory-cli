# Trip Agent CLI

A small, friendly trip recommendation agent that demonstrates the
[Redis Agent Memory Python SDK](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/)
directly from an interactive CLI. Redis Agent Memory supplies session and long-term context;
the OpenAI Responses API generates the answer and can use its built-in web search for current
recommendations.

## What the demo shows

- Direct profile onboarding briefly rewrites explicit preferences into clear facts, then writes them to long-term memory immediately.
- Each normal user and assistant turn is stored as a Redis Agent Memory session event.
- Session history and an optional Redis-generated summary reconstruct the current conversation.
- Owner-scoped long-term memory recalls useful preferences across fresh sessions.
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
OPENAI_MODEL=gpt-5.6-luna
REDIS_AGENT_MEMORY_ENDPOINT=https://replace-with-your-agent-memory-endpoint
REDIS_AGENT_MEMORY_STORE_ID=replace-with-your-store-id
REDIS_AGENT_MEMORY_API_KEY=replace-with-your-agent-memory-api-key
TRIP_AGENT_USER_ID=traveler
```

`OPENAI_MODEL` and `TRIP_AGENT_USER_ID` are optional. The default model is the cost-conscious
`gpt-5.6-luna`; the default traveler ID supplies the startup prompt's default value. The entered
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
| `/memories` | Semantically search the active owner's known travel plans and preferences. |
| `/memories food preferences` | Semantically search the active owner's memories using a custom query. |
| `/why` | Show the long-term memories retrieved for the most recent answer. |
| `/user Maya` | Normalize Maya as the active `owner_id`, start a fresh session, clear `/why`'s prior receipt, check Maya's direct profile, then welcome a returning owner or automatically begin onboarding for a new one. |
| `/onboard` | Update the active traveler's explicit profile preferences directly in long-term memory. |
| `/help` | Show the command reference. |
| `/exit` | Close the client and leave the agent. |

## Suggested video flow

1. At startup, enter a traveler name such as `Maya Chen`. The CLI displays a new session UUID,
   checks for a direct profile, and automatically starts onboarding when none exists. A returning
   owner is greeted warmly and skips these questions.

2. With no confirmation step, answer the four durable profile questions:

   ```text
   What kinds of trips and places do you enjoy?
   What food or dietary needs should I remember?
   What budget works for you?
   What city do you usually travel from?
   ```

   Blank answers are skipped. If every answer is blank, onboarding ends without an OpenAI rewrite
   or Redis profile write. Otherwise, a short LLM pass turns the remaining answers into concise,
   fact-preserving profile statements.
   Those explicit facts are then written directly to owner-scoped long-term memory, so
   `/memories` can show them immediately. This avoids a cold start. Enter `/cancel` at any
   question—or use Ctrl+C or EOF—to discard the entire attempt: there is no OpenAI rewrite and
   no Redis profile write. Running onboarding again updates the answered categories rather than
   duplicating them.

3. Ask for a current recommendation:

   ```text
   Where should I eat in Kyoto, and which places are currently open on Sundays?
   ```

   Point out the OpenAI web-search citations in the answer.

   Then run `/why` to show the exact direct and learned memories that were retrieved for the
   answer. This is a retrieval receipt, not a claim that one memory mechanically caused every
   part of the response.

4. Add a preference naturally in chat:

   ```text
   For shorter trips, I prefer trains when the journey is practical. Please remember that.
   ```

   This turn is saved as session memory. Redis Agent Memory extracts, deduplicates, and promotes
   salient facts in the background; it is eventually consistent, so do not expect the new memory
   to appear immediately.

5. After a brief pause, run:

   ```text
   /memories
   ```

6. Exit the application and run `uv run trip-agent` again. Enter the same traveler name, point
   out the different session UUID, then ask a question that depends on durable preferences:

   ```text
   Can you suggest a different city break that fits what you know about me?
   ```

New long-term memories may not appear immediately because extraction runs asynchronously.
Session summarization is also handled by Redis Agent Memory in the background. `/new` remains a
quick way to create another session in one process; restarting is the clearest video proof that
only server-side long-term memory persisted.

## Direct writes and automatic learning

Use direct long-term-memory writes for explicit, trusted facts you already have, such as an
onboarding profile, imported preferences, or business reference data. Use session events for
normal conversation and let Redis Agent Memory identify durable information in the background.

`/memories` and `/why` display two independent dimensions for every returned record:

- **Provenance:** `direct` for records deliberately written by the app, or `learned` for records
  Redis promoted from session history.
- **Kind:** `semantic fact`, `episodic event`, `retained message`, or a service-defined custom
  type shown exactly as Redis returns it. Direct profile facts are semantic; dated trip plans are
  episodic. The app shows Redis-promoted kinds rather than guessing them.

The normal reply path and `/memories` use owner-scoped semantic search with a relevance threshold,
so they return relevant matches rather than every record. Direct-profile checks and dated-trip-plan
checks are different: they use owner-scoped filters only, then the app applies the deterministic
profile or date-overlap rule in code.

Retrieved memory is reference context, not executable instruction. Keep authorization, security,
and hard safety rules in application code and system instructions rather than relying on memory
retrieval to enforce them.

## Optional `trip_preference` memory type

The app works with Redis Agent Memory's built-in extraction. For a more visual demo, configure
the optional custom memory type from Redis's travel-planning quickstart:

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

## References

- [Redis Agent Memory overview](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/)
- [Redis Agent Memory developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/)
- [Redis Agent Memory Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/)
- [OpenAI Responses API web search](https://developers.openai.com/api/docs/guides/tools-web-search)
