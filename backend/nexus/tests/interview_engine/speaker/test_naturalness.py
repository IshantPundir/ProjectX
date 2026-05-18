"""Unit tests for naturalness flag computations."""
from app.modules.interview_engine.speaker.naturalness import (
    detect_banned_phrases,
    detect_exceeded_soft_target,
    detect_name_overuse,
    detect_repeated_opener,
)


# ---------------- detect_repeated_opener ----------------

def test_repeated_opener_detected_when_first_words_match() -> None:
    assert detect_repeated_opener("Got it. Now —", ["Got it. Now"]) is True


def test_repeated_opener_case_insensitive() -> None:
    assert detect_repeated_opener("GOT IT. Now —", ["Got it. Now"]) is True


def test_repeated_opener_false_when_no_match() -> None:
    assert detect_repeated_opener(
        "See — kindly walk me", ["Got it. Now", "Mm OK"],
    ) is False


def test_repeated_opener_false_when_no_recent_starts() -> None:
    assert detect_repeated_opener("Got it. Now —", []) is False


def test_repeated_opener_false_on_empty_output() -> None:
    assert detect_repeated_opener("", ["Anything"]) is False


def test_repeated_opener_catches_consecutive_three_word_slug() -> None:
    """Session bf34128f turn 3: output 'Mm, OK — iPaaS means…' should
    flag against recent_reply_starts=['Mm, OK — an']. The 4-word
    comparison missed this (divergent 4th word); 3-word slug catches
    it — the candidate hears 'Mm, OK —' twice in a row."""
    assert detect_repeated_opener(
        "Mm, OK — iPaaS means integration platform as a service",
        ["Mm, OK — an"],
    ) is True


# ---------------- detect_banned_phrases ----------------

def test_banned_phrase_detected_case_insensitive() -> None:
    result = detect_banned_phrases("Great question. Let me delve into this.")
    assert "Great question" in result
    assert "delve" in result


def test_banned_phrase_empty_when_clean() -> None:
    assert detect_banned_phrases(
        "See — kindly walk me through your design.",
    ) == []


def test_banned_phrase_substring_match() -> None:
    # "leverage" appears inside "leveraged" (substring match on inflected form)
    result = detect_banned_phrases("We leveraged the cache effectively.")
    assert "leverage" in result


def test_banned_phrase_empty_input() -> None:
    assert detect_banned_phrases("") == []


# ---------------- detect_name_overuse ----------------

def test_name_overuse_true_when_in_both() -> None:
    assert detect_name_overuse(
        "Punar, what's next?",
        candidate_name="Punar",
        prior_output="Right, Punar — back to the design.",
    ) is True


def test_name_overuse_false_when_only_current() -> None:
    assert detect_name_overuse(
        "Punar, what's next?",
        candidate_name="Punar",
        prior_output="See — walk me through your approach.",
    ) is False


def test_name_overuse_false_when_no_name() -> None:
    assert detect_name_overuse(
        "Walk me through that.",
        candidate_name=None,
        prior_output="Anything",
    ) is False


def test_name_overuse_false_when_no_prior() -> None:
    assert detect_name_overuse(
        "Punar, what's next?",
        candidate_name="Punar",
        prior_output=None,
    ) is False


# ---------------- detect_exceeded_soft_target ----------------

def test_soft_target_exceeded_at_50_percent_over() -> None:
    # deliver_question soft cap = 25; 38 words = 52% over
    long_output = " ".join(["word"] * 38)
    assert detect_exceeded_soft_target(long_output, "deliver_question") is True


def test_soft_target_not_exceeded_below_threshold() -> None:
    # deliver_question soft cap = 25; 30 words = 20% over (under 50%)
    output = " ".join(["word"] * 30)
    assert detect_exceeded_soft_target(output, "deliver_question") is False


def test_soft_target_unknown_kind_returns_false() -> None:
    assert detect_exceeded_soft_target("any", "nonexistent_kind") is False


def test_soft_target_repeat_kind_never_flags() -> None:
    # repeat = verbatim replay, no cap
    long = " ".join(["word"] * 200)
    assert detect_exceeded_soft_target(long, "repeat") is False


def test_soft_target_empty_output_returns_false() -> None:
    assert detect_exceeded_soft_target("", "deliver_question") is False
