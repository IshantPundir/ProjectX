"""Export ALL context data consumed by the interview AI agent for one job.

This is a read-only forensic dump. It walks the SAME path the live engine's
`interview_runtime.build_session_config` walks (job -> pipeline -> stages ->
bank -> latest confirmed signal snapshot -> ancestry-walked company profile ->
questions), but at the JOB level instead of a single candidate session, so it
captures the context for every AI-driven stage of the job.

Run inside the nexus container (it has DB connectivity + all ORM models):

    docker compose exec nexus python /app/scripts/export_job_agent_context.py \
        <job_id> /app/tmp/<outfile>.json

Output mirrors the engine's `SessionConfig` wire contract per stage, plus the
upstream raw sources (raw signal snapshot, full org-unit ancestry) and a
per-stage engine-readiness verdict.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import desc, select

from app.database import get_bypass_session
from app.modules.jd import JobPosting, JobPostingSignalSnapshot
from app.modules.org_units import (
    OrganizationalUnit,
    find_company_profile_in_ancestry,
    get_org_unit_ancestry,
)
from app.modules.pipelines import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank import StageQuestion, StageQuestionBank

# The exact projection the engine uses to turn snapshot.signals JSONB into
# the per-signal metadata the Judge sees (weight / knockout / priority /
# evaluation_method). Importing it keeps this dump faithful to the engine.
from app.modules.interview_runtime.service import _project_signal_metadata
from app.modules.interview_runtime import SessionConfig
from app.modules.interview_engine.speaker.persona import DEFAULT_PERSONA

# Stage types the engine will actually drive (everything else is human-led /
# bookend). Mirrors interview_runtime.service._AI_STAGE_TYPES.
AI_STAGE_TYPES = frozenset({"ai_screening", "phone_screen"})


def _enc(o: object) -> object:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"not JSON serializable: {type(o)!r}")


def _question_row(q: StageQuestion) -> dict:
    """Full per-question payload — every field the engine consumes plus the
    recruiter-facing provenance fields, so the dump is a superset."""
    return {
        "id": str(q.id),
        "position": q.position,
        "source": q.source,
        "text": q.text,
        "signal_values": list(q.signal_values),
        "estimated_minutes": float(q.estimated_minutes),
        "is_mandatory": q.is_mandatory,
        "question_kind": q.question_kind,
        "difficulty": q.difficulty,
        "follow_ups": q.follow_ups,
        "positive_evidence": q.positive_evidence,
        "red_flags": q.red_flags,
        "rubric": q.rubric,
        "evaluation_hint": q.evaluation_hint,
        "edited_by_recruiter": q.edited_by_recruiter,
    }


async def export(job_id: uuid.UUID) -> dict:
    out: dict = {
        "_meta": {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "job_id": str(job_id),
            "description": (
                "Context data consumed by the ProjectX interview AI agent for "
                "this job. Walks the same path as "
                "interview_runtime.build_session_config, at job scope. Each "
                "AI-driven stage gets a SessionConfig-equivalent block — that "
                "object IS the engine's full input contract."
            ),
        },
        "warnings": [],
    }

    async with get_bypass_session() as db:
        job = (
            await db.execute(select(JobPosting).where(JobPosting.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            raise SystemExit(f"job {job_id} not found")

        tenant_id = job.tenant_id

        # ── Job-level context ──────────────────────────────────────────────
        out["job"] = {
            "id": str(job.id),
            "tenant_id": str(tenant_id),
            "org_unit_id": str(job.org_unit_id) if job.org_unit_id else None,
            "title": job.title,
            "status": job.status,
            "enrichment_status": job.enrichment_status,
            "seniority_level": None,  # filled from snapshot below
            "employment_type": job.employment_type,
            "work_arrangement": job.work_arrangement,
            "location": job.location,
            # jd_text the engine threads to the Speaker for clarify(role_context):
            # enriched JD preferred, raw JD as fallback (see SessionConfig.jd_text).
            "description_raw": job.description_raw,
            "description_enriched": job.description_enriched,
            "enriched_manually_edited": job.enriched_manually_edited,
            "project_scope_raw": job.project_scope_raw,
        }

        # ── Org-unit ancestry + company profile (ancestry-walked) ──────────
        ancestry: list[OrganizationalUnit] = []
        company_profile = None
        hiring_company_name = None
        if job.org_unit_id is not None:
            ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
            company_profile = await find_company_profile_in_ancestry(
                db, job.org_unit_id
            )
            if ancestry:
                hiring_company_name = ancestry[0].name
        else:
            out["warnings"].append("job has no org_unit_id — no company profile / ancestry")

        out["org_unit_ancestry"] = [
            {
                "id": str(u.id),
                "name": u.name,
                "unit_type": u.unit_type,
                "is_root": u.is_root,
                "depth_from_job": i,
                "parent_unit_id": str(u.parent_unit_id) if u.parent_unit_id else None,
            }
            for i, u in enumerate(ancestry)
        ]
        # hiring_company_name = closest org unit to the job (depth 0). This is
        # the company the candidate is interviewing FOR — NOT the ProjectX
        # tenant (which may be a staffing agency). Used by the intro_brief turn.
        out["hiring_company_name"] = hiring_company_name
        # company_profile = {about, industry, hiring_bar} from the first
        # client_account/company owner in the ancestry. None when incomplete —
        # which would BLOCK session dispatch (CompanyProfileMissingError).
        out["company_profile"] = company_profile
        if company_profile is None and job.org_unit_id is not None:
            out["warnings"].append(
                "find_company_profile_in_ancestry returned None — the activation "
                "gate would BLOCK a live session (CompanyProfileMissingError)."
            )

        # ── Latest CONFIRMED signal snapshot ───────────────────────────────
        snapshot = (
            await db.execute(
                select(JobPostingSignalSnapshot)
                .where(
                    JobPostingSignalSnapshot.job_posting_id == job.id,
                    JobPostingSignalSnapshot.confirmed_at.is_not(None),
                )
                .order_by(desc(JobPostingSignalSnapshot.version))
                .limit(1)
            )
        ).scalar_one_or_none()

        signal_metadata_dump: list[dict] = []
        flat_signals: list[str] = []
        if snapshot is None:
            out["warnings"].append(
                "no CONFIRMED signal snapshot — a live session cannot start "
                "(build_session_config raises before dispatch)."
            )
            out["signal_snapshot"] = None
        else:
            out["job"]["seniority_level"] = snapshot.seniority_level
            sig_meta = _project_signal_metadata(snapshot.signals or [])
            signal_metadata_dump = [m.model_dump(mode="json") for m in sig_meta]
            flat_signals = [
                s["value"] if isinstance(s, dict) and "value" in s else str(s)
                for s in (snapshot.signals or [])
            ]
            out["signal_snapshot"] = {
                "id": str(snapshot.id),
                "version": snapshot.version,
                "confirmed_at": snapshot.confirmed_at.isoformat()
                if snapshot.confirmed_at
                else None,
                "seniority_level": snapshot.seniority_level,
                "role_summary": snapshot.role_summary,
                "prompt_version": snapshot.prompt_version,
                # raw signals JSONB exactly as stored (recruiter-facing,
                # includes provenance: source / inference_basis):
                "signals_raw": snapshot.signals,
                # engine-projected views:
                "signals_flat": flat_signals,
                "signal_metadata": signal_metadata_dump,
            }

        # ── Pipeline instance + stages ─────────────────────────────────────
        instance = (
            await db.execute(
                select(JobPipelineInstance).where(
                    JobPipelineInstance.job_posting_id == job.id
                )
            )
        ).scalar_one_or_none()

        out["pipeline"] = None
        if instance is None:
            out["warnings"].append("job has no pipeline instance")
            out["stages"] = []
            return out

        stages = (
            await db.execute(
                select(JobPipelineStage)
                .where(JobPipelineStage.instance_id == instance.id)
                .order_by(JobPipelineStage.position.asc())
            )
        ).scalars().all()

        out["pipeline"] = {
            "instance_id": str(instance.id),
            "pipeline_version": instance.pipeline_version,
            "source_template_id": str(instance.source_template_id)
            if instance.source_template_id
            else None,
            "stage_count": len(stages),
        }

        stage_dumps: list[dict] = []
        for stage in stages:
            is_ai = stage.stage_type in AI_STAGE_TYPES
            stage_block: dict = {
                "stage_id": str(stage.id),
                "position": stage.position,
                "name": stage.name,
                "stage_type": stage.stage_type,
                "is_ai_driven": is_ai,
                "duration_minutes": stage.duration_minutes,
                "difficulty": stage.difficulty,
                "advance_behavior": stage.advance_behavior,
                "signal_filter": stage.signal_filter,
                "pass_criteria": stage.pass_criteria,
                "otp_required_default": stage.otp_required_default,
                "paused_at": stage.paused_at.isoformat() if stage.paused_at else None,
            }

            # Load the bank for this stage (1:1).
            bank = (
                await db.execute(
                    select(StageQuestionBank).where(
                        StageQuestionBank.stage_id == stage.id
                    )
                )
            ).scalar_one_or_none()

            if bank is None:
                stage_block["question_bank"] = None
            else:
                questions = (
                    await db.execute(
                        select(StageQuestion)
                        .where(StageQuestion.bank_id == bank.id)
                        # Same ordering the engine applies in build_session_config:
                        .order_by(
                            StageQuestion.is_mandatory.desc(),
                            StageQuestion.position.asc(),
                        )
                    )
                ).scalars().all()
                stage_block["question_bank"] = {
                    "bank_id": str(bank.id),
                    "status": bank.status,
                    "is_stale": bank.is_stale,
                    "prompt_version": bank.prompt_version,
                    "pipeline_version_at_generation": bank.pipeline_version_at_generation,
                    "signal_snapshot_id": str(bank.signal_snapshot_id),
                    "confirmed_at": bank.confirmed_at.isoformat()
                    if bank.confirmed_at
                    else None,
                    "coverage_notes": bank.coverage_notes,
                    "generation_status_by_kind": bank.generation_status_by_kind,
                    # STT keyterm-prompting list cached at generation time.
                    "extracted_keyterms": bank.extracted_keyterms,
                    "question_count": len(questions),
                    "mandatory_count": sum(1 for q in questions if q.is_mandatory),
                    "questions": [_question_row(q) for q in questions],
                }

            # For AI-driven stages, assemble the engine-readiness verdict and
            # the exact SessionConfig the engine would receive.
            if is_ai:
                stage_block["engine_readiness"] = _readiness(
                    stage=stage, bank=bank, snapshot=snapshot,
                    company_profile=company_profile, signal_metadata=signal_metadata_dump,
                )
                stage_block["session_config_for_engine"] = _build_session_config_block(
                    job=job, stage=stage, bank=bank, snapshot=snapshot,
                    hiring_company_name=hiring_company_name,
                    company_profile=company_profile, flat_signals=flat_signals,
                    signal_metadata=signal_metadata_dump,
                    questions=stage_block.get("question_bank", {}).get("questions", [])
                    if stage_block.get("question_bank") else [],
                    warnings=out["warnings"],
                )

            stage_dumps.append(stage_block)

        out["stages"] = stage_dumps

    # ── Static engine context the agent always consumes (not job-specific) ──
    out["engine_static_context"] = {
        "note": (
            "Identity + behavior the Speaker LLM is grounded on every turn. "
            "Locked in code (not per-tenant). Included so the full agent input "
            "surface is visible alongside the job-specific config above."
        ),
        "persona": {
            "name": DEFAULT_PERSONA.name,
            "register": DEFAULT_PERSONA.register,
            "behavior_bullets": list(DEFAULT_PERSONA.behavior_bullets),
            "vocab_banned": list(DEFAULT_PERSONA.vocab_banned),
            "fallback_session_ended": DEFAULT_PERSONA.fallback_session_ended,
        },
    }
    return out


def _readiness(*, stage, bank, snapshot, company_profile, signal_metadata) -> dict:
    """Mirror the gates in build_session_config that would block a session."""
    reasons: list[str] = []
    if bank is None:
        reasons.append("no question bank for stage")
    else:
        if bank.status != "confirmed":
            reasons.append(f"bank.status={bank.status!r} (need 'confirmed')")
        if bank.is_stale:
            reasons.append("bank.is_stale=True (pipeline edited since generation)")
    if snapshot is None:
        reasons.append("no confirmed signal snapshot")
    if company_profile is None:
        reasons.append("company profile incomplete/missing in ancestry")
    if not signal_metadata:
        reasons.append("empty signal_metadata (EmptySignalMetadataError)")
    return {"engine_would_dispatch": not reasons, "blocking_reasons": reasons}


def _build_session_config_block(
    *, job, stage, bank, snapshot, hiring_company_name, company_profile,
    flat_signals, signal_metadata, questions, warnings,
) -> dict:
    """Build the exact dict the engine receives as SessionConfig for this
    stage (candidate fields are placeholders — they're per-session, not
    per-job). Also attempts strict SessionConfig validation and reports it."""
    cp = company_profile or {}
    # Engine applies `difficulty = q.difficulty or stage.difficulty` per question
    # (build_session_config, service.py:236). Mirror that fallback here so the
    # questions in this block match exactly what the engine constructs — the
    # raw NULL-difficulty rows live under stages[].question_bank.questions.
    engine_questions = []
    for q in questions:
        eq = dict(q)
        eq["difficulty"] = q.get("difficulty") or stage.difficulty or "medium"
        engine_questions.append(eq)
    block = {
        "session_id": "<assigned-per-session>",
        "job_id": str(job.id),
        "candidate_id": "<assigned-per-session>",
        "job_title": job.title,
        "hiring_company_name": hiring_company_name,
        "role_summary": snapshot.role_summary if snapshot else None,
        "jd_text": job.description_enriched or job.description_raw,
        "seniority_level": snapshot.seniority_level if snapshot else None,
        "company": {
            "about": cp.get("about", ""),
            "industry": cp.get("industry", ""),
            "company_stage": cp.get("company_stage", ""),
            "hiring_bar": cp.get("hiring_bar", ""),
        },
        "candidate": {"name": "<candidate first name per session>"},
        "stage": {
            "stage_id": str(stage.id),
            "stage_type": stage.stage_type,
            "name": stage.name,
            "duration_minutes": stage.duration_minutes or 30,
            "difficulty": stage.difficulty,
            "advance_behavior": stage.advance_behavior or "manual_review",
            "questions": engine_questions,
        },
        "signals": flat_signals,
        "signal_metadata": signal_metadata,
        "keyterms": list(bank.extracted_keyterms)
        if (bank and bank.extracted_keyterms is not None)
        else [],
    }

    # Strict-validation probe: would the wire contract actually accept this?
    validation = {"valid": None, "errors": None}
    if snapshot is not None and company_profile is not None and questions:
        try:
            probe = dict(block)
            probe["session_id"] = str(uuid.uuid4())
            probe["candidate_id"] = str(uuid.uuid4())
            probe["candidate"] = {"name": "Probe"}
            SessionConfig.model_validate(probe)
            validation["valid"] = True
        except Exception as exc:  # noqa: BLE001
            validation["valid"] = False
            validation["errors"] = str(exc)[:2000]
    block["_strict_sessionconfig_validation"] = validation
    return block


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: export_job_agent_context.py <job_id> <outfile>")
    job_id = uuid.UUID(sys.argv[1])
    outfile = Path(sys.argv[2])
    data = asyncio.run(export(job_id))
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text(json.dumps(data, indent=2, default=_enc, ensure_ascii=False))
    n_ai = sum(1 for s in data.get("stages", []) if s.get("is_ai_driven"))
    print(
        f"WROTE {outfile} | stages={len(data.get('stages', []))} "
        f"ai_stages={n_ai} warnings={len(data.get('warnings', []))}"
    )


if __name__ == "__main__":
    main()
