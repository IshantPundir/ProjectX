import asyncio

import pytest

from app.modules.interview_engine_v2.triage import TriagePlane
from app.modules.interview_engine_v2.triage import service as triage_service
from app.modules.interview_engine_v2.triage.decision import (
    TriageDecision, TriageKind, TriageRoute)

pytestmark = pytest.mark.asyncio


def _patch(monkeypatch, decision):
    async def _fake(**kwargs):
        return decision
    monkeypatch.setattr(triage_service, "_call_triage", _fake)


def _plane():
    return TriagePlane(persona_name="Arjun", job_title="Backend Engineer")


async def test_handled_hold_returns_decision_unchanged(monkeypatch):
    _patch(monkeypatch, TriageDecision(reasoning="thinking", kind=TriageKind.answering,
        answer_complete=False, route=TriageRoute.handled, spoken_line="Take your time…"))
    d = await _plane().triage(active_question="Q?", accumulated_answer="let me think",
                              last_spoken_question="Q?")
    assert d.route is TriageRoute.handled and d.spoken_line == "Take your time…"


async def test_to_brain_answer(monkeypatch):
    _patch(monkeypatch, TriageDecision(reasoning="answer", kind=TriageKind.answering,
        answer_complete=True, route=TriageRoute.to_brain, spoken_line="Mm — five years…"))
    d = await _plane().triage(active_question="Q?", accumulated_answer="five years python",
                              last_spoken_question="Q?")
    assert d.route is TriageRoute.to_brain


async def test_timeout_falls_back_to_canned_ack_and_to_brain(monkeypatch):
    async def _hang(**kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(triage_service, "_call_triage", _hang)
    d = await _plane().triage(active_question="Q?", accumulated_answer="x",
                              last_spoken_question="Q?", budget_ms=50)
    assert d.route is TriageRoute.to_brain        # safe default — never wrongly skip the brain
    assert d.spoken_line                          # a canned ack filler


async def test_error_falls_back(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("triage down")
    monkeypatch.setattr(triage_service, "_call_triage", _boom)
    d = await _plane().triage(active_question="Q?", accumulated_answer="x",
                              last_spoken_question="Q?")
    assert d.route is TriageRoute.to_brain and d.spoken_line
