# Profile Write Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report an onboarding profile category as created or updated only after its exact Redis long-term-memory record reads back with the expected fields.

**Architecture:** Keep `TripAgent.save_profile()` as the single profile-write coordinator. Add one small exact-record validator that calls the existing Redis SDK client, handles verification failures per category, and leaves the current bulk-create and legacy-ID update paths intact.

**Tech Stack:** Python 3.12, `redis-agent-memory` 0.2.1, pytest, Ruff, mypy, uv

---

## File Structure

- Modify `tests/test_agent.py`: make the Redis fake retain acknowledged profile records and add regression cases for unreadable and mismatched writes.
- Modify `src/trip_agent/agent.py`: verify acknowledged creates and successful updates with `get_long_term_memory()` before classifying their categories as saved.
- Modify `README.md`: explain that direct profile success is confirmed by exact-ID reads.
- Modify `docs/trip-memory-video-script.md`: align the presenter narrative with verified direct writes.

### Task 1: Exact-ID Profile Verification

**Files:**
- Modify: `tests/test_agent.py:32-181`
- Modify: `tests/test_agent.py:858-1315`
- Modify: `src/trip_agent/agent.py:292-362`
- Modify: `src/trip_agent/agent.py:500-535`

- [ ] **Step 1: Make the Redis fake model readable profile state**

Add `missing_profile_read_ids` and `profile_read_overrides` inputs. Retain successful profile
creates and updates in an ID-keyed dictionary, then expose them through an exact-read fake:

```python
def get_long_term_memory(self, **kwargs: object) -> object:
    self.timeline.append("memory.get_long_term_memory")
    self.calls.append(Call("get_long_term_memory", kwargs))
    memory_id = cast(str, kwargs["memory_id"])
    if memory_id in self.profile_read_overrides:
        return self.profile_read_overrides[memory_id]
    if memory_id in self.missing_profile_read_ids:
        return SimpleNamespace()
    return self.long_term_by_id[memory_id]
```

Successful fake creates store `SimpleNamespace(**record)`. Successful fake updates replace the
same ID with a namespace containing the submitted fields and ID.

- [ ] **Step 2: Write failing create-verification tests**

Add tests proving that an acknowledged but unreadable category fails while another matching
category succeeds, and that a readable record with mismatched content fails:

```python
def test_save_profile_verifies_acknowledged_creates_and_continues_after_missing_record() -> None:
    dietary_id = str(uuid5(NAMESPACE_URL, "profile:sam:dietary"))
    memory = FakeMemory(timeline, missing_profile_read_ids={dietary_id})

    result = agent.save_profile(
        (
            ProfileFact(category="dietary", text="Vegetarian"),
            ProfileFact(category="budget", text="Moderate"),
        )
    )

    assert result.created_categories == ("budget",)
    assert result.failed_categories == ("dietary",)
```

The mismatch test supplies a `profile_read_overrides` record whose text differs from the submitted
fact and expects that category in `failed_categories`.

- [ ] **Step 3: Write a failing update-verification test**

Use an existing legacy ID, let `update_long_term_memory()` succeed, make its exact read unreadable,
and assert that the category is failed rather than updated. Also assert that verification used the
legacy ID.

- [ ] **Step 4: Run the focused tests and confirm RED**

Run:

```bash
uv run pytest -q \
  tests/test_agent.py::test_save_profile_verifies_acknowledged_creates_and_continues_after_missing_record \
  tests/test_agent.py::test_save_profile_rejects_acknowledged_create_with_mismatched_stored_fields \
  tests/test_agent.py::test_save_profile_verifies_successful_legacy_id_update
```

Expected: failures because `save_profile()` does not call `get_long_term_memory()` and still trusts
write acknowledgements.

- [ ] **Step 5: Add the exact profile-record validator**

Add a private `TripAgent` method:

```python
def _profile_write_is_readable(self, memory_id: str, fact: ProfileFact) -> bool:
    try:
        record = self.memory.get_long_term_memory(memory_id=memory_id)
    except MEMORY_EXCEPTIONS:
        return False
    return _matches_profile_fact(record, memory_id, self.user_id, fact)
```

Add a pure helper that requires matching ID, text, owner ID, namespace, memory type, and exactly the
two expected topics:

```python
def _matches_profile_fact(
    record: object,
    memory_id: str,
    owner_id: str,
    fact: ProfileFact,
) -> bool:
    topics = getattr(record, "topics", None)
    return (
        getattr(record, "id", None) == memory_id
        and getattr(record, "text", None) == fact.text
        and getattr(record, "owner_id", None) == owner_id
        and getattr(record, "namespace", None) == "profile"
        and getattr(record, "memory_type", None) == "semantic"
        and isinstance(topics, Sequence)
        and not isinstance(topics, (str, bytes))
        and len(topics) == 2
        and set(topics) == {"direct", fact.category}
    )
```

- [ ] **Step 6: Gate create and update success on verification**

After a successful update, append to `updated_categories` only when
`_profile_write_is_readable(existing_id, fact)` returns true; otherwise append to
`failed_categories`. After bulk create, keep the existing acknowledgement/error checks and verify
each eligible `(fact, record)` pair before appending to `created_categories`.

- [ ] **Step 7: Run focused tests and confirm GREEN**

Run the Step 4 command again.

Expected: all three tests pass.

- [ ] **Step 8: Run all agent tests**

Run:

```bash
uv run pytest -q tests/test_agent.py
```

Expected: all agent tests pass after updating existing timeline assertions to include the new exact
reads.

- [ ] **Step 9: Commit the behavior**

```bash
git add src/trip_agent/agent.py tests/test_agent.py
git commit -m "fix: verify profile writes before reporting success"
```

### Task 2: Documentation and Full Verification

**Files:**
- Modify: `README.md:107-116`
- Modify: `docs/trip-memory-video-script.md:50-68`

- [ ] **Step 1: Document verified direct writes**

State that onboarding uses exact-ID read-after-write checks and only confirmed records appear in
the saved count. Explain that failed verification is reported by category and can be retried with
`/onboard`.

- [ ] **Step 2: Run all verification commands**

Run each command separately:

```bash
uv run pytest -ra
uv run ruff format --check .
uv run ruff check .
uv run mypy src
git diff --check
```

Expected: the unit suite passes with only the opt-in live integration test skipped; formatting,
linting, typing, and whitespace checks exit successfully.

- [ ] **Step 3: Run the existing read-only live integration check**

Run:

```bash
RUN_REDIS_INTEGRATION=1 uv run pytest -q tests/test_integration.py
```

Expected: pass without creating sessions or memories. Do not perform a live onboarding write as
part of automated verification.

- [ ] **Step 4: Commit documentation and plan progress**

```bash
git add README.md docs/trip-memory-video-script.md \
  docs/superpowers/plans/2026-08-24-profile-write-verification.md
git commit -m "docs: explain verified profile writes"
```
