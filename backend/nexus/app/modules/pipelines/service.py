"""Pipeline Builder service layer.

Template CRUD (per org unit), instance creation/mutation (per job),
and the ensure_minimal_pipeline_for_job helper that jd.confirm_signals
calls to auto-create the bookend Intake → Debrief pipeline on signal
confirmation.

All functions take an AsyncSession — transaction management is the caller's
responsibility (FastAPI dependency handles commit/rollback)."""

from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth import User
from app.modules.candidates import CandidateJobAssignment
from app.modules.jd import JobPosting
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    PipelineTemplate,
    PipelineTemplateStage,
)
from app.modules.question_bank import StageQuestionBank, recompute_and_persist_stale
from app.modules.pipelines.errors import (
    CannotDeleteDefaultError,
    JobNotInConfirmedStateError,
    NoSourceTemplateError,
    PipelineAlreadyExistsError,
    StageOtpNotApplicableError,
    StagePauseForbiddenError,
    StarterKeyNotFoundError,
)
from app.modules.pipelines.schemas import (
    CreateJobPipelineFromScratch,
    CreateJobPipelineFromStarter,
    CreateJobPipelineFromTemplate,
    CreateJobPipelineRequest,
    PipelineStageInput,
    PipelineStageUpdateInput,
    StageParticipantInput,
)
from app.modules.pipelines.participants import (
    replace_stage_participants,
    validate_participants_eligible,
)
from app.modules.pipelines.starter_pack import STARTER_TEMPLATES

logger = structlog.get_logger()


async def bump_pipeline_version(
    db: AsyncSession, instance: JobPipelineInstance
) -> None:
    """Atomically increment the instance's pipeline_version.

    Caller must be inside the same transaction as the mutation that triggers
    the bump. The increment is flushed immediately so subsequent reads within
    the same transaction see the new value.
    """
    instance.pipeline_version = instance.pipeline_version + 1
    await db.flush()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _seed_bookends(
    stages: list[JobPipelineStage],
    *,
    tenant_id: UUID,
    instance_id: UUID,
) -> list[JobPipelineStage]:
    """Ensure the stage list has an intake at position 0 and a debrief at the end.

    Mutates nothing.  Returns a NEW list (bookends prepended/appended as needed)
    with positions re-indexed 0..N-1.  Callers must add all returned rows to the
    session — only the newly-created bookend rows need adding; existing rows are
    returned as-is so callers can simply call db.add() on all of them without
    double-inserting (SQLAlchemy de-dupes adds for already-tracked objects).

    Columns that are FORBIDDEN / NULL-relaxed for intake/debrief (migration 0019):
      - duration_minutes, difficulty, signal_filter  → None
      - pass_criteria                                → None  (LOCKED: server stamps)
      - advance_behavior                             → None  (LOCKED: server stamps)
    The Pydantic validator stamps pass_criteria and advance_behavior to their
    canonical values on the way through the API; when we create rows directly
    here we set them explicitly so the DB value is authoritative regardless of
    whether the validator runs.
    """
    result = list(stages)

    if not result or result[0].stage_type != "intake":
        intake = JobPipelineStage(
            tenant_id=tenant_id,
            instance_id=instance_id,
            position=0,  # will be re-indexed below
            name="Intake",
            stage_type="intake",
            duration_minutes=None,
            difficulty=None,
            signal_filter=None,
            pass_criteria=None,
            advance_behavior="auto_advance",
            sla_days=None,
            otp_required_default=False,
        )
        result.insert(0, intake)

    if result[-1].stage_type != "debrief":
        debrief = JobPipelineStage(
            tenant_id=tenant_id,
            instance_id=instance_id,
            position=len(result),  # will be re-indexed below
            name="Debrief",
            stage_type="debrief",
            duration_minutes=None,
            difficulty=None,
            signal_filter=None,
            pass_criteria=None,
            advance_behavior="manual_review",
            sla_days=None,
            otp_required_default=False,
        )
        result.append(debrief)

    # Re-index positions to be contiguous 0..N-1.
    for idx, stage in enumerate(result):
        stage.position = idx

    return result


def _stage_input_to_row_dict(
    stage: PipelineStageInput,
    tenant_id: UUID,
    template_id: UUID | None = None,
    instance_id: UUID | None = None,
) -> dict:
    """Convert a PipelineStageInput into a dict for row constructors.

    intake and debrief stages have FORBIDDEN fields that the validator sets to
    None — the DB columns for those fields are now nullable (migration 0019).
    Guard every optional field with an explicit None check before calling
    .model_dump() so we never crash with AttributeError on None.model_dump().
    """
    base = {
        "tenant_id": tenant_id,
        "position": stage.position,
        "name": stage.name,
        "stage_type": stage.stage_type,
        "duration_minutes": stage.duration_minutes,
        "difficulty": stage.difficulty,
        "signal_filter": stage.signal_filter.model_dump() if stage.signal_filter is not None else None,
        "pass_criteria": stage.pass_criteria.model_dump() if stage.pass_criteria is not None else None,
        "advance_behavior": stage.advance_behavior,
        "sla_days": stage.sla_days,
    }
    if template_id is not None:
        base["template_id"] = template_id
    if instance_id is not None:
        base["instance_id"] = instance_id
    return base


