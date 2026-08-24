# Agent Memory Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Make onboarding, profile updates, trip-plan storage, memory retrieval, and memory display reliable and easy to explain in a Redis Agent Memory demo.

**Architecture:** Keep the existing two-boundary design. `TripAgent` owns typed Redis Agent Memory and OpenAI operations; the CLI owns user/session transitions and friendly terminal behavior. Add small pure helpers for category selection and memory labeling instead of introducing adapters or services.

**Tech Stack:** Python 3.12, `redis-agent-memory` Python SDK, OpenAI Responses API, Typer, Rich, Pydantic, pytest, Ruff, mypy, uv.

---

## Task 1: Add semantic retrieval thresholds and truthful memory labels

**Files:**
- Modify: `src/trip_agent/agent.py`
- Test: `tests/test_agent.py`
- Modify: `src/trip_agent/cli.py`
- Test: `tests/test_cli.py`

1. Add failing agent tests proving that chat retrieval and `/memories` searches send an owner-scoped request with `similarity_threshold=0.7`, while filter-only profile and trip-plan queries omit `text` and the threshold.

   Expected request shape:

   ```python
   {
       "text": "vegetarian city break",
       "filter_": {"owner_id": {"eq": "maya"}},
       "limit": 5,
       "similarity_threshold": 0.7,
   }
   ```

2. Add failing conversion tests for both independent display dimensions:

   ```python
   MemoryView(memory_type="semantic", text="Vegetarian", source="direct")
   MemoryView(
       memory_type="episodic",
       text="[trip-plan] destination=Kyoto | start=2027-04-03 | end=2027-04-08",
       source="direct",
   )
   MemoryView(memory_type="message", text="Prefers trains", source="learned")
   ```

   A record is direct when `topics` contains `direct`; legacy `profile` and `trip-plans` namespaces are the compatibility fallback. Preserve custom Redis memory types verbatim and use `memory` only when the SDK record has no type.

3. Add failing CLI tests proving memory rows explain provenance (`direct`/`learned`) separately from kind (`semantic fact`/`episodic event`/`retained message`), and safely display unknown types as supplied.

4. Implement the smallest changes:

   ```python
   MEMORY_SIMILARITY_THRESHOLD = 0.7

   def _memory_request(self, text: str, limit: int) -> MemoryRequest:
       return cast(MemoryRequest, {
           "text": text,
           "filter_": {"owner_id": {"eq": self.user_id}},
           "limit": limit,
           "similarity_threshold": MEMORY_SIMILARITY_THRESHOLD,
       })
   ```

   Add focused `_memory_source(item)` and CLI `_memory_kind(memory_type)` helpers. Do not infer or replace the Redis-provided `memory_type` in `MemoryView`.

5. Run:

   ```bash
   uv run pytest tests/test_agent.py tests/test_cli.py -q
   uv run ruff check src/trip_agent/agent.py src/trip_agent/cli.py tests/test_agent.py tests/test_cli.py
   uv run mypy src
   ```

   Expected: all selected tests and static checks pass.

6. Commit:

   ```bash
   git add src/trip_agent/agent.py src/trip_agent/cli.py tests/test_agent.py tests/test_cli.py
   git commit -m "feat: clarify memory retrieval and labels"
   ```

## Task 2: Upsert onboarding profile facts by category

**Files:**
- Modify: `src/trip_agent/agent.py`
- Test: `tests/test_agent.py`

1. Replace the count-only result contract in failing tests with category-level outcomes:

   ```python
   ProfileSaveResult(
       created_categories=("budget",),
       updated_categories=("dietary",),
       failed_categories=(),
   )
   ```

2. Add failing tests for:
   - an empty fact sequence making no Redis call;
   - one owner-and-`profile` filter-only lookup per save;
   - an existing category calling `update_long_term_memory()` with the existing record ID;
   - a missing category using a deterministic UUID derived from owner ID and category;
   - all missing categories being sent in one bulk create call;
   - legacy random-ID records still being updated;
   - duplicate records for one category selecting the newest `updated_at` record and leaving other duplicates untouched;
   - update failures and bulk `errors` being mapped back to exact categories;
   - missing or malformed timestamps using a stable, non-crashing fallback;
   - categories not present in the submitted facts being left untouched.

