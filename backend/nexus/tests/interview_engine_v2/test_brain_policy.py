from app.modules.interview_engine_v2.brain.decision import (
    BrainDecision,
    BrainMove,
    CandidateIntent,
    CoverageDeltaItem,
)
from app.modules.interview_engine_v2.brain.policy import evaluate_policy


def _cov(**kw) -> list[CoverageDeltaItem]:
    return [CoverageDeltaItem(signal=s, state=st) for s, st in kw.items()]


def _d(**over):
    base = dict(reasoning="r", candidate_intent=CandidateIntent.answer, move=BrainMove.advance)
    base.update(over)
    return BrainDecision(**base)


def test_advance_passes_clean():
    res = evaluate_policy(_d(move=BrainMove.advance, grade="strong"))
    assert res.ok and res.effective_move is BrainMove.advance
    assert res.checks  # at least one gate recorded


def test_probe_without_a_gradeable_answer_downgrades_to_clarify():
    """fe3a5434 t-6: the candidate asked a clarifying question (grade=null) and the brain PROBED a
    HARDER question at a confused candidate, who then quit. A probe needs an answer to probe."""
    res = evaluate_policy(_d(move=BrainMove.probe, grade=None, target_signal="python"))
    assert res.effective_move is BrainMove.clarify
    assert "probe_without_answer" in res.violations


def test_knockout_on_or_group_without_checking_alternatives_is_downgraded():
    """The b99d8cc6 bug: never close on 'no Java' when the req was Java OR Python OR Ruby."""
    res = evaluate_policy(_d(
        move=BrainMove.knockout_close, is_knockout=True,
        or_alternatives=["java", "python", "ruby"],
        or_alternatives_checked=False, reflect_confirmed=True,
    ))
    assert not res.ok
    assert res.effective_move is BrainMove.probe  # downgraded to keep probing the alternatives
    assert "knockout_or_unverified" in res.violations


def test_knockout_without_reflect_confirm_is_downgraded():
    res = evaluate_policy(_d(
        move=BrainMove.knockout_close, is_knockout=True,
        or_alternatives=["java"], or_alternatives_checked=True, reflect_confirmed=False,
    ))
    assert not res.ok
    assert res.effective_move is BrainMove.confirm  # reflect-to-confirm before closing
    assert "knockout_unconfirmed" in res.violations


def test_verified_single_signal_knockout_passes():
    res = evaluate_policy(_d(
        move=BrainMove.knockout_close, is_knockout=True,
        or_alternatives=["java"], or_alternatives_checked=True, reflect_confirmed=True,
    ))
    assert res.ok and res.effective_move is BrainMove.knockout_close
    assert "knockout_or_verified" in res.checks


def test_verified_or_group_knockout_passes():
    res = evaluate_policy(_d(
        move=BrainMove.knockout_close, is_knockout=True,
        or_alternatives=["java", "python", "ruby"],
        or_alternatives_checked=True, reflect_confirmed=True,
    ))
    assert res.ok and res.effective_move is BrainMove.knockout_close


def test_incoherent_probe_after_strong_grade_is_downgraded_to_advance():
    """Coherence rule (doc 09 §2): never push for more when grade is strong/sufficient."""
    res = evaluate_policy(_d(
        move=BrainMove.probe, grade="strong",
        coverage_delta=_cov(python="sufficient"), target_signal="python",
    ))
    assert not res.ok
    assert res.effective_move is BrainMove.advance
    assert "incoherent_probe_on_sufficient" in res.violations


def test_no_leak_precheck_flags_rubric_in_composed_say():
    res = evaluate_policy(_d(
        move=BrainMove.clarify,
        composed_say="We're looking for strong Kafka here.",
    ))
    assert not res.ok
    assert "no_leak" in res.violations
    # downgraded: composed_say cleared, mouth composes from hint
    assert res.sanitized_say is None


def test_clean_composed_say_passes_no_leak():
    res = evaluate_policy(_d(
        move=BrainMove.clarify,
        composed_say="Sure — have you set up Kafka yourself?",
    ))
    assert res.ok and "no_leak_ok" in res.checks
    assert res.sanitized_say == "Sure — have you set up Kafka yourself?"


def test_incoherent_probe_on_sufficient_coverage_fires_even_without_strong_grade():
    # the `sufficient`-only branch of Gate 2 (grade is NOT "strong" but target signal is sufficient)
    res = evaluate_policy(_d(
        move=BrainMove.probe, grade="concrete",
        coverage_delta=_cov(python="sufficient"), target_signal="python",
    ))
    assert not res.ok
    assert res.effective_move is BrainMove.advance
    assert "incoherent_probe_on_sufficient" in res.violations


def test_coherent_probe_on_thin_grade_passes():
    # the pass side of Gate 2: probe on a thin/partial signal is coherent
    res = evaluate_policy(_d(
        move=BrainMove.probe, grade="thin",
        coverage_delta=_cov(python="partial"), target_signal="python",
    ))
    assert res.ok
    assert "coherent_probe" in res.checks


def test_single_signal_knockout_checked_flag_irrelevant_when_confirmed():
    # contract lock: a single-signal knockout passes when reflect_confirmed,
    # regardless of or_alternatives_checked (the checked flag only gates multi-signal OR groups)
    res = evaluate_policy(_d(
        move=BrainMove.knockout_close, is_knockout=True,
        or_alternatives=["java"], or_alternatives_checked=False, reflect_confirmed=True,
    ))
    assert res.ok
    assert res.effective_move is BrainMove.knockout_close
    assert "knockout_or_verified" in res.checks


def _adv(setup):
    return BrainDecision(reasoning="r", candidate_intent=CandidateIntent.answer,
                         move=BrainMove.advance, bank_question_id="q3", spoken_setup=setup)


def test_benign_spoken_setup_is_preserved():
    res = evaluate_policy(_adv("Say tickets arrive from a system like Jira."))
    assert res.sanitized_setup == "Say tickets arrive from a system like Jira."
    assert "setup_leak" not in res.violations


def test_leaky_spoken_setup_is_dropped():
    res = evaluate_policy(_adv("The rubric wants idempotency and retries."))
    assert res.sanitized_setup is None
    assert "setup_leak" in res.violations


def test_none_spoken_setup_is_fine():
    res = evaluate_policy(_adv(None))
    assert res.sanitized_setup is None
    assert "setup_leak" not in res.violations
