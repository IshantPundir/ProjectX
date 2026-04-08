"""Tests for the JD state machine — legal and illegal transition pure logic."""

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


def test_extracted_to_extracting_illegal():
    """Retrying a successfully extracted job is not allowed."""
    assert not is_legal_transition("signals_extracted", "signals_extracting")


def test_extracted_is_terminal_in_2a():
    assert LEGAL_TRANSITIONS["signals_extracted"] == set()


def test_unknown_from_state_is_illegal():
    assert not is_legal_transition("made_up_state", "signals_extracting")