3. Implement pure helpers:

   ```python
   PROFILE_CATEGORIES = ("preferences", "dietary", "budget", "origin")

   def _profile_category(item: object) -> str | None:
       topics = getattr(item, "topics", ()) or ()
       return next((category for category in PROFILE_CATEGORIES if category in topics), None)

   def _profile_updated_at(item: object) -> datetime:
       value = getattr(item, "updated_at", None)
       if isinstance(value, datetime):
           return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
       if isinstance(value, str):
           try:
               return datetime.fromisoformat(value.replace("Z", "+00:00"))
           except ValueError:
               pass
       return datetime.min.replace(tzinfo=UTC)

   def _profile_memory_id(owner_id: str, category: str) -> str:
       return str(uuid5(NAMESPACE_URL, f"profile:{owner_id}:{category}"))
   ```

   Only recognize the supported categories `preferences`, `dietary`, `budget`, and `origin`. Select an existing record from its category topic and preserve its ID when updating.

4. Implement `save_profile()` as one lookup followed by per-existing-record updates and at most one bulk create. Catch SDK/network failures at each boundary, continue independent categories where safe, and return sorted input-order category tuples. Inspect each bulk error's `id` to identify the failed category; treat an unaccounted-for create as failed rather than claiming success.

5. Run:

   ```bash
   uv run pytest tests/test_agent.py -q
   uv run ruff check src/trip_agent/agent.py tests/test_agent.py
   uv run mypy src
   ```

   Expected: all agent tests and static checks pass.

6. Commit:

   ```bash
   git add src/trip_agent/agent.py tests/test_agent.py
   git commit -m "feat: upsert direct travel profiles"
   ```

## Task 3: Make onboarding atomic from the CLI user's perspective

**Files:**
- Modify: `src/trip_agent/cli.py`
- Test: `tests/test_cli.py`

1. Add failing tests proving:
   - exact `/cancel` at any question cancels the whole attempt;
   - `KeyboardInterrupt` and `EOFError` cancel the whole attempt;
   - cancellation invokes neither `rewrite_profile()` nor `save_profile()`;
   - blank answers are skipped;
   - four blank answers perform no OpenAI or Redis write;
   - rewrite failure performs no Redis write and prints a retry invitation;
   - partial and complete `ProfileSaveResult` outcomes name created, updated, and failed categories without overstating success;
   - manual `/onboard` uses the same code path.

2. Add a small cancellation signal local to the CLI, such as a private exception or sentinel. Keep answers only in a local tuple/list until all questions finish.

3. Implement the flow:

   ```python
   try:
       raw_answers = _collect_profile_answers(read_input, console)
   except (OnboardingCancelled, KeyboardInterrupt, EOFError):
       console.print("No worries — I didn't save any profile changes.")
       return
   ```

   Return early for no facts, catch `TripAgentError` around rewrite and save independently, and base the success copy entirely on `ProfileSaveResult` category tuples.

4. Run:

   ```bash
   uv run pytest tests/test_cli.py -q
   uv run ruff check src/trip_agent/cli.py tests/test_cli.py
   uv run mypy src
   ```

   Expected: all CLI tests and static checks pass.

5. Commit:

   ```bash
   git add src/trip_agent/cli.py tests/test_cli.py
   git commit -m "feat: make onboarding safely cancellable"
   ```

## Task 4: Onboard new owners automatically after `/user`

**Files:**
- Modify: `src/trip_agent/cli.py`
- Test: `tests/test_cli.py`

1. Add failing command tests for:
   - `/user` with no name and an invalid name changing nothing;
   - a valid different owner updating `TripAgent`, updating `SessionState`, generating a new UUID, and clearing the `/why` receipt;
   - the same normalized owner still receiving a fresh session;
   - an owner with a profile getting a friendly returning-traveler message and no onboarding;
   - an owner without a profile immediately entering onboarding;
   - a profile-check failure leaving the newly selected owner/session active, warning without guessing, and allowing later chat or `/onboard`.

2. Normalize and validate before mutation. Then switch both owner holders before the profile lookup:

   ```python
   new_user_id = normalize_user_id(display_name)
   state.switch_user(new_user_id)
   agent.set_user(new_user_id)
   show_session_started(state, console)
   ```

3. Run `agent.has_profile()` after the switch. On `False`, invoke `run_onboarding()` with the command's injected input reader. On `True`, print the returning-traveler message. On `TripAgentError`, retain the new owner/session, print that the profile could not be checked, and mention `/onboard`.

4. Ensure startup and `/user` share the same profile-check/onboarding helper so behavior does not drift.

5. Run:

   ```bash
   uv run pytest tests/test_cli.py -q
   uv run ruff check src/trip_agent/cli.py tests/test_cli.py
   uv run mypy src
   ```

   Expected: all CLI tests and static checks pass.

6. Commit:

   ```bash
   git add src/trip_agent/cli.py tests/test_cli.py
   git commit -m "feat: onboard newly selected travelers"
   ```