# ---------------------------------------------------------------------------
# Template CRUD
# ---------------------------------------------------------------------------


async def list_templates_for_org_unit(
    db: AsyncSession, org_unit_id: UUID
) -> list[tuple[PipelineTemplate, list[PipelineTemplateStage]]]:
    """List all templates in an org unit's library with their stages."""
    result = await db.execute(
        select(PipelineTemplate)
        .where(PipelineTemplate.org_unit_id == org_unit_id)
        .order_by(desc(PipelineTemplate.is_default), PipelineTemplate.created_at)
    )
    templates = list(result.scalars().all())
    if not templates:
        return []

    template_ids = [t.id for t in templates]
    stages_result = await db.execute(
        select(PipelineTemplateStage)
        .where(PipelineTemplateStage.template_id.in_(template_ids))
        .order_by(PipelineTemplateStage.template_id, PipelineTemplateStage.position)
    )
    stages_by_template: dict[UUID, list[PipelineTemplateStage]] = {}
    for stage in stages_result.scalars().all():
        stages_by_template.setdefault(stage.template_id, []).append(stage)

    return [(t, stages_by_template.get(t.id, [])) for t in templates]


async def get_template_with_stages(
    db: AsyncSession, template_id: UUID
) -> tuple[PipelineTemplate, list[PipelineTemplateStage]] | None:
    """Load a single template and its stages."""
    template_result = await db.execute(
        select(PipelineTemplate).where(PipelineTemplate.id == template_id)
    )
    template = template_result.scalar_one_or_none()
    if template is None:
        return None

    stages_result = await db.execute(
        select(PipelineTemplateStage)
        .where(PipelineTemplateStage.template_id == template_id)
        .order_by(PipelineTemplateStage.position)
    )
    stages = list(stages_result.scalars().all())
    return template, stages


async def _clear_existing_default(db: AsyncSession, org_unit_id: UUID) -> None:
    """Clear `is_default` on any existing default template in this org unit.
    Called before setting a new default to satisfy the partial unique index."""
    result = await db.execute(
        select(PipelineTemplate).where(
            and_(
                PipelineTemplate.org_unit_id == org_unit_id,
                PipelineTemplate.is_default == True,
            )
        )
    )
    for tpl in result.scalars().all():
        tpl.is_default = False
    await db.flush()


async def create_template_from_scratch(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    org_unit_id: UUID,
    created_by: UUID,
    name: str,
    description: str | None,
    is_default: bool,
    stages: list[PipelineStageInput],
) -> PipelineTemplate:
    """Create a new template with the given stages."""
    if is_default:
        await _clear_existing_default(db, org_unit_id)

    template = PipelineTemplate(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        name=name,
        description=description,
        is_default=is_default,
        from_starter=None,
        created_by=created_by,
    )
    db.add(template)
    await db.flush()

    for stage in stages:
        row = PipelineTemplateStage(
            **_stage_input_to_row_dict(stage, tenant_id, template_id=template.id)
        )
        db.add(row)

    await db.flush()
    logger.info(
        "pipelines.template_created",
        template_id=str(template.id),
        org_unit_id=str(org_unit_id),
        from_starter=None,
    )
    return template


async def create_template_from_starter(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    org_unit_id: UUID,
    created_by: UUID,
    starter_key: str,
    name: str,
    description: str | None,
    is_default: bool,
) -> PipelineTemplate:
    """Copy a starter pack template into the org unit's library."""
    if starter_key not in STARTER_TEMPLATES:
        raise StarterKeyNotFoundError(starter_key)

    starter = STARTER_TEMPLATES[starter_key]

    if is_default:
        await _clear_existing_default(db, org_unit_id)

    template = PipelineTemplate(
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        name=name,
        description=description or starter.get("description"),
        is_default=is_default,
        from_starter=starter_key,
        created_by=created_by,
    )
    db.add(template)
    await db.flush()

    for stage in starter["stages"]:
        row = PipelineTemplateStage(
            tenant_id=tenant_id,
            template_id=template.id,
            position=stage["position"],
            name=stage["name"],
            stage_type=stage["stage_type"],
            duration_minutes=stage["duration_minutes"],
            difficulty=stage["difficulty"],
            signal_filter=stage["signal_filter"],
            pass_criteria=stage["pass_criteria"],
            advance_behavior=stage["advance_behavior"],
            sla_days=stage.get("sla_days"),
        )
        db.add(row)

    await db.flush()
    logger.info(
        "pipelines.template_created",
        template_id=str(template.id),
        org_unit_id=str(org_unit_id),
        from_starter=starter_key,
    )
    return template


