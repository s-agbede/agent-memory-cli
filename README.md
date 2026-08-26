# Trip Agent CLI

[![CI](https://github.com/s-agbede/agent-memory-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/s-agbede/agent-memory-cli/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A trip recommendation agent that remembers you between runs — five small Python modules, no
infrastructure of its own.

It is a working demo of the
[Redis Agent Memory Python SDK](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/):
Redis supplies session and long-term memory, and the OpenAI Responses API writes the answer using
its built-in web search. No adapter, no MCP layer, no custom search client, no local Docker stack.

## Quickstart

You need Python 3.12+, [`uv`](https://docs.astral.sh/uv/), an OpenAI API key, and a Redis Cloud
Agent Memory service.

Create the Agent Memory service with the
[Redis Cloud setup guide](https://redis.io/docs/latest/operate/rc/context-engine/agent-memory/create-service/).
From its Configuration tab, copy the HTTPS endpoint and Store ID, and save the API key when it is
shown — Redis displays it only once.

```bash
git clone https://github.com/s-agbede/agent-memory-cli.git
cd agent-memory-cli
uv sync --all-groups --locked
cp .env.example .env
```

Fill in `.env`:

```dotenv
OPENAI_API_KEY=replace-with-your-openai-api-key
REDIS_AGENT_MEMORY_ENDPOINT=https://replace-with-your-agent-memory-endpoint
REDIS_AGENT_MEMORY_STORE_ID=replace-with-your-store-id
REDIS_AGENT_MEMORY_API_KEY=replace-with-your-agent-memory-api-key
```

`OPENAI_MODEL` (default `gpt-5.5`) and `TRIP_AGENT_USER_ID` (default `traveler`) are optional.
`.env` is gitignored; keep it that way.

```bash
uv run trip-agent
```

The CLI checks the Redis service, then asks for a traveler name. A new traveler gets four profile
questions; a returning one is greeted and skips them. Enter the same traveler name on a later run
to pick up that traveler's long-term memory.

## What it does

- **Onboards you directly.** Four profile questions are rewritten into concise facts and written
  straight to long-term memory, then read back and verified — so there is no cold start.
- **Remembers across restarts.** Each turn is stored as a session event; Redis promotes durable
  facts from the conversation in the background.
- **Grounds every reply in your profile,** then adds relevant semantic and episodic recall on top.
- **Searches the web** through the Responses API's built-in `web_search`, with citations as
  clickable terminal links.
- **Shows its work.** `/memories` and `/why` make the retrieved Redis records visible, labelled by
  where they came from and what kind they are.

## Commands

| Command | Behavior |
| --- | --- |
| `/memories` | Browse this traveler's saved long-term memories, unranked. |
| `/memories food preferences` | Semantically search this traveler's memories. |
| `/why` | Show the memories retrieved for the most recent answer. |
| `/onboard` | Update the profile preferences held in long-term memory. |
| `/user Maya` | Switch active traveler, start a fresh session, and welcome or onboard them. |
| `/new` | Start a fresh session, keeping long-term memories. |
| `/help` | Show the command reference. |
| `/exit` | Leave the agent. |

## How memory works

Two things write to memory. The app writes **direct** facts it already trusts, like your
onboarding profile. Redis promotes **learned** facts out of session history on its own.

That distinction is the one thing to know while using the demo, because it governs timing.
Extraction is asynchronous and eventually consistent, so a preference you mention in chat will
not show up in `/memories` right away. Verified direct profile writes are queryable immediately.

Every record shown by `/memories` and `/why` carries both labels: provenance (`direct` or
`learned`) and kind (`semantic fact`, `episodic event`, `retained message`, or a custom type).

For retrieval paths, merge order, and why the profile is loaded with a filter-only request rather
than semantic search, see **[docs/memory-design.md](docs/memory-design.md)**. Custom memory types
are covered in **[docs/custom-types.md](docs/custom-types.md)**.

## Privacy and safety

The traveler name is normalized into an Agent Memory `owner_id` (`Maya Chen` becomes `maya-chen`).
It is a demo scoping key — not authentication, authorization, or a secure identity.

Retrieved memory is reference context, not executable instruction. Keep authorization and hard
safety rules in application code, not in retrieval.

Session content reaches the configured model provider. Redis's sensitive-data exclusions guide
the extraction model but are advisory, not a guarantee. **Do not enter real secrets, payment
details, recovery codes, or booking confirmation codes.** See [SECURITY.md](SECURITY.md).

## Development

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

CI runs exactly these four. The default suite never calls Redis or OpenAI. To exercise the real
read-only Redis health check:

```bash
RUN_REDIS_INTEGRATION=1 uv run pytest tests/test_integration.py -v
```

```text
src/trip_agent/
  cli.py          Typer CLI, the interactive loop, and slash commands
  agent.py        Redis Agent Memory + OpenAI Responses calls
  config.py       Pydantic Settings loaded from .env
  prompt.py       System instructions and context assembly
  formatting.py   Rich rendering, including citation links
```

See [CONTRIBUTING.md](CONTRIBUTING.md) to contribute. Released under the [MIT License](LICENSE).

## References

- [Redis Agent Memory overview](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/)
- [Redis Agent Memory developer guide](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/developer-guide/)
- [Redis Agent Memory Python SDK quickstart](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/)
- [OpenAI Responses API web search](https://developers.openai.com/api/docs/guides/tools-web-search)
