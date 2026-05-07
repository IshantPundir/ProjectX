"""JudgeService — calls OpenAI Responses API with structured output + retry/fallback."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

from app.ai.tracing import set_llm_span_attributes
from app.modules.interview_engine.judge.fallback import (
    FallbackReason, synthesize_fallback,
)
from app.modules.interview_engine.judge.input_builder import JudgeInputPayload
from app.modules.interview_engine.models.judge import JudgeOutput


@dataclass(slots=True)
class JudgeCallResult:
    judge_output: JudgeOutput
    is_fallback: bool
    fallback_reason: FallbackReason | None
    original_failure_context: dict[str, Any] | None
    latency_ms: int
    usage: dict[str, int] | None
    model_used: str


class JudgeService:
    """Calls the Judge LLM with one retry, 3s total budget, and fallback synthesis.

    The OpenAI Responses API is invoked with `response_format` set to a JSON Schema
    derived from JudgeOutput. We parse the text and validate via Pydantic. Any
    failure (timeout, parse, schema validation) routes to the fallback synthesizer.
    """

    def __init__(
        self,
        *,
        openai_client: Any,
        model: str,
        system_prompt: str,
        system_prompt_hash: str,
        next_pending_mandatory_resolver: Callable[[], str | None],
        total_budget_ms: int = 3000,
        retry_wait_ms: int = 250,
    ) -> None:
        self._client = openai_client
        self._model = model
        self._system_prompt = system_prompt
        self._system_prompt_hash = system_prompt_hash
        self._next_pending_resolver = next_pending_mandatory_resolver
        self._total_budget_ms = total_budget_ms
        self._retry_wait_ms = retry_wait_ms

    async def call(
        self,
        *,
        turn_id: str,
        input_payload: JudgeInputPayload,
        correlation_id: str,
        tenant_id: str,
    ) -> JudgeCallResult:
        set_llm_span_attributes(
            prompt_name="engine/judge.system",
            prompt_version="v1",
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            turn_id=turn_id,
            model=self._model,
        )

        budget_seconds = self._total_budget_ms / 1000.0
        retry_wait_seconds = self._retry_wait_ms / 1000.0
        # Split the wall-clock budget across the two attempts (initial + 1 retry),
        # subtracting the flat retry wait. This keeps overall latency bounded while
        # guaranteeing the retry actually fires on a transient failure.
        per_attempt_seconds = max(
            0.001,
            (budget_seconds - retry_wait_seconds) / 2.0,
        )

        started = time.monotonic()
        attempt_text: str | None = None
        last_exc: Exception | None = None
        usage: Any = None

        async def _one_attempt() -> tuple[str, Any]:
            response = await self._client.responses.create(
                model=self._model,
                instructions=self._system_prompt,
                input=input_payload.model_dump_json(),
                text={"format": {"type": "json_object"}},
            )
            return response.output_text, response.usage

        # Attempt #1
        try:
            attempt_text, usage = await asyncio.wait_for(
                _one_attempt(), timeout=per_attempt_seconds,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            last_exc = exc
            attempt_text = None
        except Exception as exc:  # network / 5xx / rate-limit
            last_exc = exc
            attempt_text = None

        # If first attempt didn't yield text, retry once after a flat wait.
        if attempt_text is None:
            await asyncio.sleep(retry_wait_seconds)
            try:
                attempt_text, usage = await asyncio.wait_for(
                    _one_attempt(), timeout=per_attempt_seconds,
                )
            except Exception as exc:
                last_exc = exc

        latency_ms = int((time.monotonic() - started) * 1000)

        if attempt_text is None:
            # Both attempts failed → timeout fallback.
            return self._fallback(
                FallbackReason.timeout,
                {"exception_class": type(last_exc).__name__ if last_exc else "Unknown",
                 "exception_message": str(last_exc)[:500] if last_exc else ""},
                latency_ms=latency_ms, usage=None,
            )

        # Try to parse + validate.
        try:
            data = json.loads(attempt_text)
        except json.JSONDecodeError as exc:
            return self._fallback(
                FallbackReason.parse_error,
                {"raw_text": attempt_text[:1000], "error": str(exc)},
                latency_ms=latency_ms,
                usage=self._usage_dict(usage),
            )

        try:
            judge_output = JudgeOutput.model_validate(data)
        except ValidationError as exc:
            return self._fallback(
                FallbackReason.validation_error,
                {"raw_data": data, "errors": exc.errors()},
                latency_ms=latency_ms,
                usage=self._usage_dict(usage),
            )

        return JudgeCallResult(
            judge_output=judge_output,
            is_fallback=False,
            fallback_reason=None,
            original_failure_context=None,
            latency_ms=latency_ms,
            usage=self._usage_dict(usage),
            model_used=self._model,
        )

    # --- Helpers ---

    def _fallback(
        self,
        reason: FallbackReason,
        context: dict[str, Any],
        *,
        latency_ms: int,
        usage: dict[str, int] | None,
    ) -> JudgeCallResult:
        synthesized = synthesize_fallback(
            reason=reason,
            next_pending_mandatory_id=self._next_pending_resolver(),
        )
        return JudgeCallResult(
            judge_output=synthesized,
            is_fallback=True,
            fallback_reason=reason,
            original_failure_context=context,
            latency_ms=latency_ms,
            usage=usage,
            model_used=self._model,
        )

    @staticmethod
    def _usage_dict(usage: Any) -> dict[str, int] | None:
        if usage is None:
            return None
        return {
            "prompt_tokens": getattr(usage, "input_tokens", 0),
            "completion_tokens": getattr(usage, "output_tokens", 0),
        }
