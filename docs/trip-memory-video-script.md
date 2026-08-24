# Build a Memory-Enabled Travel Agent with Redis

**Target length:** 8–10 minutes

## 0:00 — Hook

Imagine a traveler tells an agent: “I’m vegetarian, I dislike rushed itineraries, and I’m trying to keep this affordable.” They return tomorrow and ask, “Where should I go next?”

Without memory, the agent starts from zero. The traveler repeats themselves, recommendations become generic, and the application feels like it has amnesia.

In this video, I’ll build a small terminal travel agent that remembers the right things across sessions with Redis Agent Memory.

**Show:** A quick split screen: “stateless chat” repeats questions; “memory-enabled agent” gives a personal answer.

## 0:35 — Why memory exists

Before we write code, let’s take a step back. Language models are stateless. They can use the information included in the current request, but they do not inherently carry a person’s preferences into the next conversation.

Applications that feel like they remember you have added a memory layer around the model.

Short-term memory—also called working memory—is the ordered conversation for this current session: what the traveler just asked, options we already discussed, and context the agent needs right now.

Long-term memory is durable information worth bringing into future sessions: dietary needs, budget, travel pace, favorite activities, or a decision the traveler wants remembered.

The design question is not “save everything forever.” It is: what will make a future answer more useful?

**Show:**

```text
Current conversation → session / short-term memory
Useful durable facts → long-term memory → future sessions
```

## 1:25 — What we are building

Our demo is intentionally small: a terminal application called Trip Agent.

You choose a traveler by username. During onboarding, the app asks a few explicit profile questions and writes those answers directly to long-term memory. That avoids the cold-start experience where an agent has to ask the same basics before it can help.

Then normal chat turns are stored as session events. Redis Agent Memory can identify salient, durable facts from those interactions and promote them in the background.

We will also use web search for current travel information and show the sources directly in the answer.

**Show:**

```text
traveler → direct profile memory
chat → session events → background promotion
/memories → inspect long-term context
restart → new session, same long-term context
```

## 2:05 — Direct writes versus automatic learning

There are two useful ways memory is written in this app.

First, direct long-term writes. During onboarding, we already have clear, structured facts. If Maya says she is vegetarian and prefers a moderate budget, we can save those facts immediately. This is useful for preferences, imported data, and trusted business reference information.

Second, automatic promotion. During chat, we save the actual user and assistant messages as ordered session events. Redis Agent Memory evaluates those events in the background and can extract durable information that is useful beyond this conversation.

That keeps session memory as a faithful record of the conversation, while long-term memory stays focused on facts worth retrieving later.

One important detail: background promotion is asynchronous. A successful session write does not mean a newly extracted fact is available in long-term memory instantly. Design your product and tests with that eventual consistency in mind.

**Show:**

```text
Onboarding: “Vegetarian” → direct LTM write
Conversation: “I prefer trains under six hours”
  → session event → background extraction → possible LTM
```

## 2:50 — Onboard a traveler

Start the application and enter `Maya Chen` as the traveler. Point out the generated session ID.

```text
$ uv run trip-agent
Traveler name [traveler]: Maya Chen

Traveler: maya-chen
Fresh session started.
Session ID: 8b7d3d9e-1a9c-4a92-a1ef-0a4b0710cb42
Save a travel profile directly to long-term memory? [Y/n]
```

Answer the onboarding questions:

```text
What kinds of trips and places do you enjoy?
> food, museums, local neighborhoods, easy hikes

What food or dietary needs should I remember?
> vegetarian

What budget works for you?
> moderate, but I’ll splurge on one great experience

What city do you usually travel from?
> relaxed — I don’t want to move hotels every night
```

Explain: Maya’s basic travel profile went straight to owner-scoped long-term memory. It is tied to Maya’s identity, not to this one session ID.

Run `/memories` and show the direct labels. This is the cold-start solution: the agent can personalize immediately.

## 4:00 — Ask for a current recommendation

```text
You: I have five days in Lisbon in October. What should I do?
```

Walk through the answer. It should use Maya’s remembered interests, dietary needs, budget, and relaxed pace. It should also show current web citations.

Say: “Memory personalizes the answer. Web search verifies time-sensitive details such as current opening hours, events, and transport information.”

## 4:55 — Learn from normal conversation

Now add a new preference naturally:

```text
You: For shorter trips, I’d rather take trains than fly when the journey is practical. Please remember that.
```

Explain: “This is not a direct profile form write. The turn is saved to session memory immediately. Redis Agent Memory can extract, deduplicate, and consolidate durable facts from the conversation in the background.”

Optionally add: “Walkability matters more to me than nightlife.”

**Show:**

```text
Session events, in order
• prefers practical rail travel for short trips
• prioritizes walkability over nightlife
        ↓
background extraction and consolidation
        ↓
durable long-term context
```

Use a rehearsed pause or a pre-promoted run, then run `/memories` again. Say clearly that recent automatic memories may take time to appear.

## 6:15 — Restart to prove persistence

Exit the CLI and launch it again. Enter `Maya Chen` again.

```text
$ uv run trip-agent
Traveler name [traveler]: Maya Chen

Fresh session started.
Session ID: 01efdb77-8fb0-46c8-aa1d-9e20d0877ea2
```

This is the key moment. The previous process stopped. The old session ID is gone. This is a completely new session.

But Maya’s long-term profile is still available because it was stored outside the running process. That is the difference between session memory and long-term memory in one screen.

## 7:15 — Retrieve memory in the new session

```text
You: I’m choosing between Amsterdam and Copenhagen for a long weekend. Which is a better fit?
```

Point out that the agent does not ask Maya to repeat her profile. It retrieves relevant long-term context, combines it with current web results, and returns a cited recommendation.

Then demonstrate tenant isolation:

```text
/user Alex
```

Explain that Alex gets a new session and only Alex’s owner-scoped memories are searched. The app does not claim whether Alex is a newly created account—an empty long-term-memory result can also mean background promotion is pending. Use `/onboard` to seed Alex’s profile if needed.

## 8:30 — What belongs in memory

A useful rule of thumb: if you would want to retrieve something in a future conversation, it may belong in long-term memory. If it is only useful for the current task, keep it in session memory.

For a travel agent, good long-term candidates include dietary requirements, budget range, accessibility needs, preferred pace, activities someone enjoys, and transport preferences.

Direct memory can also hold trusted reference data or business guidance. But retrieved memories are reference context, not executable security controls. Authorization, hard safety rules, and access control belong in application code and infrastructure.

## 9:20 — Close

We built a small memory-enabled travel agent, but the pattern generalizes. A coding agent can remember framework preferences. A support agent can retain product context. A research assistant can remember preferred source types and level of detail.

Start with direct writes for facts you explicitly collect. Record natural conversations as session events. Let durable context be promoted in the background. Then make memory visible, retrievable, and manageable.

That is how you move from a chatbot that merely responds to an agent that builds continuity with a user.

**Recording notes:** Use live citations returned on the recording day, rehearse automatic-promotion timing, and never enter real secrets, payment details, or booking codes in the demo.
