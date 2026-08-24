# GPT-5.5 Default Design

## Goal

Make `gpt-5.5` the official default OpenAI model for the trip-agent CLI while preserving the
existing `OPENAI_MODEL` environment override.

## Approaches Considered

1. **Align runtime, tests, and current setup documentation — selected.** Keep the existing
   `Settings.openai_model = "gpt-5.5"` change and update the configuration contract everywhere a
   new user sees or verifies the default.
2. **Update only the failing test.** This would make the suite green but leave `.env.example` and
   README instructions pointing at the previous model.
3. **Keep the previous default and require an environment override.** This avoids documentation
   edits but does not satisfy the requested official default.

## Changes

- `src/trip_agent/config.py` defaults `openai_model` to `gpt-5.5`.
- `tests/test_config.py` asserts `gpt-5.5` when `OPENAI_MODEL` is absent.
- `.env.example` uses `OPENAI_MODEL=gpt-5.5`.
- `README.md` names `gpt-5.5` as the default and uses it in the sample configuration.

Historical design documents, implementation plans, and explicit test-agent fixtures remain
unchanged because they record earlier decisions or intentionally supply a model rather than relying
on the runtime default.

## Verification

Run the configuration test first, followed by the full pytest suite, Ruff formatting and linting,
mypy, and `git diff --check`. No Redis or OpenAI write is needed for this configuration-only change.
