"""Resolve human-facing labels (candidate / job / stage) for a session.

Shared by the public share envelope and the PDF share actor. Best-effort: any
missing link in the chain falls back to a generic label, never raises.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.candidates.models import Candidate, CandidateJobAssignment
from app.modules.jd.models import JobPosting
from app.modules.pipelines.models import JobPipelineStage
from app.modules.session.models import Session as SessionRow


async def load_session_labels(
    db: AsyncSession, *, session_id: UUID, tenant_id: UUID
) -> tuple[str, str, str]:
    """Return (candidate_name, job_title, stage_label)."""
    sess = (await db.execute(
        select(SessionRow).where(
            SessionRow.id == session_id, SessionRow.tenant_id == tenant_id)
    )).scalar_one_or_none()
    assignment = (await db.execute(
        select(CandidateJobAssignment).where(
            CandidateJobAssignment.id == sess.assignment_id,
            CandidateJobAssignment.tenant_id == tenant_id)
    )).scalar_one_or_none() if sess else None
    candidate = (await db.execute(
        select(Candidate).where(Candidate.id == assignment.candidate_id,
                                Candidate.tenant_id == tenant_id)
    )).scalar_one_or_none() if assignment else None
    job = (await db.execute(
        select(JobPosting).where(JobPosting.id == assignment.job_posting_id,
                                 JobPosting.tenant_id == tenant_id)
    )).scalar_one_or_none() if assignment else None
    stage = (await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.id == sess.stage_id,
                                       JobPipelineStage.tenant_id == tenant_id)
    )).scalar_one_or_none() if sess else None

    candidate_name = (candidate.name if candidate else None) or "Candidate"
    job_title = (job.title if job else None) or "Role"
    stage_label = (stage.name if stage else None) or "Interview"
    return candidate_name, job_title, stage_label
