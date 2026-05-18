"""Unit tests for opener slug + filter helpers."""
from app.modules.interview_engine.speaker.openers import (
    filter_available_openers,
    opener_slug,
)


# ---------------- opener_slug ----------------


def test_opener_slug_truncates_to_three_words_lowercase() -> None:
    assert opener_slug("Mm, OK — kindly walk me") == "mm, ok —"
    assert opener_slug("See — kindly walk me through") == "see — kindly"
    assert opener_slug("Right, so — for this case") == "right, so —"


def test_opener_slug_handles_whitespace_and_punctuation() -> None:
    assert opener_slug("   See   —   walk  ") == "see — walk"
    assert opener_slug("") == ""
    assert opener_slug("   ") == ""


def test_opener_slug_under_three_words_returns_full() -> None:
    assert opener_slug("See") == "see"
    assert opener_slug("See —") == "see —"


# ---------------- filter_available_openers ----------------


_ROTATION: tuple[str, ...] = (
    "See —",
    "Right, so —",
    "Mm, OK —",
    "Let me put it this way —",
    "Thanks for that. Now —",
    "Got it. Let's —",
    "Fair enough —",
    "I see —",
    "Hmm —",
)


def test_filter_empty_recent_returns_full_rotation() -> None:
    assert filter_available_openers(_ROTATION, []) == list(_ROTATION)


def test_filter_removes_used_openers_by_slug() -> None:
    used = ["Mm, OK — kindly walk", "See —"]
    fresh = filter_available_openers(_ROTATION, used)
    assert "Mm, OK —" not in fresh
    assert "See —" not in fresh
    assert "Right, so —" in fresh
    assert "Hmm —" in fresh


def test_filter_case_insensitive() -> None:
    used = ["mm, ok — walk me"]
    fresh = filter_available_openers(_ROTATION, used)
    assert "Mm, OK —" not in fresh


def test_filter_empty_safety_fallback_returns_full_rotation() -> None:
    # If every opener's 3-word slug matches a recent start, the safety
    # branch returns the full rotation rather than emitting an empty list.
    used = [opener_slug(op) for op in _ROTATION]
    fresh = filter_available_openers(_ROTATION, used)
    assert fresh == list(_ROTATION)


def test_filter_ignores_whitespace_only_recent_entries() -> None:
    used = ["   ", "", "Mm, OK — let's"]
    fresh = filter_available_openers(_ROTATION, used)
    assert "Mm, OK —" not in fresh
    # The whitespace entries did NOT cause anything else to be filtered.
    assert "See —" in fresh
