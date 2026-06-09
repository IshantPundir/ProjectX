from unittest.mock import AsyncMock, patch

import pytest

from app.modules.interview_runtime.evidence import (
    EvidenceNote, EvidenceStance, EvidenceTexture, TimeSpan,
)
from app.modules.reporting.scoring.question_grade import question_base_level


def _note(seq, stance, texture, retracts=None):
    return EvidenceNote(
        seq=seq, turn_ref=f"t{seq}", signal="s", stance=stance, texture=texture,
        quote="x", span=TimeSpan(start_ms=0, end_ms=1),
        from_question_id="q1", via_probe=False, retracts_seq=retracts,
    )


def test_base_level_best_supporting_texture():
    notes = [_note(1, EvidenceStance.supports, EvidenceTexture.thin),
             _note(2, EvidenceStance.supports, EvidenceTexture.strong)]
    assert question_base_level(notes) == "strong"


def test_base_level_concrete_maps_solid():
    notes = [_note(1, EvidenceStance.supports, EvidenceTexture.concrete)]
    assert question_base_level(notes) == "solid"


def test_base_level_unretracted_contradiction_is_absent():
    notes = [_note(1, EvidenceStance.contradicts, EvidenceTexture.concrete)]
    assert question_base_level(notes) == "absent"


def test_base_level_no_notes_is_not_reached():
    assert question_base_level([]) == "not_reached"


@pytest.mark.asyncio
async def test_grade_question_refusal_keeps_base_level():
    from app.modules.reporting.scoring.question_grade import grade_question
    q = {"id": "q1", "text": "Tell me about an Intune change.",
         "rubric": {"excellent": "…", "meets_bar": "…", "below_bar": "…"},
         "positive_evidence": ["names the artifact"], "red_flags": ["only 'we'"],
         "evaluation_hint": "h", "question_kind": "behavioral", "difficulty": "medium"}
    fake = AsyncMock()
    fake.responses.parse = AsyncMock(return_value=type("R", (), {"output_parsed": None})())
    with patch("app.modules.reporting.scoring.question_grade.get_raw_openai_client",
               return_value=fake):
        out = await grade_question(question=q, notes=[], probes_used=0, probes_available=3,
                                   base_level="thin", correlation_id="cid")
    assert out.level == "thin"          # falls back to the engine base on refusal
    assert out.overridden is False
