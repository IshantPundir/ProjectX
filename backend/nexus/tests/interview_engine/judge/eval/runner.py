"""Eval runner: calls the real JudgeService for one fixture, applies assertions.

Driven by JUDGE_PROMPT_VERSION env var (default v2). Costs are estimated
from token counts using OpenAI's published pricing (input $1.25/M, output
$5/M for gpt-5.4-mini class — adjust as pricing changes).

Spec: docs/superpowers/specs/2026-05-17-interview-engine-v2-design.md §4.4.
"""
from __future__ import annotations

import hashlib
import os
from typing import Literal

from openai import AsyncOpenAI

from app.ai.prompts import PromptLoader
from app.config import settings
from app.modules.interview_engine.judge.service import JudgeService

from .corpus import EvalFixture, EvalResult, assert_output


INPUT_COST_PER_M_TOKENS_USD = 1.25
OUTPUT_COST_PER_M_TOKENS_USD = 5.0


async def run_fixture(
    fixture: EvalFixture,
    *,
    prompt_version: Literal["v1", "v2"] = "v2",
) -> EvalResult:
    """Run one fixture against the real OpenAI API."""
    loader = PromptLoader(version=prompt_version)
    try:
        system_prompt = loader.get("engine/judge.system")
    except FileNotFoundError:
        return EvalResult(
            fixture_id=fixture.id,
            output=None,
            error=f"prompt file not found for version={prompt_version}",
            passed=False,
            failures=[f"missing prompt file for version={prompt_version}"],
            soft_warnings=[],
            latency_ms=0,
            cost_estimate_usd=0.0,
        )
    prompt_hash = "sha256:" + hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    judge = JudgeService(
        openai_client=openai_client,
        model=os.getenv("ENGINE_JUDGE_MODEL", settings.engine_judge_model),
        system_prompt=system_prompt,
        system_prompt_hash=prompt_hash,
        next_pending_mandatory_resolver=lambda: fixture.judge_input.next_pending_mandatory_question_id,
        total_budget_ms=int(os.getenv("ENGINE_JUDGE_TOTAL_BUDGET_MS", "10000")),
        retry_wait_ms=int(os.getenv("ENGINE_JUDGE_RETRY_WAIT_MS", "250")),
    )

    call_result = await judge.call(
        turn_id=f"eval-{fixture.id}",
        input_payload=fixture.judge_input,
        correlation_id=f"eval-{fixture.id}",
        tenant_id="eval-tenant",
    )

    if call_result.is_fallback:
        return EvalResult(
            fixture_id=fixture.id,
            output=call_result.judge_output,
            error=f"judge service fellback: {call_result.fallback_reason}",
            passed=False,
            failures=[f"fallback path fired: {call_result.fallback_reason}"],
            soft_warnings=[],
            latency_ms=call_result.latency_ms,
            cost_estimate_usd=_estimate_cost(call_result.usage),
        )

    failures, warnings = assert_output(call_result.judge_output, fixture.expected)
    return EvalResult(
        fixture_id=fixture.id,
        output=call_result.judge_output,
        error=None,
        passed=not failures,
        failures=failures,
        soft_warnings=warnings,
        latency_ms=call_result.latency_ms,
        cost_estimate_usd=_estimate_cost(call_result.usage),
    )


def _estimate_cost(usage: dict[str, int] | None) -> float:
    if not usage:
        return 0.0
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    return (
        input_tokens * INPUT_COST_PER_M_TOKENS_USD / 1_000_000
        + output_tokens * OUTPUT_COST_PER_M_TOKENS_USD / 1_000_000
    )


def format_failure(result: EvalResult) -> str:
    """Compose a readable assertion-failure message for pytest."""
    lines = [
        f"[FIXTURE {result.fixture_id}] FAILED",
        f"  latency_ms: {result.latency_ms}",
        f"  cost_usd: ${result.cost_estimate_usd:.6f}",
    ]
    if result.error:
        lines.append(f"  error: {result.error}")
    for f in result.failures:
        lines.append(f"  FAIL: {f}")
    if result.output:
        lines.append(f"  actual next_action: {result.output.next_action.value}")
        lines.append(f"  actual observations count: {len(result.output.observations)}")
        lines.append(
            f"  actual reasoning ({len(result.output.reasoning)} chars): "
            f"{result.output.reasoning[:200]}..."
        )
    for w in result.soft_warnings:
        lines.append(f"  WARN: {w}")
    return "\n".join(lines)
