"""OpenTelemetry helper for LLM span attribute tagging.

This module adds prompt-version, tenant, and correlation metadata to the
currently-active OTel span. The OpenAI auto-instrumentor
(opentelemetry-instrumentation-openai-v2) creates the span automatically;
this helper enriches it.

Naming follows the OpenTelemetry GenAI semantic conventions:
- gen_ai.* are reserved for LLM-related attributes
- app.* and tenant.* are local custom attributes for cross-cutting context

Calling without an active span is a no-op (defensive — instrumentation
must never crash the business path).
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace


def set_llm_span_attributes(
    *,
    prompt_name: str,
    prompt_version: str,
    tenant_id: str | None = None,
    correlation_id: str | None = None,
    **extra: Any,
) -> None:
    """Tag the current OTel span with prompt + tenant + correlation metadata.

    Args:
        prompt_name: Logical name of the prompt template (e.g. "jd_enrichment").
        prompt_version: Prompt template version (e.g. "v1").
        tenant_id: Optional tenant UUID as a string.
        correlation_id: Optional correlation ID for cross-service tracing.
        **extra: Additional attributes; prefixed with `app.` and coerced to
                 OTel-compatible primitive types (str, int, float, bool).

    Safe to call when no span is active — becomes a no-op.
    """
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return

    span.set_attribute("gen_ai.prompt.name", prompt_name)
    span.set_attribute("gen_ai.prompt.version", prompt_version)
    if tenant_id is not None:
        span.set_attribute("tenant.id", str(tenant_id))
    if correlation_id is not None:
        span.set_attribute("app.correlation_id", str(correlation_id))

    for key, value in extra.items():
        # OTel attributes accept str, bool, int, float, or sequences thereof.
        # Coerce anything else to its string representation.
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(f"app.{key}", value)
        else:
            span.set_attribute(f"app.{key}", str(value))
