"""Pipeline Builder service layer.

Template CRUD (per org unit), instance creation/mutation (per job),
and the auto_apply_pipeline_on_confirmation hook called from jd.confirm_signals.

All functions take an AsyncSession — transaction management is the caller's
responsibility (FastAPI dependency handles commit/rollback)."""

from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    PipelineTemplate,
    PipelineTemplateStage,
)
from app.modules.pipelines.errors import (
    CannotDeleteDefaultError,
    JobNotInConfirmedStateError,
    NoSourceTemplateError,
    PipelineAlreadyExistsError,
    StarterKeyNotFoundError,
)
from app.modules.pipelines.schemas import (
    CreateJobPipelineFromScratch,
    CreateJobPipelineFromStarter,
    CreateJobPipelineFromTemplate,
    CreateJobPipelineRequest,
    PipelineStageInput,
)
from app.modules.pipelines.starter_pack import (
    STARTER_TEMPLATES,
    SYSTEM_FALLBACK_STARTER,
)

logger = structlog.get_logger()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stage_input_to_row_dict(
    stage: PipelineStageInput,
    tenant_id: UUID,
    template_id: UUID | None = None,
    instance_id: UUID | None = None,
) -> dict:
    """Convert a PipelineStageInput into a dict for row constructors."""
    base = {
        "tenant_id": tenant_id,
        "position": stage.position,
        "name": stage.name,
        "stage_type": stage.stage_type,
        "duration_minutes": stage.duration_minutes,
        "difficulty": stage.difficulty,
        "signal_filter": stage.signal_filter.model_dump(),
        "pass_criteria": stage.pass_criteria.model_dump(),
        "advance_behavior": stage.advance_behavior,
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
) -> tuple[JobPipelineInstance, list[JobPipelineStage], PipelineTemplate | None] | None:
    """Load a job pipeline instance, its stages, and (if linked) the source template."""
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

    return instance, stages, source_template


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
    for src_stage in stages_result.scalars().all():
        db.add(
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
            )
        )
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

    for stage in starter["stages"]:
        db.add(
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
            )
        )
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

    for stage in stages:
        db.add(
            JobPipelineStage(
                **_stage_input_to_row_dict(stage, job.tenant_id, instance_id=instance.id)
            )
        )
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
    stages: list[PipelineStageInput],
) -> JobPipelineInstance:
    """Replace all stages on a job pipeline instance atomically."""
    existing = await db.execute(
        select(JobPipelineStage).where(JobPipelineStage.instance_id == instance.id)
    )
    for s in existing.scalars().all():
        await db.delete(s)
    await db.flush()

    for stage in stages:
        db.add(
            JobPipelineStage(
                **_stage_input_to_row_dict(stage, instance.tenant_id, instance_id=instance.id)
            )
        )
    instance.updated_at = _now_utc()
    await db.flush()
    logger.info(
        "pipelines.job_instance_stages_replaced",
        instance_id=str(instance.id),
    )
    return instance


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

    for src in src_stages:
        db.add(
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
            )
        )
    instance.updated_at = _now_utc()
    await db.flush()
    logger.info("pipelines.job_instance_reset", instance_id=str(instance.id))
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
# Auto-apply hook — called from jd.confirm_signals
# ---------------------------------------------------------------------------


async def auto_apply_pipeline_on_confirmation(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID,
) -> JobPipelineInstance | None:
    """Create a pipeline instance for a freshly-confirmed job.

    Resolution order:
      1. Last-used template in this org unit (from job_pipeline_instances history)
      2. Org unit's default template (is_default = true)
      3. System fallback (SYSTEM_FALLBACK_STARTER directly from starter pack)

    Caller must wrap this in try/except — failures are logged but should not
    block signal confirmation."""
    # Guard: do nothing if an instance already exists
    existing = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        logger.info(
            "pipelines.auto_apply_skipped_existing",
            job_posting_id=str(job.id),
        )
        return None

    # Resolution 1: last-used template in this org unit
    last_used = await db.execute(
        select(JobPipelineInstance.source_template_id)
        .join(JobPosting, JobPipelineInstance.job_posting_id == JobPosting.id)
        .where(
            and_(
                JobPosting.org_unit_id == job.org_unit_id,
                JobPipelineInstance.source_template_id.isnot(None),
            )
        )
        .order_by(desc(JobPipelineInstance.created_at))
        .limit(1)
    )
    last_template_id = last_used.scalar_one_or_none()
    if last_template_id is not None:
        tpl_check = await db.execute(
            select(PipelineTemplate).where(PipelineTemplate.id == last_template_id)
        )
        if tpl_check.scalar_one_or_none() is not None:
            logger.info(
                "pipelines.auto_apply_using_last_used",
                job_posting_id=str(job.id),
                template_id=str(last_template_id),
            )
            return await create_job_pipeline_from_template(
                db, job=job, template_id=last_template_id
            )

    # Resolution 2: org unit default
    default_result = await db.execute(
        select(PipelineTemplate).where(
            and_(
                PipelineTemplate.org_unit_id == job.org_unit_id,
                PipelineTemplate.is_default == True,
            )
        )
    )
    default_tpl = default_result.scalar_one_or_none()
    if default_tpl is not None:
        logger.info(
            "pipelines.auto_apply_using_org_default",
            job_posting_id=str(job.id),
            template_id=str(default_tpl.id),
        )
        return await create_job_pipeline_from_template(
            db, job=job, template_id=default_tpl.id
        )

    # Resolution 3: system fallback starter
    logger.info(
        "pipelines.auto_apply_using_system_fallback",
        job_posting_id=str(job.id),
        starter_key=SYSTEM_FALLBACK_STARTER,
    )
    return await create_job_pipeline_from_starter(
        db, job=job, starter_key=SYSTEM_FALLBACK_STARTER
    )
