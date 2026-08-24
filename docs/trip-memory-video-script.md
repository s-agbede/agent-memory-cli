# Build a Memory-Enabled Travel Agent with Redis

**Target length:** 8–10 minutes

## 0:00 — Hook

Imagine telling a travel agent your preferences once, then returning tomorrow without repeating them.

This terminal travel agent uses Redis Agent Memory to save an explicit profile immediately, learn from conversation over time, and carry context into a fresh session.

**Show:** A quick before-and-after: generic reply versus a personalized recommendation.

## 0:25 — The whole application in one diagram

Before the terminal, show the architecture diagram.

Say: “Every chat request is recorded as a session event. The app loads session context and
owner-scoped long-term memory, sends both to the model, and records the answer. The OpenAI
Responses API can use its built-in web search for current information; Redis promotes durable
facts asynchronously.”

Point to the direct path separately: “Onboarding is different. These are explicit facts we collect deliberately, so we rewrite them into concise profile statements and write them straight to long-term memory.”

**Show:** chat request → session event → session context + long-term search → LLM/web search →
assistant event; dashed path → memory promotion → long-term memory.

## 0:55 — Start the agent and create Maya’s profile

Launch the application with a new demo owner, `Maya Chen`. The app creates a new session UUID,
finds no direct profile, then goes straight into four onboarding questions—no confirmation screen.

```text
$ uv run trip-agent
Traveler name [traveler]: Maya Chen

Traveler: maya-chen
Fresh session started.
Session ID: 8b7d3d9e-1a9c-4a92-a1ef-0a4b0710cb42

Quick travel profile
Trip agent: What kinds of trips and places do you enjoy?
You: food, museums, local neighborhoods, and easy hikes

Trip agent: What food or dietary needs should I remember?
You: vegetarian

Trip agent: What budget works for you?
You: moderate, but I’ll splurge on one great experience

Trip agent: What city do you usually travel from?
You: London
```

Say: “A short LLM pass turns these explicit answers into concise, fact-preserving statements,
then writes them directly to long-term memory.”

Add: “A blank answer is skipped. If all four answers are blank, onboarding ends without an OpenAI
rewrite or Redis profile write. At any question, `/cancel`, Ctrl+C, or EOF discards the whole
attempt before the rewrite or any Redis profile write. Repeating onboarding updates the answered
categories instead of duplicating them.”

Show `/memories`. Each row has independent provenance (`direct` or `learned`) and kind
(`semantic fact`, `episodic event`, `retained message`, or a service-defined custom type). Profile
facts are direct semantic facts; dated plans are direct episodic events. Redis-provided kinds are
shown as returned, never guessed. The direct profile solves the cold start.

## 2:20 — Ask for a current recommendation

```text
You: I have five days in Lisbon in October. What should I do?
```

Point out the answer’s personalized details and current web citations. Say: “Memory
personalizes; web search handles time-sensitive facts.”

Run `/why`: it is the retrieval receipt for this answer, showing the retrieved direct and learned
records rather than claiming one record mechanically caused every response.

## 2:55 — Save a reproducible dated plan

While Maya is active, record the exact plan that powers the later conflict:

```text
You: I’m planning a trip to Japan from 2027-05-10 to 2027-05-20.
```

Explain: “This stores a direct episodic plan server-side under Maya’s owner ID. On a clean store,
record this step before switching owners so the later Nigeria request has a known overlap.”
Before recording, rehearse these two dated turns on a clean store to confirm the conflict; the
captured sequence records the Japan turn here, before the owner switch.

## 3:20 — Let the agent learn naturally

Add a preference in normal conversation:

```text
You: For shorter trips, I’d rather take trains than fly when the journey is practical. Please remember that.
```

Say: “This is not a direct profile write. Both turns are session events; Redis can extract,
deduplicate, and consolidate durable details in the background.”

**Show:**

```text
conversation → session events → asynchronous promotion → long-term memory
```

Add: “Promotion is asynchronous, so use a rehearsed pause or a pre-promoted example.”

## 4:35 — Show what retrieval actually means

Run `/memories` again, optionally narrowing it with `/memories transport preferences`.

Say: “This is owner-scoped semantic search with a relevance threshold: relevant direct and learned
matches, not a raw dump.”

Explain once: “A traveler name becomes an owner ID—`Maya Chen` becomes `maya-chen`. It scopes
this demo’s server-side retrieval; it is not authentication, authorization, account creation, or
a secure identity.”

## 5:30 — Restart to prove persistence

Exit the CLI and launch it again with `Maya Chen`.

```text
$ uv run trip-agent
Traveler name [traveler]: Maya Chen

Fresh session started.
Session ID: 01efdb77-8fb0-46c8-aa1d-9e20d0877ea2
```

Say: “The process and session are new, but Maya’s long-term profile remains in Redis Agent Memory.”

Then ask:

```text
You: I’m choosing between Amsterdam and Copenhagen for a long weekend. Which is a better fit?
```

Point out that Maya skips onboarding and receives a cited, personalized answer.

## 6:40 — Switch travelers

```text
/user Alex Demo 2026
```

Show the fresh session and automatic onboarding. At the first prompt, enter:

```text
You: /cancel
Onboarding cancelled. No profile changes were saved.
```

Say: “`/user` normalizes this to `alex-demo-2026`, starts a fresh session, clears the prior
`/why` receipt, and checks for a direct profile. Alex is new, so onboarding begins automatically;
`/cancel` atomically discards it. A returning owner gets the warm profile-available welcome and
skips questions.”

## 7:00 — Switch back to Maya

Before demonstrating Maya’s saved May plan, return to Maya explicitly:

```text
/user Maya Chen
```

Show the new session and welcome: Maya is returning, so onboarding is skipped.

## 7:20 — Let memory prevent a planning mistake

With Maya’s May trip to Asia already saved, ask:

```text
You: Plan me a trip to Nigeria for the entire month of May 2027.
```

The agent should stop before generating another itinerary and flag the overlap. Explain: “Trip
plans are stored as direct episodic records in a dedicated owner-scoped memory namespace with
normalized start and end dates. Before the agent plans a dated trip, the app uses a filter-only
owner-and-namespace check, then compares those dates in code. This is deliberate: memory gives
us the context, but date overlap is a product rule we check deterministically.”

## 7:45 — Show the important code

Keep this compact and use the diagram to orient the viewer:

1. `add_session_event()` records each user and assistant turn.
2. `get_session_memory()` loads short-term context for the current session.
3. `search_long_term_memory()` performs owner-scoped, relevance-thresholded semantic retrieval;
   direct profile and trip-plan checks use filter-only requests.
4. Onboarding upserts: `update_long_term_memory()` updates existing categories; one
   `bulk_create_long_term_memories()` call creates missing ones.
5. `/why` exposes the retrieved records from the latest answer as a transparent retrieval receipt.
6. The normal chat path does not manually promote or deduplicate memories; Redis Agent Memory manages that asynchronously.

Say: “Direct writes are intentional facts; session events preserve conversation; background
promotion decides what carries forward.”

## 8:30 — Close

This pattern generalizes to coding preferences, customer context, and learning goals: use direct
writes for intentional facts, session events for conversation, background promotion for durable
details, and visible retrieval for trust.

**Recording notes:** Use live citations returned on the recording day, rehearse automatic-promotion timing, and never enter real secrets, payment details, or booking codes in the demo.
