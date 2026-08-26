# How memory works

Detail behind the [README's summary](../README.md#how-memory-works). This describes what the
application does with the Redis Agent Memory SDK and why.

## Two ways facts arrive

**Direct writes** are for explicit, trusted facts you already have: an onboarding profile,
imported preferences, or business reference data. The app writes these itself with
`bulk_create_long_term_memories()`, reads each one back with `get_long_term_memory()` to verify
it, and revises an existing category with `update_long_term_memory()`.

**Session events** are for normal conversation. Every user and assistant turn is stored with
`add_session_event()`, and Redis Agent Memory extracts, deduplicates, and promotes durable facts
in the background.

Use direct writes when you already know the fact is worth keeping. Use session events when you
want the service to decide.

## Timing

Extraction and session summarization are asynchronous and eventually consistent. A preference
mentioned in chat will not appear in `/memories` immediately.

Direct profile writes are the exception. `/onboard` reads each record back and verifies its text
and profile metadata before counting the category as created or updated, so confirmed facts are
queryable right away. Any category that cannot be confirmed is reported as failed and can be
retried with `/onboard`. This is what avoids a cold start on a fresh traveler.

## The two labels on every record

`/memories` and `/why` show two independent dimensions:

**Provenance** — `direct` for records the app deliberately wrote, `learned` for records Redis
promoted from session history. Determined in `_memory_source()` by checking for a `direct` topic
tag or a `profile` / `trip-plans` namespace.

**Kind** — `semantic fact`, `episodic event`, `retained message`, or a service-defined custom type
shown exactly as Redis returns it. Direct profile facts are semantic; dated trip plans are
episodic. The app displays the kind Redis reports rather than guessing it.

The two are independent: a `direct` record can be either semantic or episodic.

## Retrieval paths

The application uses four distinct request shapes.

| Path | Request | Why |
| --- | --- | --- |
| Normal reply | Filter-only profile load, then owner-scoped semantic search | Profile facts must be present even when the message doesn't resemble them |
| `/memories` | Owner-scoped filter-only browse | Every direct onboarding fact visible immediately, unranked |
| `/memories <query>` | Owner-scoped semantic search with a relevance threshold | Ranked, filtered recall |
| Profile and trip-plan checks | Owner-scoped filters only | The profile and date-overlap rules are then applied deterministically in code |

The normal reply path is the one worth understanding. It first loads the direct profile with an
owner-and-namespace filter-only request, then runs owner-scoped semantic search with a relevance
threshold for learned and episodic context. The two result sets are merged with the profile first
and duplicate Redis record IDs removed.

That ordering is deliberate. A question like *"suggest a city break"* is not semantically similar
to *"The traveler's usual departure city is Glasgow."*, so a threshold-based semantic search alone
would drop it. The filter-only profile load guarantees baseline context regardless of wording.
`/why` shows this merged result.

Dated future trip plans are checked against saved plans before an overlapping itinerary is
generated, using the same filter-only approach with the date-overlap rule in code.

## `/why` is a receipt, not a causal claim

`/why` shows the memories that were retrieved for the most recent answer. It does not claim that
one memory mechanically caused any particular part of the response.

## Retrieved memory is untrusted input

Retrieved memory is reference context, not executable instruction. Keep authorization, security,
and hard safety rules in application code and system instructions rather than relying on
retrieval to enforce them. See [SECURITY.md](../SECURITY.md).

## Related

- [Custom memory types](custom-types.md)
- [Agent memory reliability design](superpowers/specs/2026-08-24-agent-memory-reliability-design.md)
