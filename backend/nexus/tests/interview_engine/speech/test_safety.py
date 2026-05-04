"""Tests for the Speech Agent safety regex.

Covers:
- Empty / clean text passes.
- Every outcome word fires (case-insensitive, word-boundary).
- Salary number patterns fire across currency / suffix / bare-number forms.
- Scheduling / hiring-manager commitments fire.
- Multiple violations in one utterance are all reported.
- Sample wrap_normal.v1.txt's approved phrasings DO NOT trigger violations
  (template authoring is consistent with the safety check).
- Word-boundary discipline: "passing" / "passes" don't match the
  "passed" rule (only the exact form does — keeps the false-positive
  surface small for legitimate continuous-tense usage).
- Approved-phrase substring containment: "thanks" alone is fine; only
  "thanks for your interest" trips.
"""
from __future__ import annotations

from app.modules.interview_engine.speech import (
    SafetyResult,
    SafetyViolation,
    check_safety,
)

# ---------------------------------------------------------------------------
# Clean text
# ---------------------------------------------------------------------------


def test_empty_text_is_safe():
    result = check_safety("")
    assert isinstance(result, SafetyResult)
    assert result.is_safe is True
    assert result.violations == ()


def test_clean_intro_is_safe():
    text = (
        "Hi Charlie, I'll be running a short technical screen for the "
        "Senior Engineer role today. We'll be about 15 minutes. "
        "Take your time. Let's get started with the first one."
    )
    result = check_safety(text)
    assert result.is_safe, f"Expected safe, got: {result.violations!r}"


def test_clean_ask_question_is_safe():
    text = (
        "Got it — next one. Walk me through how you'd handle a slow database "
        "query in a high-traffic API."
    )
    assert check_safety(text).is_safe


def test_clean_wrap_normal_is_safe():
    """The two approved phrasings from the wrap_normal.v1 template must
    pass — proves template authoring is consistent with the safety check."""
    approved_a = (
        "I have what I needed for this stage. Thanks for taking the time "
        "today — the recruiting team will be in touch with next steps."
    )
    approved_b = (
        "That's everything from my side. Thanks for the conversation — the "
        "team will follow up with next steps soon."
    )
    assert check_safety(approved_a).is_safe
    assert check_safety(approved_b).is_safe


# ---------------------------------------------------------------------------
# Outcome words — every one fires
# ---------------------------------------------------------------------------


def _violation_categories(result: SafetyResult) -> list[str]:
    return [v.category for v in result.violations]


def _violation_names(result: SafetyResult) -> list[str]:
    return [v.pattern_name for v in result.violations]


def test_passed_fires_outcome_violation():
    result = check_safety("Great, you passed the first part.")
    assert result.is_safe is False
    assert "outcome" in _violation_categories(result)
    assert "outcome.passed" in _violation_names(result)


def test_failed_fires_outcome_violation():
    result = check_safety("That didn't quite land — you failed the test.")
    assert "outcome.failed" in _violation_names(result)


def test_rejected_fires_outcome_violation():
    result = check_safety("Your application was rejected for that role.")
    assert "outcome.rejected" in _violation_names(result)


def test_advanced_fires_outcome_violation():
    result = check_safety("You've advanced to the next stage.")
    assert "outcome.advanced" in _violation_names(result)


def test_unfortunately_fires_outcome_violation():
    result = check_safety("Unfortunately, that's the end of our session.")
    assert "outcome.unfortunately" in _violation_names(result)


def test_best_of_luck_fires_outcome_violation():
    result = check_safety("Best of luck with the rest of your search.")
    assert "outcome.best_of_luck" in _violation_names(result)


def test_thanks_for_your_interest_fires_outcome_violation():
    result = check_safety("Thanks for your interest in our company.")
    assert "outcome.thanks_for_interest" in _violation_names(result)


def test_outcome_words_case_insensitive():
    """`PASSED` / `Passed` / `passed` all fire."""
    for variant in ("PASSED", "Passed", "passed"):
        text = f"You {variant} that question."
        assert check_safety(text).is_safe is False, f"variant={variant!r}"


# ---------------------------------------------------------------------------
# Word-boundary discipline
# ---------------------------------------------------------------------------


def test_passes_does_not_fire_passed_rule():
    """Continuous tense / different word form — different rule, not present.
    `passes` / `passing` are not in the outcome list (only `passed` is)."""
    safe = check_safety("This passes through three layers of validation.")
    assert "outcome.passed" not in _violation_names(safe)


