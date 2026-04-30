"""Tests for app.ai.tracing — OTel span attribute helper.

Uses InMemorySpanExporter to capture spans in-process and assert on attributes
without needing a real OTel collector. This is the pattern OTel recommends for
unit-testing custom instrumentation.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


@pytest.fixture
def in_memory_exporter():
    """A TracerProvider with an in-memory span exporter, scoped to the test.

    Creates a fresh tracer provider for each test and directly uses it
    to create spans, avoiding the global tracer provider entirely.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider


def test_set_llm_span_attributes_writes_gen_ai_prompt_metadata(in_memory_exporter):
    """`set_llm_span_attributes` writes prompt name + version + tenant + correlation
    onto the current active span using the OpenTelemetry GenAI conventions."""
    from app.ai.tracing import set_llm_span_attributes

    exporter, provider = in_memory_exporter
    tracer = provider.get_tracer("test")

    # Create a span and explicitly use trace.use_span to set the context
    span = tracer.start_span("test-span")
    with trace.use_span(span):
        set_llm_span_attributes(
            prompt_name="jd_enrichment",
            prompt_version="v1",
            tenant_id="00000000-0000-0000-0000-000000000001",
            correlation_id="corr-abc",
        )
    span.end()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("gen_ai.prompt.name") == "jd_enrichment"
    assert attrs.get("gen_ai.prompt.version") == "v1"
    assert attrs.get("tenant.id") == "00000000-0000-0000-0000-000000000001"
    assert attrs.get("app.correlation_id") == "corr-abc"


def test_set_llm_span_attributes_accepts_extra_kwargs(in_memory_exporter):
    """Extra kwargs flow through as additional attributes (prefixed `app.`)."""
    from app.ai.tracing import set_llm_span_attributes

    exporter, provider = in_memory_exporter
    tracer = provider.get_tracer("test")

    span = tracer.start_span("test-span")
    with trace.use_span(span):
        set_llm_span_attributes(
            prompt_name="jd_enrichment",
            prompt_version="v1",
            retries_so_far=2,
            source_jd="enriched",
        )
    span.end()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("app.retries_so_far") == 2
    assert attrs.get("app.source_jd") == "enriched"


def test_set_llm_span_attributes_no_active_span_is_safe(in_memory_exporter):
    """Calling outside a span context is a no-op, not a crash."""
    from app.ai.tracing import set_llm_span_attributes

    # No active span — should not raise.
    set_llm_span_attributes(prompt_name="foo", prompt_version="v1")

    # No span emitted; nothing to assert beyond "no exception".