async def update_template(
    db: AsyncSession,
    *,
    template: PipelineTemplate,
    name: str | None,
    description: str | None,
    stages: list[PipelineStageInput] | None,
    actor_id: UUID,
) -> PipelineTemplate:
    """Update template fields. If stages are provided, replaces all stages atomically."""
    if name is not None:
        template.name = name
    if description is not None:
        template.description = description
    template.updated_by = actor_id
    template.updated_at = _now_utc()

    if stages is not None:
        existing = await db.execute(
            select(PipelineTemplateStage).where(
                PipelineTemplateStage.template_id == template.id
            )
        )
        for s in existing.scalars().all():
            await db.delete(s)
        await db.flush()

        for stage in stages:
            row = PipelineTemplateStage(
                **_stage_input_to_row_dict(stage, template.tenant_id, template_id=template.id)
            )
            db.add(row)
        await db.flush()

    logger.info("pipelines.template_updated", template_id=str(template.id))
    return template


async def set_template_as_default(
    db: AsyncSession, template: PipelineTemplate, actor_id: UUID
) -> PipelineTemplate:
    """Atomically clear the existing default and set this one."""
    await _clear_existing_default(db, template.org_unit_id)
    template.is_default = True
    template.updated_by = actor_id
    template.updated_at = _now_utc()
    await db.flush()
    logger.info("pipelines.template_set_default", template_id=str(template.id))
    return template


async def delete_template(db: AsyncSession, template: PipelineTemplate) -> None:
    """Delete a template. Refuses if it's the default."""
    if template.is_default:
        raise CannotDeleteDefaultError()
    await db.delete(template)
    await db.flush()
    logger.info("pipelines.template_deleted", template_id=str(template.id))


# ---------------------------------------------------------------------------
# Job pipeline instances
# ---------------------------------------------------------------------------


async def get_job_pipeline_with_stages(
    db: AsyncSession, job_posting_id: UUID
) -> tuple[
    JobPipelineInstance,
    list[JobPipelineStage],
    PipelineTemplate | None,
    dict[UUID, list[dict]],  # participants keyed by stage_id
] | None:
    """Load a job pipeline instance, its stages, source template (if linked),
    and participants for each stage (empty list for stages with none)."""
    instance_result = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job_posting_id
        )
    )
    instance = instance_result.scalar_one_or_none()
    if instance is None:
        return None

    stages_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    stages = list(stages_result.scalars().all())

    source_template: PipelineTemplate | None = None
    if instance.source_template_id is not None:
        tpl_result = await db.execute(
            select(PipelineTemplate).where(
                PipelineTemplate.id == instance.source_template_id
            )
        )
        source_template = tpl_result.scalar_one_or_none()

    # Bulk-load participants joined with users for display fields.
    participants_by_stage: dict[UUID, list[dict]] = {s.id: [] for s in stages}
    if stages:
        stage_ids = [s.id for s in stages]
        part_result = await db.execute(
            select(PipelineStageParticipant, User)
            .join(User, User.id == PipelineStageParticipant.user_id)
            .where(PipelineStageParticipant.stage_id.in_(stage_ids))
        )
        for part, user in part_result.all():
            participants_by_stage[part.stage_id].append(
                {
                    "user_id": part.user_id,
                    "role": part.role,
                    "full_name": user.full_name or "",
                    "email": user.email,
                }
            )

    return instance, stages, source_template, participants_by_stage


