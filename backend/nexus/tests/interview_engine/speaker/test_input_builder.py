from app.modules.interview_engine.models.speaker import InstructionKind
from app.modules.interview_engine.models.judge import (
    AdvancePayload, JudgeOutput, NextAction, ProbePayload, TurnMetadata,
    AcknowledgeNoExperiencePayload, RedirectPayload,
)
from app.modules.interview_engine.speaker.input_builder import build_speaker_input
from app.modules.interview_engine.state.claims import CandidateClaimsPool
from app.modules.interview_engine.state.queue import QuestionQueue
from app.modules.interview_runtime.schemas import QuestionConfig, QuestionRubric


def _q(text="Tell me about your work.", follow_ups=None):
    return QuestionConfig(
        id="q1", position=0, text=text, signal_values=["S1"], estimated_minutes=2.0,
        is_mandatory=True, follow_ups=follow_ups or [],
        positive_evidence=["EVIDENCE-A", "EVIDENCE-B", "EVIDENCE-C"],
        red_flags=["FLAG-A", "FLAG-B"],
        rubric=QuestionRubric(excellent="EX", meets_bar="MB", below_bar="BB"),
        evaluation_hint="HINT-CONTENT-VERY-SECRET",
        question_kind="technical_depth",
    )


def _judge(action, payload):
    return JudgeOutput(
        observations=[], candidate_claims=[],
        next_action=action, next_action_payload=payload,
        turn_metadata=TurnMetadata(),
    )


def test_speaker_input_does_not_leak_positive_evidence():
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=_judge(NextAction.advance, AdvancePayload(target_question_id="q1")),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance=None,
    )
    serialized = s.model_dump_json()
    for forbidden in ("EVIDENCE-A", "EVIDENCE-B", "EVIDENCE-C", "FLAG-A", "FLAG-B",
                      "EX", "MB", "BB", "HINT-CONTENT-VERY-SECRET"):
        assert forbidden not in serialized, f"{forbidden} leaked into Speaker input"


def test_probe_input_carries_correct_followup_text():
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": ["FU-0", "FU-1"]}],
    )
    queue.advance_to("q1", at_turn=0)
    queue.apply_probe(probe_id="1", at_turn=1)
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_probe,
        judge_output=_judge(NextAction.probe, ProbePayload(probe_id="1")),
        active_question=_q(follow_ups=["FU-0", "FU-1"]),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="answer",
    )
    assert s.bank_text == "FU-1"


def test_speaker_input_carries_candidate_name():
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_first_question,
        judge_output=_judge(NextAction.advance, AdvancePayload(target_question_id="q1")),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance=None,
        candidate_name="Alice",
    )
    assert s.candidate_name == "Alice"
    assert s.persona_name == "Sam"
    # Anti-leak guarantee still holds.
    serialized = s.model_dump_json()
    for forbidden in ("EVIDENCE-A", "EVIDENCE-B", "EVIDENCE-C", "FLAG-A", "FLAG-B"):
        assert forbidden not in serialized


def test_redirect_kind_carries_turn_metadata_only():
    """Task 8: For instruction_kind=redirect, build_speaker_input copies
    JudgeOutput.turn_metadata into SpeakerInput.turn_metadata. The Speaker
    needs both bank_text (to restate the active question) AND turn_metadata
    (to pick tone) for the redirect scaffold.
    """
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    judge_out = JudgeOutput(
        observations=[], candidate_claims=[],
        next_action=NextAction.redirect,
        next_action_payload=RedirectPayload(),
        turn_metadata=TurnMetadata(
            candidate_social_or_greeting=True, candidate_off_topic=True,
        ),
    )
    s = build_speaker_input(
        instruction_kind=InstructionKind.redirect,
        judge_output=judge_out,
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=10),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="Hi",
        candidate_name="Ishant",
    )
    assert s.turn_metadata is not None
    assert s.turn_metadata.candidate_social_or_greeting is True
    assert s.turn_metadata.candidate_off_topic is True
    # Anti-leak still holds — the rubric must NOT leak through the redirect path.
    serialized = s.model_dump_json()
    for forbidden in ("EVIDENCE-A", "EVIDENCE-B", "EVIDENCE-C", "FLAG-A", "FLAG-B",
                      "EX", "MB", "BB", "HINT-CONTENT-VERY-SECRET"):
        assert forbidden not in serialized, f"{forbidden} leaked into Speaker input"


