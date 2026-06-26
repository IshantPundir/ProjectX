"""Post-session report compilation, score aggregation, PDF generation."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.config import ai_config
from app.modules.interview_runtime.evidence import EvidenceStance, Speaker, TranscriptTurn
from app.modules.reporting.models import SessionReport
from app.modules.reporting.schemas import (
    QuestionOut,
    ReportRead,
    ScoreOut,
    ScoringManifest,
    SignalAssessmentOut,
)
from app.modules.reporting.scoring.aggregate import (
    apply_holistic,
    clamp_to_ceiling,
    confidence_from_coverage,
    make_scored_signal,
    must_have_cap,
    resolve_verdict,
    score_dimension,
    score_overall,
)
from app.modules.reporting.scoring.constants import (
    BEHAVIORAL_TYPES,
    NARRATIVE_NOTES_PER_SIGNAL,
    NARRATIVE_TRANSCRIPT_CHAR_BUDGET,
    SCORECARD_EVIDENCE_MAX,
    TECHNICAL_TYPES,
    tier_label,
)
from app.modules.reporting.scoring.evidence_adapter import EvidenceView
from app.modules.reporting.scoring.holistic import score_holistic
from app.modules.reporting.scoring.judge import grade_communication
from app.modules.reporting.scoring.narrative import write_narrative
from app.modules.reporting.scoring.question_grade import grade_question, question_base_level
from app.modules.reporting.scoring.rollup import pick_dedicated_question, roll_up_signal
from app.modules.reporting.scoring.signal_labels import generate_signal_labels
from app.modules.reporting.scoring.status import badge_for_question

logger = structlog.get_logger()


def asked_at_ms_by_question(transcript: list[TranscriptTurn]) -> dict[str, int]:
    """Map each bank question_id → the session-relative ms at which it was first ASKED.

    The "asked" moment is the EARLIEST agent turn tagged with that question_id
    (`span.start_ms`). Candidate turns are ignored (the candidate doesn't ask the
    question), and agent turns without a question_id (bridges, meta-asides) are
    ignored. If the same question is voiced across several agent turns, the
    earliest start_ms wins. Pure — no IO/LLM.
    """
    out: dict[str, int] = {}
    for turn in transcript:
        if turn.speaker != Speaker.agent or turn.question_id is None:
            continue
        start_ms = turn.span.start_ms
        existing = out.get(turn.question_id)
        if existing is None or start_ms < existing:
            out[turn.question_id] = start_ms
    return out

_COMM_POINTS = {"weak": 30, "adequate": 70, "strong": 100}

# Bump when the scoring algorithm changes in a way that affects scores, so a
# report's manifest records which scorer produced it (cross-candidate audit).
SCORER_CODE_VERSION = "qa-1"  # question-anchored, gen-1


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _tone_by_score(s: int | None) -> str:
    if s is None:
        return "neutral"
    return "ok" if s >= 65 else "caution" if s >= 40 else "danger"


def _scorecard_evidence(
    sig: str, grade_by_sig: dict, notes_by_signal: dict
) -> list[str]:
    """Per-signal evidence quotes: prefer the dedicated question grade's grounded
    quotes; fall back to the engine's own SUPPORTING notes (verbatim candidate words
    by contract) when the dedicated grade didn't run or returned none — so every
    assessed signal shows real evidence."""
    g = grade_by_sig.get(sig)
    if g and g.evidence_quotes:
        return g.evidence_quotes
    return [
        n.quote for n in notes_by_signal.get(sig, [])
        if n.stance == EvidenceStance.supports and n.quote
    ][:SCORECARD_EVIDENCE_MAX]


def _narrative_notes(sig: str, notes_by_signal: dict) -> list[dict]:
    """Bounded engine notes for one signal, threaded into the narrative ground truth
    so the narrative LLM can GROUND its claims in the candidate's own words."""
    return [
        {"quote": n.quote, "texture": n.texture.value,
         "stance": n.stance.value, "via_probe": n.via_probe}
        for n in notes_by_signal.get(sig, []) if n.quote
    ][:NARRATIVE_NOTES_PER_SIGNAL]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_report(*, evidence, questions, signal_metadata, correlation_id,
                       bank_id=None, signal_snapshot_id=None, n_samples=None):
    """Three-layer report over the gen-3 SessionEvidence.

    Layer 2 is QUESTION-anchored: every ASKED question is graded against its own
    full bank card (rubric + listen-for + red-flags + evaluation_hint), then those
    grades roll up to each PRIMARY signal's level (dedicated question anchors;
    cross-credit can lift by one tier). Downstream is unchanged:
    dimensions → overall → fit-ceiling → holistic ±5 → communication → verdict;
    then the LLM narrative (sees final numbers, never changes them).
    """
    view = EvidenceView(evidence)
    primary_set = view.primary_set
    notes_by_signal = view.notes_by_signal
    provenance_by_signal = view.provenance_by_signal

    def_by_value = {m["value"]: m for m in signal_metadata}
    engine_identity = view.signal_by_name  # dict[str, SignalEvidence]

    def _identity(sig: str) -> dict:
        m = def_by_value.get(sig)
        if m is not None:
            return {"value": sig, "type": m["type"], "weight": m["weight"],
                    "knockout": m["knockout"], "priority": m["priority"]}
        se = engine_identity.get(sig)
        if se is not None:
            # SignalEvidence carries authoritative identity copied from the role config at
            # session start — use it rather than silently defaulting a possible must-have.
            return {"value": sig, "type": se.signal_type.value, "weight": se.weight,
                    "knockout": se.knockout, "priority": se.priority.value}
        logger.warning("reporting.build_report.signal_identity_missing", signal=sig,
                       correlation_id=correlation_id)
        return {"value": sig, "type": "competency", "weight": 1,
                "knockout": False, "priority": "preferred"}

    def _provenance_str(sig: str) -> str:
        p = provenance_by_signal.get(sig, "not_reached")
        return p.value if hasattr(p, "value") else str(p)

    notes_by_question = view.notes_by_question
    outcome_by_question = view.outcome_by_question
    probes_by_q = {
        q.question_id: (len(q.probes_used), q.probes_available) for q in evidence.questions}

    # --- Layer 2: grade every ASKED question against its OWN full card ---------
    async def _grade(q: dict):
        qid = q["id"]
        qnotes = notes_by_question.get(qid, [])
        used, avail = probes_by_q.get(qid, (0, 0))
        base = question_base_level(qnotes)
        return qid, await grade_question(
            question=q, notes=qnotes, probes_used=used, probes_available=avail,
            base_level=base, correlation_id=correlation_id)
    asked_qids = [q["id"] for q in questions if outcome_by_question.get(q["id"]) == "asked"]
    grades = dict(await asyncio.gather(*[_grade(q) for q in questions if q["id"] in asked_qids])) \
        if asked_qids else {}

    # --- Roll question grades up to each PRIMARY signal -----------------------
    def _cross_credit_level(sig: str) -> str | None:
        ded = pick_dedicated_question(sig, questions, outcome_by_question)
        ded_id = ded["id"] if ded else None
        other = [n for n in notes_by_signal.get(sig, [])
                 if n.from_question_id != ded_id and n.stance == EvidenceStance.supports]
        if not other:
            return None
        return question_base_level(other)

    rollups: dict[str, object] = {}
    final_level: dict[str, str] = {}
    for sig in primary_set:
        ded = pick_dedicated_question(sig, questions, outcome_by_question)
        ded_id = ded["id"] if ded else None
        ded_outcome = outcome_by_question.get(ded_id) if ded_id else None
        ded_level = grades[ded_id].level if (ded_id in grades) else None
        r = roll_up_signal(signal=sig, dedicated_level=ded_level, dedicated_outcome=ded_outcome,
                           cross_credit_level=_cross_credit_level(sig))
        rollups[sig] = r
        final_level[sig] = r.level

    # --- Build ScoredSignal list over the PRIMARY set ----------------------
    scored = []
    for sig in primary_set:
        m = _identity(sig)
        scored.append(make_scored_signal(
            value=sig, type=m["type"], weight=m["weight"], knockout=m["knockout"],
            priority=m["priority"], level=final_level[sig]))

    tech = score_dimension("technical", scored, TECHNICAL_TYPES)
    beh = score_dimension("behavioral", scored, BEHAVIORAL_TYPES)
    base, coverage = score_overall(scored)

    must_haves = [s for s in scored if s.knockout]
    ceiling = must_have_cap(must_haves, coverage=coverage)
    session_score = clamp_to_ceiling(base, ceiling)

    adjustment = await score_holistic(
        session_score=session_score, scored=scored,
        coverage=coverage, transcript_text=view.candidate_transcript_text,
        demonstrated_secondaries=sorted(view.demonstrated_secondaries),
        correlation_id=correlation_id)
    overall = apply_holistic(session_score, adjustment.delta, ceiling)

    comm = await grade_communication(transcript_text=view.candidate_transcript_text,
                                     correlation_id=correlation_id)
    comm_score = _COMM_POINTS[comm.level]

    verdict = resolve_verdict(overall=overall, coverage=coverage, must_haves=must_haves)

    # Per-signal evidence quotes (dedicated-grade grounded, else engine supporting notes) —
    # computed once and reused by both the scorecards and the narrative ground truth.
    grade_by_sig = {
        s.value: (grades.get(ded["id"]) if (ded := pick_dedicated_question(s.value, questions, outcome_by_question)) else None)
        for s in scored
    }
    evidence_by_sig = {
        s.value: _scorecard_evidence(s.value, grade_by_sig, notes_by_signal) for s in scored
    }

    # Crisp glance titles for the verbose competency statements (best-effort LLM;
    # {} on failure → consumers fall back to the full `signal` string).
    signal_labels = await generate_signal_labels(
        [s.value for s in scored], correlation_id=correlation_id)

    signal_assessments = []
    for s in scored:
        g = grade_by_sig.get(s.value)
        signal_assessments.append(SignalAssessmentOut(
            signal=s.value, signal_label=signal_labels.get(s.value),
            type=s.type, weight=s.weight, knockout=s.knockout, priority=s.priority,
            provenance=_provenance_str(s.value), level=s.level, score=s.score,
            evidence=evidence_by_sig[s.value],
            overridden=bool(g and g.overridden),
            override_reason=(g.override_reason if g else None),
            cross_credit_applied=rollups[s.value].cross_credit_applied,
            level_basis=rollups[s.value].level_basis,
        ))

    # Human-verify charity flags: a question graded against its bank card whose
    # required facts were not fully elicited. Explanatory only — never a silent penalty.
    human_verify = [
        {"signal": sig, "note": g.verification_note}
        for sig in primary_set
        if (g := grade_by_sig.get(sig)) and g.needs_verification and g.verification_note
    ]

    gt = json.dumps({
        "verdict": verdict.verdict, "verdict_reason": verdict.reason,
        "scores": {"overall": overall, "technical": tech.score,
                   "behavioral": beh.score, "communication": comm_score},
        "signals": [{"signal": s.value, "type": s.type, "level": s.level,
                     "provenance": _provenance_str(s.value), "must_have": s.knockout,
                     "priority": s.priority,
                     "evidence_quotes": evidence_by_sig[s.value],
                     "notes": _narrative_notes(s.value, notes_by_signal)} for s in scored],
        "transcript": view.candidate_transcript_text[:NARRATIVE_TRANSCRIPT_CHAR_BUDGET],
        "human_verify": human_verify,
    }, ensure_ascii=False)
    narrative = await write_narrative(ground_truth_json=gt, correlation_id=correlation_id)

    # Per-question cards from the engine's question records. QUESTION-anchored: the
    # badge comes from THIS question's outcome/closure and the quote from the notes
    # THIS question elicited (from_question_id) — never the primary signal, which may be
    # shared with another (e.g. not-reached) question.
    q_text_by_id = {q["id"]: q for q in questions}
    must_have_signals = {s.value for s in scored if s.knockout}

    def _question_quote(qid: str) -> str:
        """First supporting candidate quote elicited BY this question (not its signal)."""
        for notes in notes_by_signal.values():
            for n in notes:
                if n.from_question_id == qid and n.stance == EvidenceStance.supports and n.quote:
                    return n.quote
        return ""

    # When each bank question was first asked (session-relative ms), derived from the
    # earliest agent transcript turn tagged with that question_id. Powers the report
    # capsules' seek/highlight into the recording.
    asked_at_by_qid = asked_at_ms_by_question(evidence.transcript)

    q_out: list[QuestionOut] = []
    for i, qr in enumerate(evidence.questions):
        sig = qr.primary_signal
        qdict = q_text_by_id.get(qr.question_id, {})
        outcome = qr.outcome.value if hasattr(qr.outcome, "value") else qr.outcome
        closure = (qr.closure.value if hasattr(qr.closure, "value") else qr.closure)
        if outcome == "not_reached":
            # Never asked (ran out of time/budget) — no judgment, no quote.
            badge, tone, quote = "not_attempted", "neutral", ""
        elif closure == "truncated":
            # Asked but the time budget cut the thread before fair resolution.
            badge, tone, quote = "not_fully_assessed", "neutral", _question_quote(qr.question_id)
        else:
            badge, tone = badge_for_question(
                level=final_level.get(sig, "not_reached"),
                provenance=_provenance_str(sig),
                knockout=sig in must_have_signals)
            quote = _question_quote(qr.question_id)
        text = qdict.get("text", "")
        g = grades.get(qr.question_id)
        q_out.append(QuestionOut(
            seq=i + 1, question_id=qr.question_id, title=text[:60],
            status_badge=badge, status_tone=tone, question_text=text,
            candidate_quote=quote, asked_at_ms=asked_at_by_qid.get(qr.question_id),
            level=(g.level if g else "not_reached"),
            closure=closure,
            difficulty=qdict.get("difficulty"),
            listen_for_hits=(g.listen_for_hits if g else []),
            red_flags_tripped=(g.red_flags_tripped if g else []),
            probes_used=len(qr.probes_used), probes_available=qr.probes_available,
            score=(g.score if g else None),
        ))

    # Fold in narrative per-question prose (our_read / refined candidate_quote).
    read_by_qid = {qn.question_id: qn for qn in narrative.questions}
    for qo in q_out:
        nq = read_by_qid.get(qo.question_id)
        if nq:
            qo.our_read = nq.our_read
            if nq.candidate_quote:
                qo.candidate_quote = nq.candidate_quote

    def _score_out(score, cov, conf):
        return ScoreOut(score=score, tier_label=tier_label(score), tone=_tone_by_score(score),
                        confidence=conf, coverage=cov)

    logger.info("reporting.service.build_report.done", verdict=verdict.verdict,
                overall_score=overall, overall_coverage=coverage, correlation_id=correlation_id)

    return ReportRead(
        verdict=verdict.verdict, verdict_reason=narrative.decision.headline or verdict.reason,
        overall_score=overall, overall_coverage=coverage,
        overall_confidence=confidence_from_coverage(coverage) if overall is not None else "low",
        decision=narrative.decision,
        scores={
            "overall": ScoreOut(
                score=overall, tier_label=tier_label(overall), tone=_tone_by_score(overall),
                confidence=confidence_from_coverage(coverage) if overall is not None else "low",
                coverage=coverage, session_score=session_score, holistic_delta=adjustment.delta),
            "technical": _score_out(tech.score, tech.coverage, tech.confidence),
            "behavioral": _score_out(beh.score, beh.coverage, beh.confidence),
            "communication": _score_out(comm_score, 1.0, "medium"),
        },
        quick_summary=narrative.quick_summary, strengths=narrative.strengths,
        concerns=narrative.concerns, questions=q_out, methodology=narrative.methodology,
        signal_assessments=signal_assessments, engine_version="v3", status="ready",
        scoring_manifest=ScoringManifest(
            scorer_model=ai_config.report_scorer_model,
            prompt_version=ai_config.report_scorer_prompt_version,
            generated_at=datetime.now(UTC).isoformat(), correlation_id=correlation_id,
            evidence_grounding_summary={
                "n_questions_graded": len(grades),
                "n_overrides": sum(1 for g in grades.values() if g.overridden),
                "level_map": {s.value: s.level for s in scored},
                "cross_credit_signals": [
                    s.value for s in scored if rollups[s.value].cross_credit_applied],
                "session_score": session_score, "holistic_delta": adjustment.delta,
                "holistic_justification": adjustment.justification, "ceiling_applied": ceiling,
            },
            scorer_code_version=SCORER_CODE_VERSION,
            bank_id=bank_id,
            signal_snapshot_id=signal_snapshot_id,
        ),
    )


