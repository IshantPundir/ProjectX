"""Shared loader for question-generation prompt context.

Used by:
  - question_bank.actors._generate_one_bank (full-bank generation)
  - question_bank.refine.refine_single_question (Refine endpoint, Task 16)
  - question_bank.refine.draft_single_question (Add endpoint, Task 16)
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.org_units.service import find_company_profile_in_ancestry


@dataclass
class QuestionContext:
    """Context shape consumed by the per-stage and single-question prompt templates.

    All list/dict fields that come from DB queries are stored as their native
    Python types (not pre-serialised JSON) so callers can inspect them directly.
    The ``*_json`` properties serialise on demand for prompt assembly.
    """

    # Raw loaded data
    company_profile: dict | None
    pipeline_stages: list[dict]
    prior_stages_questions: list[dict]

    # Stage metadata (derived from the stage ORM object — passed in from callers
    # that already hold the ORM row, so no extra round-trip needed).
    stage_name: str
    stage_type: str
    stage_difficulty: str
    stage_duration_minutes: int
    signal_filter_types: list[str]
    pass_criteria: dict

    # Existing bank questions for the current stage (empty for first generation).
    existing_bank_questions: list[dict] = field(default_factory=list)

    # -----------------------------------------------------------------------
    # Convenience JSON serialisations used by prompt templates (Task 16).
    # -----------------------------------------------------------------------

    @property
    def pass_criteria_json(self) -> str:
        return _json.dumps(self.pass_criteria, indent=2)

    @property
    def existing_bank_json(self) -> str:
        return _json.dumps(self.existing_bank_questions, indent=2)

    @property
    def prior_banks_json(self) -> str:
        return _json.dumps(self.prior_stages_questions, indent=2)


async def _load_pipeline_stages(
    db: AsyncSession, *, instance_id: UUID
) -> list[dict]:
    """Load all stages in the instance with their metadata, ordered by position."""
    result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance_id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(result.scalars().all())
    return [
        {
            "id": str(s.id),
            "position": s.position,
            "name": s.name,
            "stage_type": s.stage_type,
            "duration_minutes": s.duration_minutes,
            "difficulty": s.difficulty,
            "advance_behavior": s.advance_behavior,
        }
        for s in stages
    ]


async def _load_prior_stage_questions(
    db: AsyncSession, *, instance_id: UUID, current_position: int
) -> list[dict]:
    """Load questions from stages with position < current_position, grouped by stage."""
    stage_result = await db.execute(
        select(JobPipelineStage)
        .where(
            JobPipelineStage.instance_id == instance_id,
            JobPipelineStage.position < current_position,
        )
        .order_by(JobPipelineStage.position)
    )
    prior_stages = list(stage_result.scalars().all())

    out = []
    for stage in prior_stages:
        bank_result = await db.execute(
            select(StageQuestionBank).where(StageQuestionBank.stage_id == stage.id)
        )
        bank = bank_result.scalar_one_or_none()
        questions: list[dict] = []
        if bank is not None:
            q_result = await db.execute(
                select(StageQuestion)
                .where(StageQuestion.bank_id == bank.id)
                .order_by(StageQuestion.position)
            )
            for q in q_result.scalars().all():
                questions.append(
                    {
                        "position": q.position,
                        "text": q.text,
                        "signal_values": q.signal_values,
                        "is_mandatory": q.is_mandatory,
                        "rubric_meets_bar": q.rubric.get("meets_bar", ""),
                    }
                )
        out.append(
            {
                "stage_name": stage.name,
                "stage_type": stage.stage_type,
                "duration_minutes": stage.duration_minutes,
                "difficulty": stage.difficulty,
                "questions": questions,
            }
        )
    return out


async def _load_existing_bank_questions(
    db: AsyncSession, *, stage_id: UUID
) -> list[dict]:
    """Load the current bank's questions for the given stage (may be empty)."""
    bank_result = await db.execute(
        select(StageQuestionBank).where(StageQuestionBank.stage_id == stage_id)
    )
    bank = bank_result.scalar_one_or_none()
    if bank is None:
        return []
    q_result = await db.execute(
        select(StageQuestion)
        .where(StageQuestion.bank_id == bank.id)
        .order_by(StageQuestion.position)
    )
    return [
        {
            "position": q.position,
            "text": q.text,
            "signal_values": q.signal_values,
            "is_mandatory": q.is_mandatory,
            "rubric_meets_bar": q.rubric.get("meets_bar", ""),
        }
        for q in q_result.scalars().all()
    ]


async def build_question_context(
    db: AsyncSession,
    *,
    job: JobPosting,
    instance: JobPipelineInstance,
    stage: JobPipelineStage,
) -> QuestionContext:
    """Build the full context for question generation / refinement / drafting on a stage.

    Runs three DB queries:
    1. Company profile ancestry walk (org_units tree).
    2. All pipeline stages for the instance (ordered by position).
    3. Prior-stage question banks (stages with position < stage.position).

    Plus an optional fourth query to load the stage's existing bank questions
    (used by refine / draft endpoints in Task 16; returns [] for a fresh bank).
    """
    company_profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    pipeline_stages = await _load_pipeline_stages(db, instance_id=instance.id)
    prior_stages_questions = await _load_prior_stage_questions(
        db, instance_id=instance.id, current_position=stage.position
    )
    existing_bank_questions = await _load_existing_bank_questions(
        db, stage_id=stage.id
    )

    return QuestionContext(
        company_profile=company_profile,
        pipeline_stages=pipeline_stages,
        prior_stages_questions=prior_stages_questions,
        stage_name=stage.name,
        stage_type=stage.stage_type,
        stage_difficulty=stage.difficulty,
        stage_duration_minutes=stage.duration_minutes,
        signal_filter_types=(stage.signal_filter or {}).get("include_types", []),
        pass_criteria=stage.pass_criteria or {},
        existing_bank_questions=existing_bank_questions,
    )
