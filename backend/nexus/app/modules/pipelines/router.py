"""Pipeline Builder HTTP surface.

Route groups:
  - GET  /api/pipeline-templates/starter-pack                    (public starter pack)
  - GET  /api/org-units/{unit_id}/pipeline-templates             (list library)
  - POST /api/org-units/{unit_id}/pipeline-templates             (create from scratch or starter)
  - PATCH  /api/pipeline-templates/{template_id}                 (update)
  - POST   /api/pipeline-templates/{template_id}/set-default     (toggle default)
  - DELETE /api/pipeline-templates/{template_id}                 (delete)
  - GET  /api/jobs/{job_id}/pipeline                             (get instance)
  - POST /api/jobs/{job_id}/pipeline                             (create instance)
  - PATCH /api/jobs/{job_id}/pipeline                            (update stages)
  - POST /api/jobs/{job_id}/pipeline/reset                       (reset to source)
  - POST /api/jobs/{job_id}/pipeline/save-as-template            (save as new template)
  - POST /api/jobs/{job_id}/pipeline/update-source-template      (write back to source)"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_tenant_db
from app.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineTemplate,
    PipelineTemplateStage,
)
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.org_units.service import get_org_unit_ancestry
from app.modules.pipelines.authz import (
    require_instance_access,
    require_template_access,
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
    CreateTemplateFromScratch,
    CreateTemplateFromStarter,
    CreateTemplateRequest,
    JobPipelineInstanceResponse,
    PipelineStageBase,
    PipelineStageResponse,
    PipelineTemplateResponse,
    SaveAsTemplateRequest,
    SignalFilter,
    StarterTemplate,
    UpdateJobPipelineRequest,
    UpdateTemplateRequest,
)
from app.modules.pipelines.service import (
    create_job_pipeline_from_scratch,
    create_job_pipeline_from_starter,
    create_job_pipeline_from_template,
    create_template_from_scratch,
    create_template_from_starter,
    delete_template,
    get_job_pipeline_with_stages,
    get_template_with_stages,
    list_templates_for_org_unit,
    reset_job_pipeline_to_source,
    save_job_pipeline_as_template,
    set_template_as_default,
    swap_job_pipeline,
    update_job_pipeline_stages,
    update_source_template_from_job,
    update_template,
)
from app.modules.pipelines.starter_pack import STARTER_TEMPLATES

router = APIRouter(tags=["pipelines"])


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _stage_row_to_response(
    row: PipelineTemplateStage | JobPipelineStage,
) -> PipelineStageResponse:
    return PipelineStageResponse(
        id=row.id,
        position=row.position,
        name=row.name,
        stage_type=row.stage_type,  # type: ignore[arg-type]
        duration_minutes=row.duration_minutes,
        difficulty=row.difficulty,  # type: ignore[arg-type]
        signal_filter=SignalFilter(**row.signal_filter),
        pass_criteria=row.pass_criteria,  # type: ignore[arg-type]
        advance_behavior=row.advance_behavior,  # type: ignore[arg-type]
        sla_days=row.sla_days,
    )


def _template_to_response(
    template: PipelineTemplate, stages: list[PipelineTemplateStage]
) -> PipelineTemplateResponse:
    return PipelineTemplateResponse(
        id=template.id,
        org_unit_id=template.org_unit_id,
        name=template.name,
        description=template.description,
        is_default=template.is_default,
        from_starter=template.from_starter,
        stages=[_stage_row_to_response(s) for s in stages],
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _instance_to_response(
    instance: JobPipelineInstance,
    stages: list[JobPipelineStage],
    source_template: PipelineTemplate | None,
) -> JobPipelineInstanceResponse:
    return JobPipelineInstanceResponse(
        id=instance.id,
        job_posting_id=instance.job_posting_id,
        source_template_id=instance.source_template_id,
        source_template_name=source_template.name if source_template else None,
        stages=[_stage_row_to_response(s) for s in stages],
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


async def _require_org_unit_manage(
    db: AsyncSession,
    org_unit_id: UUID,
    user: UserContext,
) -> None:
    """Check org_units.manage in ancestry. Raises 403 otherwise."""
    if user.is_super_admin:
        return
    ancestry = await get_org_unit_ancestry(db, org_unit_id)
    if not any(
        user.has_permission_in_unit(u.id, "org_units.manage") for u in ancestry
    ):
        raise HTTPException(
            status_code=403,
            detail="Missing org_units.manage in org unit ancestry",
        )


# ---------------------------------------------------------------------------
# Starter pack endpoint
# ---------------------------------------------------------------------------


@router.get("/api/pipeline-templates/starter-pack", response_model=list[StarterTemplate])
async def get_starter_pack(
    user: UserContext = Depends(get_current_user_roles),
) -> list[StarterTemplate]:
    """Return the hand-written starter pack templates."""
    return [
        StarterTemplate(
            key=key,
            name=tpl["name"],
            description=tpl["description"],
            stages=[
                PipelineStageBase(
                    position=stage["position"],
                    name=stage["name"],
                    stage_type=stage["stage_type"],
                    duration_minutes=stage["duration_minutes"],
                    difficulty=stage["difficulty"],
                    signal_filter=SignalFilter(**stage["signal_filter"]),
                    pass_criteria=stage["pass_criteria"],
                    advance_behavior=stage["advance_behavior"],
                )
                for stage in tpl["stages"]
            ],
        )
        for key, tpl in STARTER_TEMPLATES.items()
    ]


# ---------------------------------------------------------------------------
# Template library endpoints (nested under org-units)
# ---------------------------------------------------------------------------


@router.get(
    "/api/org-units/{unit_id}/pipeline-templates",
    response_model=list[PipelineTemplateResponse],
)
async def list_templates(
    unit_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[PipelineTemplateResponse]:
    await _require_org_unit_manage(db, unit_id, user)
    pairs = await list_templates_for_org_unit(db, unit_id)
    return [_template_to_response(tpl, stages) for tpl, stages in pairs]


@router.post(
    "/api/org-units/{unit_id}/pipeline-templates",
    response_model=PipelineTemplateResponse,
    status_code=201,
)
async def create_template(
    unit_id: UUID,
    body: CreateTemplateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> PipelineTemplateResponse:
    await _require_org_unit_manage(db, unit_id, user)

    try:
        if isinstance(body, CreateTemplateFromStarter):
            template = await create_template_from_starter(
                db,
                tenant_id=user.user.tenant_id,
                org_unit_id=unit_id,
                created_by=user.user.id,
                starter_key=body.starter_key,
                name=body.name,
                description=body.description,
                is_default=body.is_default,
            )
        else:
            scratch: CreateTemplateFromScratch = body  # type: ignore[assignment]
            template = await create_template_from_scratch(
                db,
                tenant_id=user.user.tenant_id,
                org_unit_id=unit_id,
                created_by=user.user.id,
                name=scratch.name,
                description=scratch.description,
                is_default=scratch.is_default,
                stages=scratch.stages,
            )
    except StarterKeyNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    pair = await get_template_with_stages(db, template.id)
    if pair is None:
        raise HTTPException(status_code=500, detail="Template creation succeeded but reload failed")
    return _template_to_response(pair[0], pair[1])


@router.patch(
    "/api/pipeline-templates/{template_id}",
    response_model=PipelineTemplateResponse,
)
async def update_template_endpoint(
    template_id: UUID,
    body: UpdateTemplateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> PipelineTemplateResponse:
    template = await require_template_access(db, template_id, user, "manage")
    await update_template(
        db,
        template=template,
        name=body.name,
        description=body.description,
        stages=body.stages,
        actor_id=user.user.id,
    )
    pair = await get_template_with_stages(db, template.id)
    if pair is None:
        raise HTTPException(status_code=500, detail="Template update succeeded but reload failed")
    return _template_to_response(pair[0], pair[1])


@router.post(
    "/api/pipeline-templates/{template_id}/set-default",
    response_model=PipelineTemplateResponse,
)
async def set_default_endpoint(
    template_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> PipelineTemplateResponse:
    template = await require_template_access(db, template_id, user, "manage")
    await set_template_as_default(db, template, user.user.id)
    pair = await get_template_with_stages(db, template.id)
    if pair is None:
        raise HTTPException(status_code=500, detail="Reload failed")
    return _template_to_response(pair[0], pair[1])


@router.delete("/api/pipeline-templates/{template_id}", status_code=204)
async def delete_template_endpoint(
    template_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> None:
    template = await require_template_access(db, template_id, user, "manage")
    try:
        await delete_template(db, template)
    except CannotDeleteDefaultError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------
# Job pipeline endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/api/jobs/{job_id}/pipeline",
    response_model=JobPipelineInstanceResponse,
)
async def get_job_pipeline(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPipelineInstanceResponse:
    await require_instance_access(db, job_id, user, "view")
    result = await get_job_pipeline_with_stages(db, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No pipeline for this job")
    instance, stages, source_template, _participants_by_stage = result
    return _instance_to_response(instance, stages, source_template)


@router.post(
    "/api/jobs/{job_id}/pipeline",
    response_model=JobPipelineInstanceResponse,
    status_code=201,
)
async def create_job_pipeline(
    job_id: UUID,
    body: CreateJobPipelineRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPipelineInstanceResponse:
    job, _ = await require_instance_access(db, job_id, user, "manage")

    try:
        if isinstance(body, CreateJobPipelineFromTemplate):
            await create_job_pipeline_from_template(
                db, job=job, template_id=body.template_id
            )
        elif isinstance(body, CreateJobPipelineFromStarter):
            await create_job_pipeline_from_starter(
                db, job=job, starter_key=body.starter_key
            )
        else:
            scratch: CreateJobPipelineFromScratch = body  # type: ignore[assignment]
            await create_job_pipeline_from_scratch(
                db, job=job, stages=scratch.stages
            )
    except JobNotInConfirmedStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PipelineAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except StarterKeyNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    result = await get_job_pipeline_with_stages(db, job_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Instance created but reload failed")
    instance, stages, source_template, _participants_by_stage = result
    return _instance_to_response(instance, stages, source_template)


@router.patch(
    "/api/jobs/{job_id}/pipeline",
    response_model=JobPipelineInstanceResponse,
)
async def update_job_pipeline(
    job_id: UUID,
    body: UpdateJobPipelineRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPipelineInstanceResponse:
    _job, instance = await require_instance_access(db, job_id, user, "manage")
    if instance is None:
        raise HTTPException(status_code=404, detail="No pipeline for this job")
    await update_job_pipeline_stages(db, instance=instance, stages=body.stages, actor_id=user.user.id)
    result = await get_job_pipeline_with_stages(db, job_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Reload failed")
    new_instance, stages, source_template, _participants_by_stage = result
    return _instance_to_response(new_instance, stages, source_template)


@router.post(
    "/api/jobs/{job_id}/pipeline/swap",
    response_model=JobPipelineInstanceResponse,
)
async def swap_job_pipeline_endpoint(
    job_id: UUID,
    body: CreateJobPipelineRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPipelineInstanceResponse:
    """Swap a job's pipeline to a different template or starter, atomically."""
    job, instance = await require_instance_access(db, job_id, user, "manage")
    if instance is None:
        raise HTTPException(status_code=404, detail="No pipeline to swap")
    try:
        await swap_job_pipeline(db, job=job, instance=instance, body=body)
    except StarterKeyNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    result = await get_job_pipeline_with_stages(db, job_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Swap succeeded but reload failed")
    new_instance, stages, source_template, _participants_by_stage = result
    return _instance_to_response(new_instance, stages, source_template)


@router.post(
    "/api/jobs/{job_id}/pipeline/reset",
    response_model=JobPipelineInstanceResponse,
)
async def reset_job_pipeline(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPipelineInstanceResponse:
    _job, instance = await require_instance_access(db, job_id, user, "manage")
    if instance is None:
        raise HTTPException(status_code=404, detail="No pipeline for this job")
    try:
        await reset_job_pipeline_to_source(db, instance=instance)
    except NoSourceTemplateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    result = await get_job_pipeline_with_stages(db, job_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Reload failed")
    new_instance, stages, source_template, _participants_by_stage = result
    return _instance_to_response(new_instance, stages, source_template)


@router.post(
    "/api/jobs/{job_id}/pipeline/save-as-template",
    response_model=PipelineTemplateResponse,
    status_code=201,
)
async def save_job_pipeline_as_template_endpoint(
    job_id: UUID,
    body: SaveAsTemplateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> PipelineTemplateResponse:
    job, instance = await require_instance_access(db, job_id, user, "manage")
    if instance is None:
        raise HTTPException(status_code=404, detail="No pipeline for this job")
    await _require_org_unit_manage(db, job.org_unit_id, user)
    template = await save_job_pipeline_as_template(
        db,
        job=job,
        instance=instance,
        name=body.name,
        description=body.description,
        is_default=body.is_default,
        actor_id=user.user.id,
    )
    pair = await get_template_with_stages(db, template.id)
    if pair is None:
        raise HTTPException(status_code=500, detail="Reload failed")
    return _template_to_response(pair[0], pair[1])


@router.post(
    "/api/jobs/{job_id}/pipeline/update-source-template",
    response_model=PipelineTemplateResponse,
)
async def update_source_template_endpoint(
    job_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> PipelineTemplateResponse:
    _job, instance = await require_instance_access(db, job_id, user, "manage")
    if instance is None:
        raise HTTPException(status_code=404, detail="No pipeline for this job")
    if instance.source_template_id is None:
        raise HTTPException(status_code=409, detail="No source template to update")
    # Load source template's org unit for permission check
    await require_template_access(db, instance.source_template_id, user, "manage")
    try:
        updated = await update_source_template_from_job(
            db, instance=instance, actor_id=user.user.id
        )
    except NoSourceTemplateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    pair = await get_template_with_stages(db, updated.id)
    if pair is None:
        raise HTTPException(status_code=500, detail="Reload failed")
    return _template_to_response(pair[0], pair[1])
