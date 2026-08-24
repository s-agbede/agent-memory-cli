# Guided Trip Demo Design

**Status:** Approved in conversation

**Date:** 2026-08-24

## Purpose

Make the existing terminal trip agent feel like a polished, repeatable eight-to-ten-minute Redis Agent Memory demonstration. The terminal remains the product surface: no web UI, authentication system, local database, or direct long-term-memory writes are added.

## Goals

- Ask for a traveler name when the process starts instead of requiring a demo presenter to edit `TRIP_AGENT_USER_ID`.
- Guide a first session through a small set of durable travel preferences.
- Store each answer through the existing session-event path, allowing Redis Agent Memory's managed background promotion to create long-term memory.
- Make the distinction between immediate session persistence and asynchronous promotion clear in terminal copy.
- Preserve the existing `/memories` and `/new` reveal: a new session loses short-term conversation history but can retrieve the same traveler's long-term memories.
- Reject unsafe owner IDs and non-HTTPS Agent Memory endpoints before startup.

## Non-goals

- Persisting accounts, names, or active sessions outside Redis Agent Memory.
- Waiting for, polling, or directly triggering long-term promotion.
- Claiming that deduplication or promotion finishes at a fixed time.
- Changing the Redis service's custom memory types or extraction configuration.
- Adding a graphical UI.

## Interaction Design

At launch, the CLI prompts for a display name. It normalizes the name to a stable, Redis-safe owner ID: lowercase words joined with hyphens, using only letters, numbers, and hyphens. If a supplied name cannot produce an ID, it asks again. The default is the configured `TRIP_AGENT_USER_ID`, retaining a convenient scripted-demo fallback.

After the greeting, the CLI offers onboarding only for the current first session. It asks these four concise questions:

1. Where are you hoping to travel next?
2. When do you plan to go?
3. What food or dietary preferences should I remember?
4. What budget and travel style suit you?

For every non-empty answer, the CLI sends a normal natural-language message to `TripAgent.reply`. This preserves the existing required order: the user's detail is first stored as a session event, session and owner-scoped long-term context are loaded, an answer is generated, and the assistant response is stored as a session event. The CLI prints a short confirmation after each successful turn: the session event is saved and durable preferences may be promoted asynchronously. It does not manufacture a long-term-memory result.

At the end of onboarding, the CLI gives the presenter the next two commands to show: `/memories` after a short wait or an edit, and `/new` to create the clean-session recall reveal. Existing free-form chat remains available after onboarding.

## Components

- `src/trip_agent/cli.py` owns name normalization, prompts, onboarding sequencing, terminal lifecycle copy, and construction of `TripAgent` using the prompted owner ID.
- `src/trip_agent/config.py` validates an HTTPS Agent Memory endpoint and keeps `TRIP_AGENT_USER_ID` as a safe default owner ID.
- `tests/test_cli.py` covers normalized identity, invalid re-prompting, onboarding ordering, skip behavior, lifecycle copy, and the existing slash-command flow.
- `tests/test_config.py` covers HTTP endpoint rejection and invalid owner IDs.
- `README.md` documents the guided video sequence and clearly frames promotion and deduplication as managed, asynchronous Redis behavior.

## Error Handling

An empty onboarding answer is skipped rather than stored. A `TripAgentError` or assistant-memory warning is rendered using the existing REPL behavior and onboarding proceeds to the next question. A user can decline the entire onboarding sequence and enter free-form chat. Owner IDs are normalized before `TripAgent` is constructed, so every session event and long-term-memory filter uses the same validated owner.

## Testing

Follow red-green-refactor. Test every new pure helper and observable CLI outcome before production changes. Use existing fake agents and a scripted `read_input` callback; no unit test contacts Redis or OpenAI. Run the complete unit suite, formatting, linting, and static type checks after the change.

## Demo Script

1. Start the CLI and enter `Maya Chen` as the traveler.
2. Choose onboarding and give durable preferences such as vegetarian food, a £40 meal budget, and neighborhood-focused travel.
3. Point out that each response is saved to session memory immediately and promotion happens in the background.
4. After a rehearsed pause or edit, use `/memories` to show extracted memory. Explain that managed promotion and deduplication timing is asynchronous.
5. Run `/new` and ask for a current recommendation. The terminal can use owner-scoped long-term context and web search while the new session begins with no prior conversation history.
