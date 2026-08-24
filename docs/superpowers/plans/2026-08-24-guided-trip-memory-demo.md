# Guided Trip Memory Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the trip-agent CLI with direct long-term profile onboarding, visible session identity, traveler switching, and a video-ready memory demonstration.

**Architecture:** `TripAgent` gains one direct-profile write method while its existing `reply` method continues to append session events for managed promotion. The CLI owns traveler normalization, onboarding, switching, and terminal status.

**Tech Stack:** Python 3.12, Typer, Rich, Pydantic Settings, redis-agent-memory, OpenAI SDK, pytest, Ruff, mypy.

---

### Task 1: Direct profile-memory operation

**Files:**

- Modify: `src/trip_agent/agent.py`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write the failing tests.** Add a `FakeMemory.bulk_create_long_term_memories` recorder and this behavior test.

```python
def test_save_profile_writes_owner_scoped_semantic_records() -> None:
    memory = FakeMemory([])
    agent = make_agent(memory, FakeOpenAI([]))

    agent.save_profile((
        ProfileFact(category="dietary", text="Vegetarian"),
        ProfileFact(category="budget", text="Moderate budget"),
    ))

    records = memory.calls[0].kwargs["memories"]
    assert all(record["owner_id"] == "sam" for record in records)
    assert all(record["memory_type"] == "semantic" for record in records)
    assert all(record["namespace"] == "profile" for record in records)
```

Add a second test that `httpx.ConnectError` becomes `TripAgentError` with a direct-profile error message.

- [ ] **Step 2: Verify red.** Run `uv run pytest tests/test_agent.py -q`; expect failure because `ProfileFact` and `save_profile` do not exist.

- [ ] **Step 3: Implement the minimum.** Add an immutable `ProfileFact(category: str, text: str)` and `TripAgent.save_profile(facts)`. For each fact, generate a UUID record ID; write `text`, the active `owner_id`, `memory_type="semantic"`, `namespace="profile"`, and topics `("direct", category)` in one `bulk_create_long_term_memories` call. Convert Redis and HTTP failures to `TripAgentError`. Return created IDs and do not call Redis for an empty sequence.

- [ ] **Step 4: Verify green.** Run `uv run pytest tests/test_agent.py -q`; expect pass.

- [ ] **Step 5: Commit.** Run `git add src/trip_agent/agent.py tests/test_agent.py` followed by `git commit -m "feat: add direct travel profile memory"`.

### Task 2: Traveler identity, onboarding, and session visibility

**Files:**

- Modify: `src/trip_agent/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests.** Test `normalize_user_id("Maya Chen") == "maya-chen"`; test `/new` prints the newly generated ID; test four onboarding inputs cause `FakeAgent.save_profile` to receive categories `preferences`, `dietary`, `budget`, and `travel-style`; test blank inputs are skipped; and test `/user Alex` changes both active user and session UUID.

- [ ] **Step 2: Verify red.** Run `uv run pytest tests/test_cli.py -q`; expect failure because normalization, onboarding, and user-switching APIs do not exist.

- [ ] **Step 3: Implement the minimum.** Add `normalize_user_id`, a startup traveler prompt, `run_onboarding`, and a shared session-start renderer. Add `/user <name>` and `/onboard` to help and dispatch. Start onboarding using the four agreed prompts. Make `/new`, startup, and `/user` display the active session UUID. Construct a new `TripAgent` with the selected owner ID while reusing the existing Redis and OpenAI clients.

- [ ] **Step 4: Verify green.** Run `uv run pytest tests/test_cli.py -q`; expect pass.

- [ ] **Step 5: Commit.** Run `git add src/trip_agent/cli.py tests/test_cli.py` followed by `git commit -m "feat: guide traveler onboarding and switching"`.

### Task 3: Safe settings and source-aware memory display

**Files:**

- Modify: `src/trip_agent/config.py`
- Modify: `src/trip_agent/agent.py`
- Modify: `src/trip_agent/cli.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests.** Assert `http://memory.example.com` fails `Settings`; assert a `MemoryView(source="direct")` displays a direct label; and assert a Redis result with `namespace="profile"` maps to that source.

- [ ] **Step 2: Verify red.** Run `uv run pytest tests/test_config.py tests/test_agent.py tests/test_cli.py -q`; expect failure because HTTP is accepted and `MemoryView` has no source.

- [ ] **Step 3: Implement the minimum.** Add an endpoint validator that allows only `https`. Extend `MemoryView` with `source`; derive `direct` when Redis returns `namespace == "profile"`, otherwise `learned`; render the label using Rich `Text` appends so memory content stays plain data.

- [ ] **Step 4: Verify green.** Run `uv run pytest tests/test_config.py tests/test_agent.py tests/test_cli.py -q`; expect pass.

- [ ] **Step 5: Commit.** Run `git add src/trip_agent/config.py src/trip_agent/agent.py src/trip_agent/cli.py tests/test_config.py tests/test_agent.py tests/test_cli.py` followed by `git commit -m "feat: clarify direct and learned memory"`.

### Task 4: Video-ready documentation and full verification

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-24-guided-trip-demo-design.md`

- [ ] **Step 1: Update the README.** Document direct onboarding fields, `/user`, `/onboard`, visible session UUIDs, session-event promotion, the asynchronous caveat, and the restart-based persistence reveal. Preserve privacy guidance and state that retrieved policy/reference memories are not executable security controls.

- [ ] **Step 2: Verify all changes.** Run `git diff --check && uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run pytest`; expect exit code 0.

- [ ] **Step 3: Commit.** Run `git add README.md docs/superpowers/specs/2026-08-24-guided-trip-demo-design.md` followed by `git commit -m "docs: explain direct and automatic memory demo"`.
