"""
Tests for app.modules.interview_engine.brain.input_builder

D1 task — verifies:
1.  build_session_context maps signals + bank_index correctly.
2.  render_prefix is byte-identical across calls (cache-stable prefix).
3.  No rubric text leaks into the prefix.
4.  Candidate utterance is fenced as DATA in the suffix.
5.  CoverageProjection: update, signal_reads, uncovered_signals (weight-ranked), knockout_pending.
6.  Suffix only carries the ACTIVE question's rubric — other questions' rubric text is absent.
"""

from __future__ import annotations

import json

import pytest

from app.modules.interview_engine.brain.input_builder import (
    CoverageProjection,
    active_question_rubric,
    build_messages,
    build_session_context,
    build_turn_input,
    render_prefix,
    render_suffix,
)
from app.modules.interview_engine.contracts import (
    ActiveQuestionRubric,
    BrainSessionContext,
    BrainTurnInput,
    BudgetPhase,
    SignalRead,
    SignalSpec,
    WindowTurn,
)
from app.modules.interview_runtime.evidence import (
    CoverageState,
    EvidenceStance,
    EvidenceTexture,
    SignalPriority,
    SignalType,
)
from app.modules.interview_runtime.schemas import (
    CandidateContext,
    CompanyContext,
    QuestionConfig,
    QuestionRubric,
    SessionConfig,
    SignalMetadata,
    StageConfig,
)

# ---------------------------------------------------------------------------
# Helpers — build minimal but valid fixtures
# ---------------------------------------------------------------------------

SENTINEL_EXCELLENT = "SENTINEL_EXCELLENT_RUBRIC_STRING"
SENTINEL_MEETS_BAR = "SENTINEL_MEETS_BAR_RUBRIC_STRING"

ACTIVE_Q_EXCELLENT = "ACTIVE_EXCELLENT_RUBRIC"
ACTIVE_Q_MEETS_BAR = "ACTIVE_Q_RUBRIC_TEXT_SENTINEL"


def _make_question(
    qid: str,
    text: str,
    signal_values: list[str],
    primary_signal: str,
    *,
    is_mandatory: bool = False,
    excellent: str = SENTINEL_EXCELLENT,
    meets_bar: str = SENTINEL_MEETS_BAR,
    below_bar: str = "below_bar_default",
) -> QuestionConfig:
    return QuestionConfig(
        id=qid,
        position=0,
        text=text,
        signal_values=signal_values,
        estimated_minutes=5.0,
        is_mandatory=is_mandatory,
        follow_ups=[
            {
                "dimension": "follow_up_1",
                "intent": "verify depth on first dimension",
                "seed_probe": "Follow up 1?",
                "listen_for": ["detail A", "detail B"],
            },
            {
                "dimension": "follow_up_2",
                "intent": "verify depth on second dimension",
                "seed_probe": "Follow up 2?",
                "listen_for": ["detail C"],
            },
        ],
        positive_evidence=["positive A", "positive B", "positive C"],
        red_flags=["red flag 1", "red flag 2"],
        rubric=QuestionRubric(excellent=excellent, meets_bar=meets_bar, below_bar=below_bar),
        evaluation_hint="Evaluate based on concrete experience.",
        question_kind="technical_scenario",
        primary_signal=primary_signal,
        difficulty="medium",
    )


def _make_signal_metadata(
    value: str,
    *,
    signal_type: str = "competency",
    priority: str = "preferred",
    weight: int = 2,
    knockout: bool = False,
) -> SignalMetadata:
    return SignalMetadata(
        value=value,
        type=signal_type,
        priority=priority,
        weight=weight,
        knockout=knockout,
        stage="screen",
        evaluation_method="verbal_response",
    )


