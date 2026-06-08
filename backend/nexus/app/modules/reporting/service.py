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
from app.modules.interview_runtime.evidence import EvidenceStance
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
    level_for_signal,
    make_scored_signal,
    resolve_verdict,
    score_dimension,
    score_overall,
    signal_ceiling,
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
from app.modules.reporting.scoring.recheck import recheck_signal
from app.modules.reporting.scoring.status import badge_for_question
from app.modules.reporting.scoring.types import SignalDef

logger = structlog.get_logger()

_COMM_POINTS = {"weak": 30, "adequate": 70, "strong": 100}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _tone_by_score(s: int | None) -> str:
    if s is None:
        return "neutral"
    return "ok" if s >= 65 else "caution" if s >= 40 else "danger"


def _eliciting_question(
    sig: str,
    notes_by_signal: dict,
    q_by_id: dict[str, dict],
    q_by_signal: dict[str, dict],
) -> dict | None:
    """The bank question that actually ELICITED this signal's evidence — the question
    on the floor when its first SUPPORTING note was recorded (via from_question_id).
    Falls back to the primary-signal match when no supporting note exists.

    D5: a signal can be shared by several bank questions (e.g. an experience_check that
    was answered AND a technical_scenario that was never reached). Re-check must grade
    against the rubric of the question that elicited the answer — not the first question
    that merely lists the signal in signal_values.
    """
    for n in notes_by_signal.get(sig, []):
        if n.stance == EvidenceStance.supports and n.from_question_id in q_by_id:
            return q_by_id[n.from_question_id]
    return q_by_signal.get(sig)


def _scorecard_evidence(
    sig: str, recheck_results: dict, notes_by_signal: dict
) -> list[str]:
    """Per-signal evidence quotes: prefer grounded re-check quotes; fall back to the
    engine's own SUPPORTING notes (verbatim candidate words by contract) when re-check
    didn't run or returned none — so every assessed signal shows real evidence."""
    rc = recheck_results.get(sig)
    if rc and rc.evidence_quotes:
        return rc.evidence_quotes
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