async def create_job_pipeline_from_template(
    db: AsyncSession,
    *,
    job: JobPosting,
    template_id: UUID,
) -> JobPipelineInstance:
    """Create an instance by copying a template's stages."""
    if job.status != "signals_confirmed":
        raise JobNotInConfirmedStateError(job.status)

    existing = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise PipelineAlreadyExistsError()

    tpl_result = await db.execute(
        select(PipelineTemplate).where(PipelineTemplate.id == template_id)
    )
    template = tpl_result.scalar_one_or_none()
    if template is None:
        raise ValueError(f"Template {template_id} not found")

    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=template.id,
    )
    db.add(instance)
    await db.flush()

    stages_result = await db.execute(
        select(PipelineTemplateStage)
        .where(PipelineTemplateStage.template_id == template.id)
        .order_by(PipelineTemplateStage.position)
    )
    copied_stages: list[JobPipelineStage] = [
        JobPipelineStage(
            tenant_id=job.tenant_id,
            instance_id=instance.id,
            position=src_stage.position,
            name=src_stage.name,
            stage_type=src_stage.stage_type,
            duration_minutes=src_stage.duration_minutes,
            difficulty=src_stage.difficulty,
            signal_filter=src_stage.signal_filter,
            pass_criteria=src_stage.pass_criteria,
            advance_behavior=src_stage.advance_behavior,
            sla_days=src_stage.sla_days,
        )
        for src_stage in stages_result.scalars().all()
    ]
    final_stages = _seed_bookends(
        copied_stages, tenant_id=job.tenant_id, instance_id=instance.id
    )
    for stage in final_stages:
        db.add(stage)
    await db.flush()
    logger.info(
        "pipelines.job_instance_created",
        job_posting_id=str(job.id),
        instance_id=str(instance.id),
        source="template",
        template_id=str(template.id),
    )
    return instance


async def create_job_pipeline_from_starter(
    db: AsyncSession,
    *,
    job: JobPosting,
    starter_key: str,
) -> JobPipelineInstance:
    """Create an instance directly from a starter pack entry (no template in library)."""
    if job.status != "signals_confirmed":
        raise JobNotInConfirmedStateError(job.status)

    existing = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise PipelineAlreadyExistsError()

    if starter_key not in STARTER_TEMPLATES:
        raise StarterKeyNotFoundError(starter_key)

    starter = STARTER_TEMPLATES[starter_key]

    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    starter_stages: list[JobPipelineStage] = [
        JobPipelineStage(
            tenant_id=job.tenant_id,
            instance_id=instance.id,
            position=stage["position"],
            name=stage["name"],
            stage_type=stage["stage_type"],
            duration_minutes=stage["duration_minutes"],
            difficulty=stage["difficulty"],
            signal_filter=stage["signal_filter"],
            pass_criteria=stage["pass_criteria"],
            advance_behavior=stage["advance_behavior"],
            sla_days=stage.get("sla_days"),
        )
        for stage in starter["stages"]
    ]
    final_stages = _seed_bookends(
        starter_stages, tenant_id=job.tenant_id, instance_id=instance.id
    )
    for stage in final_stages:
        db.add(stage)
    await db.flush()
    logger.info(
        "pipelines.job_instance_created",
        job_posting_id=str(job.id),
        instance_id=str(instance.id),
        source="starter",
        starter_key=starter_key,
    )
    return instance


async def create_job_pipeline_from_scratch(
    db: AsyncSession,
    *,
    job: JobPosting,
    stages: list[PipelineStageInput],
) -> JobPipelineInstance:
    """Create an instance with explicit stages (no source template)."""
    if job.status != "signals_confirmed":
        raise JobNotInConfirmedStateError(job.status)

    existing = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise PipelineAlreadyExistsError()

    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    scratch_stages: list[JobPipelineStage] = [
        JobPipelineStage(
            **_stage_input_to_row_dict(stage, job.tenant_id, instance_id=instance.id)
        )
        for stage in stages
    ]
    final_stages = _seed_bookends(
        scratch_stages, tenant_id=job.tenant_id, instance_id=instance.id
    )
    for stage in final_stages:
        db.add(stage)
    await db.flush()
    logger.info(
        "pipelines.job_instance_created",
        job_posting_id=str(job.id),
        instance_id=str(instance.id),
        source="scratch",
    )
    return instance


async def swap_job_pipeline(
    db: AsyncSession,
    *,
    job: JobPosting,
    instance: JobPipelineInstance,
    body: CreateJobPipelineRequest,
) -> JobPipelineInstance:
    """Atomically replace a job's pipeline instance with one built from a new source.

    Deletes the existing instance (cascading its stages) and creates a new one
    via the existing create helpers — all within a single transaction.

    Note: the returned instance has a new `id`. Callers should reload it via
    `get_job_pipeline_with_stages` before returning to HTTP.
    """
    old_instance_id = instance.id
    await db.delete(instance)
    await db.flush()

    if isinstance(body, CreateJobPipelineFromTemplate):
        new_instance = await create_job_pipeline_from_template(
            db, job=job, template_id=body.template_id
        )
    elif isinstance(body, CreateJobPipelineFromStarter):
        new_instance = await create_job_pipeline_from_starter(
            db, job=job, starter_key=body.starter_key
        )
    else:
        scratch: CreateJobPipelineFromScratch = body  # type: ignore[assignment]
        new_instance = await create_job_pipeline_from_scratch(
            db, job=job, stages=scratch.stages
        )

    logger.info(
        "pipelines.job_instance_swapped",
        job_posting_id=str(job.id),
        old_instance_id=str(old_instance_id),
        new_instance_id=str(new_instance.id),
    )
    return new_instance