## Task 5: Store trip plans as checked episodic memories

**Files:**
- Modify: `src/trip_agent/agent.py`
- Test: `tests/test_agent.py`

1. Add failing tests proving a saved trip plan retains its deterministic ID, owner filter, `trip-plans` namespace, and `direct`/`trip-plan` topics, but uses `memory_type="episodic"`.

2. Add failing tests for bulk outcomes:
   - created ID present and no errors succeeds;
   - an error whose ID matches the requested plan raises `TripAgentError`;
   - no created record and no matching success raises `TripAgentError`;
   - a top-level SDK/network error raises the same friendly error.

3. Implement strict result inspection after `bulk_create_long_term_memories()`. A rejected or absent create must stop the reply path before OpenAI itinerary generation so the CLI never implies that the conflict guard is active when the plan was not stored.

4. Run:

   ```bash
   uv run pytest tests/test_agent.py -q
   uv run ruff check src/trip_agent/agent.py tests/test_agent.py
   uv run mypy src
   ```

   Expected: all agent tests and static checks pass.

5. Commit:

   ```bash
   git add src/trip_agent/agent.py tests/test_agent.py
   git commit -m "fix: verify episodic trip plan writes"
   ```

## Task 6: Validate the configured Agent Memory Store ID at startup

**Files:**
- Modify: `src/trip_agent/cli.py`
- Test: `tests/test_cli.py`
- Modify: `tests/test_integration.py`

1. Add failing entrypoint tests proving startup calls both `health()` and read-only `list_sessions(limit=1, include_all=True)` before the REPL. The SDK requires this explicit all-sessions scope when no owner filter is supplied.

2. Add a failing test proving a store-read failure exits with a clear Store ID/configuration message and does not start the REPL.

3. Implement the read-only validation immediately after health:

   ```python
   memory.health()
   memory.list_sessions(limit=1, include_all=True)
   ```

   Keep connection/health and Store ID/read errors distinguishable in user-facing copy where possible.

4. Extend the opt-in integration test to run the same health and read-only `list_sessions(limit=1, include_all=True)` checks. Do not write a session event because it could trigger background promotion and pollute the demonstration store.

5. Run:

   ```bash
   uv run pytest tests/test_cli.py tests/test_integration.py -q
   uv run ruff check src/trip_agent/cli.py tests/test_cli.py tests/test_integration.py
   uv run mypy src
   ```

   Expected: unit tests pass and the integration test remains skipped unless explicitly enabled.

6. Commit:

   ```bash
   git add src/trip_agent/cli.py tests/test_cli.py tests/test_integration.py
   git commit -m "fix: validate agent memory store at startup"
   ```

## Task 7: Update the demo documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/trip-memory-video-script.md`

1. Update the command table and walkthrough so `/user Alex` is documented as selecting an owner ID, starting a fresh session, checking for a direct profile, and auto-onboarding only when none exists.

2. Explain the two visible memory dimensions independently:
   - provenance: `direct` versus `learned`;
   - kind: semantic fact, episodic event, retained message, or a service-configured custom type.

3. State explicitly that the normalized traveler name is an Agent Memory `owner_id`, not authentication or a locally created account.

4. Update the trip-plan explanation to say direct trip plans are episodic records because they describe dated events, while profile preferences are semantic facts.

5. Update development-check copy to describe both read-only integration checks and their no-write guarantee.

6. Run a terminology scan:

   ```bash
   rg -n "semantic|episodic|direct|learned|/user|owner_id|list_sessions" README.md docs/trip-memory-video-script.md
   ```

   Expected: both documents use consistent terms and no passage says `/user` requires a separate manual `/onboard` for a new owner.

7. Commit:

   ```bash
   git add README.md docs/trip-memory-video-script.md
   git commit -m "docs: explain memory kinds and user onboarding"
   ```

## Task 8: Full review and verification

**Files:**
- Review: all modified files

1. Run the normal suite:

   ```bash
   uv run pytest -q
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy src
   uv run trip-agent --help
   ```

   Expected: all commands exit successfully. The integration test is skipped by default.

2. Run the opt-in Redis Cloud validation using the existing `.env` without printing secrets:

   ```bash
   RUN_REDIS_INTEGRATION=1 uv run pytest tests/test_integration.py -v
   ```

   Expected: health and `list_sessions(limit=1, include_all=True)` pass without creating any events or long-term memories.

3. Request an independent code review against the approved design. Fix every confirmed issue test-first, then rerun the complete verification suite.

4. Inspect the final diff and status. Preserve unrelated user files, including `.DS_Store` and `.superpowers/`, and commit only the intentional implementation changes.