async def build_report(*, evidence, questions, signal_metadata, correlation_id, n_samples=None):
    """Three-layer report over the gen-3 SessionEvidence.

    Deterministic: roll each PRIMARY signal's notes → level → score; dimensions;
    coverage/confidence; provenance-aware must-have gate; verdict.
    AI: re-check (refine evidenced levels) → holistic ±5 → communication → narrative.
    """
    view = EvidenceView(evidence)
    primary_set = view.primary_set
    notes_by_signal = view.notes_by_signal
    provenance_by_signal = view.provenance_by_signal
    closure_by_primary = view.closure_by_primary

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

    q_by_signal: dict[str, dict] = {}
    for q in questions:
        for sv in q.get("signal_values", []):
            q_by_signal.setdefault(sv, q)
    q_by_id: dict[str, dict] = {q["id"]: q for q in questions if q.get("id")}

    def _provenance_str(sig: str) -> str:
        p = provenance_by_signal.get(sig, "not_reached")
        return p.value if hasattr(p, "value") else str(p)

    # --- Deterministic per-PRIMARY level (the graded denominator) ----------
    base_level: dict[str, str] = {}
    for sig in primary_set:
        base_level[sig] = level_for_signal(
            notes_by_signal.get(sig, []), provenance=_provenance_str(sig),
            closure=closure_by_primary.get(sig),
        )

    # --- Layer 2 re-check: every evidenced primary (skip only not_reached). The
    #     bank rubric is the contract; factual gates are NOT skipped — instead the
    #     re-check is told the question_kind and grades them against the rubric's own
    #     bar (D5). Each signal is graded against the question that ELICITED its
    #     evidence (from_question_id), not a never-reached question sharing the signal.
    recheck_targets = [
        sig for sig in primary_set
        if _provenance_str(sig) in ("asked_directly", "cross_credited", "probed_absent")
    ]

    async def _one(sig: str):
        m = _identity(sig)
        d = SignalDef(value=m["value"], type=m["type"], weight=m["weight"],
                      knockout=m["knockout"], priority=m["priority"])
        q = _eliciting_question(sig, notes_by_signal, q_by_id, q_by_signal) or {}
        ctx = f"Q: {q.get('text','')}\nrubric: {json.dumps(q.get('rubric', {}))}"
        return sig, await recheck_signal(signal_def=d, notes=notes_by_signal.get(sig, []),
                                         question_context=ctx, engine_level=base_level[sig],
                                         correlation_id=correlation_id,
                                         question_kind=q.get("question_kind"))
    recheck_results = (
        dict(await asyncio.gather(*[_one(s) for s in recheck_targets]))
        if recheck_targets else {}
    )

    final_level = dict(base_level)
    for sig, rc in recheck_results.items():
        final_level[sig] = rc.level

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
    ceiling = signal_ceiling(
        must_haves, is_knockout_close=view.is_knockout_close, coverage=coverage)
    session_score = clamp_to_ceiling(base, ceiling)

    adjustment = await score_holistic(
        session_score=session_score, scored=scored, is_knockout_close=view.is_knockout_close,
        coverage=coverage, transcript_text=view.candidate_transcript_text,
        demonstrated_secondaries=sorted(view.demonstrated_secondaries),
        correlation_id=correlation_id)
    overall = apply_holistic(session_score, adjustment.delta, ceiling)

    comm = await grade_communication(transcript_text=view.candidate_transcript_text,
                                     correlation_id=correlation_id)
    comm_score = _COMM_POINTS[comm.level]

    verdict = resolve_verdict(overall=overall, coverage=coverage,
                              is_knockout_close=view.is_knockout_close,
                              knockout_signal=view.knockout_signal, must_haves=must_haves)

    # Per-signal evidence quotes (re-check grounded, else engine supporting notes) — computed
    # once and reused by both the scorecards and the narrative ground truth.
    evidence_by_sig = {
        s.value: _scorecard_evidence(s.value, recheck_results, notes_by_signal) for s in scored
    }

    signal_assessments = [SignalAssessmentOut(
        signal=s.value, type=s.type, weight=s.weight, knockout=s.knockout, priority=s.priority,
        provenance=_provenance_str(s.value), level=s.level, score=s.score,
        evidence=evidence_by_sig[s.value],
        overridden=(
            recheck_results[s.value].overridden if s.value in recheck_results else False),
        override_reason=(
            recheck_results[s.value].override_reason if s.value in recheck_results else None),
    ) for s in scored]

    # Human-verify charity flags: a factual gate graded against the bank rubric whose
    # required facts were not fully elicited. Explanatory only — never a silent penalty.
    human_verify = [
        {"signal": sig, "note": rc.verification_note}
        for sig, rc in recheck_results.items()
        if rc.needs_verification and rc.verification_note
    ]

    gt = json.dumps({
        "verdict": verdict.verdict, "verdict_reason": verdict.reason,
        "scores": {"overall": overall, "technical": tech.score,
                   "behavioral": beh.score, "communication": comm_score},
        "knockout_close": ({"signal": view.knockout_signal} if view.is_knockout_close else None),
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
        q_out.append(QuestionOut(
            seq=i + 1, question_id=qr.question_id, title=text[:60],
            status_badge=badge, status_tone=tone, question_text=text,
            candidate_quote=quote, asked_at_ms=None))

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
                "n_signals_rechecked": len(recheck_results),
                "n_overrides": sum(1 for r in recheck_results.values() if r.overridden),
                "level_map": {s.value: s.level for s in scored},
                "session_score": session_score, "holistic_delta": adjustment.delta,
                "holistic_justification": adjustment.justification, "ceiling_applied": ceiling,
            }),
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
        knockout_results=[],
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