async def update_job_pipeline_stages(
    db: AsyncSession,
    *,
    instance: JobPipelineInstance,
    stages: list[PipelineStageUpdateInput | PipelineStageInput],
    actor_id: UUID,
) -> JobPipelineInstance:
    """Replace the stages on a job pipeline instance via diff-and-sync.

    Matching rule: an incoming stage with id=X updates the existing row with
    that id in place, preserving the UUID. An incoming stage with id=None is
    inserted as a new row. Existing rows whose id is not in the incoming list
    are deleted. This preserves stage row identity across edits — critical so
    question banks FK'd to stage_id survive auto-save edits.
    """
    # Load current stages for the instance
    existing_result = await db.execute(
        select(JobPipelineStage).where(
            JobPipelineStage.instance_id == instance.id
        )
    )
    existing_list = list(existing_result.scalars().all())

    # Partition incoming into id-matched updates vs new inserts
    incoming_by_id: dict[UUID, PipelineStageUpdateInput] = {}
    incoming_new: list[PipelineStageInput] = []
    for s in stages:
        if isinstance(s, PipelineStageUpdateInput) and s.id is not None:
            incoming_by_id[s.id] = s
        else:
            incoming_new.append(s)

    # Snapshot pre-mutation signal_filter/difficulty for each matched stage so
    # we can detect config drift after the update (§11.5).
    _pre_config: dict[UUID, dict] = {}
    for e in existing_list:
        if e.id in incoming_by_id:
            _pre_config[e.id] = {
                "signal_filter": e.signal_filter,
                "difficulty": e.difficulty,
            }

    # Park matched existing stages in unique negative positions so the main
    # update loop can shuffle freely without tripping
    # UNIQUE(instance_id, position). Postgres checks that constraint row-by-
    # row inside an executemany batch, so a reorder like A:0→1, B:1→2
    # would otherwise violate the constraint mid-batch when A's UPDATE
    # lands on B's current slot. Mapping old→-(old+1) guarantees the parked
    # values are all distinct and disjoint from any non-negative target.
    matched_existing = [e for e in existing_list if e.id in incoming_by_id]
    if matched_existing:
        for e in matched_existing:
            e.position = -(e.position + 1)
        await db.flush()

    # Delete unmatched rows next — before the main update loop assigns final
    # positions. SQLAlchemy's default flush order is UPDATE → DELETE, so if
    # we left the delete pending until the big flush below, a matched row
    # whose final position equals the to-be-deleted row's current position
    # would trip the unique constraint mid-UPDATE (the DELETE hasn't fired
    # yet to vacate the slot). Doing the deletes in their own flush now
    # clears those slots before finals are written.
    unmatched_existing = [e for e in existing_list if e.id not in incoming_by_id]
    if unmatched_existing:
        for e in unmatched_existing:
            await db.delete(e)
        await db.flush()

    # Update-in-place for matched existing stages.
    for existing in existing_list:
        if existing.id in incoming_by_id:
            update = incoming_by_id[existing.id]
            existing.position = update.position
            existing.name = update.name
            existing.stage_type = update.stage_type
            existing.duration_minutes = update.duration_minutes
            existing.difficulty = update.difficulty
            existing.signal_filter = update.signal_filter.model_dump() if update.signal_filter is not None else None
            existing.pass_criteria = update.pass_criteria.model_dump() if update.pass_criteria is not None else None
            existing.advance_behavior = update.advance_behavior
            existing.sla_days = update.sla_days

    # Insert new stages (id=None or plain PipelineStageInput)
    for new_stage in incoming_new:
        db.add(
            JobPipelineStage(
                **_stage_input_to_row_dict(
                    new_stage, instance.tenant_id, instance_id=instance.id
                )
            )
        )

    # Sync participants per stage. participants=None means "don't touch".
    # Only PipelineStageUpdateInput instances with a populated `id` can sync
    # participants — new stages (id is None) cannot because their row ids
    # were just assigned in this transaction, and callers pass participants
    # via a separate create path.
    updates_with_participants: list[PipelineStageUpdateInput] = [
        s for s in stages
        if isinstance(s, PipelineStageUpdateInput)
        and s.id is not None
        and s.participants is not None
    ]
    if updates_with_participants:
        # Eligibility check (single batch across all supplied users).
        job_result = await db.execute(
            select(JobPosting).where(JobPosting.id == instance.job_posting_id)
        )
        job = job_result.scalar_one()

        flat_participants: list[StageParticipantInput] = []
        for s in updates_with_participants:
            flat_participants.extend(s.participants or [])
        await validate_participants_eligible(
            db, job=job, participants=flat_participants
        )

        # Reload stage rows by id so we can diff participants against the
        # latest state (the update loop above may have mutated them).
        stage_id_list = [s.id for s in updates_with_participants if s.id is not None]
        stages_reload = await db.execute(
            select(JobPipelineStage).where(JobPipelineStage.id.in_(stage_id_list))
        )
        stage_by_id = {row.id: row for row in stages_reload.scalars().all()}
        for incoming in updates_with_participants:
            row = stage_by_id.get(incoming.id) if incoming.id is not None else None
            if row is None:
                continue  # defensive — should not happen
            await replace_stage_participants(
                db,
                stage=row,
                participants=incoming.participants or [],
                assigned_by=actor_id,
            )

    instance.updated_at = _now_utc()
    await db.flush()
    await bump_pipeline_version(db, instance)

    # Recompute is_stale for banks whose stage config changed (§11.5).
    # Lazy import to avoid circular: pipelines → question_bank → pipelines.
    await _recompute_stale_for_config_changed_stages(db, existing_list, incoming_by_id, _pre_config)

    logger.info(
        "pipelines.job_instance_stages_synced",
        instance_id=str(instance.id),
        updated=len([s for s in existing_list if s.id in incoming_by_id]),
        deleted=len([s for s in existing_list if s.id not in incoming_by_id]),
        inserted=len(incoming_new),
        pipeline_version=instance.pipeline_version,
    )
    return instance


