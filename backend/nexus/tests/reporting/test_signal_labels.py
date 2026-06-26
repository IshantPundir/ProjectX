"""Unit tests for the report scorer's signal-label generator (mocked OpenAI)."""
import pytest

from app.modules.reporting.scoring import signal_labels as sl
from app.modules.reporting.scoring.signal_labels import (
    SignalLabelsOut,
    _LabelItem,
    generate_signal_labels,
)


class _FakeResp:
    def __init__(self, parsed):
        self.output_parsed = parsed


class _FakeResponses:
    def __init__(self, parsed, exc=None):
        self._parsed, self._exc = parsed, exc

    async def parse(self, **kwargs):
        if self._exc:
            raise self._exc
        return _FakeResp(self._parsed)


class _FakeClient:
    def __init__(self, parsed, exc=None):
        self.responses = _FakeResponses(parsed, exc)


@pytest.mark.asyncio
async def test_empty_input_short_circuits_without_calling_the_model(monkeypatch):
    def _boom():  # would raise if the helper tried to call the model
        raise AssertionError("model should not be called for empty input")
    monkeypatch.setattr(sl, "get_raw_openai_client", _boom)
    assert await generate_signal_labels([], correlation_id="c") == {}
    assert await generate_signal_labels(["", "  "], correlation_id="c") == {}


@pytest.mark.asyncio
async def test_maps_verbose_values_to_short_labels_by_index(monkeypatch):
    parsed = SignalLabelsOut(labels=[
        _LabelItem(id="0", label="Intune / MDM"),
        _LabelItem(id="1", label="iOS & Android"),
    ])
    monkeypatch.setattr(sl, "get_raw_openai_client", lambda: _FakeClient(parsed))
    values = ["Microsoft Intune-based EMM & MDM administration and configuration",
              "iOS and Android device management"]
    out = await generate_signal_labels(values, correlation_id="c")
    assert out == {values[0]: "Intune / MDM", values[1]: "iOS & Android"}


@pytest.mark.asyncio
async def test_dedupes_values_before_mapping(monkeypatch):
    parsed = SignalLabelsOut(labels=[_LabelItem(id="0", label="RBAC")])
    monkeypatch.setattr(sl, "get_raw_openai_client", lambda: _FakeClient(parsed))
    out = await generate_signal_labels(["Role-based access control",
                                        "Role-based access control"], correlation_id="c")
    assert out == {"Role-based access control": "RBAC"}


@pytest.mark.asyncio
async def test_api_error_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(sl, "get_raw_openai_client",
                        lambda: _FakeClient(None, exc=RuntimeError("503")))
    assert await generate_signal_labels(["X"], correlation_id="c") == {}


@pytest.mark.asyncio
async def test_refusal_none_parsed_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(sl, "get_raw_openai_client", lambda: _FakeClient(None))
    assert await generate_signal_labels(["X"], correlation_id="c") == {}


@pytest.mark.asyncio
async def test_skips_blank_labels_and_out_of_range_ids(monkeypatch):
    parsed = SignalLabelsOut(labels=[
        _LabelItem(id="0", label="  "),     # blank → skipped
        _LabelItem(id="9", label="Ghost"),  # out of range → skipped
        _LabelItem(id="1", label="Kafka"),
    ])
    monkeypatch.setattr(sl, "get_raw_openai_client", lambda: _FakeClient(parsed))
    out = await generate_signal_labels(["Apache stream proc", "Kafka pipelines"],
                                       correlation_id="c")
    assert out == {"Kafka pipelines": "Kafka"}
