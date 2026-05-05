"""Phase C — typed render wrappers + fallback_for factory.

Each render_<name> function is decorated with a marker that exposes the
template_name as an attribute on the function itself, so the orchestrator's
_consume_pending_or_render helper can pick the right fallback factory
without an explicit dispatch table.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.modules.interview_engine.speech.agent import (
    SpeechAgent,
    SpeechRenderErrorReason,
    SpeechRenderHandle,
)
from app.modules.interview_engine.speech.fallbacks import build_fallback_text

_F = TypeVar("_F", bound=Callable[..., Awaitable[SpeechRenderHandle]])


def _delivery(template_name: str) -> Callable[[_F], _F]:
    """Marker decorator that attaches `template_name` to the function so the
    orchestrator's consumption helper can dispatch fallback by attribute lookup."""
    def _wrap(fn: _F) -> _F:
        fn.template_name = template_name  # type: ignore[attr-defined]
        return fn
    return _wrap


@_delivery("intro")
async def render_intro(
    speech_agent: SpeechAgent,
    *,
    candidate_first_name: str,
    role_title: str,
    target_duration_minutes: int,
) -> SpeechRenderHandle:
    return await speech_agent.render(
        template_name="intro",
        template_version="v1",
        inputs={
            "candidate_first_name": candidate_first_name,
            "role_title": role_title,
            "target_duration_minutes": target_duration_minutes,
        },
    )


@_delivery("ask_question_standard")
async def render_ask_question_standard(
    speech_agent: SpeechAgent,
    *,
    question_text: str,
) -> SpeechRenderHandle:
    return await speech_agent.render(
        template_name="ask_question_standard",
        template_version="v1",
        inputs={"question_text": question_text},
    )


@_delivery("wrap_normal")
async def render_wrap_normal(speech_agent: SpeechAgent) -> SpeechRenderHandle:
    return await speech_agent.render(
        template_name="wrap_normal", template_version="v1", inputs={},
    )


async def fallback_for(
    speech_agent: SpeechAgent,
    *,
    template_name: str,
    failure_reason: SpeechRenderErrorReason,
    render_id: str | None,
    **inputs: object,
) -> SpeechRenderHandle:
    """Constructs the fallback handle for a given template.

    `render_id` is the failed live render's render_id (reused so the two
    fallback events correlate per spec §4.5). May be None if the failure
    was synchronous (template_not_found / placeholder_missing) — but those
    failures don't trigger fallback in the consumption helper anyway.
    """
    text = build_fallback_text(template_name=template_name, **inputs)
    rid = render_id or "fallback-" + str(id(speech_agent))
    return speech_agent.fallback_handle(
        template_name=template_name,
        template_version="v1",
        text=text,
        failure_reason=failure_reason,
        retries_attempted=1,
        render_id=rid,
    )
