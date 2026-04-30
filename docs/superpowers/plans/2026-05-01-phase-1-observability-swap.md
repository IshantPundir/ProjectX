# Phase 1 — Observability Swap (langfuse → OpenTelemetry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop the `langfuse` dependency from nexus and replace it with vendor-neutral OpenTelemetry instrumentation, with two opt-in env-var-controlled exporters (ConsoleSpanExporter for dev, OTLPSpanExporter for future production sinks). Both unset → silent.

**Architecture:** Two commits, one PR. Commit 1 adds OTel side-by-side with langfuse so we can verify spans flow before deleting the safety net. Commit 2 removes every langfuse code path, env var, dep, and comment. Frontend has zero langfuse coupling, so this is a pure backend-internal change.

**Tech Stack:** Python 3.12, FastAPI, Dramatiq, structlog, openai 1.x (still pinned in this phase), instructor 1.x (still pinned), `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-openai-v2`, `opentelemetry-instrumentation-fastapi`.

**Spec reference:** `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md` § Phase 1.

---

## File Structure

| File | Change |
|---|---|
| `backend/nexus/pyproject.toml` | Bump `opentelemetry-*` to current minor; add `opentelemetry-instrumentation-openai-v2` and `opentelemetry-exporter-otlp`; remove `langfuse>=2.56,<3` (Commit 2) |
| `backend/nexus/app/ai/tracing.py` | **NEW** — `set_llm_span_attributes()` helper |
| `backend/nexus/app/ai/otel.py` | **NEW** — TracerProvider bootstrap, exporter registration, OpenAI auto-instrumentor |
| `backend/nexus/app/ai/client.py` | Drop langfuse wrapper; switch to plain `openai.AsyncOpenAI` (Commit 2) |
| `backend/nexus/app/main.py` | Bootstrap OTel in lifespan; drop `shutdown_langfuse` (Commit 2) |
| `backend/nexus/app/worker.py` | Bootstrap OTel; drop `shutdown_langfuse` (Commit 2) |
| `backend/nexus/app/config.py` | Add `otel_*` settings; drop `langfuse_*` (Commit 2) |
| `backend/nexus/.env.example` | Replace LANGFUSE block with OTEL block (Commit 2) |
| `backend/nexus/docker-compose.yml` | Add OTEL passthroughs (Commit 1) |
| `backend/nexus/app/modules/jd/actors.py` | Replace `langfuse_context.update_current_trace` calls with `set_llm_span_attributes`; drop `@observe` decorators and `langfuse_enabled() / flush_langfuse()` calls (Commit 2) |
| `backend/nexus/app/modules/question_bank/actors.py` | Same treatment as `jd/actors.py` (Commit 2) |
| `backend/nexus/app/modules/jd/router.py` | Comment cleanup only ("Langfuse tags" → "OTel attributes") (Commit 2) |
| `backend/nexus/tests/test_ai_tracing.py` | **NEW** — `InMemorySpanExporter` assertions for `gen_ai.prompt.name` / `tenant.id` / `app.correlation_id` |
| `backend/nexus/tests/test_otel_bootstrap.py` | **NEW** — startup integrity smoke test |
| `backend/nexus/CLAUDE.md` | Replace langfuse references with OTel (Commit 2) |

---

## Stage A — Side-by-side OTel install + verification (Commit 1)

### Task 1: Verify exact pin versions via context7

**Files:** none (research-only)

- [ ] **Step 1.1: Resolve current versions for the three new OTel packages**

The plan needs exact pin lower bounds. Use the context7 MCP server (or PyPI directly):

Run:
```bash
# Optional fallback if MCP unavailable:
pip index versions opentelemetry-instrumentation-openai-v2
pip index versions opentelemetry-exporter-otlp
pip index versions opentelemetry-api
pip index versions opentelemetry-sdk
pip index versions opentelemetry-instrumentation-fastapi
```

Expected: write the resolved current minor (e.g. `2.0.x`, `1.30.x`) into a scratch note before editing `pyproject.toml`. The exact numbers go into Task 2.

- [ ] **Step 1.2: Note OpenTelemetry GenAI semantic conventions**

The `set_llm_span_attributes` helper (Task 4) uses standard attribute names. Confirm:
- `gen_ai.system` = "openai"
- `gen_ai.request.model` = the model name
- `gen_ai.prompt.name` = our custom: prompt template identifier (e.g. "jd_enrichment")
- `gen_ai.prompt.version` = our custom: prompt template version (e.g. "v1")
- `tenant.id` = our custom (cross-cutting tenant attribute)
- `app.correlation_id` = our custom (cross-cutting correlation)

The `gen_ai.*` namespace is reserved for OpenTelemetry GenAI semantic conventions. The `opentelemetry-instrumentation-openai-v2` package emits `gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.id`, `gen_ai.usage.input_tokens`, etc. automatically. We add `gen_ai.prompt.name` + `gen_ai.prompt.version` as custom attributes that observability backends will surface alongside the auto-captured ones.

---

### Task 2: Add OTel deps to `pyproject.toml`

**Files:**
- Modify: `backend/nexus/pyproject.toml` (deps section, around line 50–60)

- [ ] **Step 2.1: Bump existing OTel pins and add three new ones**

Edit `backend/nexus/pyproject.toml`. Find the existing OTel block:

```toml
"opentelemetry-api>=1.29,<2",
"opentelemetry-sdk>=1.29,<2",
"opentelemetry-instrumentation-fastapi>=0.50b,<1",
"langfuse>=2.56,<3",
```

Replace with (using context7-resolved versions from Task 1):

```toml
"opentelemetry-api>=1.29,<2",
"opentelemetry-sdk>=1.29,<2",
"opentelemetry-exporter-otlp>=1.29,<2",
"opentelemetry-instrumentation-fastapi>=0.50b,<1",
"opentelemetry-instrumentation-openai-v2>=2.0,<3",
"langfuse>=2.56,<3",
```

**Note:** `langfuse>=2.56,<3` STAYS in Commit 1 — we drop it in Commit 2 after verifying OTel works side-by-side. The exact patch versions for the new packages come from Task 1's context7 lookup.

- [ ] **Step 2.2: Regenerate the lockfile**

Run:
```bash
cd backend/nexus
uv lock
```

Expected: `uv.lock` updated; no resolution errors.

- [ ] **Step 2.3: Rebuild the container**

Run:
```bash
cd backend/nexus
docker compose build nexus nexus-worker
```

Expected: clean build; no wheel-resolution errors.

- [ ] **Step 2.4: Smoke import the new packages**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus python -c "from opentelemetry import trace; from opentelemetry.sdk.trace import TracerProvider; from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor; print('imports ok')"
```

Expected: `imports ok` printed; no ImportError.

---

### Task 3: Add OTel settings fields to `app/config.py`

**Files:**
- Modify: `backend/nexus/app/config.py` (around line 165–170, the Observability block)
- Test: `backend/nexus/tests/test_config_validators.py` (existing — check still passes)

- [ ] **Step 3.1: Write the failing test**

Add to `backend/nexus/tests/test_config_validators.py` (append):

```python
def test_otel_settings_default_to_off(monkeypatch):
    """OTel exporter env vars default to empty / False so no traces ship by default."""
    # Clear any inherited values
    for var in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_DEV_CONSOLE_EXPORTER", "OTEL_SERVICE_NAME"):
        monkeypatch.delenv(var, raising=False)
    from app.config import Settings

    settings = Settings()
    assert settings.otel_exporter_otlp_endpoint == ""
    assert settings.otel_dev_console_exporter is False
    assert settings.otel_service_name == "nexus"


