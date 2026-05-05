"""Phase C deliveries tests — typed render wrappers + fallback_for."""
from __future__ import annotations

import string
from unittest.mock import MagicMock, AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_render_intro_calls_speech_agent_with_correct_inputs():
    from app.modules.interview_engine.speech.deliveries import render_intro

    speech_agent = AsyncMock()
    speech_agent.render = AsyncMock()
    await render_intro(
        speech_agent,
        candidate_first_name="Alex",
        role_title="Backend Engineer",
        target_duration_minutes=15,
    )
    speech_agent.render.assert_awaited_once_with(
        template_name="intro",
        template_version="v1",
        inputs={
            "candidate_first_name": "Alex",
            "role_title": "Backend Engineer",
            "target_duration_minutes": 15,
        },
    )


@pytest.mark.asyncio
async def test_render_ask_question_standard_calls_speech_agent_with_correct_inputs():
    from app.modules.interview_engine.speech.deliveries import render_ask_question_standard

    speech_agent = AsyncMock()
    speech_agent.render = AsyncMock()
    await render_ask_question_standard(speech_agent, question_text="Tell me about your last project.")
    speech_agent.render.assert_awaited_once_with(
        template_name="ask_question_standard",
        template_version="v1",
        inputs={"question_text": "Tell me about your last project."},
    )


@pytest.mark.asyncio
async def test_render_wrap_normal_takes_no_inputs():
    from app.modules.interview_engine.speech.deliveries import render_wrap_normal

    speech_agent = AsyncMock()
    speech_agent.render = AsyncMock()
    await render_wrap_normal(speech_agent)
    speech_agent.render.assert_awaited_once_with(
        template_name="wrap_normal",
        template_version="v1",
        inputs={},
    )


@pytest.mark.asyncio
async def test_fallback_for_intro_passes_through_to_fallback_handle():
    from app.modules.interview_engine.speech.deliveries import fallback_for

    speech_agent = MagicMock()
    speech_agent.fallback_handle = MagicMock(return_value="HANDLE")
    handle = await fallback_for(
        speech_agent,
        template_name="intro",
        failure_reason="openai_timeout",
        render_id="abc",
        target_duration_minutes=30,
    )
    assert handle == "HANDLE"
    speech_agent.fallback_handle.assert_called_once()
    call_kwargs = speech_agent.fallback_handle.call_args.kwargs
    assert call_kwargs["template_name"] == "intro"
    assert call_kwargs["template_version"] == "v1"
    assert "30 minutes" in call_kwargs["text"]
    assert call_kwargs["failure_reason"] == "openai_timeout"
    assert call_kwargs["retries_attempted"] == 1
    assert call_kwargs["render_id"] == "abc"


@pytest.mark.asyncio
async def test_render_wrappers_have_template_name_attribute():
    """The consumption helper uses render_fn.template_name to pick the
    right fallback factory."""
    from app.modules.interview_engine.speech.deliveries import (
        render_intro,
        render_ask_question_standard,
        render_wrap_normal,
    )
    assert render_intro.template_name == "intro"
    assert render_ask_question_standard.template_name == "ask_question_standard"
    assert render_wrap_normal.template_name == "wrap_normal"


def _placeholders_in(template_text: str) -> set[str]:
    """Extract `{placeholder}` field names from a template via stdlib Formatter."""
    names: set[str] = set()
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(
        template_text
    ):
        if field_name is not None and field_name != "":
            # Strip any attribute / index access (e.g. `obj.attr` or `arr[0]`)
            # to get the root identifier — we only care that the delivery
            # passes a key under that name.
            root = field_name.split(".")[0].split("[")[0]
            names.add(root)
    return names


@pytest.mark.parametrize(
    ("template_name", "import_name", "sentinel_inputs"),
    [
        (
            "intro",
            "render_intro",
            {
                "candidate_first_name": "Alex",
                "role_title": "Engineer",
                "target_duration_minutes": 15,
            },
        ),
        (
            "ask_question_standard",
            "render_ask_question_standard",
            {"question_text": "Sample question."},
        ),
        ("wrap_normal", "render_wrap_normal", {}),
    ],
)
@pytest.mark.asyncio
async def test_delivery_inputs_satisfy_template_placeholders(
    template_name: str,
    import_name: str,
    sentinel_inputs: dict[str, object],
) -> None:
    """Regression guard for placeholder_missing fallbacks.

    Discovered in user's first manual smoke test
    (session 06c5af57-3b7f-45ad-aa90-98f8b376faef): the
    ask_question_standard.v1.txt template had a dead trailing `# Inputs`
    echo block referencing {is_first_question} and {previous_answer_brief}
    that the delivery wrapper did NOT pass. _render_prompt's
    template_text.format(**inputs) raised KeyError → SpeechRenderError
    (placeholder_missing) → fallback path → every render fell back to
    static text (which IS question_text verbatim). 5/5 ask_question_standard
    renders fell back in that session.

    For each template, assert that every placeholder the template uses
    is present in the inputs dict the delivery wrapper passes.
    """
    from app.modules.interview_engine.speech import deliveries
    from app.modules.interview_engine.speech.templates import template_loader

    template_text = template_loader.get(
        role="speech_agent", name=template_name, version="v1",
    )
    placeholders = _placeholders_in(template_text)

    render_fn = getattr(deliveries, import_name)
    speech_agent = AsyncMock()
    speech_agent.render = AsyncMock()
    await render_fn(speech_agent, **sentinel_inputs)

    speech_agent.render.assert_awaited_once()
    inputs_passed = speech_agent.render.await_args.kwargs["inputs"]

    missing = placeholders - set(inputs_passed.keys())
    assert not missing, (
        f"Template {template_name}.v1.txt has placeholders {missing} "
        f"that the delivery wrapper does not pass. This will cause "
        f"placeholder_missing fallbacks at runtime — every render of "
        f"this template will fall through to static fallback text."
    )
