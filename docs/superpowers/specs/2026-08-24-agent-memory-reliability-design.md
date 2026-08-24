# Agent Memory Reliability Design

**Status:** Approved in conversation

**Date:** 2026-08-24

## Purpose

Make the guided trip-memory CLI reliable under user switching, onboarding retries, partial Redis
failures, and interrupted terminal input while keeping the application small enough for a concise
developer video. The change also makes Redis Agent Memory's provenance and memory-type concepts
visible without adding another command.

## Goals

- Automatically onboard a traveler selected with `/user <name>` when that owner has no direct
  profile memories.
- Skip automatic onboarding for returning travelers who already have at least one direct profile
  record.
- Allow onboarding to be cancelled without writing partial answers.
- Make repeated onboarding idempotent by updating profile categories instead of creating
  duplicate records.
- Preserve compatibility with profile records created by the existing random-ID implementation.
- Report partial Redis writes by profile category.
- Use owner-scoped, relevance-bounded long-term-memory retrieval.
- Expose both memory provenance and Redis memory type in human-readable terminal output.
- Store durable profile facts as semantic memories and dated trip plans as episodic memories.
- Detect an invalid Agent Memory Store ID before the first chat message.

## Non-goals

- Adding accounts, authentication, authorization, or a local user database.
- Treating a normalized display name as a secure identity.
- Adding profile deletion, a full profile editor, or destructive duplicate cleanup.
- Polling or triggering managed background promotion.
- Reclassifying memory types assigned by Redis background promotion.
- Adding another slash command solely to explain memory types.
- Changing how the application decides that a chat message proposes a dated trip.

## User Switching

`/user <name>` performs these steps in order:

1. Normalize and validate the supplied name before changing state.
2. Switch `SessionState` and `TripAgent` to the normalized owner ID.
3. Generate a fresh session UUID and clear the cached `/why` retrieval receipt.
4. Search long-term memory using an `owner_id` equality filter and the `profile` namespace.
5. If any direct profile record exists, print a brief returning-traveler message and resume chat.
6. If no direct profile record exists, begin onboarding immediately.

Entering the current traveler's name is still a user switch: it starts a new session, clears the
retrieval receipt, and skips onboarding when the profile exists. A missing or invalid name leaves
the current owner and session untouched.

If the profile check fails, the new owner remains active. The CLI must not guess whether that owner
is new, and it must not fall back to the previous owner's data. It prints a concise warning and
allows normal chat or a manual `/onboard` retry.

## Onboarding Lifecycle

Onboarding continues to ask the existing four questions. All answers remain in a local collection
until the prompt sequence completes. An exact `/cancel` answer, `KeyboardInterrupt`, or EOF cancels
the entire attempt, prints a friendly message, and performs no OpenAI or Redis writes. Blank answers
are allowed and omitted.

If every answer is blank, no profile is written. The traveler remains active and can use normal
chat or run `/onboard` later. This does not create a separate "onboarding complete" marker.

After collection completes:

1. OpenAI rewrites the non-empty answers into concise, fact-preserving statements.
2. If rewriting fails or returns invalid structured data, Redis is not called and the user is
   invited to retry `/onboard`.
3. The rewritten categories are upserted into owner-scoped long-term memory.
4. The CLI reports created, updated, and failed categories without exposing credentials.

Manual `/onboard` uses the same lifecycle for both new and returning travelers.

## Profile Upsert Semantics

Before writing, the agent performs one filter-only search scoped by:

- `owner_id == active owner`
- `namespace == profile`

Existing records are grouped by the category topic (`preferences`, `dietary`, `budget`, or
`origin`). This supports records created by the current random-UUID implementation. When more than
one legacy record exists for a category, the most recently updated record is selected for update;
other records are left untouched because automatic destructive cleanup is out of scope.

For each supplied category:

- If a matching record exists, update it with `update_long_term_memory()`.
- If no matching record exists, create it with a deterministic UUID derived from the owner ID and
  category.

All missing categories are created in one `bulk_create_long_term_memories()` call. Bulk-operation
errors are mapped from record ID back to category. Successful updates and creates are never retried
as part of the same operation. If the bulk request itself fails, every category in that batch is
reported as failed.

Profile records use:

- `owner_id`: active traveler
- `namespace`: `profile`
- `memory_type`: `semantic`
- `topics`: `direct` and the profile category

The result model exposes created categories, updated categories, and failed categories rather than
counts alone.

## Trip-Plan Persistence

The existing deterministic trip-plan ID remains based on owner, normalized destination, start
date, and end date. Direct trip-plan records change from `semantic` to `episodic` because they
describe dated events. They retain the `trip-plans` namespace and the `direct` and `trip-plan`
topics.