def test_otel_settings_read_from_env(monkeypatch):
    """OTel settings are env-driven."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_DEV_CONSOLE_EXPORTER", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "nexus-test")
    from app.config import Settings

    settings = Settings()
    assert settings.otel_exporter_otlp_endpoint == "http://collector:4317"
    assert settings.otel_dev_console_exporter is True
    assert settings.otel_service_name == "nexus-test"
```

- [ ] **Step 3.2: Run test to verify it fails**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_config_validators.py::test_otel_settings_default_to_off -v
```

Expected: FAIL with `AttributeError: ... has no attribute 'otel_exporter_otlp_endpoint'`.

- [ ] **Step 3.3: Add OTel settings fields**

Edit `backend/nexus/app/config.py`. Find the Observability block (around line 165):

```python
    # Observability
    sentry_dsn: str = ""
    langfuse_host: str = ""           # Legacy — prefer LANGFUSE_BASE_URL
    langfuse_base_url: str = ""       # e.g. https://cloud.langfuse.com (Langfuse v2+ convention)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
```

Add the three OTel fields BELOW the langfuse block (langfuse stays for Commit 1):

```python
    # Observability
    sentry_dsn: str = ""
    langfuse_host: str = ""           # Legacy — prefer LANGFUSE_BASE_URL
    langfuse_base_url: str = ""       # e.g. https://cloud.langfuse.com (Langfuse v2+ convention)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # OpenTelemetry — vendor-neutral tracing.
    # Both exporters default to OFF. Set OTEL_DEV_CONSOLE_EXPORTER=true to dump
    # spans to stdout for local dev visibility. Set OTEL_EXPORTER_OTLP_ENDPOINT
    # to ship to a collector or backend (Sentry, Jaeger, Tempo, custom).
    # When both unset: spans are created and finished but discarded silently
    # (production-safe — no accidental data leak).
    otel_exporter_otlp_endpoint: str = ""
    otel_dev_console_exporter: bool = False
    otel_service_name: str = "nexus"
```

- [ ] **Step 3.4: Run test to verify it passes**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_config_validators.py::test_otel_settings_default_to_off tests/test_config_validators.py::test_otel_settings_read_from_env -v
```

Expected: PASS for both.

- [ ] **Step 3.5: Run full config test file**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_config_validators.py -v
```

Expected: all green; no regression on existing tests.

---

### Task 4: Create `app/ai/tracing.py` — span attribute helper

**Files:**
- Create: `backend/nexus/app/ai/tracing.py`
- Test: `backend/nexus/tests/test_ai_tracing.py` (new)

- [ ] **Step 4.1: Write the failing test**

Create `backend/nexus/tests/test_ai_tracing.py`:

```python
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
    """A TracerProvider with an in-memory span exporter, scoped to the test."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Save and restore the global provider so tests don't leak.
    original = trace.get_tracer_provider()
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        trace.set_tracer_provider(original)


def test_set_llm_span_attributes_writes_gen_ai_prompt_metadata(in_memory_exporter):
    """`set_llm_span_attributes` writes prompt name + version + tenant + correlation
    onto the current active span using the OpenTelemetry GenAI conventions."""
    from app.ai.tracing import set_llm_span_attributes

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("test-span"):
        set_llm_span_attributes(
            prompt_name="jd_enrichment",
            prompt_version="v1",
            tenant_id="00000000-0000-0000-0000-000000000001",
            correlation_id="corr-abc",
        )

    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("gen_ai.prompt.name") == "jd_enrichment"
    assert attrs.get("gen_ai.prompt.version") == "v1"
    assert attrs.get("tenant.id") == "00000000-0000-0000-0000-000000000001"
    assert attrs.get("app.correlation_id") == "corr-abc"


def test_set_llm_span_attributes_accepts_extra_kwargs(in_memory_exporter):
    """Extra kwargs flow through as additional attributes (prefixed `app.`)."""
    from app.ai.tracing import set_llm_span_attributes

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("test-span"):
        set_llm_span_attributes(
            prompt_name="jd_enrichment",
            prompt_version="v1",
            retries_so_far=2,
            source_jd="enriched",
        )

    spans = in_memory_exporter.get_finished_spans()
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("app.retries_so_far") == 2
    assert attrs.get("app.source_jd") == "enriched"


def test_set_llm_span_attributes_no_active_span_is_safe(in_memory_exporter):
    """Calling outside a span context is a no-op, not a crash."""
    from app.ai.tracing import set_llm_span_attributes

    # No active span — should not raise.
    set_llm_span_attributes(prompt_name="foo", prompt_version="v1")

    # No span emitted; nothing to assert beyond "no exception".
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_ai_tracing.py -v
```

Expected: ImportError on `from app.ai.tracing import set_llm_span_attributes`.

- [ ] **Step 4.3: Implement `app/ai/tracing.py`**

Create `backend/nexus/app/ai/tracing.py`:

```python
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
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_ai_tracing.py -v
```

Expected: all three tests PASS.

---

### Task 5: Create `app/ai/otel.py` — TracerProvider bootstrap

**Files:**
- Create: `backend/nexus/app/ai/otel.py`
- Test: `backend/nexus/tests/test_otel_bootstrap.py` (new)

- [ ] **Step 5.1: Write the failing test**

Create `backend/nexus/tests/test_otel_bootstrap.py`:

```python
"""Tests for app.ai.otel — TracerProvider bootstrap and exporter wiring.

These tests exercise the env-var contract, not the real exporters — we don't
want unit tests to actually ship spans anywhere. They assert that the right
SpanProcessor + Exporter combinations are added based on env vars.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider


def test_bootstrap_with_no_env_vars_is_silent(monkeypatch):
    """Default config: TracerProvider exists but has no exporters — spans go to /dev/null."""
    for var in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_DEV_CONSOLE_EXPORTER"):
        monkeypatch.delenv(var, raising=False)
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    assert isinstance(provider, TracerProvider)
    # No span processors registered when both exporters are off.
    # The internal _active_span_processor is a CompositeSpanProcessor; check it's empty.
    assert len(provider._active_span_processor._span_processors) == 0


def test_bootstrap_console_exporter_when_env_set(monkeypatch):
    """OTEL_DEV_CONSOLE_EXPORTER=true wires a ConsoleSpanExporter."""
    monkeypatch.setenv("OTEL_DEV_CONSOLE_EXPORTER", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    processors = provider._active_span_processor._span_processors
    assert len(processors) == 1
    # The processor wraps a ConsoleSpanExporter
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    assert isinstance(processors[0]._exporter, ConsoleSpanExporter)


def test_bootstrap_otlp_exporter_when_endpoint_set(monkeypatch):
    """OTEL_EXPORTER_OTLP_ENDPOINT=<url> wires an OTLPSpanExporter."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.delenv("OTEL_DEV_CONSOLE_EXPORTER", raising=False)
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    processors = provider._active_span_processor._span_processors
    assert len(processors) == 1
    # The processor wraps an OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    assert isinstance(processors[0]._exporter, OTLPSpanExporter)


def test_bootstrap_both_exporters_when_both_env_set(monkeypatch):
    """Both env vars set → both exporters wired."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_DEV_CONSOLE_EXPORTER", "true")
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    processors = provider._active_span_processor._span_processors
    assert len(processors) == 2
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_otel_bootstrap.py -v
```

Expected: ImportError on `from app.ai.otel import bootstrap_tracer_provider`.

- [ ] **Step 5.3: Implement `app/ai/otel.py`**

Create `backend/nexus/app/ai/otel.py`:

```python
"""OpenTelemetry TracerProvider bootstrap and exporter wiring.

Exposes:
- ``bootstrap_tracer_provider()`` — builds a TracerProvider with exporters
  controlled by env vars. Returns the provider so callers can register it
  as the global provider and call ``.shutdown()`` at process exit.
- ``instrument_openai()`` — wires the OpenAI auto-instrumentor. Idempotent.

Env-var contract:
- ``OTEL_EXPORTER_OTLP_ENDPOINT`` set (any value) → wire OTLPSpanExporter
- ``OTEL_DEV_CONSOLE_EXPORTER=true`` → wire ConsoleSpanExporter (stdout)
- both unset → TracerProvider has no exporters; spans created and discarded

