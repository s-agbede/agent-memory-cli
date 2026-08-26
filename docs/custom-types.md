# Custom memory types

The app works with Redis Agent Memory's built-in extraction and needs no configuration to run.
Custom memory types are optional.

## What is supported

Custom types flow through without code changes:

- **Retrieval** — search filters on `owner_id` only, with no `memory_type` restriction, so
  custom-typed records are returned by `/memories`, `/memories <query>`, and `/why`.
- **Display** — the kind label passes unmapped type names straight through, so a record shows as
  `trip_preference` rather than being coerced into a built-in label.
- **Prompt context** — custom-typed records reach the model in the assembled context like any
  other memory.

## What is not supported

The app renders each record's `text` field. It does not read `MemoryRecord.attributes`, which is
where Redis stores a custom type's structured fields:

> `attributes` — Structured record attributes: a map from the custom memory type's field name to
> that field's value.

So the four structured fields below are retrieved and then dropped at the display layer. A custom
type appears as a labelled record, not a structured card. Rendering them would mean extending
`_memory_views()` in `src/trip_agent/agent.py` and `_show_memory_rows()` in
`src/trip_agent/cli.py`.

Worth knowing before you configure one: because the visible difference is only the type label,
a custom type is not currently a more visual demo than the built-in kinds.

## Configuring `trip_preference`

From Redis's travel-planning quickstart:

- Name: `trip_preference`
- Description: `Structured requirements for a planned trip`
- Destinations: `list[str]`
- Travel period: `str`
- Dietary requirements: `list[str]`
- Food preferences: `list[str]`

A suitable extraction instruction:

```text
Extract trip requirements only when the user states a destination or travel plan.
Preserve explicit dietary requirements and food preferences.
```

Custom types are configured on the Redis Agent Memory service, not in this application.

## Related

- [How memory works](memory-design.md)