def test_failure_does_not_fire_failed_rule():
    """Noun form `failure` is not the verb form `failed`."""
    safe = check_safety("This is a graceful failure-handling pattern.")
    assert "outcome.failed" not in _violation_names(safe)


def test_thanks_alone_does_not_fire_thanks_for_interest_rule():
    """The full phrase `thanks for your interest` is the outcome rule;
    `thanks` alone is fine (used in wrap_normal approved phrasings)."""
    result = check_safety("Thanks for taking the time today.")
    assert "outcome.thanks_for_interest" not in _violation_names(result)


# ---------------------------------------------------------------------------
# Salary patterns
# ---------------------------------------------------------------------------


def test_salary_with_dollar_and_thousands_fires():
    result = check_safety("The role pays around $80,000 per year.")
    assert "salary" in _violation_categories(result)


def test_salary_with_pound_and_thousands_fires():
    result = check_safety("The salary band is £75,000 to £90,000.")
    assert "salary" in _violation_categories(result)


def test_salary_with_k_suffix_fires():
    result = check_safety("It's around $80k for this role.")
    assert "salary" in _violation_categories(result)


def test_salary_bare_number_with_currency_word_fires():
    result = check_safety("The base is 80,000 USD plus equity.")
    assert "salary" in _violation_categories(result)


def test_clean_phrase_about_compensation_does_not_fire():
    """Generic compensation-deflection language without a specific number
    must not trip the salary regex."""
    deflection = (
        "Good question — the recruiting team will cover that with you "
        "after this stage."
    )
    assert check_safety(deflection).is_safe


# ---------------------------------------------------------------------------
# Scheduling / hiring-manager commitments
# ---------------------------------------------------------------------------


def test_ill_schedule_fires_scheduling_violation():
    result = check_safety("I'll schedule the next interview for you.")
    assert "scheduling" in _violation_categories(result)


def test_well_schedule_fires_scheduling_violation():
    result = check_safety("We'll schedule a follow-up with the team.")
    assert "scheduling" in _violation_categories(result)


def test_hiring_manager_specific_promise_fires():
    result = check_safety("The hiring manager wants to meet next week.")
    assert "scheduling" in _violation_categories(result)


def test_next_round_specifics_fires():
    result = check_safety("The next round is scheduled for Monday.")
    assert "scheduling" in _violation_categories(result)


def test_neutral_recruiting_team_phrase_does_not_fire():
    """`the recruiting team will be in touch` — generic, no commitment.
    Must NOT trip the scheduling / hiring-manager rules."""
    text = "The recruiting team will be in touch with next steps."
    assert check_safety(text).is_safe


# ---------------------------------------------------------------------------
# Multiple violations in one utterance
# ---------------------------------------------------------------------------


def test_multiple_categories_all_reported():
    text = (
        "Unfortunately you failed this round — but the role pays $80,000 "
        "and I'll schedule a follow-up."
    )
    result = check_safety(text)
    cats = set(_violation_categories(result))
    # All three categories present
    assert cats == {"outcome", "salary", "scheduling"}
    assert len(result.violations) >= 4  # unfortunately + failed + salary + schedule


def test_each_pattern_fires_at_most_once_per_call():
    """Multiple occurrences of the same word are one violation, not many.
    Avoids over-reporting on a tongue-tied LLM output."""
    text = "You passed. You passed. You passed."
    result = check_safety(text)
    pattern_hits = [v for v in result.violations if v.pattern_name == "outcome.passed"]
    assert len(pattern_hits) == 1


# ---------------------------------------------------------------------------
# SafetyViolation surface
# ---------------------------------------------------------------------------


def test_violation_records_matched_text():
    result = check_safety("I'll schedule the follow-up.")
    sched = next(v for v in result.violations if v.category == "scheduling")
    assert sched.matched_text  # non-empty
    assert "schedule" in sched.matched_text.lower()


def test_violation_is_immutable():
    """Dataclass is frozen — accidental mutation in audit-logging code raises."""
    import dataclasses

    v = SafetyViolation(
        category="outcome", pattern_name="outcome.passed", matched_text="passed",
    )
    try:
        # frozen dataclass should raise on assignment
        v.matched_text = "modified"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("Expected FrozenInstanceError on mutation")
