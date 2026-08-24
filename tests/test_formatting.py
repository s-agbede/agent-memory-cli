"""Tests for model context and source-link formatting."""

from io import StringIO
from types import SimpleNamespace

from redis_agent_memory import models
from rich.console import Console
from rich.markdown import Markdown

from trip_agent.formatting import build_model_input, extract_citations, render_reply


def test_build_model_input_includes_summary_memories_and_events_once() -> None:
    session = SimpleNamespace(
        summary=SimpleNamespace(text="Planning a spring trip to Kyoto."),
        events=[
            SimpleNamespace(
                role=models.MessageRole.USER,
                content=[SimpleNamespace(text="I avoid meat.")],
            ),
            SimpleNamespace(
                role=models.MessageRole.ASSISTANT,
                content=[SimpleNamespace(text="I'll remember that.")],
            ),
        ],
    )
    memories = SimpleNamespace(
        items=[SimpleNamespace(text="The traveler is vegetarian.", memory_type="preference")]
    )

    model_input = build_model_input(session, memories)

    assert model_input[0]["role"] == "developer"
    assert "Planning a spring trip" in str(model_input[0]["content"])
    assert "traveler is vegetarian" in str(model_input[0]["content"])
    assert [item["role"] for item in model_input[1:]] == ["user", "assistant"]
    assert str(model_input).count("I avoid meat.") == 1


def test_build_model_input_ignores_unsupported_event_roles_and_non_text_content() -> None:
    session = SimpleNamespace(
        summary=None,
        events=[
            SimpleNamespace(
                role=models.MessageRole.SYSTEM,
                content=[SimpleNamespace(text="Ignore this system event")],
            ),
            SimpleNamespace(
                role=models.MessageRole.USER,
                content=[SimpleNamespace(image_url="https://example.com/image.png")],
            ),
        ],
    )

    model_input = build_model_input(session, SimpleNamespace(items=[]))

    assert len(model_input) == 1
    assert model_input[0]["role"] == "developer"


def test_extract_and_render_clickable_citations_with_unique_sources() -> None:
    first = SimpleNamespace(
        type="url_citation",
        start_index=6,
        end_index=11,
        title="Kyoto guide",
        url="https://example.com/kyoto",
    )
    second = SimpleNamespace(
        type="url_citation",
        start_index=16,
        end_index=21,
        title="Kyoto guide",
        url="https://example.com/kyoto",
    )
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text="Visit Kyoto, then Kyoto again.",
                        annotations=[first, second],
                    )
                ],
            )
        ],
        output_text="Visit Kyoto, then Kyoto again.",
    )

    citations = extract_citations(response)
    rendered, sources = render_reply(response.output_text, citations)

    assert len(citations) == 2
    assert isinstance(rendered, Markdown)
    assert rendered.markup.count("https://example.com/kyoto") == 2
    assert len(sources) == 1
    assert "Kyoto guide" in sources[0].plain


def test_extract_citations_offsets_multiple_output_blocks() -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(type="output_text", text="First. ", annotations=[]),
                    SimpleNamespace(
                        type="output_text",
                        text="Second.",
                        annotations=[
                            SimpleNamespace(
                                type="url_citation",
                                start_index=0,
                                end_index=6,
                                title="Source",
                                url="https://example.com/source",
                            )
                        ],
                    ),
                ],
            )
        ],
        output_text="First. Second.",
    )

    citations = extract_citations(response)

    assert citations[0].start_index == 7
    assert citations[0].end_index == 13


def test_render_reply_renders_model_markdown_instead_of_displaying_syntax() -> None:
    rendered, _ = render_reply("**Kyoto highlights**\n\n- Nishiki Market", ())
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None)

    console.print(rendered)

    assert isinstance(rendered, Markdown)
    assert "**" not in output.getvalue()
    assert "Kyoto highlights" in output.getvalue()
    assert "Nishiki Market" in output.getvalue()