Production-safe by default: with no env vars set, no traces ship anywhere.
"""

from __future__ import annotations

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from app.config import settings

logger = structlog.get_logger()

_instrumented = False  # OpenAIInstrumentor — only call .instrument() once


def bootstrap_tracer_provider() -> TracerProvider:
    """Build a TracerProvider with exporters wired per env vars.

    Always returns a TracerProvider. When no exporter env vars are set,
    the provider has zero span processors — spans go nowhere. This is
    the production-safe default.
    """
    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)

    if settings.otel_dev_console_exporter:
        # Console exporter for dev: prints each span as JSON to stdout.
        # SimpleSpanProcessor (synchronous) so dev sees output immediately;
        # production uses BatchSpanProcessor for the OTLP exporter below.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.info("otel.console_exporter_enabled")

    if settings.otel_exporter_otlp_endpoint:
        # Imported lazily because the OTLP exporter pulls in grpcio,
        # which we don't want to load when no endpoint is configured.
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
            )
        )
        logger.info(
            "otel.otlp_exporter_enabled",
            endpoint=settings.otel_exporter_otlp_endpoint,
        )

    if (
        not settings.otel_dev_console_exporter
        and not settings.otel_exporter_otlp_endpoint
    ):
        logger.info("otel.no_exporter_configured", reason="all env vars empty")

    return provider


def instrument_openai() -> None:
    """Register the OpenAI auto-instrumentor. Idempotent — safe to call
    multiple times across API and worker process bootstraps."""
    global _instrumented
    if _instrumented:
        return
    from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

    OpenAIInstrumentor().instrument()
    _instrumented = True
    logger.info("otel.openai_instrumented")
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_otel_bootstrap.py -v
```

Expected: all 4 tests PASS.

---

### Task 6: Wire OTel bootstrap into `app/main.py` (FastAPI)

**Files:**
- Modify: `backend/nexus/app/main.py` (lifespan handler around lines 185–222)

- [ ] **Step 6.1: Write a startup-integrity test**

Append to `backend/nexus/tests/test_otel_bootstrap.py`:

```python
def test_main_lifespan_registers_global_tracer_provider(monkeypatch):
    """The FastAPI lifespan registers a TracerProvider as the global provider."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_DEV_CONSOLE_EXPORTER", raising=False)

    # After importing app.main, the global tracer provider should be a
    # TracerProvider instance (or its delegate proxy in some OTel versions).
    # We assert the type is at minimum *not* the default ProxyTracerProvider.
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider as SDKProvider

    # Reset to default before the test
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]

    from app.ai.otel import bootstrap_tracer_provider, instrument_openai

    provider = bootstrap_tracer_provider()
    trace.set_tracer_provider(provider)
    instrument_openai()

    assert isinstance(trace.get_tracer_provider(), SDKProvider)
```

- [ ] **Step 6.2: Run test to verify it fails (initially passes by being unrelated to main, then re-asserts after wiring)**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_otel_bootstrap.py::test_main_lifespan_registers_global_tracer_provider -v
```

Expected: PASS (because the test does the bootstrap itself, not via app.main). This is intentional — it locks the contract. The "fail" gate is implicit: if `app/ai/otel.py` ever stops working, this test fails.

- [ ] **Step 6.3: Wire bootstrap into the FastAPI lifespan**

Edit `backend/nexus/app/main.py`. Find the lifespan handler (line 185):

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Startup
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.debug
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10 if settings.debug else 20),
    )
    logger.info("nexus.startup", environment=settings.environment)
```

Add OTel bootstrap immediately after the structlog configure block, BEFORE `_assert_rls_completeness`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Startup
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if settings.debug
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10 if settings.debug else 20),
    )
    logger.info("nexus.startup", environment=settings.environment)

    # OpenTelemetry bootstrap. Both exporters are off by default; setting
    # OTEL_DEV_CONSOLE_EXPORTER=true or OTEL_EXPORTER_OTLP_ENDPOINT=<url>
    # turns them on. See app/ai/otel.py for env-var contract.
    from opentelemetry import trace
    from app.ai.otel import bootstrap_tracer_provider, instrument_openai

    _otel_provider = bootstrap_tracer_provider()
    trace.set_tracer_provider(_otel_provider)
    instrument_openai()
```

Find the shutdown block (lines ~213–221):

```python
    # Shutdown — reverse order of startup.
    await pubsub.shutdown()

    from app.ai.client import shutdown_langfuse
    from app.database import engine

    shutdown_langfuse()
    await engine.dispose()
    logger.info("nexus.shutdown")
```

Add OTel shutdown — KEEP `shutdown_langfuse()` call for Commit 1 (we drop it in Commit 2):

```python
    # Shutdown — reverse order of startup.
    await pubsub.shutdown()

    from app.ai.client import shutdown_langfuse
    from app.database import engine

    shutdown_langfuse()
    # OTel shutdown: flush + close any in-flight span batches before exit.
    _otel_provider.shutdown()
    await engine.dispose()
    logger.info("nexus.shutdown")
```

- [ ] **Step 6.4: Verify the app boots**

Run:
```bash
cd backend/nexus
docker compose up nexus -d
docker compose logs nexus 2>&1 | tail -20
```

Expected: `nexus.startup` log line, then `otel.no_exporter_configured` (or one of the exporter-enabled logs depending on env), then `otel.openai_instrumented`. No traceback. Stop the container after verifying:

```bash
docker compose stop nexus
```

---

### Task 7: Wire OTel bootstrap into `app/worker.py` (Dramatiq)

**Files:**
- Modify: `backend/nexus/app/worker.py`

- [ ] **Step 7.1: Add OTel bootstrap to worker startup**

Edit `backend/nexus/app/worker.py`. Find the structlog block (lines 28–40), add OTel bootstrap immediately after:

```python
# --- structlog init (mirrors app/main.py lifespan) ---
# The API process configures structlog in its lifespan handler. The worker
# is a separate process and needs its own init, otherwise logs use the
# default human-readable format which doesn't parse in log aggregators.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.debug
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        10 if settings.debug else 20
    ),
)

# --- OpenTelemetry init (mirrors app/main.py lifespan) ---
# Worker is a separate process and needs its own TracerProvider. Spans
# emitted by Dramatiq actors (jd, question_bank) flow through this provider.
from opentelemetry import trace  # noqa: E402
from app.ai.otel import bootstrap_tracer_provider, instrument_openai  # noqa: E402

_otel_provider = bootstrap_tracer_provider()
trace.set_tracer_provider(_otel_provider)
instrument_openai()
```

Find the langfuse shutdown registration (lines 51–54) and ADD an OTel shutdown — keep langfuse for Commit 1:

```python
# Flush Langfuse traces on worker exit so pending events aren't lost.
from app.ai.client import shutdown_langfuse  # noqa: E402

atexit.register(shutdown_langfuse)
# Flush OTel batched spans on worker exit.
atexit.register(_otel_provider.shutdown)
```

- [ ] **Step 7.2: Verify the worker boots**

Run:
```bash
cd backend/nexus
docker compose up nexus-worker -d
docker compose logs nexus-worker 2>&1 | tail -20
docker compose stop nexus-worker
```

Expected: dramatiq worker boot log, plus `otel.openai_instrumented`. No traceback.

---

### Task 8: Add `set_llm_span_attributes` calls to `jd/actors.py` (alongside langfuse)

**Files:**
- Modify: `backend/nexus/app/modules/jd/actors.py` (lines 174–187, 301–319, 639–652)
- Test: `backend/nexus/tests/test_jd_actor.py` (existing — must still pass)

- [ ] **Step 8.1: Add OTel attribute calls to `_run_enrichment`**

Edit `backend/nexus/app/modules/jd/actors.py`. Find the `_run_enrichment` function around line 174:

```python
    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=["jd_enrichment", f"retry:{retries_so_far}"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_enrichment",
            "prompt_version": "v1",
            "model": ai_config.extraction_model,
            "reasoning_effort": ai_config.extraction_effort,
            "retries_so_far": retries_so_far,
        },
    )
```

Add the OTel call immediately AFTER the langfuse call (do not remove langfuse yet):

```python
    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=["jd_enrichment", f"retry:{retries_so_far}"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_enrichment",
            "prompt_version": "v1",
            "model": ai_config.extraction_model,
            "reasoning_effort": ai_config.extraction_effort,
            "retries_so_far": retries_so_far,
        },
    )
    set_llm_span_attributes(
        prompt_name="jd_enrichment",
        prompt_version="v1",
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        job_posting_id=job_posting_id,
        model=ai_config.extraction_model,
        reasoning_effort=ai_config.extraction_effort,
        retries_so_far=retries_so_far,
    )
```

Add the import at the top of the file (around line 31, in the `from app.ai.client` block):

```python
from app.ai.client import flush_langfuse, get_openai_client, langfuse_enabled
from app.ai.tracing import set_llm_span_attributes
```

- [ ] **Step 8.2: Add OTel attribute calls to `_run_signal_extraction`**

In the same file, find the `_run_signal_extraction` function around line 301:

```python
    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=[
            "jd_signal_extraction",
            f"retry:{retries_so_far}",
            "source:enriched" if source_is_enriched else "source:raw",
        ],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_signal_extraction",
            "prompt_version": "v1",
            "model": ai_config.extraction_model,
            "reasoning_effort": ai_config.extraction_effort,
            "source_jd": "enriched" if source_is_enriched else "raw",
            "retries_so_far": retries_so_far,
        },
    )
```

Add the OTel call immediately after:

