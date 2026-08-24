# Citation Rendering Fix Design

## Problem

OpenAI web-search responses can contain a Markdown link in `output_text` while also attaching a URL citation annotation whose character span overlaps that link. The CLI currently inserts another Markdown link around every annotated span. This creates nested Markdown, which Rich cannot parse as a link and therefore displays literally.

## Intended Behavior

- Render headings, lists, emphasis, and ordinary Markdown as Rich Markdown.
- Keep web citations clickable inline when the terminal supports hyperlinks.
- Keep the existing de-duplicated, clickable `Sources` list.
- Never expose raw Markdown link syntax merely because an OpenAI citation overlaps an existing link.
- Continue normalizing labels shaped like `[(example.com)](https://example.com)` to `[example.com](https://example.com)`.

## Design

`render_reply` remains the single display boundary for assistant replies. Before inserting citation links, it will identify the spans occupied by valid Markdown inline links in the original response text.

For each citation, processed from the end of the response toward the beginning:

1. Clamp its annotation indexes to the original response bounds.
2. Ignore an empty span.
3. If the citation span overlaps an existing Markdown link, leave that existing link intact rather than wrapping any part of it again.
4. Otherwise, wrap the cited plain-text span in a Markdown link using the citation URL.
5. Normalize parenthesized domain labels after citation insertion.

The source-list construction remains unchanged and continues to de-duplicate by URL.

## Edge Cases

- A citation may cover only the label of an existing link, the whole link, or another overlapping portion. Any overlap suppresses the second wrapper.
- Out-of-range indexes remain safely clamped.
- Empty annotation spans do not create empty links.
- Multiple ordinary citation spans continue to be applied in reverse index order so earlier indexes remain valid.
- Duplicate citation URLs still produce one source-list entry.
- Ordinary model Markdown unrelated to citations remains untouched.

## Testing

Add focused formatting regressions that render output through a plain Rich console and assert that:

- a citation covering an existing link label does not print raw Markdown;
- a citation covering an entire existing link does not print raw Markdown;
- an ordinary plain-text citation remains an inline Markdown link;
- malformed or empty citation spans do not corrupt surrounding output;
- the existing de-duplicated source list remains intact.

Run the full unit suite plus formatting, linting, type checking, and whitespace validation.

## Scope

This change does not alter prompts, OpenAI requests, Redis Agent Memory behavior, citation extraction, or terminal commands. It only makes reply rendering robust to overlapping citation representations.