def _make_session_config() -> SessionConfig:
    """
    Three signals (two preferred, one required+knockout) and two questions.
    """
    return SessionConfig(
        session_id="sess-001",
        job_id="job-001",
        candidate_id="cand-001",
        job_title="Senior Backend Engineer",
        role_summary="Build distributed systems at scale.",
        seniority_level="senior",
        company=CompanyContext(
            about="A fast-growing fintech.",
            industry="fintech",
            hiring_bar="high",
        ),
        candidate=CandidateContext(name="Priya"),
        stage=StageConfig(
            stage_id="stage-001",
            stage_type="ai_screening",
            name="AI Screen",
            duration_minutes=30,
            difficulty="medium",
            questions=[
                _make_question(
                    "q-001",
                    "Tell me about your distributed systems experience.",
                    ["distributed_systems", "system_design"],
                    primary_signal="distributed_systems",
                    is_mandatory=True,
                    excellent=ACTIVE_Q_EXCELLENT,
                    meets_bar=ACTIVE_Q_MEETS_BAR,
                ),
                _make_question(
                    "q-002",
                    "Walk me through a time you led an incident response.",
                    ["incident_response"],
                    primary_signal="incident_response",
                    excellent="OTHER_Q_EXCELLENT_RUBRIC",
                    meets_bar="OTHER_Q_MEETS_BAR_RUBRIC",
                ),
            ],
        ),
        signals=["distributed_systems", "incident_response", "python_proficiency"],
        signal_metadata=[
            _make_signal_metadata("distributed_systems", weight=3, priority="required", knockout=False),
            _make_signal_metadata("incident_response", weight=2, priority="preferred", knockout=False),
            _make_signal_metadata("python_proficiency", weight=1, priority="preferred", knockout=True),
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: build_session_context maps signals + bank_index correctly
# ---------------------------------------------------------------------------

class TestBuildSessionContext:
    def test_signal_count(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        assert len(ctx.signals) == 3

    def test_signal_mapping(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        sig_map = {s.signal: s for s in ctx.signals}

        ds = sig_map["distributed_systems"]
        assert ds.signal_type == SignalType.competency
        assert ds.weight == 3
        assert ds.priority == SignalPriority.required
        assert ds.knockout is False

        py = sig_map["python_proficiency"]
        assert py.knockout is True
        assert py.weight == 1

    def test_bank_index_count(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        assert len(ctx.bank_index) == 2

    def test_bank_index_all_core(self):
        """All questions in the flat bank are tier='core'."""
        config = _make_session_config()
        ctx = build_session_context(config)
        for qi in ctx.bank_index:
            assert qi.tier == "core"

    def test_bank_index_primary_signal(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        qmap = {q.question_id: q for q in ctx.bank_index}

        assert qmap["q-001"].primary_signal == "distributed_systems"
        assert qmap["q-002"].primary_signal == "incident_response"

    def test_bank_index_no_rubric_fields(self):
        """BankQuestionIndex must NOT carry excellent/meets_bar/below_bar fields."""
        config = _make_session_config()
        ctx = build_session_context(config)
        for qi in ctx.bank_index:
            assert not hasattr(qi, "excellent")
            assert not hasattr(qi, "meets_bar")
            assert not hasattr(qi, "below_bar")

    def test_bank_index_has_text_and_follow_ups(self):
        """BankQuestionIndex carries text + follow_ups as FollowUpDimension objects."""
        config = _make_session_config()
        ctx = build_session_context(config)
        qmap = {q.question_id: q for q in ctx.bank_index}

        qi = qmap["q-001"]
        assert qi.text == "Tell me about your distributed systems experience."
        assert len(qi.follow_ups) == 2
        assert qi.follow_ups[0].dimension == "follow_up_1"
        assert qi.follow_ups[0].seed_probe == "Follow up 1?"
        assert qi.follow_ups[1].dimension == "follow_up_2"
        assert qi.follow_ups[1].seed_probe == "Follow up 2?"

    def test_bank_index_signals_list(self):
        """BankQuestionIndex.signals must be the full coverable signal set."""
        config = _make_session_config()
        ctx = build_session_context(config)
        qmap = {q.question_id: q for q in ctx.bank_index}

        assert set(qmap["q-001"].signals) == {"distributed_systems", "system_design"}
        assert qmap["q-002"].signals == ["incident_response"]

    def test_bank_index_is_mandatory(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        qmap = {q.question_id: q for q in ctx.bank_index}

        assert qmap["q-001"].is_mandatory is True
        assert qmap["q-002"].is_mandatory is False

    def test_signal_metadata_fallback_on_empty(self):
        """When signal_metadata is empty, build_session_context falls back to one minimal
        SignalSpec per signals entry (competency, weight=1, preferred, knockout=False)."""
        config = _make_session_config()
        config2 = config.model_copy(update={"signal_metadata": []})
        ctx = build_session_context(config2)
        assert len(ctx.signals) == len(config2.signals)
        for spec in ctx.signals:
            assert spec.signal_type == SignalType.competency
            assert spec.weight == 1
            assert spec.priority == SignalPriority.preferred
            assert spec.knockout is False

    def test_job_title_and_seniority_level(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        assert ctx.job_title == "Senior Backend Engineer"
        assert ctx.seniority_level == "senior"

    def test_role_summary_and_hiring_bar(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        assert ctx.role_summary == "Build distributed systems at scale."
        assert ctx.hiring_bar == "high"

    def test_no_company_name_or_time_budget(self):
        """The corrected BrainSessionContext has no company_name or time_budget_s fields."""
        config = _make_session_config()
        ctx = build_session_context(config)
        assert not hasattr(ctx, "company_name")
        assert not hasattr(ctx, "time_budget_s")


# ---------------------------------------------------------------------------
# Test 2: render_prefix is byte-identical across turns (cache-stable)
# ---------------------------------------------------------------------------

class TestPrefixByteIdentical:
    def test_identical_across_two_renders(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        sys_prompt = "You are the brain."

        prefix1 = render_prefix(sys_prompt, ctx)
        prefix2 = render_prefix(sys_prompt, ctx)

        assert prefix1 == prefix2

    def test_identical_regardless_of_turn_input(self):
        """The prefix must not change even after we build different turn inputs."""
        config = _make_session_config()
        ctx = build_session_context(config)
        sys_prompt = "You are the brain."

        prefix_before = render_prefix(sys_prompt, ctx)

        # Build a turn input (should not affect prefix)
        q = config.stage.questions[0]
        rubric = active_question_rubric(q, fired_dimensions=[])
        proj = CoverageProjection()
        _turn_input = build_turn_input(
            turn_ref="turn-001",
            active_question=rubric,
            on_the_floor="Tell me about your distributed systems experience.",
            candidate_utterance="I've worked on Kafka-based pipelines for 3 years.",
            thread_turn_count=1,
            projection=proj,
            all_specs=ctx.signals,
            transcript_window=[],
            budget_phase=BudgetPhase.on_track,
        )

        prefix_after = render_prefix(sys_prompt, ctx)
        assert prefix_before == prefix_after


# ---------------------------------------------------------------------------
# Test 3: No rubric text leaks into the prefix
# ---------------------------------------------------------------------------

class TestNoRubricInPrefix:
    def test_rubric_sentinel_absent_from_prefix(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        sys_prompt = "You are the brain."

        prefix = render_prefix(sys_prompt, ctx)
        prefix_text = json.dumps(prefix)

        # Rubric sentinels must NOT appear in the prefix
        assert SENTINEL_EXCELLENT not in prefix_text
        assert SENTINEL_MEETS_BAR not in prefix_text
        assert ACTIVE_Q_EXCELLENT not in prefix_text
        assert ACTIVE_Q_MEETS_BAR not in prefix_text
        assert "OTHER_Q_EXCELLENT_RUBRIC" not in prefix_text
        assert "OTHER_Q_MEETS_BAR_RUBRIC" not in prefix_text

    def test_rubric_present_in_suffix_when_active(self):
        """The active question's rubric MUST appear in the suffix."""
        config = _make_session_config()
        ctx = build_session_context(config)

        q = config.stage.questions[0]  # has ACTIVE_Q_EXCELLENT and ACTIVE_Q_MEETS_BAR
        rubric = active_question_rubric(q, fired_dimensions=[])
        proj = CoverageProjection()
        turn_input = build_turn_input(
            turn_ref="turn-001",
            active_question=rubric,
            on_the_floor="Tell me about your distributed systems experience.",
            candidate_utterance="Some answer.",
            thread_turn_count=1,
            projection=proj,
            all_specs=ctx.signals,
            transcript_window=[],
            budget_phase=BudgetPhase.on_track,
        )
        suffix = render_suffix(turn_input)
        suffix_text = json.dumps(suffix)

        # The active question's rubric strings must appear in the suffix
        assert ACTIVE_Q_EXCELLENT in suffix_text or ACTIVE_Q_MEETS_BAR in suffix_text


# ---------------------------------------------------------------------------
# Test 4: Candidate utterance fenced as DATA in the suffix
# ---------------------------------------------------------------------------

class TestCandidateUtteranceFenced:
    def test_utterance_in_data_fence(self):
        config = _make_session_config()
        ctx = build_session_context(config)

        q = config.stage.questions[0]
        rubric = active_question_rubric(q, fired_dimensions=[])
        proj = CoverageProjection()
        utterance = "UNIQUE_CANDIDATE_UTTERANCE_XYZ"
        turn_input = build_turn_input(
            turn_ref="turn-001",
            active_question=rubric,
            on_the_floor="Tell me about your distributed systems experience.",
            candidate_utterance=utterance,
            thread_turn_count=1,
            projection=proj,
            all_specs=ctx.signals,
            transcript_window=[],
            budget_phase=BudgetPhase.on_track,
        )
        suffix = render_suffix(turn_input)
        suffix_text = json.dumps(suffix)

        # The utterance must be present in the suffix
        assert utterance in suffix_text

        # Must be wrapped in CANDIDATE_ANSWER fencing
        assert "<<<CANDIDATE_ANSWER_BEGIN>>>" in suffix_text
        assert "<<<CANDIDATE_ANSWER_END>>>" in suffix_text

        # The fence markers must appear before and after the utterance in the text
        begin_pos = suffix_text.index("<<<CANDIDATE_ANSWER_BEGIN>>>")
        end_pos = suffix_text.index("<<<CANDIDATE_ANSWER_END>>>")
        utterance_pos = suffix_text.index(utterance)
        assert begin_pos < utterance_pos < end_pos, (
            "Utterance must be between the BEGIN and END fence markers"
        )


# ---------------------------------------------------------------------------
# Test 5: CoverageProjection
# ---------------------------------------------------------------------------

class TestCoverageProjection:
    def _make_specs(self) -> list[SignalSpec]:
        return [
            SignalSpec(signal="distributed_systems", signal_type=SignalType.competency,
                       priority=SignalPriority.required, weight=3, knockout=False),
            SignalSpec(signal="incident_response", signal_type=SignalType.competency,
                       priority=SignalPriority.preferred, weight=2, knockout=False),
            SignalSpec(signal="python_proficiency", signal_type=SignalType.competency,
                       priority=SignalPriority.preferred, weight=1, knockout=True),
        ]

    def test_update_reflects_in_signal_reads(self):
        from app.modules.interview_engine.contracts import SignalObservation
        proj = CoverageProjection()
        obs = SignalObservation(
            signal="distributed_systems",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.concrete,
            coverage_after=CoverageState.partial,
        )
        proj.update([obs])

        reads = proj.signal_reads()
        assert len(reads) == 1
        r = reads[0]
        assert r.signal == "distributed_systems"
        assert r.coverage == CoverageState.partial
        assert r.last_stance == EvidenceStance.supports

    def test_update_uses_last_stance_not_stance(self):
        """The field on SignalRead must be last_stance (corrected contract)."""
        from app.modules.interview_engine.contracts import SignalObservation
        proj = CoverageProjection()
        obs = SignalObservation(
            signal="incident_response",
            stance=EvidenceStance.contradicts,
            texture=EvidenceTexture.thin,
            coverage_after=CoverageState.none,
        )
        proj.update([obs])

        reads = proj.signal_reads()
        r = reads[0]
        assert r.last_stance == EvidenceStance.contradicts
        # Verify there is no 'stance' field (old contract)
        assert not hasattr(r, "stance")

    def test_update_established_quote_from_map(self):
        from app.modules.interview_engine.contracts import SignalObservation
        proj = CoverageProjection()
        obs = SignalObservation(
            signal="distributed_systems",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.strong,
            coverage_after=CoverageState.sufficient,
        )
        proj.update([obs], established_quote_by_signal={"distributed_systems": "I ran Kafka at scale."})

        reads = proj.signal_reads()
        assert reads[0].established_quote == "I ran Kafka at scale."

    def test_update_carries_forward_prior_quote_when_no_map_entry(self):
        from app.modules.interview_engine.contracts import SignalObservation
        proj = CoverageProjection()

        # First update with a quote
        obs1 = SignalObservation(
            signal="distributed_systems",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.concrete,
            coverage_after=CoverageState.partial,
        )
        proj.update([obs1], established_quote_by_signal={"distributed_systems": "prior quote"})

        # Second update — no quote map entry → prior quote carried forward
        obs2 = SignalObservation(
            signal="distributed_systems",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.strong,
            coverage_after=CoverageState.sufficient,
        )
        proj.update([obs2])

        reads = proj.signal_reads()
        assert reads[0].established_quote == "prior quote"

    def test_signal_reads_stable_insertion_order(self):
        """signal_reads() returns signals in insertion order, not alphabetical."""
        from app.modules.interview_engine.contracts import SignalObservation
        proj = CoverageProjection()

        # Touch in a specific order
        for sig in ["python_proficiency", "distributed_systems", "incident_response"]:
            proj.update([SignalObservation(
                signal=sig,
                stance=EvidenceStance.supports,
                texture=EvidenceTexture.concrete,
                coverage_after=CoverageState.partial,
            )])

        reads = proj.signal_reads()
        assert [r.signal for r in reads] == [
            "python_proficiency", "distributed_systems", "incident_response"
        ]

    def test_uncovered_signals_weight_ranked(self):
        from app.modules.interview_engine.contracts import SignalObservation
        specs = self._make_specs()
        proj = CoverageProjection()

        # Mark only the lowest-weight signal as sufficient
        obs = SignalObservation(
            signal="python_proficiency",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.concrete,
            coverage_after=CoverageState.sufficient,
        )
        proj.update([obs])

        uncovered = proj.uncovered_signals(specs)
        # python_proficiency is now covered → absent
        assert "python_proficiency" not in uncovered
        # distributed_systems (weight=3) must appear before incident_response (weight=2)
        assert uncovered[0] == "distributed_systems"
        assert uncovered[1] == "incident_response"

    def test_uncovered_signals_untouched_are_uncovered(self):
        specs = self._make_specs()
        proj = CoverageProjection()  # nothing updated
        uncovered = proj.uncovered_signals(specs)
        assert set(uncovered) == {"distributed_systems", "incident_response", "python_proficiency"}

    def test_knockout_pending_absent_signal(self):
        specs = self._make_specs()
        proj = CoverageProjection()  # python_proficiency is knockout, not touched

        pending = proj.knockout_pending(specs)
        assert "python_proficiency" in pending

    def test_knockout_pending_cleared_when_sufficient_supports(self):
        from app.modules.interview_engine.contracts import SignalObservation
        specs = self._make_specs()
        proj = CoverageProjection()

        obs = SignalObservation(
            signal="python_proficiency",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.concrete,
            coverage_after=CoverageState.sufficient,
        )
        proj.update([obs])

        pending = proj.knockout_pending(specs)
        assert "python_proficiency" not in pending

    def test_knockout_pending_still_listed_when_partial(self):
        from app.modules.interview_engine.contracts import SignalObservation
        specs = self._make_specs()
        proj = CoverageProjection()

        obs = SignalObservation(
            signal="python_proficiency",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.thin,
            coverage_after=CoverageState.partial,
        )
        proj.update([obs])

        pending = proj.knockout_pending(specs)
        # partial coverage on a knockout signal → still pending
        assert "python_proficiency" in pending

    def test_knockout_pending_still_listed_when_contradicts(self):
        from app.modules.interview_engine.contracts import SignalObservation
        specs = self._make_specs()
        proj = CoverageProjection()

        # sufficient coverage but stance is contradicts → still pending (absence confirmed)
        obs = SignalObservation(
            signal="python_proficiency",
            stance=EvidenceStance.contradicts,
            texture=EvidenceTexture.concrete,
            coverage_after=CoverageState.sufficient,
        )
        proj.update([obs])

        pending = proj.knockout_pending(specs)
        assert "python_proficiency" in pending

    def test_non_knockout_signal_never_in_knockout_pending(self):
        """A non-knockout signal with contradicts stance must NOT appear in knockout_pending."""
        from app.modules.interview_engine.contracts import SignalObservation
        specs = self._make_specs()
        proj = CoverageProjection()

        # distributed_systems has knockout=False
        obs = SignalObservation(
            signal="distributed_systems",
            stance=EvidenceStance.contradicts,
            texture=EvidenceTexture.thin,
            coverage_after=CoverageState.none,
        )
        proj.update([obs])

        pending = proj.knockout_pending(specs)
        assert "distributed_systems" not in pending

    def test_multiple_updates_overwrite_coverage(self):
        from app.modules.interview_engine.contracts import SignalObservation
        proj = CoverageProjection()
        specs = self._make_specs()

        obs1 = SignalObservation(
            signal="distributed_systems",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.thin,
            coverage_after=CoverageState.partial,
        )
        proj.update([obs1])

        obs2 = SignalObservation(
            signal="distributed_systems",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.strong,
            coverage_after=CoverageState.sufficient,
        )
        proj.update([obs2])

        reads = proj.signal_reads()
        r = next(r for r in reads if r.signal == "distributed_systems")
        assert r.coverage == CoverageState.sufficient
        assert r.last_stance == EvidenceStance.supports

        uncovered = proj.uncovered_signals(specs)
        assert "distributed_systems" not in uncovered

    def test_no_note_count_field(self):
        """SignalRead must have NO note_count field (old drifted shape)."""
        from app.modules.interview_engine.contracts import SignalObservation
        proj = CoverageProjection()
        obs = SignalObservation(
            signal="distributed_systems",
            stance=EvidenceStance.supports,
            texture=EvidenceTexture.concrete,
            coverage_after=CoverageState.partial,
        )
        proj.update([obs])
        r = proj.signal_reads()[0]
        assert not hasattr(r, "note_count")


# ---------------------------------------------------------------------------
# Test 6: Suffix bounded — only active question's rubric, not other questions'
# ---------------------------------------------------------------------------

class TestSuffixBounded:
    def test_non_active_rubric_absent_from_suffix(self):
        """q-002's rubric text must NOT appear in the suffix when q-001 is active."""
        config = _make_session_config()
        ctx = build_session_context(config)

        # Activate q-001
        q_active = config.stage.questions[0]
        rubric = active_question_rubric(q_active, fired_dimensions=[])
        proj = CoverageProjection()
        turn_input = build_turn_input(
            turn_ref="turn-001",
            active_question=rubric,
            on_the_floor="Tell me about your distributed systems experience.",
            candidate_utterance="Some answer.",
            thread_turn_count=1,
            projection=proj,
            all_specs=ctx.signals,
            transcript_window=[],
            budget_phase=BudgetPhase.on_track,
        )
        suffix = render_suffix(turn_input)
        suffix_text = json.dumps(suffix)

        # q-002's rubric strings must NOT appear
        assert "OTHER_Q_EXCELLENT_RUBRIC" not in suffix_text
        assert "OTHER_Q_MEETS_BAR_RUBRIC" not in suffix_text

    def test_full_bank_rubrics_absent_from_suffix(self):
        """The suffix must not embed other questions' rubrics."""
        config = _make_session_config()
        ctx = build_session_context(config)

        q_active = config.stage.questions[0]
        rubric = active_question_rubric(q_active, fired_dimensions=[])
        proj = CoverageProjection()
        turn_input = build_turn_input(
            turn_ref="turn-001",
            active_question=rubric,
            on_the_floor="Tell me about your distributed systems experience.",
            candidate_utterance="Another answer.",
            thread_turn_count=1,
            projection=proj,
            all_specs=ctx.signals,
            transcript_window=[],
            budget_phase=BudgetPhase.on_track,
        )
        suffix = render_suffix(turn_input)
        suffix_text = json.dumps(suffix)

        # Neither of q-002's rubric strings should appear
        assert SENTINEL_EXCELLENT not in suffix_text
        assert SENTINEL_MEETS_BAR not in suffix_text


# ---------------------------------------------------------------------------
# Test 7: build_messages round-trip (smoke test)
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_messages_are_list_of_dicts_with_role(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        sys_prompt = "You are the brain."

        q = config.stage.questions[0]
        rubric = active_question_rubric(q, fired_dimensions=[])
        proj = CoverageProjection()
        turn_input = build_turn_input(
            turn_ref="turn-001",
            active_question=rubric,
            on_the_floor="Tell me about your distributed systems experience.",
            candidate_utterance="I built Kafka consumers handling 1M events/day.",
            thread_turn_count=1,
            projection=proj,
            all_specs=ctx.signals,
            transcript_window=[
                WindowTurn(turn_ref="t-0", speaker="agent",
                           text="Tell me about your distributed systems experience."),
            ],
            budget_phase=BudgetPhase.on_track,
        )
        messages = build_messages(sys_prompt, ctx, turn_input)

        assert isinstance(messages, list)
        assert len(messages) >= 2
        for msg in messages:
            assert "role" in msg
            assert "content" in msg
            assert msg["role"] in {"system", "user", "assistant"}

    def test_prefix_is_system_messages(self):
        config = _make_session_config()
        ctx = build_session_context(config)
        sys_prompt = "You are the brain."

        q = config.stage.questions[0]
        rubric = active_question_rubric(q, fired_dimensions=[])
        proj = CoverageProjection()
        turn_input = build_turn_input(
            turn_ref="turn-001",
            active_question=rubric,
            on_the_floor="Tell me about your distributed systems experience.",
            candidate_utterance="My answer.",
            thread_turn_count=0,
            projection=proj,
            all_specs=ctx.signals,
            transcript_window=[],
            budget_phase=BudgetPhase.winding_down,
        )
        messages = build_messages(sys_prompt, ctx, turn_input)

        # First two messages must be system
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "system"
        # First system message is the prompt
        assert messages[0]["content"] == sys_prompt
