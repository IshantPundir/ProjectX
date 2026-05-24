"""TriagePlane — the fast first-tier call (no livekit). Renders the cache-stable prefix once, then
per turn: build messages → bounded instructor call on engine_triage_model → TriageDecision. A
timeout/error yields a SAFE fallback: a canned ack filler + route=to_brain (never wrongly skip the
brain). The LLM call is isolated in `_call_triage` so tests mock it at the app/ai boundary."""
from __future__ import annotations

import asyncio
import random

import structlog

from app.ai.config import ai_config
from app.config import settings
from app.modules.interview_engine_v2.triage.decision import (
    TriageDecision,
    TriageKind,
    TriageRoute,
)
from app.modules.interview_engine_v2.triage.input_builder import (
    build_triage_messages,
    render_triage_prefix,
)

log = structlog.get_logger("interview_engine_v2.triage")


async def _call_triage(*, messages: list[dict[str, str]], correlation_id: str) -> TriageDecision:
    from app.ai.client import get_openai_client
    client = get_openai_client()
    kwargs: dict[str, object] = {
        "model": ai_config.engine_triage_model,
        "response_model": TriageDecision,
        "messages": messages,
        "max_retries": 1,
        "prompt_cache_key": "triage:v1",
    }
    if ai_config.engine_triage_effort:
        kwargs["reasoning_effort"] = ai_config.engine_triage_effort
    return await client.chat.completions.create(**kwargs)


class TriagePlane:
    def __init__(self, *, persona_name: str, job_title: str) -> None:
        from app.ai.prompts import PromptLoader
        loader = PromptLoader(version=ai_config.engine_triage_prompt_version)
        self._prefix = render_triage_prefix(
            system_prompt=loader.get("engine/triage.system"),
            persona_name=persona_name, job_title=job_title)

    def _fallback(self) -> TriageDecision:
        return TriageDecision(
            reasoning="triage unavailable — safe fallback", kind=TriageKind.answering,
            answer_complete=True, route=TriageRoute.to_brain,
            spoken_line=random.choice(settings.engine_v2_ack_messages))

    async def triage(
        self, *, active_question: str | None, accumulated_answer: str,
        last_spoken_question: str | None, recent_fillers: list[str] | None = None,
        correlation_id: str = "", budget_ms: int | None = None,
    ) -> TriageDecision:
        messages = build_triage_messages(
            triage_prefix=self._prefix, active_question=active_question,
            accumulated_answer=accumulated_answer, last_spoken_question=last_spoken_question,
            recent_fillers=recent_fillers)
        timeout = (budget_ms if budget_ms is not None
                   else ai_config.engine_triage_total_budget_ms) / 1000.0
        try:
            return await asyncio.wait_for(
                _call_triage(messages=messages, correlation_id=correlation_id), timeout=timeout)
        except TimeoutError:
            log.warning("engine.v2.triage.timeout", correlation_id=correlation_id)
            return self._fallback()
        except Exception:  # noqa: BLE001 — triage must never crash a turn
            log.warning("engine.v2.triage.error", exc_info=True, correlation_id=correlation_id)
            return self._fallback()
