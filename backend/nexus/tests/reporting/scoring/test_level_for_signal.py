from app.modules.interview_runtime.evidence import EvidenceNote, TimeSpan
from app.modules.reporting.scoring.aggregate import level_for_signal


def _note(signal, stance, texture, seq=1, retracts=None):
    return EvidenceNote(
        seq=seq, turn_ref=f"t-{seq}", signal=signal, stance=stance, texture=texture,
        quote="x", span=TimeSpan(start_ms=0, end_ms=1), from_question_id="q1",
        via_probe=False, retracts_seq=retracts,
    )


def test_strong_from_best_texture():
    notes = [_note("s", "supports", "thin", 1), _note("s", "supports", "strong", 2)]
    assert level_for_signal(notes, provenance="asked_directly", closure="satisfied") == "strong"


def test_solid_from_concrete():
    notes = [_note("s", "supports", "concrete", 1)]
    assert level_for_signal(notes, provenance="asked_directly", closure="satisfied") == "solid"


def test_thin_only():
    notes = [_note("s", "supports", "thin", 1)]
    assert level_for_signal(notes, provenance="asked_directly", closure="tapped_out") == "thin"


def test_probed_absent_is_absent():
    assert level_for_signal([], provenance="probed_absent", closure="absent") == "absent"


def test_not_reached_is_not_reached():
    assert level_for_signal([], provenance="not_reached", closure=None) == "not_reached"


def test_truncated_with_no_support_is_not_reached_even_if_asked():
    assert level_for_signal([], provenance="not_reached", closure="truncated") == "not_reached"


def test_unretracted_contradiction_is_absent():
    notes = [_note("s", "contradicts", "concrete", 1)]
    assert level_for_signal(notes, provenance="probed_absent", closure="absent") == "absent"


def test_retracted_contradiction_does_not_force_absent():
    notes = [_note("s", "supports", "concrete", 1)]
    assert level_for_signal(notes, provenance="asked_directly", closure="satisfied") == "solid"