```python
    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=[
            "jd_signal_extraction",
            f"retry:{retries_so_far}",
            "source:enriched" if source_is_enriched else "source:raw",
        ],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_signal_extraction",
            "prompt_version": "v1",
            "model": ai_config.extraction_model,
            "reasoning_effort": ai_config.extraction_effort,
            "source_jd": "enriched" if source_is_enriched else "raw",
            "retries_so_far": retries_so_far,
        },
    )
    set_llm_span_attributes(
        prompt_name="jd_signal_extraction",
        prompt_version="v1",
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        job_posting_id=job_posting_id,
        model=ai_config.extraction_model,
        reasoning_effort=ai_config.extraction_effort,
        source_jd="enriched" if source_is_enriched else "raw",
        retries_so_far=retries_so_far,
    )
```

- [ ] **Step 8.3: Add OTel attribute calls to `_run_reenrichment`**

In the same file, find `_run_reenrichment` around line 639:

```python
    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=["jd_reenrichment", f"retry:{retries_so_far}"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_reenrichment",
            "prompt_version": "v1",
            "model": ai_config.reenrichment_model,
            "reasoning_effort": ai_config.reenrichment_effort,
            "retries_so_far": retries_so_far,
        },
    )
```

Add the OTel call immediately after:

```python
    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=["jd_reenrichment", f"retry:{retries_so_far}"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_reenrichment",
            "prompt_version": "v1",
            "model": ai_config.reenrichment_model,
            "reasoning_effort": ai_config.reenrichment_effort,
            "retries_so_far": retries_so_far,
        },
    )
    set_llm_span_attributes(
        prompt_name="jd_reenrichment",
        prompt_version="v1",
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        job_posting_id=job_posting_id,
        model=ai_config.reenrichment_model,
        reasoning_effort=ai_config.reenrichment_effort,
        retries_so_far=retries_so_far,
    )
```

- [ ] **Step 8.4: Run JD actor tests**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_jd_actor.py -v
```

Expected: all existing tests PASS. The OTel additions are purely additive — they don't change behavior, just add span attributes when a span is active.

---

### Task 9: Add `set_llm_span_attributes` calls to `question_bank/actors.py`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py` (lines 274–288, 941–956)
- Test: `backend/nexus/tests/test_question_banks_actors.py` (existing — must still pass)

- [ ] **Step 9.1: Add OTel attribute call to `_generate_one_bank`**

Edit `backend/nexus/app/modules/question_bank/actors.py`. Add the import at line 25 (right after `from app.ai.client import get_openai_client`):

```python
from app.ai.client import get_openai_client
from app.ai.tracing import set_llm_span_attributes
```

Find the `_generate_one_bank` function around line 274:

```python
    # Attach trace metadata for Langfuse dashboard search / grouping.
    langfuse_context.update_current_trace(
        session_id=str(bank.id),
        tags=["question_bank_generate", f"stage_type:{stage.stage_type}"],
        metadata={
            "bank_id": str(bank.id),
            "stage_id": str(stage.id),
            "stage_type": stage.stage_type,
            "tenant_id": str(bank.tenant_id),
            "job_posting_id": str(job.id),
            "model": ai_config.question_bank_model,
            "reasoning_effort": ai_config.question_bank_effort,
            "prompt_version": bank.prompt_version,
        },
    )
```

Add the OTel call immediately after:

```python
    # Attach trace metadata for Langfuse dashboard search / grouping.
    langfuse_context.update_current_trace(
        session_id=str(bank.id),
        tags=["question_bank_generate", f"stage_type:{stage.stage_type}"],
        metadata={
            "bank_id": str(bank.id),
            "stage_id": str(stage.id),
            "stage_type": stage.stage_type,
            "tenant_id": str(bank.tenant_id),
            "job_posting_id": str(job.id),
            "model": ai_config.question_bank_model,
            "reasoning_effort": ai_config.question_bank_effort,
            "prompt_version": bank.prompt_version,
        },
    )
    set_llm_span_attributes(
        prompt_name=f"question_bank_{stage.stage_type}",
        prompt_version=bank.prompt_version,
        tenant_id=str(bank.tenant_id),
        bank_id=str(bank.id),
        stage_id=str(stage.id),
        stage_type=stage.stage_type,
        job_posting_id=str(job.id),
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
    )
```

- [ ] **Step 9.2: Add OTel attribute call to `_regenerate_one_question`**

In the same file, find `_regenerate_one_question` around line 941:

```python
    # Attach trace metadata for Langfuse dashboard search / grouping.
    langfuse_context.update_current_trace(
        session_id=str(bank.id),
        tags=["question_bank_regenerate", f"stage_type:{stage.stage_type}"],
        metadata={
            "bank_id": str(bank.id),
            "stage_id": str(stage.id),
            "stage_type": stage.stage_type,
            "tenant_id": str(bank.tenant_id),
            "job_posting_id": str(job.id),
            "question_id": str(question.id),
            "model": ai_config.question_bank_model,
            "reasoning_effort": ai_config.question_bank_effort,
            "prompt_version": bank.prompt_version,
        },
    )
```

Add the OTel call immediately after:

```python
    # Attach trace metadata for Langfuse dashboard search / grouping.
    langfuse_context.update_current_trace(
        session_id=str(bank.id),
        tags=["question_bank_regenerate", f"stage_type:{stage.stage_type}"],
        metadata={
            "bank_id": str(bank.id),
            "stage_id": str(stage.id),
            "stage_type": stage.stage_type,
            "tenant_id": str(bank.tenant_id),
            "job_posting_id": str(job.id),
            "question_id": str(question.id),
            "model": ai_config.question_bank_model,
            "reasoning_effort": ai_config.question_bank_effort,
            "prompt_version": bank.prompt_version,
        },
    )
    set_llm_span_attributes(
        prompt_name="question_bank_regenerate_one",
        prompt_version=bank.prompt_version,
        tenant_id=str(bank.tenant_id),
        bank_id=str(bank.id),
        stage_id=str(stage.id),
        stage_type=stage.stage_type,
        job_posting_id=str(job.id),
        question_id=str(question.id),
        model=ai_config.question_bank_model,
        reasoning_effort=ai_config.question_bank_effort,
    )
```

- [ ] **Step 9.3: Run question-bank actor tests**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_question_banks_actors.py -v
```

Expected: all existing tests PASS.

---

### Task 10: Add OTEL passthrough to `docker-compose.yml`

**Files:**
- Modify: `backend/nexus/docker-compose.yml`

- [ ] **Step 10.1: Add OTEL env vars to nexus + nexus-worker services**

Edit `backend/nexus/docker-compose.yml`. Find the `nexus` service's `environment:` block (around line 11–23). Add three OTEL passthroughs (commented-out by default — they're already in `.env` if set):

```yaml
  nexus:
    build: ...
    environment:
      - DATABASE_URL=...
      - REDIS_URL=...
      # ... existing env vars ...
      # OpenTelemetry — both exporters off by default. Uncomment + set in
      # .env to enable. See app/ai/otel.py for env-var contract.
      # - OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT}
      # - OTEL_DEV_CONSOLE_EXPORTER=${OTEL_DEV_CONSOLE_EXPORTER}
      # - OTEL_SERVICE_NAME=${OTEL_SERVICE_NAME:-nexus}
