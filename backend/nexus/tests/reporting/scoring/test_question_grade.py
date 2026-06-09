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
