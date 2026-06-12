"""Tests for the JD state machine — legal and illegal transition pure logic."""

import pytest

from app.modules.jd.state_machine import LEGAL_TRANSITIONS, is_legal_transition


def test_draft_to_signals_extracting_legal():
    assert is_legal_transition("draft", "signals_extracting")


def test_signals_extracting_to_extracted_legal():
    assert is_legal_transition("signals_extracting", "signals_extracted")


def test_signals_extracting_to_failed_legal():
    assert is_legal_transition("signals_extracting", "signals_extraction_failed")


def test_failed_to_extracting_retry_legal():
    assert is_legal_transition("signals_extraction_failed", "signals_extracting")


def test_draft_to_extracted_illegal():
    assert not is_legal_transition("draft", "signals_extracted")


def test_extracted_to_extracting_legal():
    """Re-extraction from signals_extracted is now allowed (unlock & re-run)."""
    assert is_legal_transition("signals_extracted", "signals_extracting")


def test_extracted_to_confirmed_legal():
    """Phase 2B: recruiter can confirm signals from extracted state."""
    assert is_legal_transition("signals_extracted", "signals_confirmed")


def test_confirmed_to_extracted_legal():
    """Phase 2B: editing chips after confirming auto-clears back to extracted."""
    assert is_legal_transition("signals_confirmed", "signals_extracted")


def test_confirmed_to_extracting_legal():
    """Re-triggering extraction from a confirmed job is now allowed (unlock & re-run)."""
    assert is_legal_transition("signals_confirmed", "signals_extracting")


def test_unknown_from_state_is_illegal():
    assert not is_legal_transition("made_up_state", "signals_extracting")


def test_signals_confirmed_to_pipeline_built_legal():
    from app.modules.jd.state_machine import is_legal_transition
    assert is_legal_transition("signals_confirmed", "pipeline_built") is True


def test_pipeline_built_to_active_legal():
    from app.modules.jd.state_machine import is_legal_transition
    assert is_legal_transition("pipeline_built", "active") is True


def test_active_can_re_extract():
    """Active jobs can be unlocked and re-run through signal extraction."""
    assert LEGAL_TRANSITIONS["active"] == {"signals_extracting"}


def test_archived_has_no_outbound_transitions():
    from app.modules.jd.state_machine import LEGAL_TRANSITIONS
    assert LEGAL_TRANSITIONS["archived"] == set()


def test_pipeline_built_back_to_signals_confirmed_illegal():
    # Pipeline-built does not transition back to signals_confirmed in this design.
    from app.modules.jd.state_machine import is_legal_transition
    assert is_legal_transition("pipeline_built", "signals_confirmed") is False


@pytest.mark.parametrize("src", ["signals_extracted", "signals_confirmed", "pipeline_built", "active"])
def test_reextract_allowed_from_locked_states(src):
    assert is_legal_transition(src, "signals_extracting") is True


def test_reextract_not_allowed_from_archived():
    assert is_legal_transition("archived", "signals_extracting") is False


def test_existing_transitions_unchanged():
    assert is_legal_transition("signals_confirmed", "pipeline_built") is True
    assert is_legal_transition("pipeline_built", "active") is True
    assert is_legal_transition("draft", "signals_extracting") is True
    assert is_legal_transition("signals_confirmed", "signals_extracted") is True
