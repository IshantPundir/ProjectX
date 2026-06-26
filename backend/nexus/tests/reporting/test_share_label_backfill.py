"""The share actor backfills short titles on legacy reports (no re-score)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.reporting import actors
from app.modules.reporting.schemas import SignalAssessmentOut


def _sa(signal: str, label: str | None = None) -> SignalAssessmentOut:
    return SignalAssessmentOut(
        signal=signal, signal_label=label, type="skill", weight=2, knockout=False,
        priority="required", provenance="asked_directly", level="solid", score=7.0)


@pytest.mark.asyncio
async def test_backfills_only_missing_labels(monkeypatch):
    report = SimpleNamespace(signal_assessments=[
        _sa("Verbose competency A"),
        _sa("Verbose competency B", "Already Set"),
    ])
    monkeypatch.setattr(actors, "generate_signal_labels",
                        AsyncMock(return_value={"Verbose competency A": "Short A"}))
    await actors._ensure_signal_labels(report, correlation_id="c")
    assert report.signal_assessments[0].signal_label == "Short A"
    assert report.signal_assessments[1].signal_label == "Already Set"  # untouched


@pytest.mark.asyncio
async def test_noop_when_all_labels_present(monkeypatch):
    gen = AsyncMock(return_value={})
    monkeypatch.setattr(actors, "generate_signal_labels", gen)
    report = SimpleNamespace(signal_assessments=[_sa("X", "X Short")])
    await actors._ensure_signal_labels(report, correlation_id="c")
    gen.assert_not_awaited()  # new reports never trigger the model


@pytest.mark.asyncio
async def test_generator_failure_leaves_labels_none(monkeypatch):
    monkeypatch.setattr(actors, "generate_signal_labels", AsyncMock(return_value={}))
    report = SimpleNamespace(signal_assessments=[_sa("Y")])
    await actors._ensure_signal_labels(report, correlation_id="c")
    assert report.signal_assessments[0].signal_label is None  # graceful fallback