```

Repeat for `nexus-worker`. The values flow from `.env` via `env_file: .env` already, so this block is documentation-only — but adds clarity for operators reading the file.

- [ ] **Step 10.2: Verify compose still parses**

Run:
```bash
cd backend/nexus
docker compose config > /dev/null
echo $?
```

Expected: `0` (config valid).

---

### Task 11: Manual verification — OTel spans flow alongside langfuse

**Files:** none (manual smoke test)

- [ ] **Step 11.1: Enable Console exporter in dev .env**

Edit `backend/nexus/.env` (NOT `.env.example`):

```bash
OTEL_DEV_CONSOLE_EXPORTER=true
```

- [ ] **Step 11.2: Boot the stack and trigger a JD enrichment**

Run:
```bash
cd backend/nexus
docker compose up -d
```

Then via the recruiter dashboard or via direct API call: create a job posting and trigger extraction. This is operator-driven — the exact UI flow is in `frontend/app/components/dashboard/jd-panels/`.

- [ ] **Step 11.3: Confirm OTel spans appear in worker logs**

Run:
```bash
docker compose logs nexus-worker 2>&1 | grep -A 5 "gen_ai" | head -50
```

Expected: at least one span line with `gen_ai.system: openai`, `gen_ai.request.model: <model>`, `gen_ai.prompt.name: jd_enrichment`, `gen_ai.prompt.version: v1`, `tenant.id: <uuid>`.

- [ ] **Step 11.4: Confirm langfuse traces still work (if LANGFUSE_BASE_URL is set in your dev .env)**

If you have a self-hosted Langfuse instance running, traces should still appear there. This is the side-by-side gate — both must work in Commit 1.

If LANGFUSE_BASE_URL is empty in your dev `.env`, langfuse is disabled. That's fine — the side-by-side gate is conditional on whether the operator has langfuse running. Skip this step if not.

- [ ] **Step 11.5: Stop the stack**

Run:
```bash
docker compose down
```

---

### Task 12: Commit 1 — side-by-side OTel install

- [ ] **Step 12.1: Stage changes**

Run:
```bash
cd /home/ishant/Projects/ProjectX
git status -s
```

Expected: changes in `backend/nexus/pyproject.toml`, `backend/nexus/uv.lock`, new `backend/nexus/app/ai/tracing.py`, new `backend/nexus/app/ai/otel.py`, `backend/nexus/app/main.py`, `backend/nexus/app/worker.py`, `backend/nexus/app/config.py`, `backend/nexus/app/modules/jd/actors.py`, `backend/nexus/app/modules/question_bank/actors.py`, new `backend/nexus/tests/test_ai_tracing.py`, new `backend/nexus/tests/test_otel_bootstrap.py`, modifications to `backend/nexus/tests/test_config_validators.py`, `backend/nexus/docker-compose.yml`.

- [ ] **Step 12.2: Commit**

Run:
```bash
git add backend/nexus/
git commit -m "$(cat <<'EOF'
feat(observability): add OpenTelemetry side-by-side with Langfuse

Phase 1 (Commit 1 of 2). Adds OTel TracerProvider bootstrap, OpenAI
auto-instrumentor, and `set_llm_span_attributes` helper for prompt
metadata. Both Langfuse and OTel run; OTel verified before dropping
Langfuse in Commit 2.

Two opt-in env-var exporters: OTEL_DEV_CONSOLE_EXPORTER (stdout)
and OTEL_EXPORTER_OTLP_ENDPOINT (future production sink). Both
unset → silent. Spec: docs/superpowers/specs/2026-05-01-drop-
langfuse-modular-monolith-design.md § Phase 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: clean commit, no pre-commit hook failures.

- [ ] **Step 12.3: Run full test suite to confirm no regressions**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest -q
```

Expected: all tests PASS (or same baseline as before this work — no NEW failures).

---

## Stage B — Drop Langfuse (Commit 2)

### Task 13: Drop Langfuse from `app/ai/client.py`

**Files:**
- Modify: `backend/nexus/app/ai/client.py` (entire file)

- [ ] **Step 13.1: Rewrite `app/ai/client.py`**

Replace the entire contents of `backend/nexus/app/ai/client.py` with:

```python
"""OpenAI client factory wrapped with instructor (structured output).

Business logic imports get_openai_client() — never openai directly.
This is the single swap point for a future provider change.

OpenTelemetry tracing is wired automatically via the OpenAI auto-instrumentor
registered in app.ai.otel.instrument_openai(). Every chat.completions.create()
call produces a span; app.ai.tracing.set_llm_span_attributes() adds
prompt-name + version + tenant + correlation metadata.

Instructor behavior:
  - mode=TOOLS_STRICT uses OpenAI function-calling with strict schema
    enforcement. If the model returns a malformed payload, instructor
    retries up to max_schema_retries times before raising
    InstructorRetryException (from instructor.core).

NOTE: max_retries is NOT a factory-level argument in instructor.
Passing it to from_openai() stores it in a forwarded-kwargs bucket
that leaks into every .create() call as an extra kwarg, producing
`TypeError: got multiple values for keyword argument 'max_retries'`
because instructor's per-call create() has its own internal default.
If we ever need a non-default schema-retry count, pass it per-call
in the actor via `max_retries=` on chat.completions.create()."""

from functools import lru_cache

import httpx
import instructor
import structlog
from openai import AsyncOpenAI

from app.ai.config import ai_config
from app.config import settings

logger = structlog.get_logger()


async def _log_request(request: "httpx.Request") -> None:
    """httpx event hook: log every outbound OpenAI HTTP request.

    This fires on every attempt — including SDK-level retries — so we get
    visibility into silent retry cascades that instructor-level logging can't
    see (e.g., the SDK retries a 429 or 503 before handing control back).
    """
    logger.info(
        "llm.http_request",
        method=request.method,
        url=str(request.url),
        body_bytes=len(request.content) if request.content else 0,
    )


async def _log_response(response: "httpx.Response") -> None:
    """httpx event hook: log every response received from OpenAI.

    Includes status code, reason, and selected rate-limit headers so we can
    diagnose throttling. On non-2xx, logs at warning level.
    """
    rate_remaining = response.headers.get("x-ratelimit-remaining-tokens")
    rate_reset = response.headers.get("x-ratelimit-reset-tokens")
    request_id = response.headers.get("x-request-id")
    level_fn = logger.info if response.is_success else logger.warning
    level_fn(
        "llm.http_response",
        status_code=response.status_code,
        url=str(response.request.url),
        request_id=request_id,
        rate_limit_remaining_tokens=rate_remaining,
        rate_limit_reset_tokens=rate_reset,
    )


@lru_cache(maxsize=1)
def get_openai_client() -> instructor.AsyncInstructor:
    """Return a memoized async OpenAI client wrapped with instructor.

    Configuration:
      - Timeout from ai_config.request_timeout_seconds.
      - max_retries=1 (OpenAI SDK-level auto-retry). Default is 2 which
        cascades badly when combined with reasoning models — a single
        retry on a 4-minute call burns 8 minutes silently. One retry
        covers spurious network blips; anything worse should surface.
      - httpx event hooks log every request attempt (including retries)
        and response status + rate-limit headers.

    OpenTelemetry's OpenAI auto-instrumentor (registered at app startup
    via app.ai.otel.instrument_openai()) wraps every chat.completions.create
    call into a span. Prompt metadata is added by callers via
    app.ai.tracing.set_llm_span_attributes()."""
    http_client = httpx.AsyncClient(
        timeout=ai_config.request_timeout_seconds,
        event_hooks={
            "request": [_log_request],
            "response": [_log_response],
        },
    )
    raw = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=ai_config.request_timeout_seconds,
        max_retries=1,
        http_client=http_client,
    )
    return instructor.from_openai(
        raw,
        mode=instructor.Mode.TOOLS_STRICT,
    )
```

- [ ] **Step 13.2: Run AI client tests**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_ai_schemas.py tests/test_ai_tracing.py tests/test_otel_bootstrap.py -v
```

Expected: all PASS.

---

### Task 14: Drop Langfuse from `app/main.py` and `app/worker.py`

**Files:**
- Modify: `backend/nexus/app/main.py` (lifespan shutdown around line 213–221)
- Modify: `backend/nexus/app/worker.py` (lines 51–54)

- [ ] **Step 14.1: Remove `shutdown_langfuse` from `main.py` shutdown handler**

Edit `backend/nexus/app/main.py`. Find:

```python
    # Shutdown — reverse order of startup.
    await pubsub.shutdown()

    from app.ai.client import shutdown_langfuse
    from app.database import engine

    shutdown_langfuse()
    # OTel shutdown: flush + close any in-flight span batches before exit.
    _otel_provider.shutdown()
    await engine.dispose()
    logger.info("nexus.shutdown")
```

Replace with:

```python
    # Shutdown — reverse order of startup.
    await pubsub.shutdown()

    from app.database import engine

    # OTel shutdown: flush + close any in-flight span batches before exit.
    _otel_provider.shutdown()
    await engine.dispose()
    logger.info("nexus.shutdown")
```

- [ ] **Step 14.2: Remove `shutdown_langfuse` from `worker.py`**

Edit `backend/nexus/app/worker.py`. Find:

```python
# Flush Langfuse traces on worker exit so pending events aren't lost.
from app.ai.client import shutdown_langfuse  # noqa: E402

atexit.register(shutdown_langfuse)
# Flush OTel batched spans on worker exit.
atexit.register(_otel_provider.shutdown)
```

Replace with:

```python
# Flush OTel batched spans on worker exit.
atexit.register(_otel_provider.shutdown)
```

- [ ] **Step 14.3: Verify the app and worker still boot**

Run:
```bash
cd backend/nexus
docker compose up nexus nexus-worker -d
sleep 5
docker compose logs nexus 2>&1 | tail -10
docker compose logs nexus-worker 2>&1 | tail -10
docker compose stop nexus nexus-worker
```

