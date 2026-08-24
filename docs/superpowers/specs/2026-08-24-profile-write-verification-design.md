# Profile Write Verification Design

## Goal

Prevent onboarding from claiming that profile categories were created or updated when Redis's
write response is not reflected in readable long-term-memory state.

## Problem

The application currently treats IDs returned in `bulk_create_long_term_memories().created` and
successful `update_long_term_memory()` calls as final proof of persistence. In the observed live
case, Redis acknowledged four deterministic profile IDs as created, but subsequent owner-scoped
searches and exact-ID reads found only the budget record. The CLI therefore printed `4 created`
when only one record was readable.

## Approaches Considered

1. **Exact-ID read-after-write verification — selected.** Fetch every successfully acknowledged
   record with `get_long_term_memory()` and validate its stored fields. This adds at most four
   small reads during onboarding but directly tests the records the CLI is about to report.
2. **One profile-namespace search after writing.** This uses fewer requests, but search visibility,
   pagination, duplicate legacy categories, and index behavior make the result less definitive
   than an exact-ID lookup.
3. **Continue trusting write acknowledgements.** This follows the documented API contract but
   preserves the demonstrated false-success behavior.

## Behavior

`TripAgent.save_profile()` retains the existing create-or-update flow and deterministic IDs.
After each write that Redis reports as successful:

- Fetch the record by its exact ID.
- Confirm the ID, text, owner ID, `profile` namespace, `semantic` memory type, and the exact
  `direct` plus category topics.
- Count the category as created or updated only when every field matches.
- Count a missing, unreadable, or mismatched record as failed.
- Continue verifying the remaining categories after one verification failure.

Existing profile records with legacy random IDs remain supported: updates are verified using the
actual ID selected by the existing newest-record logic. Records rejected or unaccounted for by the
write response remain failed without a retry.

The application will not retry missing records automatically. Automatic retry could hide a Redis
service problem and adds write behavior beyond the requested verification. A later `/onboard` run
can retry missing categories using the existing deterministic-ID and upsert behavior.

## User Experience

The existing `ProfileSaveResult` and CLI output remain unchanged structurally. Their meaning
becomes stronger:

- `created` and `updated` mean acknowledged by Redis and confirmed readable with the expected
  stored fields.
- `failed` includes write errors, unaccounted write results, exact-read failures, and stored-field
  mismatches.

This means the observed case would report one created category and three failed categories rather
than claiming that all four were saved.

## Error Handling

Verification errors are handled per category so one failed read does not erase confirmed results
for other categories. Credentials and raw Redis errors are not printed. A profile lookup failure
before any write remains a turn-level error, preserving the current behavior.

## Testing

Unit tests will prove that:

- acknowledged and matching creates are reported as created;
- an acknowledged create that cannot be read back is reported as failed;
- an acknowledged create with mismatched stored fields is reported as failed;
- a successful update is reported as updated only after the existing ID reads back correctly;
- verification continues after another category fails;
- existing partial-write, legacy-ID, error-boundary, and ordering behavior remains intact.

The full pytest, Ruff, and mypy checks will run after implementation. Live verification will remain
read-only; the user can exercise the hardened write path by rerunning `/onboard` afterward.
