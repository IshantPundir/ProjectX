"""Phase C deliveries tests — typed render wrappers + fallback_for."""
from __future__ import annotations

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
