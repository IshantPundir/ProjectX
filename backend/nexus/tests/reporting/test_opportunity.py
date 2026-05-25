from app.modules.reporting.scoring.opportunity import classify
from app.modules.reporting.scoring.types import ScoredUnit


def _unit(**kw):
    base = dict(question_id="q", question_text="Q", candidate_answer="A",
                answer_start_ms=0, probes_fired=0, clarifies=0, word_count=0,
                candidate_engaged=True, question_kind=None)
    base.update(kw)
    return ScoredUnit(**base)

def test_full_when_probed():
    assert classify(_unit(probes_fired=1, word_count=3, candidate_engaged=True)) == "full"

def test_full_when_substantive_answer():
    assert classify(_unit(probes_fired=0, word_count=20, candidate_engaged=True)) == "full"

def test_partial_when_barely_engaged_no_probe():
    assert classify(_unit(probes_fired=0, word_count=3, candidate_engaged=True)) == "partial"

def test_none_when_not_engaged_and_no_probe():
    assert classify(_unit(probes_fired=0, word_count=2, candidate_engaged=False)) == "none"

def test_full_for_on_target_idk_when_probed():
    assert classify(_unit(candidate_answer="I don't know", probes_fired=1,
                          word_count=3, candidate_engaged=True)) == "full"


# ---------------------------------------------------------------------------
# Factual question kind fast-path (experience_check + compliance_binary)
# ---------------------------------------------------------------------------

def test_experience_check_short_answer_is_full_opportunity():
    """'More than sixteen years' (4 words, no probe) → full for experience_check.

    This is the motivating bug: the SUBSTANTIVE_WORD_FLOOR (8) must NOT apply
    to factual kinds because a concise factual answer IS a complete answer.
    """
    assert classify(_unit(
        question_kind="experience_check",
        word_count=4,
        probes_fired=0,
        candidate_engaged=True,
    )) == "full"


def test_compliance_binary_two_words_is_full_opportunity():
    """'Yes, compliant' (2 words, no probe) → full for compliance_binary."""
    assert classify(_unit(
        question_kind="compliance_binary",
        word_count=2,
        probes_fired=0,
        candidate_engaged=True,
    )) == "full"


def test_technical_scenario_short_answer_still_partial():
    """A technical_scenario question with 4 words and no probe stays partial.

    The factual fast-path must NOT fire for non-factual kinds; the
    SUBSTANTIVE_WORD_FLOOR still applies.
    """
    assert classify(_unit(
        question_kind="technical_scenario",
        word_count=4,
        probes_fired=0,
        candidate_engaged=True,
    )) == "partial"


def test_experience_check_not_engaged_is_not_full():
    """An experience_check answer where the candidate is not engaged (no_experience
    triage) must NOT hit the factual fast-path — falls through to 'none'."""
    assert classify(_unit(
        question_kind="experience_check",
        word_count=0,
        probes_fired=0,
        candidate_engaged=False,
    )) == "none"


def test_experience_check_zero_words_not_engaged_is_none():
    """experience_check with zero words AND not engaged → none (no fast-path)."""
    assert classify(_unit(
        question_kind="experience_check",
        word_count=0,
        probes_fired=0,
        candidate_engaged=False,
    )) == "none"