async def _recompute_stale_for_config_changed_stages(
    db: AsyncSession,
    existing_list: list[JobPipelineStage],
    incoming_by_id: dict[UUID, "PipelineStageUpdateInput"],
    pre_config: dict[UUID, dict],
) -> None:
    """Recompute is_stale for every matched stage whose signal_filter or
    difficulty changed. Lazy import avoids a circular import chain."""
    for stage in existing_list:
        if stage.id not in incoming_by_id:
            continue
        old = pre_config.get(stage.id, {})
        if (
            old.get("signal_filter") != stage.signal_filter
            or old.get("difficulty") != stage.difficulty
        ):
            bank_result = await db.execute(
                select(StageQuestionBank).where(
                    StageQuestionBank.stage_id == stage.id
                )
            )
            bank = bank_result.scalar_one_or_none()
            if bank is not None:
                await recompute_and_persist_stale(
                    db,
                    bank,
                    current_stage_config={
                        "signal_filter": stage.signal_filter,
                        "difficulty": stage.difficulty,
                    },
                )


async def reset_job_pipeline_to_source(
    db: AsyncSession,
    *,
    instance: JobPipelineInstance,
) -> JobPipelineInstance:
    """Re-copy stages from the source template, discarding local edits."""
    if instance.source_template_id is None:
        raise NoSourceTemplateError()

    tpl_stages_result = await db.execute(
        select(PipelineTemplateStage)
        .where(PipelineTemplateStage.template_id == instance.source_template_id)
        .order_by(PipelineTemplateStage.position)
    )
    src_stages = list(tpl_stages_result.scalars().all())
    if not src_stages:
        raise NoSourceTemplateError()

    existing = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.instance_id == instance.id)
    )
    for s in existing.scalars().all():
        await db.delete(s)
    await db.flush()

    reset_stages: list[JobPipelineStage] = [
        JobPipelineStage(
            tenant_id=instance.tenant_id,
            instance_id=instance.id,
            position=src.position,
            name=src.name,
            stage_type=src.stage_type,
            duration_minutes=src.duration_minutes,
            difficulty=src.difficulty,
            signal_filter=src.signal_filter,
            pass_criteria=src.pass_criteria,
            advance_behavior=src.advance_behavior,
            sla_days=src.sla_days,
        )
        for src in src_stages
    ]
    final_stages = _seed_bookends(
        reset_stages, tenant_id=instance.tenant_id, instance_id=instance.id
    )
    for stage in final_stages:
        db.add(stage)
    instance.updated_at = _now_utc()
    await db.flush()
    await bump_pipeline_version(db, instance)
    logger.info(
        "pipelines.job_instance_reset",
        instance_id=str(instance.id),
        pipeline_version=instance.pipeline_version,
    )
    return instance