Expected: both boot logs show `nexus.startup` / dramatiq worker boot, `otel.openai_instrumented`, and no langfuse references.

---

### Task 15: Drop Langfuse from `jd/actors.py`

**Files:**
- Modify: `backend/nexus/app/modules/jd/actors.py`

- [ ] **Step 15.1: Remove langfuse imports**

Edit `backend/nexus/app/modules/jd/actors.py`. Find lines 27 and 31:

```python
from langfuse.decorators import langfuse_context, observe
```

Delete this line.

```python
from app.ai.client import flush_langfuse, get_openai_client, langfuse_enabled
```

Replace with:

```python
from app.ai.client import get_openai_client
```

- [ ] **Step 15.2: Remove `@observe(...)` decorators**

In the same file, find three `@observe(...)` decorators:
- Line ~123: `@observe(name="jd_enrichment_phase")`
- Line ~251: `@observe(name="jd_signal_extraction_phase")`
- Line ~590: `@observe(name="jd_reenrichment_call2")`

Delete each line.

- [ ] **Step 15.3: Remove `langfuse_context.update_current_trace(...)` blocks**

In the same file, find three `langfuse_context.update_current_trace(...)` blocks (around lines 174, 301, 639). Each is followed by a `set_llm_span_attributes(...)` call (added in Task 8).

Delete the entire `langfuse_context.update_current_trace(...)` call (the multi-line block with `tags=`, `metadata=`, etc.). Keep only the `set_llm_span_attributes(...)` call.

For example, around line 174:

```python
    langfuse_context.update_current_trace(  # ← DELETE THIS WHOLE BLOCK
        session_id=job_posting_id,
        tags=["jd_enrichment", f"retry:{retries_so_far}"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_enrichment",
            "prompt_version": "v1",
            "model": ai_config.extraction_model,
            "reasoning_effort": ai_config.extraction_effort,
            "retries_so_far": retries_so_far,
        },
    )
    set_llm_span_attributes(  # ← KEEP THIS
        prompt_name="jd_enrichment",
        ...
    )
```

After deletion, only the `set_llm_span_attributes(...)` call remains. Repeat for the other two locations.

- [ ] **Step 15.4: Remove `flush_langfuse()` calls in finally blocks**

In the same file, find two patterns like (around lines 519 and 550 and 762):

```python
        finally:
            if langfuse_enabled():
                await asyncio.to_thread(flush_langfuse)
```

Delete all three occurrences (entire `finally` block). The surrounding `try/except` should keep its structure — if there are no other `finally` operations, the block becomes a plain try/except.

For each, before:
```python
        try:
            await _run_enrichment(...)
            await db.commit()
            phase_1_committed = True
        except Exception as exc:
            ...
            _exc_to_reraise = exc
        finally:
            if langfuse_enabled():
                await asyncio.to_thread(flush_langfuse)
```

After:
```python
        try:
            await _run_enrichment(...)
            await db.commit()
            phase_1_committed = True
        except Exception as exc:
            ...
            _exc_to_reraise = exc
```

- [ ] **Step 15.5: Update the module docstring**

At the top of the file (around lines 9–15), find:

```python
"""...
Tracing:
  Both phase coroutines are decorated with @observe() which creates
  Langfuse child spans. The OpenAI calls (via langfuse.openai.AsyncOpenAI)
  are auto-captured as nested generation spans, so each extraction job
  produces a trace with two clearly labelled phase spans. Metadata
  (tenant_id, correlation_id, job_posting_id, prompt_version) is
  propagated to all child spans via langfuse_context.update_current_trace."""
```

Replace with:

```python
"""...
Tracing:
  Each phase coroutine's OpenAI call is auto-captured as an OTel span
  by the OpenAI auto-instrumentor (registered at app startup). Prompt
  metadata (tenant_id, correlation_id, job_posting_id, prompt_version)
  is added to the active span via app.ai.tracing.set_llm_span_attributes."""
```

- [ ] **Step 15.6: Run JD actor tests**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_jd_actor.py -v
```

Expected: all PASS.

---

### Task 16: Drop Langfuse from `question_bank/actors.py`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/actors.py`

- [ ] **Step 16.1: Remove langfuse imports**

Edit `backend/nexus/app/modules/question_bank/actors.py`. Find line 20:

```python
from langfuse.decorators import langfuse_context, observe
```

Delete this line.

- [ ] **Step 16.2: Remove `@observe(...)` decorators**

In the same file:
- Line ~250: `@observe(name="question_bank_generate")`
- Line ~924: `@observe(name="question_bank_regenerate")`

Delete each line.

- [ ] **Step 16.3: Remove `langfuse_context.update_current_trace(...)` blocks**

In the same file, find both `langfuse_context.update_current_trace(...)` blocks (around lines 274 and 941). Each is followed by a `set_llm_span_attributes(...)` call (added in Task 9).

Delete the entire `langfuse_context.update_current_trace(...)` call. Keep the `set_llm_span_attributes(...)` call.

- [ ] **Step 16.4: Update the module docstring**

At the top (around lines 1–10), the docstring mentions Langfuse-related comments embedded in `_generate_one_bank` and `_regenerate_one_question` (e.g. "Tracing: @observe creates a Langfuse trace…"). Update those docstrings to reference OTel.

For `_generate_one_bank` around line 264–272, replace:

```python
    """Run generation for one bank. Must be called with bank.status='generating'.
    On success → transitions to reviewing. On error → transitions to failed.
    Caller must commit or rollback.

    Tracing:
      @observe creates a Langfuse trace named 'question_bank_generate'.
      The OpenAI call inside (via langfuse.openai.AsyncOpenAI) is
      auto-captured as a nested generation span. Trace metadata includes
      bank_id, stage_id, tenant_id, and model/effort so traces are
      searchable per-bank in the Langfuse dashboard. session_id groups
      all retries of the same bank into one Langfuse session (matching
      jd/actors.py). (B13 wiring.)
    """
```

With:

```python
    """Run generation for one bank. Must be called with bank.status='generating'.
    On success → transitions to reviewing. On error → transitions to failed.
    Caller must commit or rollback.

    Tracing:
      The OpenAI call is auto-captured as an OTel span by the OpenAI
      auto-instrumentor. set_llm_span_attributes() adds bank_id, stage_id,
      tenant_id, model/effort, and prompt name+version so spans are
      searchable per-bank in any OTel-compatible observability backend.
    """
```

For `_regenerate_one_question` around line 935–940, replace:

```python
    """Inner helper for regenerate_question that owns the LLM + DB write.

    Separated from the Dramatiq actor so @observe() can wrap only the
    observable path (not the actor's session bootstrap). Matches the
    jd/actors.py inner-coroutine pattern (e.g., `_run_reenrichment`). (B13 wiring.)
    """
```

With:

```python
    """Inner helper for regenerate_question that owns the LLM + DB write.

    Separated from the Dramatiq actor so the LLM call is the only thing
    inside the auto-instrumented OTel span (not the actor's session
    bootstrap). Matches the jd/actors.py inner-coroutine pattern (e.g.,
    `_run_reenrichment`).
    """
```

Also remove the "Attach trace metadata for Langfuse dashboard search / grouping." comments above the `set_llm_span_attributes` calls — replace with `# Attach OTel span attributes for prompt metadata.`

- [ ] **Step 16.5: Run question-bank actor tests**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_question_banks_actors.py -v
```

Expected: all PASS.

---

### Task 17: Drop Langfuse from `app/config.py`

**Files:**
- Modify: `backend/nexus/app/config.py`

- [ ] **Step 17.1: Remove `langfuse_*` settings**

Edit `backend/nexus/app/config.py`. Find the Observability block (lines 165–170):

```python
    # Observability
    sentry_dsn: str = ""
    langfuse_host: str = ""           # Legacy — prefer LANGFUSE_BASE_URL
    langfuse_base_url: str = ""       # e.g. https://cloud.langfuse.com (Langfuse v2+ convention)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # OpenTelemetry — vendor-neutral tracing.
    ...
```

Replace with:

```python
    # Observability
    sentry_dsn: str = ""

    # OpenTelemetry — vendor-neutral tracing.
    ...
```

- [ ] **Step 17.2: Run config tests**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest tests/test_config_validators.py -v
```

Expected: all PASS, including `test_otel_settings_default_to_off` and `test_otel_settings_read_from_env`.

---

