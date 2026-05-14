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


@pytest.mark.asyncio
async def test_run_enrichment_emits_span_with_prompt_attributes(in_memory_exporter, db, monkeypatch):
    """Phase 3 contract: the manual-span migration must keep emitting an
    OTel span per LLM call with gen_ai.prompt.name + tenant.id attributes.

    This catches:
    - The `with _tracer.start_as_current_span(...)` block being removed
      or wrapped around the wrong code.
    - `set_llm_span_attributes(...)` being called outside the with-block
      (which would make trace.get_current_span() return a no-op span and
      silently discard the attributes — the regression that motivated this
      test in code review).

    The OpenAI client is mocked at the boundary; we don't hit the network.
    """
    from unittest.mock import AsyncMock, MagicMock
    from uuid import UUID

    from sqlalchemy import func, select

    from app.ai.schemas import EnrichmentOutput
    from app.modules.jd.models import JobPosting
    from app.modules.jd.actors import _run_enrichment
    from tests.conftest import (
        create_test_client,
        create_test_org_unit,
        create_test_user,
    )

    _VALID_PROFILE = {
        "about": "We build real-time risk scoring for mid-market lenders at scale.",
        "industry": "Fintech / Financial Services",
            "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
    }

    # Seed minimal data
    tenant = await create_test_client(db)
    await db.flush()
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Sr Engineer",
        description_raw="A" * 200,
        status="signals_extracting",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    # Mock the OpenAI client at the boundary: return a structured response.
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=EnrichmentOutput(enriched_jd="E" * 200)
    )
    monkeypatch.setattr(
        "app.modules.jd.actors.get_openai_client",
        lambda: mock_client,
    )

    # Route _tracer in jd/actors to our in-memory provider so spans are
    # captured without touching the global TracerProvider (which has no
    # exporters in the test environment).
    exporter, provider = in_memory_exporter
    monkeypatch.setattr(
        "app.modules.jd.actors._tracer",
        provider.get_tracer("nexus.ai.openai"),
    )

    correlation_id = "test-corr-001"
    await _run_enrichment(
        db,
        job_posting_id=str(job.id),
        tenant_id=str(tenant.id),
        correlation_id=correlation_id,
        retries_so_far=0,
    )

    # Verify a span was emitted with the right name + attributes.
    spans = exporter.get_finished_spans()
    llm_spans = [s for s in spans if s.name == "openai.chat.completions.create"]
    assert len(llm_spans) == 1, (
        f"expected exactly 1 LLM span, got {len(llm_spans)} "
        f"(all spans: {[s.name for s in spans]})"
    )
    attrs = dict(llm_spans[0].attributes or {})
    assert attrs.get("gen_ai.prompt.name") == "jd_enrichment", attrs
    assert attrs.get("gen_ai.prompt.version") == "v1", attrs
    assert attrs.get("tenant.id") == str(tenant.id), attrs
    assert attrs.get("app.correlation_id") == correlation_id, attrs
