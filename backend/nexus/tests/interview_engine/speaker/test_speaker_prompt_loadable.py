"""Smoke test: every per-action Speaker prompt composes via PromptLoader."""
from app.ai.prompts import prompt_loader
from app.modules.interview_engine.models.speaker import InstructionKind


def test_all_per_action_speaker_prompts_load():
    """Every InstructionKind that goes through the Speaker LLM must have
    a corresponding per-action body file. The `repeat` kind is bypassed
    (cached delivery in the orchestrator) so it has no body file."""
    SPEAKER_LLM_KINDS = [
        InstructionKind.deliver_first_question,
        InstructionKind.deliver_question,
        InstructionKind.deliver_probe,
        InstructionKind.clarify,
        InstructionKind.redirect,
        InstructionKind.acknowledge_no_experience,
        InstructionKind.polite_close,
        InstructionKind.push_back,
    ]
    for kind in SPEAKER_LLM_KINDS:
        body = prompt_loader.load_pair(
            "engine/speaker/_preamble",
            f"engine/speaker/{kind.value}",
        )
        assert "OUTPUT DISCIPLINE" in body, f"preamble missing for {kind.value}"
        assert "TASK" in body, f"task statement missing for {kind.value}"
        assert "EXAMPLES" in body or "EXAMPLE" in body, f"examples missing for {kind.value}"
        assert len(body) > 200, f"body suspiciously short for {kind.value}"


def test_push_back_scaffold_documents_all_reason_codes():
    """push_back.txt must document all four reason_code shapes — they're
    the only way the Speaker knows what to ask for."""
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/push_back",
    )
    for code in (
        "vague_answer",
        "deflection",
        "missing_specifics",
        "unanswered_subquestion",
    ):
        assert code in body, f"push_back.txt missing reason_code shape for {code!r}"


def test_push_back_scaffold_anti_repetition_rule():
    """push_back fires repeatedly on stalling candidates; the scaffold
    must call out anti-repetition explicitly to avoid sounding robotic."""
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/push_back",
    )
    assert "ANTI-REPETITION" in body or "anti-repetition" in body.lower()


def test_push_back_scaffold_forbids_meta_disclosure():
    """The scaffold must explicitly forbid the Speaker from revealing
    that we're scoring or pushing back — that breaks the interview
    illusion."""
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/push_back",
    ).lower()
    assert "scoring" in body or "looking for" in body  # mentioned as forbidden phrasing


# ---------------------------------------------------------------------------
# Phase 9.3 — opener anti-repetition wired into redirect + push_back scaffolds
# ---------------------------------------------------------------------------


def test_redirect_scaffold_consumes_recent_agent_openers():
    """redirect.txt must explain how to use recent_agent_openers to vary
    the opener across consecutive redirects (the robotic-loop fix)."""
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/redirect",
    )
    assert "recent_agent_openers" in body, (
        "redirect.txt must reference the input field name verbatim"
    )


def test_push_back_scaffold_consumes_recent_agent_openers():
    """push_back.txt must also reference recent_agent_openers for
    anti-repetition across consecutive push_backs on the same question."""
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/push_back",
    )
    assert "recent_agent_openers" in body


def test_deliver_question_scaffold_documents_post_cap_advance_segue():
    """deliver_question.txt must include a branch for is_post_cap_advance
    that uses a soft topic-shift segue instead of the standard
    'Got it' acknowledgement."""
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/deliver_question",
    )
    assert "is_post_cap_advance" in body, (
        "deliver_question.txt must reference the SpeakerInput flag name"
    )
    body_lower = body.lower()
    assert "topic" in body_lower or "moving on" in body_lower or "switch" in body_lower


def test_polite_close_scaffold_handles_knockout_disclosure():
    """polite_close.txt must distinguish clean completion (failed_signal_value
    null) from knockout close (failed_signal_value populated) and acknowledge
    the disclosure in the latter case — without quoting the rubric label."""
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/polite_close",
    )
    assert "failed_signal_value" in body, (
        "polite_close.txt must reference the SpeakerInput field"
    )
    body_lower = body.lower()
    # The two-branch structure must be explicit.
    assert "branch a" in body_lower and "branch b" in body_lower, (
        "polite_close.txt must document both branches (clean vs knockout)"
    )
    # Anti-leak: must call out NEVER quoting the failed_signal_value.
    assert "never quote" in body_lower or "without naming" in body_lower or "anti-leak" in body_lower


def test_repeat_has_no_body_file():
    """`repeat` is handled deterministically by the orchestrator (cached
    delivery, bypassing the Speaker LLM). Asserting absence prevents a
    future contributor from creating a redundant repeat.txt."""
    import pathlib
    from app.ai.prompts import PROMPTS_ROOT
    repeat_path = PROMPTS_ROOT / "v1" / "engine" / "speaker" / "repeat.txt"
    assert not repeat_path.exists()


def test_clarify_handles_generic_confusion_via_rephrase():
    """When candidate is generically confused (no specific term in their
    utterance), the clarify scaffold must rephrase the whole question, NOT
    pick a rubric term and explain it. Phase 2 anti-leak guard."""
    from app.ai.prompts import prompt_loader
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/clarify",
    )
    # Path B (generic confusion) must be explicit.
    assert "rephrase" in body.lower() or "rephrasing" in body.lower(), \
        "clarify.txt must instruct rephrasing for generic confusion"
    # The legacy unconditional pick-a-term phrase must be gone.
    assert "Pick the ONE most relevant term to explain" not in body, \
        "clarify.txt's old pick-a-term-on-your-own logic was the rubric-leak vector"


def test_deliver_probe_default_no_recap():
    """The probe scaffold must default to no-recap (just ask the next
    question). Echoing the candidate's prior utterance must be the
    EXCEPTION (only when there's a specific terminology hook), not the
    default."""
    from app.ai.prompts import prompt_loader
    body = prompt_loader.load_pair(
        "engine/speaker/_preamble",
        "engine/speaker/deliver_probe",
    )
    body_lower = body.lower()
    # The new prompt must explicitly say "default" + "no recap" or equivalent.
    assert "default" in body_lower
    # Recap/echo must be conditional, not unconditional.
    assert "rare" in body_lower or "exception" in body_lower or "only" in body_lower, \
        "deliver_probe.txt must mark echo as a rare/exception path, not the default"


def test_preamble_documents_pre_spoken_opener():
    """Phase 9.8 — preamble must teach the Speaker to NOT emit its own
    opener when pre_spoken_opener is set, and to compose continuation
    content."""
    body = prompt_loader.get("engine/speaker/_preamble")
    assert "pre_spoken_opener" in body, (
        "Preamble must reference the SpeakerInput field name verbatim"
    )
    body_lower = body.lower()
    assert "pre-spoken opener" in body_lower or "pre-spoken-opener" in body_lower
    # Load-bearing instruction: do NOT include another opener.
    assert (
        "do not include another opener" in body_lower
        or "do not include any opener" in body_lower
    )
