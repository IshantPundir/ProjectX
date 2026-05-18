"""Unit tests for naturalness flag computations."""
from app.modules.interview_engine.speaker.naturalness import (
    detect_banned_phrases,
    detect_exceeded_soft_target,
    detect_name_overuse,
    detect_repeated_opener,
    detect_solution_leak,
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


# -------------------- soft target tuple keys --------------------


def test_clarify_concept_explanation_soft_target_is_fifty() -> None:
    # 75 words ≤ 50 * 1.5 → no flag
    output_75 = " ".join(["word"] * 75)
    assert detect_exceeded_soft_target(
        output_75, "clarify", clarify_kind="concept_explanation",
    ) is False
    # 76 words > 50 * 1.5 → flag
    output_76 = " ".join(["word"] * 76)
    assert detect_exceeded_soft_target(
        output_76, "clarify", clarify_kind="concept_explanation",
    ) is True


def test_clarify_term_definition_soft_target_is_twenty_five() -> None:
    output_37 = " ".join(["word"] * 37)  # 25 * 1.5 = 37.5
    assert detect_exceeded_soft_target(
        output_37, "clarify", clarify_kind="term_definition",
    ) is False
    output_38 = " ".join(["word"] * 38)
    assert detect_exceeded_soft_target(
        output_38, "clarify", clarify_kind="term_definition",
    ) is True


def test_clarify_with_none_clarify_kind_falls_back_to_legacy() -> None:
    # Legacy fallback: (clarify, None) -> 35 words. 53 words > 35 * 1.5 = 52.5 triggers.
    output_53 = " ".join(["word"] * 53)
    assert detect_exceeded_soft_target(
        output_53, "clarify", clarify_kind=None,
    ) is True
    output_52 = " ".join(["word"] * 52)
    assert detect_exceeded_soft_target(
        output_52, "clarify", clarify_kind=None,
    ) is False


def test_non_clarify_kind_unaffected_by_clarify_kind_param() -> None:
    # deliver_question target = 25 words; clarify_kind irrelevant
    output_38 = " ".join(["word"] * 38)
    assert detect_exceeded_soft_target(
        output_38, "deliver_question", clarify_kind=None,
    ) is True
    output_37 = " ".join(["word"] * 37)
    assert detect_exceeded_soft_target(
        output_37, "deliver_question", clarify_kind="anything",
    ) is False


# -------------------- detect_solution_leak --------------------


def test_solution_leak_fires_on_use_verb_last_sentence() -> None:
    output = (
        "See — retries can re-fire after partial success, so you'd "
        "want to use an idempotency key on the order ID."
    )
    assert detect_solution_leak(output, clarify_kind="concept_explanation") is True


def test_solution_leak_fires_on_implement_verb() -> None:
    output = "Mm — duplicates can happen. Implement a conditional upsert."
    assert detect_solution_leak(output, clarify_kind="concept_explanation") is True


def test_solution_leak_does_not_fire_on_correct_open_ended_question() -> None:
    output = (
        "See — your trigger fires once per new order ID, but the i-Paa-S "
        "workflow itself can retry after a network blip. The retry now "
        "fires the same order ID into the E-R-P again. So — given that, "
        "what would you put in your design to handle it?"
    )
    assert detect_solution_leak(output, clarify_kind="concept_explanation") is False


def test_solution_leak_only_fires_for_concept_explanation_kind() -> None:
    leaky_output = "See — use an idempotency key on the order ID."
    for kind in ("term_definition", "use_case_anchor", "broad_rephrase",
                 "probe_context", None):
        assert detect_solution_leak(leaky_output, clarify_kind=kind) is False
    assert detect_solution_leak(leaky_output, clarify_kind="concept_explanation") is True


def test_solution_leak_inspects_only_last_sentence() -> None:
    # Same verbs in middle sentences are fine.
    output = (
        "See — at-least-once delivery can use the same event twice. "
        "But what would you check first to confirm the duplicates?"
    )
    assert detect_solution_leak(output, clarify_kind="concept_explanation") is False
