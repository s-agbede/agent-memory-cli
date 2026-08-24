# Guided Trip Demo Design

**Status:** Approved in conversation

**Date:** 2026-08-24

## Purpose

Make the existing terminal trip agent feel like a polished, repeatable eight-to-ten-minute Redis Agent Memory demonstration. The terminal remains the product surface: no web UI, authentication system, or local database is added. It deliberately shows both direct long-term writes and managed background promotion.

## Goals

- Ask for a traveler name when the process starts instead of requiring a demo presenter to edit `TRIP_AGENT_USER_ID`.
- Guide a first session through a small set of durable travel preferences and save those explicit facts directly to long-term memory.
- Store normal chat through the existing session-event path, allowing Redis Agent Memory's managed background promotion to create long-term memory.
- Make the distinction between immediate direct writes and asynchronous promotion clear in terminal copy.
- Preserve the existing `/memories` and `/new` reveal: a new session loses short-term conversation history but can retrieve the same traveler's long-term memories.
- Allow the presenter to switch to a different traveler without restarting the CLI.
- Display a new session UUID at startup and whenever `/new` is used.
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

1. What kinds of trips and places do you enjoy?
2. What food or dietary needs should I remember?
3. What budget works for you?
4. What city do you usually travel from?

For every non-empty onboarding answer, the CLI first asks the LLM for a brief, fact-preserving rewrite, then writes an owner-scoped semantic long-term-memory record directly. It displays that the profile fact is ready to recall immediately. It batches the four records where possible and reports per-record failure without exposing credentials. These explicit profile facts solve cold start.

Normal free-form chat retains the existing required order: the user's detail is first stored as a session event, session and owner-scoped long-term context are loaded, an answer is generated, and the assistant response is stored as a session event. The CLI prints that the session event is saved and Redis Agent Memory evaluates it for asynchronous promotion. Redis Agent Memory handles extraction, deduplication, and memory consolidation; the CLI does not poll or implement a competing conflict-resolution rule.

At the end of onboarding, the CLI gives the presenter the next two commands to show: `/memories` after a short wait or an edit, and `/new` to create the clean-session recall reveal. Existing free-form chat remains available after onboarding.

`/user <name>` changes the active traveler while the CLI is running. The command normalizes `<name>` using the same rule as the startup prompt, creates a new UUID session, and reconstructs the `TripAgent` with the new owner ID. It prints the active traveler, the new session UUID, and explains that only this traveler's owner-scoped long-term memories can be recalled. It does not decide whether the traveler is a newly created account because an empty long-term-memory search may also mean background promotion is pending. The presenter may use normal chat or start onboarding with `/onboard`.

`/onboard` runs the same four prompts for the current traveler. This lets the presenter seed a second dummy traveler's profile in one run without adding account persistence or authentication. Onboarding cannot change the active traveler; `/user` is the sole identity-switching command. `/new` remains available to generate and display another session UUID, but the recommended video reveal is exiting and relaunching the CLI with the same traveler name: a new process and new UUID show that only server-side long-term memory persisted.

## Components

- `src/trip_agent/cli.py` owns name normalization, prompts, onboarding sequencing, terminal lifecycle copy, displayed session IDs, the `/user` and `/onboard` commands, and construction of `TripAgent` using the active owner ID.
- `src/trip_agent/agent.py` adds a small direct-profile-write operation using the Redis Agent Memory SDK while preserving its existing session-event turn coordinator.
- `src/trip_agent/config.py` validates an HTTPS Agent Memory endpoint and keeps `TRIP_AGENT_USER_ID` as a safe default owner ID.
- `tests/test_cli.py` covers normalized identity, invalid re-prompting, direct-profile onboarding, skip behavior, displayed session IDs, user switches, and the existing slash-command flow.
- `tests/test_agent.py` covers direct long-term profile writes, owner scoping, and direct-write errors.
- `tests/test_config.py` covers HTTP endpoint rejection and invalid owner IDs.
- `README.md` documents the guided video sequence and clearly frames promotion and deduplication as managed, asynchronous Redis behavior.

## Error Handling

An empty onboarding answer is skipped rather than stored. A direct-memory write failure is shown as a concise error and onboarding continues so a separate preference can still be saved. A user can decline the entire onboarding sequence and enter free-form chat. Owner IDs are normalized before `TripAgent` is constructed, so every direct record, session event, and long-term-memory filter uses the same validated owner.

## Testing

Follow red-green-refactor. Test every new pure helper and observable CLI outcome before production changes. Use existing fake agents and a scripted `read_input` callback; no unit test contacts Redis or OpenAI. Run the complete unit suite, formatting, linting, and static type checks after the change.

## Demo Script

1. Start the CLI, enter `Maya Chen`, and point out the new session UUID.
2. Choose onboarding and give durable preferences such as vegetarian food, a £40 meal budget, and neighborhood-focused travel. Point out the direct long-term writes and immediately run `/memories`.
3. In normal chat, add a new preference such as avoiding overnight buses. Point out the saved session event and that Redis will promote salient facts in the background.
4. After a rehearsed pause or edit, use `/memories` again to show automatically extracted memory. Explain that managed promotion, deduplication, and consolidation are asynchronous.
5. Exit and relaunch the CLI with `Maya Chen`. Point out the new process and session UUID, then ask for a current recommendation that uses the retained profile and web search.
6. Run `/user Alex` to demonstrate isolation, then `/onboard` to seed Alex's preferences in the same terminal session.
