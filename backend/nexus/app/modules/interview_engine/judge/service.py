"""JudgeService — calls OpenAI Responses API with structured output + retry/fallback."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from openai.lib._parsing._responses import type_to_response_format_param
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


def _patch_oneof_to_anyof(node: Any) -> None:
    """Recursively rewrite `oneOf` → `anyOf` in a JSON Schema dict.

    OpenAI's strict json_schema mode rejects `oneOf` outright (400
    `'oneOf' is not permitted`). It DOES accept `anyOf`, which is the
    correct semantic for a Pydantic discriminated union: the candidate
    schemas are mutually exclusive by construction (the `kind` discriminator
    + `const` on each variant), so `anyOf` and `oneOf` are equivalent here.

    Pydantic v2 emits `oneOf` for tagged unions and the OpenAI SDK's strict
    rewriter (`additionalProperties: false`, full `required`) does NOT touch
    it — we have to do this surgically.
    """
    if isinstance(node, dict):
        if "oneOf" in node and "anyOf" not in node:
            node["anyOf"] = node.pop("oneOf")
        for v in node.values():
            _patch_oneof_to_anyof(v)
    elif isinstance(node, list):
        for v in node:
            _patch_oneof_to_anyof(v)


@lru_cache(maxsize=1)
def _judge_output_text_format() -> dict[str, Any]:
    """Build the strict-mode-compatible Responses API `text.format` payload
    for `JudgeOutput`. Cached because it never changes at runtime.

    Pipeline:
      1. SDK helper rewrites Pydantic schema for OpenAI strict mode
         (adds `additionalProperties: false`, fills `required` everywhere).
      2. We patch `oneOf` → `anyOf` for the discriminated union — strict
         mode rejects `oneOf` but accepts `anyOf`.
    """
    raw = type_to_response_format_param(JudgeOutput)
    schema_dict = raw["json_schema"]["schema"]
    _patch_oneof_to_anyof(schema_dict)
    return {
        "type": "json_schema",
        "name": raw["json_schema"]["name"],
        "schema": schema_dict,
        "strict": raw["json_schema"].get("strict", True),
    }


class JudgeService:
    """Calls the Judge LLM with one retry, 10s total budget, and fallback synthesis.

    Uses the OpenAI Responses API with strict `json_schema` mode. The schema is
    derived from the `JudgeOutput` Pydantic model via the SDK's helper, then
    post-processed to swap `oneOf` (which strict mode rejects) for `anyOf`
    (which strict mode accepts) on the discriminated union. The model output
    is validated back into a typed `JudgeOutput` with Pydantic.

    Why not `responses.parse(text_format=JudgeOutput)` directly?
      The SDK's strict-mode rewriter handles `additionalProperties`/`required`
      but NOT the `oneOf` produced by Pydantic v2 for discriminated unions.
      The API rejects with 400 `'oneOf' is not permitted`. Reproduced
      end-to-end against the live API; see `test_judge_real_openai_returns_parsed_output`.

    Why not `responses.create(text={"format": {"type": "json_object"}})`?
      That mode requires the literal word "json" to appear in the `input`
      parameter or the API rejects with 400. The serialized JudgeInputPayload
      has no such word, so every call failed. This was the original Bug 1.

    Failure routing:
      * Network / timeout / 5xx → retry once, then `FallbackReason.timeout`.
      * Output isn't valid JSON → `FallbackReason.parse_error`.
      * Output doesn't validate against JudgeOutput → `FallbackReason.validation_error`.
    """

    def __init__(
        self,
        *,
        openai_client: Any,
        model: str,
        system_prompt: str,
        system_prompt_hash: str,
        next_pending_question_resolver: Callable[[], tuple[str, bool] | None],
        prompt_version: str = "v1",
        total_budget_ms: int = 10000,
        retry_wait_ms: int = 250,
    ) -> None:
        self._client = openai_client
        self._model = model
        self._system_prompt = system_prompt
        self._system_prompt_hash = system_prompt_hash
        self._next_pending_resolver = next_pending_question_resolver
        self._prompt_version = prompt_version
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
            prompt_version=self._prompt_version,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            turn_id=turn_id,
            model=self._model,
        )

        budget_seconds = self._total_budget_ms / 1000.0
        retry_wait_seconds = self._retry_wait_ms / 1000.0
        # Split the wall-clock budget across the two attempts (initial + 1 retry),
        # subtracting the flat retry wait. Keeps overall latency bounded while
        # guaranteeing the retry actually fires on a transient failure.
        per_attempt_seconds = max(
            0.001,
            (budget_seconds - retry_wait_seconds) / 2.0,
        )

        text_format = _judge_output_text_format()

        started = time.monotonic()
        attempt_text: str | None = None
        last_exc: Exception | None = None
        usage: Any = None

        async def _one_attempt() -> tuple[str, Any]:
            response = await self._client.responses.create(
                model=self._model,
                instructions=self._system_prompt,
                input=input_payload.model_dump_json(),
                text={"format": text_format},
            )
            return response.output_text, response.usage

        # Attempt #1.
        #
        # IMPORTANT: do NOT catch ``asyncio.CancelledError`` here. The
        # 2026-05-17 conversational-continuation design relies on the
        # orchestrator's ability to cancel an in-flight Judge call via
        # ``turn_task.cancel()`` when the candidate resumes speaking
        # before the commit point. Swallowing CancelledError lets the
        # turn body run to completion (Speaker → TTS → committed) and
        # then "abort" after the fact — restoring State Engine state
        # AFTER the agent has audibly responded. Session 7970e91c
        # demonstrated this exact failure mode: the candidate heard four
        # back-to-back agent responses to one user utterance because
        # cancellation was silently dropped here.
        #
        # In Python 3.13 ``asyncio.CancelledError`` inherits from
        # ``BaseException`` (not ``Exception``), so the bare
        # ``except Exception`` clauses below will not catch it either —
        # which is exactly what we want. CancelledError propagates to
        # the orchestrator and the turn body unwinds before any audible
        # commit can happen.
        try:
            attempt_text, usage = await asyncio.wait_for(
                _one_attempt(), timeout=per_attempt_seconds,
            )
        except asyncio.TimeoutError as exc:
            last_exc = exc
            attempt_text = None
        except Exception as exc:  # network / 5xx / rate-limit / 400
            last_exc = exc
            attempt_text = None

        # Retry once on any non-cancellation failure after a flat wait.
        # ``asyncio.sleep`` is itself a cancellation point — if the
        # orchestrator cancels during the retry-wait, CancelledError
        # propagates here too.
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
            # Strip ``ctx`` and ``url`` from each error entry. Pydantic v2
            # embeds the raw underlying exception object (e.g., the
            # ValueError raised by a model_validator) in ``ctx['error']``
            # — that's a non-JSON-serializable Python object. When this
            # dict later flows into the audit envelope and the envelope
            # is serialized for the sink, Pydantic raises
            # ``PydanticSerializationError: Unable to serialize unknown
            # type: <class 'ValueError'>`` and the entire envelope write
            # fails (root cause: session 83c4d309-247d-44fd-9312-8ab1d48105b5).
            # The ``msg`` field already carries the validator's message,
            # so dropping ``ctx`` loses no debugging detail that isn't
            # already covered.
            safe_errors = exc.errors(include_url=False, include_context=False)
            return self._fallback(
                FallbackReason.validation_error,
                {"raw_data": data, "errors": safe_errors},
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
        next_pending = self._next_pending_resolver()
        synthesized = synthesize_fallback(
            reason=reason,
            next_pending_question=next_pending,
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
        # Responses API: usage.input_tokens_details.cached_tokens.
        # SDK exposes input_tokens_details as a nested object; tolerate
        # absence (None / missing attr) so older/mocked responses work.
        # Use isinstance(int) to guard against MagicMock auto-generated
        # children in unit tests — int(MagicMock()) returns 1, which would
        # silently report a phantom cache hit.
        details = getattr(usage, "input_tokens_details", None)
        cached_raw = getattr(details, "cached_tokens", 0) if details is not None else 0
        cached = cached_raw if isinstance(cached_raw, int) else 0
        return {
            "prompt_tokens": getattr(usage, "input_tokens", 0),
            "completion_tokens": getattr(usage, "output_tokens", 0),
            "cached_tokens": cached,
        }
