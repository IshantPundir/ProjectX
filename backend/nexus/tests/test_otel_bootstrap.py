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
    # Force a fresh Settings read
    import app.config
    import importlib
    monkeypatch.setattr(app.config, "settings", app.config.Settings())
    # Reload otel module to pick up fresh settings
    import app.ai.otel
    importlib.reload(app.ai.otel)
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    assert isinstance(provider, TracerProvider)
    # No span processors registered when both exporters are off.
    assert len(provider._active_span_processor._span_processors) == 0


def test_bootstrap_console_exporter_when_env_set(monkeypatch):
    """OTEL_DEV_CONSOLE_EXPORTER=true wires a ConsoleSpanExporter."""
    monkeypatch.setenv("OTEL_DEV_CONSOLE_EXPORTER", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    import app.config
    import importlib
    monkeypatch.setattr(app.config, "settings", app.config.Settings())
    # Reload otel module to pick up fresh settings
    import app.ai.otel
    importlib.reload(app.ai.otel)
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    processors = provider._active_span_processor._span_processors
    assert len(processors) == 1
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    assert isinstance(processors[0].span_exporter, ConsoleSpanExporter)


def test_bootstrap_otlp_exporter_when_endpoint_set(monkeypatch):
    """OTEL_EXPORTER_OTLP_ENDPOINT=<url> wires an OTLPSpanExporter."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.delenv("OTEL_DEV_CONSOLE_EXPORTER", raising=False)
    import app.config
    import importlib
    monkeypatch.setattr(app.config, "settings", app.config.Settings())
    # Reload otel module to pick up fresh settings
    import app.ai.otel
    importlib.reload(app.ai.otel)
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    processors = provider._active_span_processor._span_processors
    assert len(processors) == 1
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    assert isinstance(processors[0].span_exporter, OTLPSpanExporter)


def test_bootstrap_both_exporters_when_both_env_set(monkeypatch):
    """Both env vars set → both exporters wired."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_DEV_CONSOLE_EXPORTER", "true")
    import app.config
    import importlib
    monkeypatch.setattr(app.config, "settings", app.config.Settings())
    # Reload otel module to pick up fresh settings
    import app.ai.otel
    importlib.reload(app.ai.otel)
    from app.ai.otel import bootstrap_tracer_provider

    provider = bootstrap_tracer_provider()
    processors = provider._active_span_processor._span_processors
    assert len(processors) == 2