async def save_job_pipeline_as_template(
    db: AsyncSession,
    *,
    job: JobPosting,
    instance: JobPipelineInstance,
    name: str,
    description: str | None,
    is_default: bool,
    actor_id: UUID,
) -> PipelineTemplate:
    """Create a new template in the org unit library, copying the job's current stages."""
    stages_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    job_stages = list(stages_result.scalars().all())
    if not job_stages:
        raise ValueError("Cannot save empty pipeline as template")

    if is_default:
        await _clear_existing_default(db, job.org_unit_id)

    template = PipelineTemplate(
        tenant_id=job.tenant_id,
        org_unit_id=job.org_unit_id,
        name=name,
        description=description,
        is_default=is_default,
        from_starter=None,
        created_by=actor_id,
    )
    db.add(template)
    await db.flush()

    for js in job_stages:
        db.add(
            PipelineTemplateStage(
                tenant_id=job.tenant_id,
                template_id=template.id,
                position=js.position,
                name=js.name,
                stage_type=js.stage_type,
                duration_minutes=js.duration_minutes,
                difficulty=js.difficulty,
                signal_filter=js.signal_filter,
                pass_criteria=js.pass_criteria,
                advance_behavior=js.advance_behavior,
                sla_days=js.sla_days,
            )
        )
    await db.flush()
    logger.info(
        "pipelines.job_instance_saved_as_template",
        instance_id=str(instance.id),
        template_id=str(template.id),
    )
    return template


async def update_source_template_from_job(
    db: AsyncSession,
    *,
    instance: JobPipelineInstance,
    actor_id: UUID,
) -> PipelineTemplate:
    """Write the job's current stages back to the source template."""
    if instance.source_template_id is None:
        raise NoSourceTemplateError()

    tpl_result = await db.execute(
        select(PipelineTemplate).where(PipelineTemplate.id == instance.source_template_id)
    )
    template = tpl_result.scalar_one_or_none()
    if template is None:
        raise NoSourceTemplateError()

    stages_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    job_stages = list(stages_result.scalars().all())

    existing_tpl_stages = await db.execute(
        select(PipelineTemplateStage).where(
            PipelineTemplateStage.template_id == template.id
        )
    )
    for s in existing_tpl_stages.scalars().all():
        await db.delete(s)
    await db.flush()

    for js in job_stages:
        db.add(
            PipelineTemplateStage(
                tenant_id=template.tenant_id,
                template_id=template.id,
                position=js.position,
                name=js.name,
                stage_type=js.stage_type,
                duration_minutes=js.duration_minutes,
                difficulty=js.difficulty,
                signal_filter=js.signal_filter,
                pass_criteria=js.pass_criteria,
                advance_behavior=js.advance_behavior,
                sla_days=js.sla_days,
            )
        )
    template.updated_by = actor_id
    template.updated_at = _now_utc()
    await db.flush()
    logger.info(
        "pipelines.source_template_updated",
        template_id=str(template.id),
        from_instance_id=str(instance.id),
    )
    return template


# ---------------------------------------------------------------------------
# Minimal default-pipeline helper — Intake → Debrief
# ---------------------------------------------------------------------------


async def ensure_minimal_pipeline_for_job(
    db: AsyncSession,
    *,
    job: JobPosting,
) -> JobPipelineInstance | None:
    """Idempotently create a 2-stage Intake → Debrief pipeline for a job.

    Used by callers that need EVERY job to have a pipeline instance
    regardless of the JD state machine:
      - The ATS orchestrator (jobs imported from a vendor don't pass
        through signal-confirmation auto-apply).
      - Manual job creation that wants pipeline-at-creation rather than
        pipeline-at-confirmation.

    Returns the new instance, or None if one already existed for the job.
    Does NOT check ``job.status`` — ATS-imported jobs are
    ``blocked_pending_client_setup`` or ``active`` and never reach
    ``signals_confirmed``.

    Idempotency is enforced by the ``uq_job_pipeline_instance_job``
    unique constraint on ``job_posting_id`` plus an explicit existence
    check before insert.
    """
    existing = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    instance = JobPipelineInstance(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    # `_seed_bookends` returns [Intake, Debrief] when called with an
    # empty stages list. We use the same helper as every other
    # pipeline-creation path so the bookend invariants stay identical.
    bookend_stages = _seed_bookends(
        [],
        tenant_id=job.tenant_id,
        instance_id=instance.id,
    )
    for stage in bookend_stages:
        db.add(stage)
    await db.flush()

    logger.info(
        "pipelines.minimal_pipeline_created",
        job_posting_id=str(job.id),
        instance_id=str(instance.id),
    )
    return instance


# ---------------------------------------------------------------------------
# Classifier helpers
# ---------------------------------------------------------------------------


async def list_stages_for_instance(
    db: AsyncSession, *, instance: JobPipelineInstance
) -> list[JobPipelineStage]:
    """Load all stages for a pipeline instance, ordered by position."""
    result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == instance.id)
        .order_by(JobPipelineStage.position)
    )
    return list(result.scalars().all())


