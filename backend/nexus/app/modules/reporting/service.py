"""Post-session report compilation, score aggregation, PDF generation."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

import structlog

from app.ai.config import ai_config
from app.modules.reporting.schemas import (
    AnswerRating,
    DimensionScoreOut,
    EvidenceOut,
    KnockoutResultOut,
    QuestionScorecard,
    ReportRead,
    ScoringManifest,
    SignalScorecard,
    SummaryOut,
)
from app.modules.reporting.scoring.aggregate import (
    KnockoutResult,
    ScoredSignal,
    SignalObservation,
    _confidence,
    combine_signal,
    knockout_status,
    resolve_verdict,
    score_dimension,
    score_overall,
)
from app.modules.reporting.scoring.constants import (
    BEHAVIORAL_TYPES,
    TECHNICAL_TYPES,
)
from app.modules.reporting.scoring.judge import grade_answer, grade_communication
from app.modules.reporting.scoring.opportunity import classify
from app.modules.reporting.scoring.transcript import segment
from app.modules.reporting.scoring.types import Opportunity, SignalDef

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _scored_signal_to_card(
    sig: ScoredSignal,
    evidence: list[EvidenceOut],
    covered_by: list[str],
    opportunity: str | None,
) -> SignalScorecard:
    return SignalScorecard(
        value=sig.value,
        type=sig.type,
        weight=sig.weight,
        knockout=sig.knockout,
        state=sig.state,
        score=sig.score,
        opportunity=opportunity,
        evidence=evidence,
        covered_by=covered_by,
    )


def _build_summary(
    verdict: str,
    verdict_reason: str,
    scored_signals: list[ScoredSignal],
    knockout_results: list[KnockoutResult],
) -> SummaryOut:
    headline_map = {
        "advance": "Candidate meets the bar — recommended for advancement.",
        "borderline": "Candidate is borderline — human review required.",
        "reject": "Candidate does not meet the bar — recommended for rejection.",
    }
    strengths = [
        s.value for s in scored_signals if s.state in ("excellent", "meets_bar")
    ]
    gaps = [s.value for s in scored_signals if s.state == "below_bar"]
    # Also surface failed knockout signals in gaps (even if already below_bar)
    failed_ko = [k.signal for k in knockout_results if k.status == "failed"]
    for fk in failed_ko:
        if fk not in gaps:
            gaps.append(fk)
    return SummaryOut(
        headline=headline_map.get(verdict, verdict),
        strengths=strengths,
        gaps=gaps,
        rationale=verdict_reason,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_report(
    *,
    transcript: list[dict],
    envelope: dict,
    questions: list[dict],
    signal_metadata: list[dict],
    correlation_id: str,
    n_samples: int = 1,
) -> ReportRead:
    """Orchestrate all scoring pipeline stages and return a :class:`ReportRead`.

    Steps:
    1. Segment transcript into one ScoredUnit per delivered question.
    2. Grade each unit via the LLM judge (grade_answer).
    3. Accumulate SignalObservations per signal value.
    4. Collapse observations → ScoredSignal (combine_signal).
    5. Score dimensions (technical / behavioral).
    6. Score overall + coverage.
    7. Resolve knockouts + verdict.
    8. Map dataclasses → Pydantic and return ReportRead.
    """
    # ------------------------------------------------------------------
    # Step 1 — Segmentation
    # ------------------------------------------------------------------
    units = segment(transcript=transcript, envelope=envelope, bank_questions=questions)

    # ------------------------------------------------------------------
    # Step 2 — Lookup helpers
    # ------------------------------------------------------------------
    question_by_id: dict[str, dict] = {q["id"]: q for q in questions}

    signal_defs: list[SignalDef] = [
        SignalDef(
            value=m["value"],
            type=m["type"],
            weight=m["weight"],
            knockout=m["knockout"],
            priority=m["priority"],
        )
        for m in signal_metadata
    ]

    # Per-signal: accumulate observations
    signal_observations: dict[str, list[SignalObservation]] = defaultdict(list)
    # Per-signal: accumulate evidence and which questions covered it
    signal_evidence: dict[str, list[EvidenceOut]] = defaultdict(list)
    signal_covered_by: dict[str, list[str]] = defaultdict(list)
    # Per-question: the dominant opportunity (from the unit)
    signal_opportunity: dict[str, Opportunity | None] = {}  # signal_value → last opportunity seen

    # Per-question scorecards
    question_scorecards: list[QuestionScorecard] = []

    # ------------------------------------------------------------------
    # Step 3 — Grade each delivered unit
    # ------------------------------------------------------------------
    for unit in units:
        question = question_by_id.get(unit.question_id)
        if question is None:
            logger.warning(
                "reporting.service.unknown_question",
                question_id=unit.question_id,
                correlation_id=correlation_id,
            )
            continue

        transcript_excerpt = (
            f"INTERVIEWER: {unit.question_text}\n"
            f"CANDIDATE: {unit.candidate_answer}"
        )
        opportunity = classify(unit)

        rating: AnswerRating = await grade_answer(
            question=question,
            transcript_excerpt=transcript_excerpt,
            correlation_id=correlation_id,
            n_samples=n_samples,
        )

        # Build evidence list for this question
        question_evidence = [
            EvidenceOut(
                quote=q,
                timestamp_ms=unit.answer_start_ms,
                question_id=unit.question_id,
            )
            for q in rating.evidence_quotes
        ]

        # Accumulate into per-signal buckets
        for sig_value in question.get("signal_values", []):
            obs = SignalObservation(
                level=rating.level,
                opportunity=opportunity,
                red_flags_hit=bool(rating.red_flags_hit),
            )
            signal_observations[sig_value].append(obs)
            signal_evidence[sig_value].extend(question_evidence)
            if unit.question_id not in signal_covered_by[sig_value]:
                signal_covered_by[sig_value].append(unit.question_id)
            signal_opportunity[sig_value] = opportunity

        # Build QuestionScorecard
        question_scorecards.append(
            QuestionScorecard(
                question_id=unit.question_id,
                question_text=unit.question_text,
                level=rating.level,
                evidence=question_evidence,
                red_flags_hit=rating.red_flags_hit,
                probes_fired=unit.probes_fired,
                opportunity=opportunity,
            )
        )

    # ------------------------------------------------------------------
    # Step 4 — Collapse observations → ScoredSignal
    # ------------------------------------------------------------------
    scored_signals: list[ScoredSignal] = []

    for sig_def in signal_defs:
        observations = signal_observations.get(sig_def.value, [])
        state, score = combine_signal(observations)
        scored_signals.append(
            ScoredSignal(
                value=sig_def.value,
                type=sig_def.type,
                weight=sig_def.weight,
                knockout=sig_def.knockout,
                priority=sig_def.priority,
                state=state,
                score=score,
            )
        )

    # ------------------------------------------------------------------
    # Step 5 — Dimension scores
    # ------------------------------------------------------------------
    tech_dim = score_dimension("technical", scored_signals, TECHNICAL_TYPES)
    beh_dim = score_dimension("behavioral", scored_signals, BEHAVIORAL_TYPES)

    # ------------------------------------------------------------------
    # Step 5b — Communication dimension (content-only; excluded from Overall)
    # ------------------------------------------------------------------
    transcript_text = "\n".join(
        t["text"] for t in transcript if t.get("role") == "candidate"
    )
    comm_verdict = await grade_communication(
        transcript_text=transcript_text,
        correlation_id=correlation_id,
    )
    _comm_level_score: dict[str, int] = {"weak": 30, "adequate": 70, "strong": 100}
    comm_score = _comm_level_score[comm_verdict.level]

    # ------------------------------------------------------------------
    # Step 6 — Overall score + coverage
    # ------------------------------------------------------------------
    # score_overall operates only on JD ScoredSignals — communication is NOT included.
    overall, coverage = score_overall(scored_signals)

    # ------------------------------------------------------------------
    # Step 7 — Knockouts + verdict
    # ------------------------------------------------------------------
    knockout_results: list[KnockoutResult] = []
    for sig in scored_signals:
        if not sig.knockout:
            continue
        status = knockout_status(state=sig.state)
        reason_map = {
            "passed": f"Signal '{sig.value}' confirmed at or above the bar.",
            "failed": f"Signal '{sig.value}' was assessed as below the bar.",
            "insufficient": f"Signal '{sig.value}' could not be confirmed — insufficient evidence.",
        }
        ko_evidence = [
            ev.__dict__ if hasattr(ev, "__dict__") else ev
            for ev in signal_evidence.get(sig.value, [])
        ]
        knockout_results.append(
            KnockoutResult(
                signal=sig.value,
                status=status,
                reason=reason_map[status],
                evidence=ko_evidence,
            )
        )

    verdict_result = resolve_verdict(
        overall=overall,
        coverage=coverage,
        knockouts=knockout_results,
    )

    # ------------------------------------------------------------------
    # Step 8 — Map dataclasses → Pydantic and assemble ReportRead
    # ------------------------------------------------------------------

    # DimensionScore → DimensionScoreOut
    dimension_scores_out: dict[str, DimensionScoreOut] = {
        "technical": DimensionScoreOut(
            name=tech_dim.name,
            score=tech_dim.score,
            coverage=tech_dim.coverage,
            confidence=tech_dim.confidence,
        ),
        "behavioral": DimensionScoreOut(
            name=beh_dim.name,
            score=beh_dim.score,
            coverage=beh_dim.coverage,
            confidence=beh_dim.confidence,
        ),
        # Communication is a content-only dimension scored across the full
        # transcript.  It is intentionally NOT included in score_overall(),
        # which aggregates only JD-signal ScoredSignals.
        "communication": DimensionScoreOut(
            name="communication",
            score=comm_score,
            coverage=1.0,
            confidence="medium",
            note=(
                "content-only; full communication scoring pending"
                " session recording (sub-project B)"
            ),
        ),
    }

    # ScoredSignal → SignalScorecard
    signal_scorecards_out: list[SignalScorecard] = [
        _scored_signal_to_card(
            sig=ss,
            evidence=signal_evidence.get(ss.value, []),
            covered_by=signal_covered_by.get(ss.value, []),
            opportunity=signal_opportunity.get(ss.value),
        )
        for ss in scored_signals
    ]

    # KnockoutResult → KnockoutResultOut
    knockout_results_out: list[KnockoutResultOut] = [
        KnockoutResultOut(
            signal=kr.signal,
            status=kr.status,
            reason=kr.reason,
            evidence=[
                EvidenceOut(**ev) if isinstance(ev, dict) else ev
                for ev in kr.evidence
            ],
        )
        for kr in knockout_results
    ]

    # Overall confidence from coverage
    overall_confidence = _confidence(coverage)

    # Summary
    summary = _build_summary(
        verdict=verdict_result.verdict,
        verdict_reason=verdict_result.reason,
        scored_signals=scored_signals,
        knockout_results=knockout_results,
    )

    # Scoring manifest
    scoring_manifest = ScoringManifest(
        scorer_model=ai_config.report_scorer_model,
        reasoning_effort=ai_config.report_scorer_effort or None,
        verbosity=ai_config.report_scorer_verbosity,
        prompt_version=ai_config.report_scorer_prompt_version,
        n_samples=n_samples,
        generated_at=datetime.now(UTC).isoformat(),
        correlation_id=correlation_id,
    )

    logger.info(
        "reporting.service.build_report.done",
        verdict=verdict_result.verdict,
        overall_score=overall,
        overall_coverage=coverage,
        correlation_id=correlation_id,
    )

    return ReportRead(
        verdict=verdict_result.verdict,
        verdict_reason=verdict_result.reason,
        overall_score=overall,
        overall_coverage=coverage,
        overall_confidence=overall_confidence,
        dimension_scores=dimension_scores_out,
        knockout_results=knockout_results_out,
        signal_scorecards=signal_scorecards_out,
        question_scorecards=question_scorecards,
        summary=summary,
        engine_version="v2",
        status="ready",
        scoring_manifest=scoring_manifest,
    )