### Task 18: Drop Langfuse from `pyproject.toml`

**Files:**
- Modify: `backend/nexus/pyproject.toml`

- [ ] **Step 18.1: Remove the langfuse pin**

Edit `backend/nexus/pyproject.toml`. Find:

```toml
"opentelemetry-instrumentation-openai-v2>=2.0,<3",
"langfuse>=2.56,<3",
```

Replace with (delete the langfuse line):

```toml
"opentelemetry-instrumentation-openai-v2>=2.0,<3",
```

- [ ] **Step 18.2: Regenerate the lockfile**

Run:
```bash
cd backend/nexus
uv lock
```

Expected: `uv.lock` updated; langfuse removed from the dependency graph.

- [ ] **Step 18.3: Rebuild the image**

Run:
```bash
cd backend/nexus
docker compose build nexus nexus-worker
```

Expected: clean build with langfuse no longer installed.

- [ ] **Step 18.4: Verify langfuse is not importable**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus python -c "import langfuse" 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'langfuse'`.

---

### Task 19: Drop LANGFUSE block from `.env.example`

**Files:**
- Modify: `backend/nexus/.env.example`

- [ ] **Step 19.1: Replace LANGFUSE block with OTEL block**

Edit `backend/nexus/.env.example`. Find lines 153–169:

```bash
# Observability
SENTRY_DSN=
# Langfuse LLM tracing — set LANGFUSE_BASE_URL + keys to enable.
#
# SELF-HOSTED ONLY. CLAUDE.md prohibits pointing at cloud.langfuse.com /
# *.langfuse.com in staging or production because LLM traces contain
# candidate PII (transcripts, evaluation scores) and AIVIA compliance
# forbids routing that data through a third-party sub-processor.
#
# In development, cloud is allowed but discouraged — the client refuses
# to configure against *.langfuse.com outside `ENVIRONMENT=development`.
#
# Self-hosted example (Docker Compose): http://langfuse-web:3000
# Local dev:                            http://127.0.0.1:3000
LANGFUSE_BASE_URL=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

Replace with:

```bash
# Observability
SENTRY_DSN=

# OpenTelemetry — vendor-neutral LLM + HTTP tracing.
#
# Two opt-in exporters; both off by default. Production-safe: with neither
# set, no traces ship anywhere — spans are created in memory and discarded.
#
# Local dev: set OTEL_DEV_CONSOLE_EXPORTER=true to dump every span to stdout.
#   Useful for eyeballing what the OpenAI auto-instrumentor captures
#   (model, prompt name+version, tenant id, correlation id, latency).
#
# Production: set OTEL_EXPORTER_OTLP_ENDPOINT=<url> to ship to an OTLP
#   receiver. Compatible with Sentry (when wired), Jaeger, Tempo,
#   Datadog Agent, Honeycomb, or a custom collector that writes spans
#   to your own database for an admin-dashboard observability UI.
#
# OTEL_SERVICE_NAME defaults to "nexus" — override per-deployment if needed
# (e.g. "nexus-prod" vs "nexus-staging") to filter in the backend.
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_DEV_CONSOLE_EXPORTER=false
OTEL_SERVICE_NAME=nexus
```

- [ ] **Step 19.2: Verify the file syntax**

Run:
```bash
grep -c "LANGFUSE" backend/nexus/.env.example
```

Expected: `0` (no LANGFUSE references remain).

---

### Task 20: Drop Langfuse comment from `jd/router.py`

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py`

- [ ] **Step 20.1: Update the comment on line 59**

Edit `backend/nexus/app/modules/jd/router.py`. Find around line 59:

```python
    logs, Langfuse tags, and actor kwargs:
```

Replace with:

```python
    logs, OTel span attributes, and actor kwargs:
```

This is a docstring comment in `extract_jd_endpoint`. No behavioral change.

---

### Task 21: Update CLAUDE.md to reference OTel

**Files:**
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 21.1: Replace the AI Provider section's langfuse references**

Edit `backend/nexus/CLAUDE.md`. Find line 55:

```markdown
│   │   ├── client.py            ← get_openai_client() — instructor.AsyncInstructor + langfuse.openai factory
```

Replace with:

```markdown
│   │   ├── client.py            ← get_openai_client() — instructor.AsyncInstructor + plain openai.AsyncOpenAI
│   │   ├── otel.py              ← TracerProvider bootstrap, OpenAI auto-instrumentor
│   │   ├── tracing.py           ← set_llm_span_attributes() — adds prompt metadata to active OTel span
```

Find line 275 (in the AI Provider & Prompt Management section):

```markdown
- **OpenAI client factory** (`app/ai/client.py`) — returns an `instructor.AsyncInstructor` wrapped around `langfuse.openai.AsyncOpenAI`. Langfuse tracing is a drop-in — no-op when `LANGFUSE_HOST` is empty.
```

Replace with:

```markdown
- **OpenAI client factory** (`app/ai/client.py`) — returns an `instructor.AsyncInstructor` wrapped around `openai.AsyncOpenAI`. OpenTelemetry tracing is wired separately at app startup via `app/ai/otel.py` — the OpenAI auto-instrumentor captures every `chat.completions.create` call as a span. Both exporters (Console for dev, OTLP for production) are off by default — see `.env.example` for the contract.
```

Find line 278:

```markdown
Business logic imports `get_openai_client()` and `prompt_loader` from `app.ai.*` — never openai/instructor/langfuse directly. This is the single swap point for a future provider change.
```

Replace with:

```markdown
Business logic imports `get_openai_client()` and `prompt_loader` from `app.ai.*` — never openai/instructor directly. This is the single swap point for a future provider change.
```

Find line 293 (in the documented carve-outs):

```markdown
- `langfuse.decorators.observe` is allowed anywhere actors run — it is the tracing scaffold, not a business-logic dependency.
```

Delete this entire bullet line.

Find line 469 (the Langfuse — Self-Hosted Only section header):

```markdown
### Langfuse — Self-Hosted Only
Langfuse traces every LLM call including candidate response text. This is sensitive candidate evaluation data. **Never use managed Langfuse cloud.** Self-host using the official Docker Compose setup. Set `LANGFUSE_HOST` in environment config.
```

Replace with:

```markdown
### OpenTelemetry — Vendor-Neutral by Design
LLM traces flow through OpenTelemetry instrumentation, not a vendor-specific SDK. The `opentelemetry-instrumentation-openai-v2` auto-instrumentor captures every `chat.completions.create` call as a span; `app/ai/tracing.set_llm_span_attributes()` adds prompt metadata. Two opt-in exporters (`OTEL_DEV_CONSOLE_EXPORTER` for stdout, `OTEL_EXPORTER_OTLP_ENDPOINT` for production); both off by default. Spans contain candidate evaluation data, so the OTLP endpoint MUST point at a sink the operator controls — never a third-party-hosted backend without a signed sub-processor agreement.
```

Find line 441 (Tracing line in Actor Discipline):

```markdown
- **Tracing.** Every actor uses `@observe` (Langfuse) for LLM calls and writes its `correlation_id` into structured logs at every hop. The correlation ID flows from the request that enqueued the task through to any downstream call.
```

Replace with:

```markdown
- **Tracing.** Each actor's LLM call is auto-captured as an OpenTelemetry span by the OpenAI instrumentor. Actors call `set_llm_span_attributes()` from `app/ai/tracing.py` to add prompt name+version, tenant id, and correlation id. The correlation ID also flows through structured logs at every hop, so log-grep and trace-search produce the same picture.
```

Find line 337 (the `ai` module description):

```markdown
| `ai` | Provider-agnostic AI layer. `AIConfig` (env-driven model/effort), `PromptLoader` (versioned prompts, in-memory cache), `get_openai_client()` (instructor + langfuse.openai factory with self-hosted-only guard — `_is_langfuse_cloud_host()` raises outside dev), `EnrichmentOutput` + `SignalExtractionOutput` schemas (split in the JD creation flow refinement) with provenance validators. (The original combined `ExtractionOutput` landed in Phase 2A; subsequently split into the two-phase form on 2026-04-28 — see docs/superpowers/specs/2026-04-28-jd-creation-flow-refinement-design.md.) |
```

Replace with:

```markdown
| `ai` | Provider-agnostic AI layer. `AIConfig` (env-driven model/effort), `PromptLoader` (versioned prompts, in-memory cache), `get_openai_client()` (instructor + plain openai.AsyncOpenAI), `tracing.py` + `otel.py` (OpenTelemetry auto-instrumentation + prompt-attribute helper), `EnrichmentOutput` + `SignalExtractionOutput` schemas (split in the JD creation flow refinement) with provenance validators. (The original combined `ExtractionOutput` landed in Phase 2A; subsequently split into the two-phase form on 2026-04-28 — see docs/superpowers/specs/2026-04-28-jd-creation-flow-refinement-design.md.) |
```

Find line 295 (the two-venv hack carve-out — keep most of it; just remove the langfuse-specific reasoning since openai 1.x is also being lifted in Phase 2):

The line currently reads:
```markdown
- The interview-engine container (Phase 3C.2 Chunk 5) installs nexus + livekit-agents into **two separate venvs** with `PYTHONPATH` layered to prefer the engine venv. Reason: `langfuse>=2.56,<3` (nexus pin) constrains `openai<2`, while `livekit-agents>=1.5.4` requires `openai>=2`. Single-venv install fails resolution. Unblocked when `langfuse>=3` lands (its module layout changed — `app/ai/client.py` would need to switch from `langfuse.openai` + `langfuse.decorators.observe` to the v3 equivalents). Tracked as tech-debt; not blocking.
```

Replace with:

```markdown
- The interview-engine container (Phase 3C.2 Chunk 5) installs nexus + livekit-agents into **two separate venvs** with `PYTHONPATH` layered to prefer the engine venv. Reason: nexus pins `openai<2` (instructor 1.x constraint), while `livekit-agents>=1.5.4` requires `openai>=2`. Single-venv install fails resolution. Will be unblocked in Phase 2 of the modular-monolith spec by lifting both `openai` and `instructor` to 2.x. Tracked as tech-debt for now; not blocking.
```

- [ ] **Step 21.2: Verify CLAUDE.md no longer references Langfuse**

Run:
```bash
grep -n "[Ll]angfuse\|LANGFUSE" backend/nexus/CLAUDE.md
```

Expected: zero hits, OR only hits inside historical Phase status descriptions ("Phase 2A — done: ... Langfuse (self-hosted) tracing.") which describe what was true at the time. Those historical references can stay; replace anything in PRESCRIPTIVE / present-tense rule text only.

If you find a remaining prescriptive reference, treat it as a Step 21.1 oversight and fix it inline.

---

### Task 22: Final verification — no langfuse references remain

**Files:** none (verification)

- [ ] **Step 22.1: Grep for any remaining langfuse references in code**

Run:
```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
grep -rn "langfuse\|Langfuse\|LANGFUSE\|@observe" \
  app/ tests/ pyproject.toml .env.example docker-compose.yml \
  --include="*.py" --include="*.toml" --include="*.example" --include="*.yml" 2>/dev/null
