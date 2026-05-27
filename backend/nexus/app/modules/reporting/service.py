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
    KnockoutResult,
    ScoredSignal,
    apply_holistic,
    clamp_to_ceiling,
    confidence_from_coverage,
    knockout_status,
    resolve_verdict,
    score_dimension,
    score_overall,
    score_signal,
    signal_ceiling,
)
from app.modules.reporting.scoring.constants import (
    BEHAVIORAL_TYPES,
    FACTUAL_QUESTION_KINDS,
    TECHNICAL_TYPES,
    tier_label,
)
from app.modules.reporting.scoring.engine_signals import (
    build_engine_states,
    collect_signal_evidence,
    detect_knockout_close,
)
from app.modules.reporting.scoring.holistic import score_holistic
from app.modules.reporting.scoring.judge import grade_communication
from app.modules.reporting.scoring.narrative import write_narrative
from app.modules.reporting.scoring.recheck import recheck_signal
from app.modules.reporting.scoring.status import derive_status
from app.modules.reporting.scoring.transcript import segment
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


def _triage_kind_by_question(envelope: dict) -> dict[str, str]:
    """Map active_question_id -> the LAST triage kind seen for it (the
    final-state representative: a candidate who disclaims after a weak attempt
    ends as no_experience; one who engages after an initial "I don't know" ends
    as answering)."""
    turn_to_q: dict[str, str] = {}
    for e in envelope.get("events", []):
        if e.get("kind") == "turn.decision":
            p = e.get("payload") or {}
            if p.get("turn_ref") and p.get("active_question_id"):
                turn_to_q[p["turn_ref"]] = p["active_question_id"]
    out: dict[str, str] = {}
    for e in envelope.get("events", []):
        if e.get("kind") == "engine.v2.triage.decision":
            p = e.get("payload") or {}
            qid = turn_to_q.get(p.get("turn_ref"))
            if qid and p.get("kind"):
                out[qid] = p["kind"]
    return out


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


