# Contributing

Thanks for your interest in this project. It is a focused demo of the
[Redis Agent Memory Python SDK](https://redis.io/docs/latest/develop/ai/context-engine/agent-memory/python-sdk-quickstart/),
so the most valuable contributions keep it small, readable, and faithful to the SDK's real usage.

## Getting set up

You need Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/<your-username>/agent-memory-cli.git
cd agent-memory-cli
uv sync --all-groups --locked
cp .env.example .env   # then fill in your own credentials
```

See the [README](README.md#install-and-configure) for what each environment variable means and how
to provision a Redis Cloud Agent Memory service.

## Development workflow

Run the full check suite before opening a pull request:

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

All four must pass. CI runs exactly these commands on every pull request.

The default test suite never calls Redis or OpenAI. To exercise the real read-only Redis
health check:

```bash
RUN_REDIS_INTEGRATION=1 uv run pytest tests/test_integration.py -v
```

## Conventions

- **Tests first.** Add or update a test that fails before you change behaviour, then make it pass.
- **Type everything.** `mypy` runs in strict mode with the Pydantic plugin. Avoid `Any` and
  `# type: ignore`; if one is unavoidable, add a comment explaining why.
- **Pydantic at the boundaries.** Configuration and structured data use Pydantic models rather
  than loose dictionaries.
- **Keep it flat.** This is a small application. Please do not add architectural layers,
  adapters, or abstractions without a concrete need.
- **Fail loudly.** Do not swallow exceptions. Surface errors at the CLI boundary with a clear
  message.
- **Formatting and linting** are handled by `ruff` (100-char lines, `E`/`F`/`I`/`UP`/`B`/`SIM`).
  Run `uv run ruff format .` to fix formatting.

## Pull requests

1. Branch off `main`.
2. Keep the change as small as it can be while still being coherent.
3. Describe what changed and why, and note anything you deliberately left out.
4. Make sure no secrets are included. `.env` is gitignored — please keep it that way, and never
   paste real API keys into issues, tests, or fixtures.

## Reporting issues

When filing a bug, include the command you ran, what you expected, what happened, and your
Python and `uv` versions. Redact endpoints, store IDs, and API keys from any logs you attach.

## Security

Please do not open a public issue for a security problem. See [SECURITY.md](SECURITY.md).