async def persist_report(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    report: ReportRead,
    rubric_snapshot: dict | None = None,
    force: bool = False,
) -> SessionReport:
    """Persist a :class:`ReportRead` to the ``session_reports`` table.

    - If no row exists for ``session_id``: create one at ``version=1``.
    - If a row exists and ``force`` is False: return existing unchanged (idempotent no-op).
    - If a row exists and ``force`` is True: overwrite every value field and
      increment ``version`` by 1.

    The caller (test or actor) owns the transaction.  This function calls
    ``await db.flush()`` but never ``commit()``.
    """
    existing = (
        await db.execute(
            select(SessionReport).where(SessionReport.session_id == session_id)
        )
    ).scalar_one_or_none()

    values: dict = dict(
        verdict=report.verdict,
        verdict_reason=report.verdict_reason,
        overall_score=report.overall_score,
        overall_coverage=(
            float(report.overall_coverage) if report.overall_coverage is not None else None),
        overall_confidence=report.overall_confidence,
        dimension_scores={k: v.model_dump(mode="json") for k, v in report.scores.items()},
        signal_scorecards=[s.model_dump(mode="json") for s in report.signal_assessments],
        question_scorecards=[q.model_dump(mode="json") for q in report.questions],
        summary={
            "decision": report.decision.model_dump(mode="json"),
            "quick_summary": report.quick_summary,
            "strengths": [s.model_dump(mode="json") for s in report.strengths],
            "concerns": [c.model_dump(mode="json") for c in report.concerns],
            "methodology": report.methodology.model_dump(mode="json"),
        },
        scoring_manifest=(
            report.scoring_manifest.model_dump(mode="json") if report.scoring_manifest else None),
        engine_version=report.engine_version or "v3",
        status="ready",
        generated_at=datetime.now(UTC),
        rubric_snapshot=rubric_snapshot,
    )

    if existing is None:
        row = SessionReport(
            session_id=session_id,
            tenant_id=tenant_id,
            assignment_id=assignment_id,
            version=1,
            **values,
        )
        db.add(row)
        await db.flush()
        logger.info(
            "reporting.service.persist_report.created",
            session_id=str(session_id),
            version=1,
        )
        return row

    if not force:
        logger.info(
            "reporting.service.persist_report.noop",
            session_id=str(session_id),
            version=existing.version,
        )
        return existing

    # force=True — overwrite fields and bump version
    for field, val in values.items():
        setattr(existing, field, val)
    existing.version = existing.version + 1
    await db.flush()
    logger.info(
        "reporting.service.persist_report.force_updated",
        session_id=str(session_id),
        version=existing.version,
    )
    return existing