def stage_to_dict(stage: JobPipelineStage) -> dict:
    """Serialize a JobPipelineStage to the dict shape that classify_pipeline_diff consumes."""
    return {
        "id": str(stage.id),
        "position": stage.position,
        "stage_type": stage.stage_type,
        "name": stage.name,
        "paused_at": stage.paused_at.isoformat() if stage.paused_at else None,
        "duration_minutes": stage.duration_minutes,
        "difficulty": stage.difficulty,
        "signal_filter": stage.signal_filter,
        "pass_criteria": stage.pass_criteria,
        "advance_behavior": stage.advance_behavior,
        "sla_days": stage.sla_days,
    }


async def get_stage_in_instance(
    db: AsyncSession, *, instance: JobPipelineInstance, stage_id: UUID,
) -> JobPipelineStage:
    """Load a stage and verify it belongs to the given instance.

    Raises HTTPException(404) if not found.
    """
    result = await db.execute(
        select(JobPipelineStage).where(
            JobPipelineStage.id == stage_id,
            JobPipelineStage.instance_id == instance.id,
        )
    )
    stage = result.scalar_one_or_none()
    if stage is None:
        from fastapi import HTTPException
        raise HTTPException(404, detail=f"Stage {stage_id} not found in this pipeline")
    return stage


_UNPAUSABLE_TYPES: frozenset[str] = frozenset({"intake", "debrief"})


async def pause_stage(
    db: AsyncSession,
    *,
    instance: JobPipelineInstance,
    stage: JobPipelineStage,
) -> JobPipelineStage:
    """Set paused_at on a stage and bump pipeline_version.

    Forbidden for intake/debrief (raises StagePauseForbiddenError).
    Idempotent: already-paused stage is returned without mutation.
    """
    if stage.stage_type in _UNPAUSABLE_TYPES:
        raise StagePauseForbiddenError(stage.stage_type)
    if stage.paused_at is not None:
        return stage  # idempotent
    stage.paused_at = datetime.now(timezone.utc)
    await db.flush()
    await bump_pipeline_version(db, instance)
    logger.info(
        "pipelines.stage_paused",
        instance_id=str(instance.id),
        stage_id=str(stage.id),
        stage_type=stage.stage_type,
        pipeline_version=instance.pipeline_version,
    )
    return stage


async def unpause_stage(
    db: AsyncSession,
    *,
    instance: JobPipelineInstance,
    stage: JobPipelineStage,
) -> JobPipelineStage:
    """Clear paused_at on a stage and bump pipeline_version.

    Idempotent: already-unpaused stage is returned without mutation.
    """
    if stage.paused_at is None:
        return stage  # idempotent
    stage.paused_at = None
    await db.flush()
    await bump_pipeline_version(db, instance)
    logger.info(
        "pipelines.stage_unpaused",
        instance_id=str(instance.id),
        stage_id=str(stage.id),
        stage_type=stage.stage_type,
        pipeline_version=instance.pipeline_version,
    )
    return stage


# OTP is OPTIONAL only for these stage types (FORBIDDEN elsewhere — see
# schemas._FIELD_RULES_BY_TYPE).
_OTP_ALLOWED_TYPES: frozenset[str] = frozenset(
    {"phone_screen", "ai_screening", "human_interview"}
)


async def set_stage_otp_required(
    db: AsyncSession,
    *,
    stage: JobPipelineStage,
    otp_required: bool,
) -> JobPipelineStage:
    """Set otp_required_default on a stage.

    Forbidden for intake/debrief/take_home (raises StageOtpNotApplicableError).
    Idempotent. Does NOT bump pipeline_version or touch bank staleness — OTP is
    an invite-time gate, orthogonal to question-bank content.
    """
    if stage.stage_type not in _OTP_ALLOWED_TYPES:
        raise StageOtpNotApplicableError(stage.stage_type)
    if stage.otp_required_default == otp_required:
        return stage  # idempotent
    stage.otp_required_default = otp_required
    await db.flush()
    logger.info(
        "pipelines.stage_otp_required_set",
        stage_id=str(stage.id),
        stage_type=stage.stage_type,
        otp_required=otp_required,
    )
    return stage


async def count_in_flight_per_stage(
    db: AsyncSession, *, instance: JobPipelineInstance,
) -> dict[str, int]:
    """Count active candidate_job_assignments per stage_id in this instance.

    Returns {stage_id_str: count}, only including stages with count > 0.
    """
    q = (
        select(CandidateJobAssignment.current_stage_id, func.count())
        .where(CandidateJobAssignment.status == "active")
        .where(
            CandidateJobAssignment.current_stage_id.in_(
                select(JobPipelineStage.id).where(
                    JobPipelineStage.instance_id == instance.id
                )
            )
        )
        .group_by(CandidateJobAssignment.current_stage_id)
    )
    rows = (await db.execute(q)).all()
    return {str(stage_id): n for stage_id, n in rows}
