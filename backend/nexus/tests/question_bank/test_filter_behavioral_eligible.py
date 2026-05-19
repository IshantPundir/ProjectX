"""Unit tests for the behavioral-eligible signal filter."""
import pytest
from app.modules.question_bank.actors import _filter_behavioral_eligible


def _sig(value: str, *, knockout: bool = False, type: str = "experience") -> dict:
    """Helper to construct a minimal signal dict for testing."""
    return {
        "value": value,
        "type": type,
        "knockout": knockout,
        "priority": "required",
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


def test_filter_excludes_non_knockouts():
    signals = [_sig("Nice-to-have skill", knockout=False, type="experience")]
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
    """Signal without explicit knockout=True is excluded."""
    signals = [{"value": "X", "type": "experience"}]  # no knockout key
    result = _filter_behavioral_eligible(signals)
    assert result == []


def test_filter_mixed():
    """Mixed signal list: only experience/behavioral knockouts pass."""
    signals = [
        _sig("8+ years integration", knockout=True, type="experience"),
        _sig("BTech degree", knockout=True, type="credential"),
        _sig("Customer-facing", knockout=True, type="behavioral"),
        _sig("Python proficiency", knockout=False, type="competency"),
    ]
    result = _filter_behavioral_eligible(signals)
    assert {s["value"] for s in result} == {
        "8+ years integration",
        "Customer-facing",
    }