def test_non_redirect_kind_has_no_turn_metadata():
    """Task 8: deliver_question (or any non-redirect kind) returns
    SpeakerInput with turn_metadata=None. Avoids tone-leak across
    scaffolds (a deliver_question Speaker call shouldn't see whether
    the candidate was off-topic on a previous turn)."""
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    judge_out = JudgeOutput(
        observations=[], candidate_claims=[],
        next_action=NextAction.advance,
        next_action_payload=AdvancePayload(target_question_id="q1"),
        # turn_metadata set, but should be ignored for non-redirect kinds.
        turn_metadata=TurnMetadata(candidate_off_topic=True),
    )
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=judge_out,
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=10),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="answer",
        candidate_name="Ishant",
    )
    assert s.turn_metadata is None


def test_acknowledge_no_experience_carries_failed_signal():
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    s = build_speaker_input(
        instruction_kind=InstructionKind.acknowledge_no_experience,
        judge_output=_judge(
            NextAction.acknowledge_no_experience,
            AcknowledgeNoExperiencePayload(failed_signal_value="JQL"),
        ),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="never used JQL",
    )
    assert s.failed_signal_value == "JQL"
    assert s.bank_text is None


def test_non_contextual_kinds_omit_recent_turns_and_claims():
    """For redirect / repeat / acknowledge_no_experience / polite_close, the
    Speaker input drops recent_turns and claims_pool_snapshot — the prompt
    payload only needs bank_text + last_candidate_utterance + flags + names
    to compose a short scaffolded utterance. Anti-regression for the
    speaker-side prompt-bloat fix that followed session 1f02f55d's
    redirect-heavy test run."""
    from app.modules.interview_engine.models.judge import (
        ClarifyPayload, PoliteClosePayload, RepeatPayload,
    )
    from app.modules.interview_runtime import TranscriptEntry
    # Judge-emitted ClaimEntry (no capture metadata) — pool.add() canonicalizes.
    from app.modules.interview_engine.models.judge import ClaimEntry as JClaim

    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)

    long_history = [
        TranscriptEntry(role="candidate", text=f"line {i}", timestamp_ms=i, question_id="q1")
        for i in range(20)
    ]
    pool = CandidateClaimsPool(max_size=10)
    pool.add(
        JClaim(claim_topic="t", claim_text="c", source_quote="q"),
        captured_at_turn=1, captured_at_seq=1,
    )

    non_contextual = [
        (InstructionKind.redirect, NextAction.redirect, RedirectPayload()),
        (InstructionKind.acknowledge_no_experience,
         NextAction.acknowledge_no_experience,
         AcknowledgeNoExperiencePayload(failed_signal_value="x")),
        (InstructionKind.polite_close, NextAction.polite_close, PoliteClosePayload()),
        (InstructionKind.repeat, NextAction.repeat, RepeatPayload()),
    ]
    for kind, action, payload in non_contextual:
        s = build_speaker_input(
            instruction_kind=kind,
            judge_output=_judge(action, payload),
            active_question=_q(),
            queue=queue,
            claims_pool=pool,
            recent_turns=long_history,
            persona_name="Sam",
            last_candidate_utterance="x",
        )
        assert s.recent_turns == [], f"{kind.value}: recent_turns must be empty"
        assert s.claims_pool_snapshot == [], (
            f"{kind.value}: claims_pool_snapshot must be empty"
        )
        # The fields the Speaker DOES still need are preserved.
        assert s.persona_name == "Sam"
        assert s.last_candidate_utterance == "x"


