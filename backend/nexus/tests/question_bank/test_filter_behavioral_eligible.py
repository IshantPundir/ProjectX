"""Unit tests for the behavioral-eligible signal filter.

Engine-v2 M2 broadened `_filter_behavioral_eligible`: in addition to knockout
experience/behavioral CLAIMS, it now also returns behavioral-TYPE required signals
(the STAR candidates — collaboration, documentation, mentoring, communication)
even when they are NOT knockouts. Competency/credential signals stay excluded.
"""
import pytest
from app.modules.question_bank.actors import _filter_behavioral_eligible


def _sig(
    value: str,
    *,
    knockout: bool = False,
    type: str = "experience",
    priority: str = "required",
) -> dict:
    """Helper to construct a minimal signal dict for testing."""
    return {
        "value": value,
        "type": type,
        "knockout": knockout,
        "priority": priority,
        "weight": 3,
        "stage": "screen",
    }


def test_filter_includes_experience_knockouts():
    signals = [_sig("8+ years integration", knockout=True, type="experience")]
    result = _filter_behavioral_eligible(signals)
    assert len(result) == 1
    assert result[0]["value"] == "8+ years integration"


def test_filter_includes_behavioral_knockouts():
    signals = [_sig("Production support ownership", knockout=True, type="behavioral")]
    result = _filter_behavioral_eligible(signals)
    assert len(result) == 1


def test_filter_includes_behavioral_required_non_knockout():
    """BROADENED CONTRACT: a behavioral-TYPE required signal that is NOT a knockout
    is now a true STAR candidate and must be INCLUDED (it was excluded before)."""
    signals = [
        _sig(
            "Cross-functional collaboration",
            knockout=False,
            type="behavioral",
            priority="required",
        )
    ]
    result = _filter_behavioral_eligible(signals)
    assert len(result) == 1
    assert result[0]["value"] == "Cross-functional collaboration"


def test_filter_excludes_non_knockout_experience():
    """A non-knockout EXPERIENCE signal is still excluded (not a STAR candidate)."""
    signals = [_sig("Nice-to-have skill", knockout=False, type="experience")]
    result = _filter_behavioral_eligible(signals)
    assert result == []


def test_filter_excludes_behavioral_non_required():
    """A behavioral-type signal that is NOT required (e.g. preferred) is excluded —
    only required behavioral-type signals warrant a STAR question."""
    signals = [
        _sig("Nice mentoring vibe", knockout=False, type="behavioral", priority="preferred")
    ]
    result = _filter_behavioral_eligible(signals)
    assert result == []


def test_filter_excludes_credential_knockouts():
    """Credential knockouts (degrees, certs) are verified by ATS, not voice."""
    signals = [_sig("BTech in CS", knockout=True, type="credential")]
    result = _filter_behavioral_eligible(signals)
    assert result == []


def test_filter_excludes_competency_knockouts():
    """Competency knockouts are depth-flavored; belong to technical."""
    signals = [_sig("REST API design", knockout=True, type="competency")]
    result = _filter_behavioral_eligible(signals)
    assert result == []


def test_filter_excludes_missing_knockout_flag():
    """An EXPERIENCE signal without explicit knockout=True is excluded."""
    signals = [{"value": "X", "type": "experience", "priority": "required"}]  # no knockout key
    result = _filter_behavioral_eligible(signals)
    assert result == []


def test_filter_dedups_by_value_order_preserving():
    """Duplicate values are deduped; first-seen order is preserved."""
    signals = [
        _sig("Mentoring", knockout=False, type="behavioral"),
        _sig("8+ years", knockout=True, type="experience"),
        _sig("Mentoring", knockout=True, type="behavioral"),  # dup value
    ]
    result = _filter_behavioral_eligible(signals)
    assert [s["value"] for s in result] == ["Mentoring", "8+ years"]


def test_filter_mixed():
    """Mixed signal list: experience/behavioral knockouts AND behavioral-type
    required non-knockouts pass; competency/credential are excluded."""
    signals = [
        _sig("8+ years integration", knockout=True, type="experience"),
        _sig("BTech degree", knockout=True, type="credential"),
        _sig("Customer-facing", knockout=True, type="behavioral"),
        _sig("Python proficiency", knockout=False, type="competency"),
        _sig("Documentation discipline", knockout=False, type="behavioral"),
    ]
    result = _filter_behavioral_eligible(signals)
    assert {s["value"] for s in result} == {
        "8+ years integration",
        "Customer-facing",
        "Documentation discipline",
    }


def test_behavioral_prompt_map_has_ai_screening():
    """The behavioral prompt map covers ai_screening at minimum."""
    from app.modules.question_bank.actors import STAGE_TYPE_TO_BEHAVIORAL_PROMPT
    assert "ai_screening" in STAGE_TYPE_TO_BEHAVIORAL_PROMPT
    assert (
        STAGE_TYPE_TO_BEHAVIORAL_PROMPT["ai_screening"]
        == "question_bank_ai_screening_behavioral"
    )


def test_technical_prompt_map_unchanged():
    """Existing technical_depth map is untouched (regression guard)."""
    from app.modules.question_bank.actors import STAGE_TYPE_TO_PROMPT
    assert STAGE_TYPE_TO_PROMPT["ai_screening"] == "question_bank_ai_screening"
    assert STAGE_TYPE_TO_PROMPT["phone_screen"] == "question_bank_phone_screen"
