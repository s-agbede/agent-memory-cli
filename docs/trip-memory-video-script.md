# Build a Memory-Enabled Travel Agent with Redis

**Target length:** 8–10 minutes

## 0:00 — Hook

Imagine telling a travel agent that you are vegetarian, prefer relaxed trips, and have a moderate budget—then returning tomorrow and having to explain all of it again.

That is the difference between a chatbot that answers and an agent that builds continuity. In this video, I am building a small terminal travel agent with Redis Agent Memory. It remembers an explicit profile immediately, learns useful details from conversation over time, and brings that context into a fresh session.

**Show:** A quick before-and-after: generic reply versus a personalized recommendation.

## 0:25 — The whole application in one diagram

Before the terminal, show the architecture diagram.

Say: “Every chat request is recorded as a session event. The app loads short-term session context and searches owner-scoped long-term memory. Those are assembled into the model prompt, the model can use web search for current information, and the answer is recorded as another session event. Redis Agent Memory then promotes durable facts asynchronously.”

Point to the direct path separately: “Onboarding is different. These are explicit facts we collect deliberately, so we rewrite them into concise profile statements and write them straight to long-term memory.”

**Show:** The request lifecycle diagram: chat request → record user event → load session context + search long-term memory → prompt assembler → LLM/web search → record assistant event → response; dashed asynchronous path → memory promotion → long-term memory.

## 0:55 — Start the agent and create Maya’s profile

Launch the application and enter `Maya Chen`. The app creates a new session UUID, then goes straight into four onboarding questions—no confirmation screen.

```text
$ uv run trip-agent
Traveler name [traveler]: Maya Chen

Traveler: maya-chen
Fresh session started.
Session ID: 8b7d3d9e-1a9c-4a92-a1ef-0a4b0710cb42

Quick travel profile
Trip agent: What kinds of trips and places do you enjoy?
> food, museums, local neighborhoods, and easy hikes

Trip agent: What food or dietary needs should I remember?
> vegetarian

Trip agent: What budget works for you?
> moderate, but I’ll splurge on one great experience

Trip agent: What city do you usually travel from?
> London
```

Point out the loading indicator. Say: “The profile answers are explicit, but they are still messy human language. A short LLM pass turns them into concise, fact-preserving memory statements. Then the app writes those statements directly to long-term memory.”

Show `/memories`. The direct labels are the cold-start solution: the agent can use this profile immediately, without waiting for a future chat turn to be promoted.

## 2:20 — Ask for a current recommendation

```text
You: I have five days in Lisbon in October. What should I do?
```

Point out the “Planning your trip…” indicator, then walk through the answer. It should use Maya’s remembered interests, dietary needs, budget, and departure city. It should also include current web citations.

Say: “Memory personalizes the answer. Web search handles time-sensitive facts such as current opening hours, events, and transport information.”

## 3:20 — Let the agent learn naturally

Add a preference in normal conversation:

```text
You: For shorter trips, I’d rather take trains than fly when the journey is practical. Please remember that.
```

Say: “This is not another direct profile write. The user message and the assistant’s answer are stored as ordered session events right away. Redis Agent Memory evaluates those events in the background and can extract, deduplicate, and consolidate details that matter beyond this trip.”

**Show:**

```text
conversation → session events → asynchronous promotion → long-term memory
```

Add: “Because promotion is asynchronous, a successful session write does not mean the new long-term memory appears instantly. For the recording, either use a rehearsed pause or a pre-promoted example.”

## 4:35 — Show what retrieval actually means

Run `/memories` again, optionally narrowing it with `/memories transport preferences`.

Say: “This is a semantic search, filtered to the active traveler’s owner ID. It can show direct onboarding records and memories Redis learned from previous conversations. The `/memories` command returns the most relevant matches, not a raw dump of everything.”

This is a good moment to explain that a username is an owner ID, not a newly-created local account. Reusing `Maya Chen` reuses `maya-chen` and therefore retrieves Maya’s existing server-side long-term memories.

## 5:30 — Restart to prove persistence

Exit the CLI and launch it again with `Maya Chen`.

```text
$ uv run trip-agent
Traveler name [traveler]: Maya Chen

Fresh session started.
Session ID: 01efdb77-8fb0-46c8-aa1d-9e20d0877ea2
```

Say: “The old process is gone and this session UUID is new. Short-term conversation context starts fresh. But Maya’s long-term profile remains because it lives in Redis Agent Memory, not in this terminal process.”

Then ask:

```text
You: I’m choosing between Amsterdam and Copenhagen for a long weekend. Which is a better fit?
```

Point out that Maya is not asked to repeat her profile. Relevant long-term context is retrieved, combined with current web results, and used in a cited answer.

## 6:40 — Switch travelers

```text
/user Alex
```

Explain: “Switching users creates a new session and changes the owner filter. Alex sees only Alex’s long-term memories. If Alex is new, `/onboard` can seed a profile in this same terminal.”

For a clean recording identity, use a unique name such as `Alex Demo 2026`; the normalized owner ID becomes `alex-demo-2026`.

## 7:00 — Let memory prevent a planning mistake

With Maya’s May trip to Asia already saved, ask:

```text
You: Plan me a trip to Nigeria for the entire month of May 2027.
```

The agent should stop before generating another itinerary and flag the overlap. Explain: “Trip plans are stored in a dedicated owner-scoped memory namespace with normalized start and end dates. Before the agent plans a dated trip, the app compares those dates in code. This is deliberate: memory gives us the context, but date overlap is a product rule we check deterministically.”

## 7:30 — Show the important code

Keep this compact and use the diagram to orient the viewer:

1. `add_session_event()` records each user and assistant turn.
2. `get_session_memory()` loads short-term context for the current session.
3. `search_long_term_memory()` performs semantic retrieval with an `owner_id` filter.
4. `bulk_create_long_term_memories()` writes the rewritten onboarding facts directly.
5. The normal chat path does not manually promote or deduplicate memories; Redis Agent Memory manages that asynchronously.

Say: “The key design choice is not to save every line as durable memory. Direct writes are for explicit, trusted facts. Session events preserve the conversation. Background promotion decides what is worth carrying forward.”

## 8:30 — Close

We built a small terminal travel agent, but the memory pattern generalizes: a coding assistant can retain framework preferences, a support agent can retain account context, and a learning assistant can remember goals and pace.

Start with direct writes for facts you intentionally collect. Record real conversations as session events. Let durable information be promoted in the background. Then make memory visible and retrievable.

That is how you move from a chatbot that merely responds to an agent that has continuity with a person.

**Recording notes:** Use live citations returned on the recording day, rehearse automatic-promotion timing, and never enter real secrets, payment details, or booking codes in the demo.