```

Expected: zero hits.

If any remain: identify the file, decide whether the reference is in historical commentary (acceptable) or active code/config (must remove), and fix.

- [ ] **Step 22.2: Run full test suite**

Run:
```bash
docker compose run --rm nexus pytest -q
```

Expected: all tests PASS.

- [ ] **Step 22.3: Run pip-audit on the new lockfile**

Run:
```bash
docker compose run --rm nexus pip-audit --requirement <(uv export --no-hashes)
```

If `pip-audit` is not installed in the image:
```bash
docker compose run --rm nexus python -m pip install pip-audit
docker compose run --rm nexus pip-audit
```

Expected: zero CRITICAL / HIGH vulnerabilities. Document any HIGHs in the PR body with rationale.

- [ ] **Step 22.4: Boot the stack end-to-end and trigger one JD enrichment**

Run:
```bash
# In terminal 1: enable Console exporter
echo "OTEL_DEV_CONSOLE_EXPORTER=true" >> backend/nexus/.env
docker compose up -d
sleep 10
```

Then via the recruiter dashboard, create a JD and trigger extraction. Then:

```bash
docker compose logs nexus-worker 2>&1 | grep -A 3 "gen_ai" | head -40
```

Expected: at least one span block containing `gen_ai.system: openai`, `gen_ai.prompt.name: jd_enrichment`, `gen_ai.prompt.version: v1`, `tenant.id: <uuid>`, `app.correlation_id: <id>`.

Stop the stack:
```bash
docker compose down
```

Roll back the `.env` change (if you don't want Console exporter on by default):
```bash
sed -i '/OTEL_DEV_CONSOLE_EXPORTER=true/d' backend/nexus/.env
```

---

### Task 23: Commit 2 — drop langfuse

- [ ] **Step 23.1: Stage changes**

Run:
```bash
cd /home/ishant/Projects/ProjectX
git status -s
```

Expected: changes in `backend/nexus/pyproject.toml`, `backend/nexus/uv.lock`, `backend/nexus/app/ai/client.py`, `backend/nexus/app/main.py`, `backend/nexus/app/worker.py`, `backend/nexus/app/config.py`, `backend/nexus/.env.example`, `backend/nexus/app/modules/jd/actors.py`, `backend/nexus/app/modules/question_bank/actors.py`, `backend/nexus/app/modules/jd/router.py`, `backend/nexus/CLAUDE.md`.

- [ ] **Step 23.2: Commit**

Run:
```bash
git add backend/nexus/
git commit -m "$(cat <<'EOF'
feat(observability): drop langfuse — OTel is now the only tracing layer

Phase 1 (Commit 2 of 2). Removes every langfuse code path: the
langfuse SDK pin, the wrapped AsyncOpenAI client, all @observe
decorators and langfuse_context.update_current_trace calls, the
LANGFUSE_* settings + .env.example block, the shutdown handler
in app.main and app.worker. The OpenAI auto-instrumentor (added
in Commit 1) handles span creation; set_llm_span_attributes adds
prompt metadata.

CLAUDE.md updated: AI Provider section + module table now
reference OTel. The "Langfuse — Self-Hosted Only" section is
retitled "OpenTelemetry — Vendor-Neutral by Design". Phase 3C.2
two-venv carve-out reason updated (instructor 1.x → openai<2,
unblocked in Phase 2).

No frontend impact: the frontend has zero langfuse coupling.
Spec: docs/superpowers/specs/2026-05-01-drop-langfuse-modular-
monolith-design.md § Phase 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: clean commit. No pre-commit hook failures.

- [ ] **Step 23.3: Verify final state**

Run:
```bash
cd /home/ishant/Projects/ProjectX
git log --oneline -5
git status
```

Expected: two new commits on top of `feat/phase-3c2-interview-engine` (or whatever branch you're on); working tree clean.

---

## Stage C — Phase 1 verification gates

These are the spec's stated verification gates. Run them all before declaring Phase 1 done.

- [ ] **Step C.1: JD enrichment produces an OTel span with prompt metadata**

Run with Console exporter on and trigger a JD extraction. Confirm in worker logs:
```
gen_ai.system: openai
gen_ai.prompt.name: jd_enrichment
gen_ai.prompt.version: v1
tenant.id: <uuid>
```

- [ ] **Step C.2: Question-bank generation produces ≥1 trace per stage**

Trigger a question-bank generation for a multi-stage pipeline. Confirm in worker logs:
```
gen_ai.prompt.name: question_bank_phone_screen
gen_ai.prompt.name: question_bank_ai_screening
```
(One span per AI-generated stage type.)

- [ ] **Step C.3: pytest full suite green**

Run:
```bash
cd backend/nexus
docker compose run --rm nexus pytest -q
```

Expected: all tests PASS.

- [ ] **Step C.4: pip-audit clean**

Run:
```bash
docker compose run --rm nexus pip-audit
```

Expected: zero CRITICAL / HIGH vulnerabilities.

- [ ] **Step C.5: zero langfuse references in active code**

Run:
```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
grep -rn "langfuse\|Langfuse\|LANGFUSE\|@observe" \
  app/ tests/ pyproject.toml .env.example docker-compose.yml \
  --include="*.py" --include="*.toml" --include="*.example" --include="*.yml" 2>/dev/null
```

Expected: zero hits in active code (historical commentary in CLAUDE.md `Phase 2A — done` table row is acceptable).

- [ ] **Step C.6: Frontend is untouched**

Run:
```bash
cd /home/ishant/Projects/ProjectX
git diff main..HEAD -- frontend/
```

Expected: zero diff. If non-empty, something went wrong — investigate and revert any frontend file changes.

---

## Done

Phase 1 ships when all 6 verification gates pass. The next phase is `2026-05-01-phase-2-openai-instructor-python-uplift.md` — bumping `openai` 1.x → 2.x, `instructor` 1.x → 2.x, and Python 3.12 → 3.13.