def test_contextual_kinds_keep_recent_turns_and_claims():
    """Probe / clarify / deliver_first_question / deliver_question carry
    the conversation history because they continue an answer thread."""
    from app.modules.interview_runtime import TranscriptEntry
    # Judge-emitted ClaimEntry (no capture metadata) — pool.add() canonicalizes.
    from app.modules.interview_engine.models.judge import ClaimEntry as JClaim
    from app.modules.interview_engine.models.judge import ClarifyPayload

    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": ["FU-0"]}],
    )
    queue.advance_to("q1", at_turn=0)

    history = [
        TranscriptEntry(role="agent", text="agent line", timestamp_ms=0, question_id="q1"),
        TranscriptEntry(role="candidate", text="cand line", timestamp_ms=1, question_id="q1"),
    ]
    pool = CandidateClaimsPool(max_size=10)
    pool.add(
        JClaim(claim_topic="t", claim_text="c", source_quote="q"),
        captured_at_turn=1, captured_at_seq=1,
    )

    contextual = [
        (InstructionKind.deliver_question, NextAction.advance,
         AdvancePayload(target_question_id="q1")),
        (InstructionKind.clarify, NextAction.clarify, ClarifyPayload()),
        (InstructionKind.deliver_probe, NextAction.probe, ProbePayload(probe_id="0")),
    ]
    for kind, action, payload in contextual:
        if kind == InstructionKind.deliver_probe:
            queue.apply_probe(probe_id="0", at_turn=1)
        s = build_speaker_input(
            instruction_kind=kind,
            judge_output=_judge(action, payload),
            active_question=_q(follow_ups=["FU-0"]),
            queue=queue,
            claims_pool=pool,
            recent_turns=history,
            persona_name="Sam",
            last_candidate_utterance="x",
        )
        assert len(s.recent_turns) == 2, f"{kind.value}: recent_turns must be carried"
        assert len(s.claims_pool_snapshot) == 1, (
            f"{kind.value}: claims_pool_snapshot must be carried"
        )


# ---------------------------------------------------------------------------
# Phase 9.2 — push_back instruction routing
# ---------------------------------------------------------------------------


def test_push_back_routes_reason_code_through():
    """When the Judge emits push_back, the SpeakerInput must carry the
    reason_code so the Speaker scaffold can pick the right per-reason
    template."""
    from app.modules.interview_engine.models.judge import PushBackPayload

    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)

    s = build_speaker_input(
        instruction_kind=InstructionKind.push_back,
        judge_output=JudgeOutput(
            observations=[],
            candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="deflection"),
            turn_metadata=TurnMetadata(),
        ),
        active_question=_q(text="What was your role in the upgrade?"),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="Not my responsibility but I helped.",
    )
    assert s.instruction_kind == InstructionKind.push_back
    assert s.push_back_reason_code == "deflection"
    # bank_text carried so the Speaker can reference the question abstractly.
    assert s.bank_text == "What was your role in the upgrade?"


def test_push_back_drops_recent_turns_and_claims_pool():
    """push_back joins _NON_CONTEXTUAL_KINDS — the Speaker only needs the
    candidate's last utterance + bank_text to compose the short scaffolded
    push. Carrying the transcript would inflate the prompt 500-1500 tok
    with no quality benefit."""
    from app.modules.interview_engine.models.judge import (
        ClaimEntry as JClaim, PushBackPayload,
    )
    from app.modules.interview_runtime import TranscriptEntry

    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)

    history = [
        TranscriptEntry(role="agent", text="agent line", timestamp_ms=0, question_id="q1"),
        TranscriptEntry(role="candidate", text="cand line", timestamp_ms=1, question_id="q1"),
    ]
    pool = CandidateClaimsPool(max_size=10)
    pool.add(
        JClaim(claim_topic="t", claim_text="c", source_quote="q"),
        captured_at_turn=1, captured_at_seq=1,
    )

    s = build_speaker_input(
        instruction_kind=InstructionKind.push_back,
        judge_output=JudgeOutput(
            observations=[], candidate_claims=[],
            next_action=NextAction.push_back,
            next_action_payload=PushBackPayload(reason_code="vague_answer"),
            turn_metadata=TurnMetadata(),
        ),
        active_question=_q(),
        queue=queue,
        claims_pool=pool,
        recent_turns=history,
        persona_name="Sam",
        last_candidate_utterance="validation checks",
    )
    assert s.recent_turns == [], "push_back is non-contextual; recent_turns must be empty"
    assert s.claims_pool_snapshot == [], (
        "push_back is non-contextual; claims pool snapshot must be empty"
    )