The application must inspect `bulk_create_long_term_memories()` errors. A failed trip-plan write
raises a clear application error and the CLI must not claim that the plan is protected by future
conflict checks. Exact repeat writes remain idempotent through the deterministic ID.

## Retrieval

Semantic searches used for chat, `/memories`, and `/why` retain the active owner's equality filter
and add an explicit similarity threshold of `0.7`. Filter-only profile and trip-plan queries omit
the text and threshold. The application does not filter broad results on the client.

The active owner changes in both the CLI state and `TripAgent` before any retrieval for the new
traveler, preventing previous-owner memory leakage. A user switch also clears the last retrieval
receipt.

## Memory Presentation

Each displayed memory shows two independent dimensions:

- Provenance: `direct` or `learned`
- Kind: `semantic fact`, `episodic event`, or `retained message`

The labels render as compact rows, for example:

```text
direct   semantic fact    The traveler prefers quiet coastal trips.
direct   episodic event   Trip to Kyoto from May 4–10.
learned  episodic event   The traveler visited Lisbon last October.
```

Provenance is `direct` when the record contains the `direct` topic. The known `profile` and
`trip-plans` namespaces are compatibility fallbacks for older records. Other records are labeled
`learned`.

Built-in memory types map as follows:

- `semantic` -> `semantic fact`
- `episodic` -> `episodic event`
- `message` -> `retained message`

Unknown or custom memory types are displayed verbatim, while a missing type is displayed as
`memory`. Types returned by Redis promotion are never guessed or overwritten by the application.

## Startup Validation

The existing `/health` request remains. Startup then calls the read-only
`list_sessions(limit=1)` operation. This validates that the configured Store ID is reachable before
entering the REPL without creating session events, promotion jobs, or test data.

The opt-in integration test performs the same health and read-only store checks. It does not write
an event because even a short-lived test event can enqueue background promotion.

## Error Handling

- Invalid `/user` input changes no state.
- Profile-check failure leaves the newly selected owner active and never restores or queries the
  previous owner implicitly.
- Cancelled onboarding writes nothing.
- OpenAI rewrite failure writes nothing.
- Profile updates and creates are non-transactional; exact successful and failed categories are
  reported.
- Trip-plan bulk errors are treated as persistence failures rather than silent success.
- Unknown custom memory types remain displayable.
- Redis and OpenAI exceptions continue to be translated into concise CLI messages.

## Components

- `src/trip_agent/cli.py` owns the profile-aware `/user` flow, onboarding cancellation, terminal
  messages, and human-readable memory labels.
- `src/trip_agent/agent.py` owns profile discovery/upsert, deterministic IDs, owner-scoped recall,
  provenance normalization, and episodic trip-plan persistence.
- `tests/test_cli.py` covers user-switch transitions, onboarding cancellation, display labels, and
  observable failure messages.
- `tests/test_agent.py` covers existing-record updates, deterministic creates, partial bulk errors,
  retrieval thresholds, provenance, and trip-plan persistence.
- `tests/test_integration.py` validates both service health and read-only Store ID access.
- `README.md` and `docs/trip-memory-video-script.md` explain profile-aware switching and the
  provenance-versus-kind distinction.

## Testing

Implementation follows red-green-refactor. Unit tests must cover:

- New user switching triggers onboarding.
- Returning user switching skips onboarding.
- Switching to the same owner starts a fresh session and skips onboarding when profiled.
- Missing and invalid `/user` arguments preserve owner and session state.
- Profile-check failure preserves new-owner isolation.
- `/cancel`, Ctrl+C, and EOF discard all collected answers and make no writes.
- Retried onboarding updates categories rather than duplicating them.
- Existing random-ID profile records remain updateable.
- Partial writes identify failed categories.
- Semantic recall contains the owner filter and `0.7` threshold.
- Semantic, episodic, retained-message, and custom labels render correctly.
- Direct provenance uses topics with namespace compatibility fallbacks.
- Trip plans are direct episodic memories and bulk errors are surfaced.
- Startup performs the read-only Store ID validation.

After implementation, run the full unit suite, formatting check, linting, strict type checking, CLI
help smoke test, and the opt-in read-only Redis integration test when credentials are available.

## Documentation Changes

The command table states that `/user <name>` automatically onboards owners without a direct
profile. The demo script shows one new owner and one returning owner. `/memories` and `/why` output
are explained as combining provenance (`direct` or `learned`) with memory kind (`semantic fact`,
`episodic event`, or `retained message`). Documentation reiterates that owner IDs are demo scoping
keys, not authenticated accounts.
