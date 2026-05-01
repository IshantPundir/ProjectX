"""OpenTelemetry TracerProvider bootstrap and exporter wiring.

Exposes:
- ``bootstrap_tracer_provider()`` — builds a TracerProvider with exporters
  controlled by env vars. Returns the provider so callers can register it
  as the global provider and call ``.shutdown()`` at process exit.

Phase 1 originally wired ``opentelemetry-instrumentation-openai-v2`` as an
auto-instrumentor here. Phase 3 dropped it because livekit-agents>=1.5.4
(absorbed when the engine merged into nexus) requires opentelemetry-api>=1.39
and the auto-instrumentor pinned api==1.37.0 via its semantic-conventions
transitive. LLM calls are now wrapped in explicit
``tracer.start_as_current_span("openai.chat.completions.create")`` blocks
at every chat.completions.create call site (see ``app/modules/jd/actors.py``
and ``app/modules/question_bank/actors.py``). Span attributes still flow
through ``app.ai.tracing.set_llm_span_attributes()`` — that helper tags
whatever span is currently active, so it works against the manual spans
identically.

Env-var contract (read via app.config.settings):
- ``OTEL_EXPORTER_OTLP_ENDPOINT`` set → wire OTLPSpanExporter (BatchSpanProcessor)
- ``OTEL_DEV_CONSOLE_EXPORTER=true`` → wire ConsoleSpanExporter (SimpleSpanProcessor)
- both unset → TracerProvider has no exporters; spans created and discarded

Production-safe by default: with no env vars set, no traces ship anywhere.
"""

from __future__ import annotations

import structlog
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from app.config import settings

logger = structlog.get_logger()


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