def test_non_push_back_kinds_have_null_reason_code():
    """push_back_reason_code must stay None for every non-push_back kind
    so the Speaker scaffolds for those kinds don't accidentally key off
    a stale reason_code from a previous turn's payload."""
    from app.modules.interview_engine.models.judge import ClarifyPayload

    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": ["FU-0"]}],
    )
    queue.advance_to("q1", at_turn=0)

    cases = [
        (InstructionKind.deliver_question, NextAction.advance,
         AdvancePayload(target_question_id="q1")),
        (InstructionKind.clarify, NextAction.clarify, ClarifyPayload()),
        (InstructionKind.redirect, NextAction.redirect, RedirectPayload()),
    ]
    for kind, action, payload in cases:
        s = build_speaker_input(
            instruction_kind=kind,
            judge_output=_judge(action, payload),
            active_question=_q(follow_ups=["FU-0"]),
            queue=queue,
            claims_pool=CandidateClaimsPool(max_size=50),
            recent_turns=[],
            persona_name="Sam",
            last_candidate_utterance="x",
        )
        assert s.push_back_reason_code is None, (
            f"{kind.value}: push_back_reason_code must be None"
        )


# ---------------------------------------------------------------------------
# recent_reply_starts — anti-repetition signal routing
# ---------------------------------------------------------------------------


def test_recent_reply_starts_threaded_for_non_contextual_kinds():
    """Non-contextual kinds (redirect / push_back / acknowledge_no_experience
    / polite_close) drop recent_turns to save tokens; in exchange they
    receive recent_reply_starts so the Speaker can vary its reply
    opening across consecutive same-kind turns."""
    from app.modules.interview_engine.models.judge import (
        PoliteClosePayload, PushBackPayload,
    )
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)
    starts = ["I hear you,", "Got it, Ishant", "Sure, let's"]

    cases = [
        (InstructionKind.redirect, NextAction.redirect, RedirectPayload()),
        (InstructionKind.push_back, NextAction.push_back,
         PushBackPayload(reason_code="vague_answer")),
        (InstructionKind.polite_close, NextAction.polite_close, PoliteClosePayload()),
    ]
    for kind, action, payload in cases:
        s = build_speaker_input(
            instruction_kind=kind,
            judge_output=_judge(action, payload),
            active_question=_q(),
            queue=queue,
            claims_pool=CandidateClaimsPool(max_size=50),
            recent_turns=[],
            persona_name="Sam",
            last_candidate_utterance="x",
            recent_reply_starts=starts,
        )
        assert s.recent_reply_starts == starts, (
            f"{kind.value}: recent_reply_starts must be threaded through "
            "for non-contextual kinds"
        )


def test_recent_reply_starts_dropped_for_contextual_kinds():
    """Contextual kinds (deliver_*, clarify, deliver_probe) already see
    recent_turns; threading reply-start slugs there is redundant prompt
    bloat."""
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": ["FU-0"]}],
    )
    queue.advance_to("q1", at_turn=0)

    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=_judge(NextAction.advance, AdvancePayload(target_question_id="q1")),
        active_question=_q(follow_ups=["FU-0"]),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="x",
        recent_reply_starts=["Should not appear"],
    )
    assert s.recent_reply_starts == [], (
        "Contextual kind must NOT carry recent_reply_starts"
    )


