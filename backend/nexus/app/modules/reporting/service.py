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
    FACTUAL_QUESTION_KINDS,
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


def _is_factual_gate_signal(signal_value: str, questions: list[dict]) -> bool:
    """True if every bank question covering this signal is a factual gate
    (experience_check / compliance_binary). Such gates are answered correctly
    by a brief, clear response; the live engine already judged them, and the
    rubric's depth anchors (employer/role/date detail) are not what the engine
    probed for — so the post-session re-check would unfairly downgrade them.
    We trust the engine's state for these and only re-check substantive signals.
    """
    covering = [q for q in questions if signal_value in q.get("signal_values", [])]
    return bool(covering) and all(
        q.get("question_kind") in FACTUAL_QUESTION_KINDS for q in covering
    )


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

    # --- Layer 2 re-check: only evidenced primaries (+probed_absent), skip
    #     not_reached and factual gates (engine already judged those) --------
    recheck_targets = [
        sig for sig in primary_set
        if _provenance_str(sig) in ("asked_directly", "cross_credited", "probed_absent")
        and not _is_factual_gate_signal(sig, questions)
    ]

    async def _one(sig: str):
        m = _identity(sig)
        d = SignalDef(value=m["value"], type=m["type"], weight=m["weight"],
                      knockout=m["knockout"], priority=m["priority"])
        q = q_by_signal.get(sig, {})
        ctx = f"Q: {q.get('text','')}\nrubric: {json.dumps(q.get('rubric', {}))}"
        return sig, await recheck_signal(signal_def=d, notes=notes_by_signal.get(sig, []),
                                         question_context=ctx, engine_level=base_level[sig],
                                         correlation_id=correlation_id)
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

    signal_assessments = [SignalAssessmentOut(
        signal=s.value, type=s.type, weight=s.weight, knockout=s.knockout, priority=s.priority,
        provenance=_provenance_str(s.value), level=s.level, score=s.score,
        evidence=(
            recheck_results[s.value].evidence_quotes if s.value in recheck_results else []),
        overridden=(
            recheck_results[s.value].overridden if s.value in recheck_results else False),
        override_reason=(
            recheck_results[s.value].override_reason if s.value in recheck_results else None),
    ) for s in scored]

    gt = json.dumps({
        "verdict": verdict.verdict, "verdict_reason": verdict.reason,
        "scores": {"overall": overall, "technical": tech.score,
                   "behavioral": beh.score, "communication": comm_score},
        "knockout_close": ({"signal": view.knockout_signal} if view.is_knockout_close else None),
        "signals": [{"signal": s.value, "type": s.type, "level": s.level,
                     "provenance": _provenance_str(s.value), "must_have": s.knockout,
                     "priority": s.priority} for s in scored],
    }, ensure_ascii=False)
    narrative = await write_narrative(ground_truth_json=gt, correlation_id=correlation_id)

    # Per-question cards from the engine's question records.
    q_text_by_id = {q["id"]: q for q in questions}
    must_have_signals = {s.value for s in scored if s.knockout}

    def _first_quote(sig: str) -> str:
        for n in notes_by_signal.get(sig, []):
            if n.stance.value == "supports" and n.quote:
                return n.quote
        return ""

    q_out: list[QuestionOut] = []
    for i, qr in enumerate(evidence.questions):
        sig = qr.primary_signal
        qdict = q_text_by_id.get(qr.question_id, {})
        badge, tone = badge_for_question(
            level=final_level.get(sig, "not_reached"),
            provenance=_provenance_str(sig),
            knockout=sig in must_have_signals)
        text = qdict.get("text", "")
        q_out.append(QuestionOut(
            seq=i + 1, question_id=qr.question_id, title=text[:60],
            status_badge=badge, status_tone=tone, question_text=text,
            candidate_quote=_first_quote(sig), asked_at_ms=None))

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