def _narrative_ground_truth(*, job_questions, scored, verdict, overall, tech, beh,
                            comm_score, knockout_close) -> str:
    return json.dumps({
        "verdict": verdict.verdict, "verdict_reason": verdict.reason,
        "scores": {"overall": overall, "technical": tech.score,
                   "behavioral": beh.score, "communication": comm_score},
        "knockout_close": (
            {"signal": knockout_close.signal, "quote": knockout_close.quote}
            if knockout_close else None),
        "signals": [{"signal": s.value, "type": s.type, "state": s.state,
                     "must_have": s.knockout, "priority": s.priority} for s in scored],
        "questions": [{"question_id": q.question_id, "question_text": q.question_text,
                       "candidate_said": q.candidate_quote, "status": q.status_badge}
                      for q in job_questions],
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_report(*, transcript, envelope, coverage_summary, questions,
                       signal_metadata, correlation_id, n_samples=None):
    """Orchestrate the three-layer report build on top of the engine coverage map.

    Layer 1 (deterministic): project the engine's coverage_summary onto the role
    signals, detect a knockout_close, score dimensions + overall, resolve the
    verdict — all pure math.

    Layer 2 (LLM re-check): for every signal the engine reached, re-check the
    candidate's evidence vs the rubric; the re-check may override the engine
    state (e.g. raise to `exceeded`).

    Layer 3 (LLM narrative): hand the final, fixed numbers to the prose writer.
    """
    signal_defs = [
        SignalDef(value=m["value"], type=m["type"], weight=m["weight"],
                  knockout=m["knockout"], priority=m["priority"])
        for m in signal_metadata
    ]
    def_by_value = {d.value: d for d in signal_defs}
    engine_states = build_engine_states(coverage_summary, signal_defs)
    knockout_close = detect_knockout_close(envelope)

    reached = [
        d for d in signal_defs
        if engine_states[d.value] != "none"
        and not _is_factual_gate_signal(d.value, questions)
    ]
    q_by_signal: dict[str, dict] = {}
    for q in questions:
        for sv in q.get("signal_values", []):
            q_by_signal.setdefault(sv, q)

    async def _one(d: SignalDef):
        ev = collect_signal_evidence(envelope, d.value)
        q = q_by_signal.get(d.value, {})
        ctx = f"Q: {q.get('text','')}\nrubric: {json.dumps(q.get('rubric', {}))}"
        return d.value, await recheck_signal(signal_def=d, evidence_turns=ev,
                                             question_context=ctx,
                                             engine_state=engine_states[d.value],
                                             correlation_id=correlation_id)
    recheck_results = dict(await asyncio.gather(*[_one(d) for d in reached])) if reached else {}

    final_state = dict(engine_states)
    for sv, rc in recheck_results.items():
        final_state[sv] = rc.state

    def _texture(sv: str) -> str:
        rc = recheck_results.get(sv)
        # un-rechecked (factual gate or none-state) → "concrete" = no bluff penalty
        # (none-state scores to None anyway)
        return rc.grade if rc else "concrete"

    scored = [ScoredSignal(value=d.value, type=d.type, weight=d.weight, knockout=d.knockout,
                           priority=d.priority, state=final_state[d.value],
                           texture=_texture(d.value),
                           score=score_signal(final_state[d.value], _texture(d.value)))
              for d in signal_defs]
    tech = score_dimension("technical", scored, TECHNICAL_TYPES)
    beh = score_dimension("behavioral", scored, BEHAVIORAL_TYPES)
    base, coverage = score_overall(scored)

    ceiling = signal_ceiling(scored, knockout_close=knockout_close is not None, coverage=coverage)
    session_score = clamp_to_ceiling(base, ceiling)

    adjustment = await score_holistic(
        session_score=session_score, scored=scored,
        knockout_close=knockout_close is not None, coverage=coverage,
        transcript_text="\n".join(t["text"] for t in transcript if t.get("role") == "candidate"),
        correlation_id=correlation_id)
    overall = apply_holistic(session_score, adjustment.delta, ceiling)

    comm = await grade_communication(
        transcript_text="\n".join(t["text"] for t in transcript if t.get("role") == "candidate"),
        correlation_id=correlation_id)
    comm_score = _COMM_POINTS[comm.level]

    knockouts = [KnockoutResult(signal=s.value, status=knockout_status(state=s.state),
                                reason="") for s in scored if s.knockout]
    verdict = resolve_verdict(overall=overall, coverage=coverage,
                              knockouts=knockouts, knockout_close=knockout_close)

    units = segment(envelope=envelope, questions=questions)
    closed_early = knockout_close is not None
    triage_kind_by_q = _triage_kind_by_question(envelope)
    q_out: list[QuestionOut] = []
    for i, u in enumerate(units):
        q = next((x for x in questions if x["id"] == u.question_id), {})
        svs = q.get("signal_values", [])
        states = {sv: final_state.get(sv, "none") for sv in svs}
        defs = {sv: (def_by_value[sv].type, def_by_value[sv].knockout, def_by_value[sv].priority)
                for sv in svs if sv in def_by_value}
        badge, tone = derive_status(
            u, signal_states=states, signal_defs=defs,
            no_experience=triage_kind_by_q.get(u.question_id) == "no_experience",
            closed_before_complete=closed_early and i == len(units) - 1)
        q_out.append(QuestionOut(
            seq=i + 1, question_id=u.question_id, title=q.get("text", "")[:60],
            status_badge=badge, status_tone=tone,
            question_text=q.get("text", ""), candidate_quote=u.candidate_answer))

    scored_by_value = {s.value: s for s in scored}
    signal_assessments = [SignalAssessmentOut(
        signal=d.value, type=d.type, weight=d.weight, knockout=d.knockout, priority=d.priority,
        engine_state=engine_states[d.value], final_state=final_state[d.value],
        grade=(recheck_results[d.value].grade if d.value in recheck_results else None),
        score=scored_by_value[d.value].score,
        evidence=(
            recheck_results[d.value].evidence_quotes if d.value in recheck_results else []),
        overridden=(
            recheck_results[d.value].overridden if d.value in recheck_results else False),
        override_reason=(
            recheck_results[d.value].override_reason if d.value in recheck_results else None),
    ) for d in signal_defs]

    gt = _narrative_ground_truth(job_questions=q_out, scored=scored, verdict=verdict,
                                 overall=overall, tech=tech, beh=beh, comm_score=comm_score,
                                 knockout_close=knockout_close)
    narrative = await write_narrative(ground_truth_json=gt, correlation_id=correlation_id)
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

    logger.info(
        "reporting.service.build_report.done",
        verdict=verdict.verdict,
        overall_score=overall,
        overall_coverage=coverage,
        correlation_id=correlation_id,
    )

    return ReportRead(
        verdict=verdict.verdict, verdict_reason=narrative.decision.headline or verdict.reason,
        overall_score=overall, overall_coverage=coverage,
        overall_confidence=confidence_from_coverage(coverage) if overall is not None else "low",
        decision=narrative.decision,
        scores={
            # Hand-rolled (not _score_out) to carry session_score + holistic_delta;
            # confidence guards overall-is-None explicitly.
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
        signal_assessments=signal_assessments, engine_version="v2", status="ready",
        scoring_manifest=ScoringManifest(
            scorer_model=ai_config.report_scorer_model,
            prompt_version=ai_config.report_scorer_prompt_version,
            generated_at=datetime.now(UTC).isoformat(), correlation_id=correlation_id,
            evidence_grounding_summary={
                "n_signals_rechecked": len(recheck_results),
                "n_overrides": sum(1 for r in recheck_results.values() if r.overridden),
                "coverage_map": {k: final_state[k] for k in final_state},
                "session_score": session_score,
                "holistic_delta": adjustment.delta,
                "holistic_justification": adjustment.justification,
                "ceiling_applied": ceiling,
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
        engine_version=report.engine_version or "v2",
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