# ---------------------------------------------------------------------------
# Phase 9.3 — is_post_cap_advance (Q-2) routing
# ---------------------------------------------------------------------------


def test_is_post_cap_advance_threaded_only_for_deliver_question():
    """The cap-forced-advance segue only makes sense for deliver_question.
    Other kinds must NOT carry the flag even if the State Engine
    accidentally passed it."""
    from app.modules.interview_engine.models.judge import ClarifyPayload, PoliteClosePayload
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": ["FU-0"]}],
    )
    queue.advance_to("q1", at_turn=0)

    # deliver_question: flag honored.
    s = build_speaker_input(
        instruction_kind=InstructionKind.deliver_question,
        judge_output=_judge(NextAction.advance, AdvancePayload(target_question_id="q1")),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="x",
        is_post_cap_advance=True,
    )
    assert s.is_post_cap_advance is True

    # deliver_first_question: flag suppressed (different scaffold).
    s2 = build_speaker_input(
        instruction_kind=InstructionKind.deliver_first_question,
        judge_output=_judge(NextAction.advance, AdvancePayload(target_question_id="q1")),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="x",
        is_post_cap_advance=True,
    )
    assert s2.is_post_cap_advance is False

    # clarify: flag suppressed.
    s3 = build_speaker_input(
        instruction_kind=InstructionKind.clarify,
        judge_output=_judge(NextAction.clarify, ClarifyPayload()),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="x",
        is_post_cap_advance=True,
    )
    assert s3.is_post_cap_advance is False


# ---------------------------------------------------------------------------
# Phase 9.3 — closing_disclosure_signal (Q-3) routing
# ---------------------------------------------------------------------------


def test_polite_close_threads_failed_signal_value_from_disclosure():
    """When the State Engine signals a knockout-induced polite_close, the
    failed_signal_value field is populated so the Speaker scaffold can
    acknowledge the no-experience disclosure."""
    from app.modules.interview_engine.models.judge import PoliteClosePayload
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)

    s = build_speaker_input(
        instruction_kind=InstructionKind.polite_close,
        judge_output=_judge(NextAction.polite_close, PoliteClosePayload()),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="I don't have any experience with that.",
        closing_disclosure_signal="5+ years hands-on Jira admin",
    )
    assert s.failed_signal_value == "5+ years hands-on Jira admin"


def test_polite_close_clean_completion_has_no_failed_signal():
    """Default close (no knockout) leaves failed_signal_value=None so
    polite_close.txt picks the clean-completion branch."""
    from app.modules.interview_engine.models.judge import PoliteClosePayload
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)

    s = build_speaker_input(
        instruction_kind=InstructionKind.polite_close,
        judge_output=_judge(NextAction.polite_close, PoliteClosePayload()),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="That covers everything.",
    )
    assert s.failed_signal_value is None


def test_closing_disclosure_signal_ignored_for_non_polite_close_kinds():
    """The closing_disclosure_signal kwarg only takes effect for
    polite_close. Other kinds (e.g. acknowledge_no_experience) have their
    own failed_signal_value extraction path."""
    from app.modules.interview_engine.models.judge import ClarifyPayload
    queue = QuestionQueue.from_initial(
        questions=[{"question_id": "q1", "is_mandatory": True, "follow_ups": []}],
    )
    queue.advance_to("q1", at_turn=0)

    s = build_speaker_input(
        instruction_kind=InstructionKind.clarify,
        judge_output=_judge(NextAction.clarify, ClarifyPayload()),
        active_question=_q(),
        queue=queue,
        claims_pool=CandidateClaimsPool(max_size=50),
        recent_turns=[],
        persona_name="Sam",
        last_candidate_utterance="x",
        closing_disclosure_signal="should-be-ignored",
    )
    assert s.failed_signal_value is None
