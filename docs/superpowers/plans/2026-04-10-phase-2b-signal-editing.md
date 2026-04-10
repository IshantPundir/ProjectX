# Phase 2B — Signal Editing, Re-enrichment & Confirmation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add signal chip editing, Call 2 re-enrichment, and confirmation workflow to the JD pipeline — transforming the read-only 2A review into an interactive editing surface.

**Architecture:** Extends the existing Dramatiq actor + SSE polling pattern from 2A. New Alembic migration adds `enrichment_status` and `enrichment_error` to `job_postings`, `prompt_version` to snapshots. State machine gains one new state (`signals_confirmed`) and two new transitions. Frontend adds Zustand for transient editing state; TanStack Query remains the server-state source of truth. Vitest is set up for the first frontend tests.

**Tech Stack:** FastAPI, SQLAlchemy async, Dramatiq, OpenAI via instructor, Pydantic v2, Alembic | Next.js 16, TypeScript, Zustand, TanStack Query v5, React Hook Form + Zod, shadcn/ui v4 (Base UI), Vitest + Testing Library

---

## File Map

### Backend — New Files
| File | Purpose |
|------|---------|
| `migrations/versions/0001_phase_2b_columns.py` | Alembic migration: add enrichment_status, enrichment_error, prompt_version |
| `prompts/v1/jd_reenrichment.txt` | Call 2 re-enrichment prompt |

### Backend — Modified Files
| File | Changes |
|------|---------|
| `app/models.py` | Add enrichment_status, enrichment_error, prompt_version columns to ORM |
| `app/modules/jd/state_machine.py` | Add signals_confirmed state + 2 transitions |
| `app/modules/jd/schemas.py` | Add SaveSignalsRequest, update JobStatus/JobPostingWithSnapshot/JobStatusEvent |
| `app/modules/jd/service.py` | Add save_signals(), confirm_signals(), trigger_reenrichment() |
| `app/modules/jd/router.py` | Add 3 new endpoints, update _safe_dispatch helper |
| `app/modules/jd/actors.py` | Add reenrich_jd actor |
| `app/modules/jd/sse.py` | Add signals_confirmed to TERMINAL_STATES |
| `app/modules/jd/errors.py` | Add enrichment-specific error messages |
| `app/main.py` | Add exception handler for signals_confirmed transitions |
| `app/worker.py` | Import reenrich_jd actors |
| `tests/test_jd_signals.py` | New test file for signal editing + confirmation endpoints |
| `tests/test_jd_reenrich.py` | New test file for re-enrichment actor + endpoint |

### Frontend — New Files
| File | Purpose |
|------|---------|
| `stores/job-edit.ts` | Zustand editing buffer store |
| `components/dashboard/jd-panels/EditableSignalsPanel.tsx` | Edit-mode signal panel with chip CRUD |
| `components/dashboard/jd-panels/SignalsPanelWrapper.tsx` | View/edit toggle orchestrator |
| `components/dashboard/jd-panels/ConfirmBar.tsx` | Bottom bar: confirm or save button |
| `components/dashboard/jd-panels/StaleBanner.tsx` | Amber "signals updated" banner |
| `lib/hooks/use-save-signals.ts` | TanStack mutation for PATCH signals |
| `lib/hooks/use-confirm-signals.ts` | TanStack mutation for POST confirm |
| `lib/hooks/use-trigger-enrich.ts` | TanStack mutation for POST enrich |
| `vitest.config.ts` | Vitest configuration |
| `tests/components/SignalsPanelWrapper.test.tsx` | Component tests |

### Frontend — Modified Files
| File | Changes |
|------|---------|
| `lib/api/jobs.ts` | Add types + API methods for signals/confirm/enrich |
| `components/dashboard/jd-panels/EnrichedJdPanel.tsx` | Add enrichment progress pill + stale banner slot |
| `app/(dashboard)/jobs/[jobId]/page.tsx` | Wire up SignalsPanelWrapper, StaleBanner, enrich trigger |
| `package.json` | Add vitest + testing-library deps, test script |

---

## Task 1: Alembic Migration

**Files:**
- Create: `backend/nexus/migrations/versions/0001_phase_2b_columns.py`
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 1: Generate the migration**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus alembic revision --autogenerate -m "phase_2b_add_enrichment_status_and_prompt_version"
```

If autogenerate doesn't detect changes (models not yet updated), create manually. The migration should contain:

```python
"""phase_2b_add_enrichment_status_and_prompt_version

Revision ID: <auto>
"""
from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    op.add_column("job_postings", sa.Column("enrichment_status", sa.Text(), server_default="idle", nullable=False))
    op.add_column("job_postings", sa.Column("enrichment_error", sa.Text(), nullable=True))
    op.add_column("job_posting_signal_snapshots", sa.Column("prompt_version", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("job_posting_signal_snapshots", "prompt_version")
    op.drop_column("job_postings", "enrichment_error")
    op.drop_column("job_postings", "enrichment_status")
```

- [ ] **Step 2: Add ORM columns to models.py**

In `JobPosting` class, after `status_error`, add:

```python
enrichment_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="idle")
enrichment_error: Mapped[str | None] = mapped_column(Text)
```

In `JobPostingSignalSnapshot` class, after `role_summary`, add:

```python
prompt_version: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 3: Run the migration**

```bash
docker compose run --rm nexus alembic upgrade head
```

Expected: migration applies cleanly, no errors.

- [ ] **Step 4: Verify columns exist**

```bash
docker compose run --rm nexus python -c "
from app.models import JobPosting, JobPostingSignalSnapshot
print('enrichment_status:', JobPosting.enrichment_status.property.columns[0].type)
print('enrichment_error:', JobPosting.enrichment_error.property.columns[0].type)
print('prompt_version:', JobPostingSignalSnapshot.prompt_version.property.columns[0].type)
print('OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/ app/models.py
git commit -m "feat(jd): add enrichment_status, enrichment_error, prompt_version columns (Phase 2B)"
```

---

## Task 2: Extend State Machine

**Files:**
- Modify: `backend/nexus/app/modules/jd/state_machine.py`
- Modify: `backend/nexus/app/modules/jd/sse.py`
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Add signals_confirmed state and transitions**

In `state_machine.py`, update `LEGAL_TRANSITIONS`:

```python
LEGAL_TRANSITIONS: Final[dict[str, set[str]]] = {
    "draft": {"signals_extracting"},
    "signals_extracting": {"signals_extracted", "signals_extraction_failed"},
    "signals_extraction_failed": {"signals_extracting"},
    "signals_extracted": {"signals_confirmed"},
    "signals_confirmed": {"signals_extracted"},
}
```

- [ ] **Step 2: Update SSE terminal states**

In `sse.py`, update `TERMINAL_STATES`:

```python
TERMINAL_STATES: frozenset[str] = frozenset(
    {"signals_extracted", "signals_extraction_failed", "signals_confirmed"}
)
```

- [ ] **Step 3: Add 409 messages for new transitions in main.py**

In `app/main.py`, add to `_ILLEGAL_TRANSITION_MESSAGES`:

```python
("signals_confirmed", "signals_confirmed"):
    "Signals are already confirmed",
("signals_confirmed", "signals_extracting"):
    "Cannot re-extract a confirmed job — edit signals instead",
```

- [ ] **Step 4: Run existing tests to verify no regressions**

```bash
docker compose run --rm nexus pytest -x -q
```

Expected: 122 passed.

- [ ] **Step 5: Commit**

```bash
git add app/modules/jd/state_machine.py app/modules/jd/sse.py app/main.py
git commit -m "feat(jd): add signals_confirmed state to state machine (Phase 2B)"
```

---

## Task 3: Backend Schemas

**Files:**
- Modify: `backend/nexus/app/modules/jd/schemas.py`

- [ ] **Step 1: Update JobStatus literal**

```python
JobStatus = Literal[
    "draft",
    "signals_extracting",
    "signals_extraction_failed",
    "signals_extracted",
    "signals_confirmed",
]
```

- [ ] **Step 2: Add SaveSignalsRequest schema**

After `JobPostingCreate`, add:

```python
class SignalItemInput(BaseModel):
    value: str = Field(min_length=1)
    source: Literal["ai_extracted", "ai_inferred", "recruiter"]
    inference_basis: str | None = None

class SaveSignalsRequest(BaseModel):
    required_skills: list[SignalItemInput]
    preferred_skills: list[SignalItemInput]
    must_haves: list[SignalItemInput]
    good_to_haves: list[SignalItemInput]
    min_experience_years: int = Field(ge=0, le=50)
    seniority_level: Literal["junior", "mid", "senior", "lead", "principal"]
    role_summary: str = Field(min_length=10, max_length=2000)
```

- [ ] **Step 3: Update JobPostingWithSnapshot**

Add these fields to `JobPostingWithSnapshot`:

```python
enrichment_status: str = "idle"
enrichment_error: str | None = None
is_confirmed: bool = False
```

- [ ] **Step 4: Update JobStatusEvent**

Add these fields to `JobStatusEvent`:

```python
enrichment_status: str = "idle"
is_confirmed: bool = False
```

- [ ] **Step 5: Commit**

```bash
git add app/modules/jd/schemas.py
git commit -m "feat(jd): add signal editing + confirmation schemas (Phase 2B)"
```

---

## Task 4: Service Layer — Save Signals & Confirm

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`

- [ ] **Step 1: Add save_signals function**

```python
async def save_signals(
    db: AsyncSession,
    *,
    job: JobPosting,
    data: "SaveSignalsRequest",
    actor_id: UUID,
    correlation_id: str,
) -> JobPostingSignalSnapshot:
    """Write a new snapshot version with recruiter-edited signals.

    If the job was in signals_confirmed, auto-transitions back to
    signals_extracted (soft confirmation clear)."""
    from app.modules.jd.schemas import SaveSignalsRequest

    # Determine next version
    max_result = await db.execute(
        select(func.max(JobPostingSignalSnapshot.version)).where(
            JobPostingSignalSnapshot.job_posting_id == job.id
        )
    )
    current_max = max_result.scalar() or 0

    snapshot = JobPostingSignalSnapshot(
        tenant_id=job.tenant_id,
        job_posting_id=job.id,
        version=current_max + 1,
        required_skills=[item.model_dump() for item in data.required_skills],
        preferred_skills=[item.model_dump() for item in data.preferred_skills],
        must_haves=[item.model_dump() for item in data.must_haves],
        good_to_haves=[item.model_dump() for item in data.good_to_haves],
        min_experience_years=data.min_experience_years,
        seniority_level=data.seniority_level,
        role_summary=data.role_summary,
        confirmed_by=None,
        confirmed_at=None,
    )
    db.add(snapshot)

    # Auto-clear confirmation if job was confirmed
    if job.status == "signals_confirmed":
        await transition(
            db, job,
            to_state="signals_extracted",
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    return snapshot
```

Add the missing import at the top of service.py:

```python
from sqlalchemy import desc, func, select
```

- [ ] **Step 2: Add confirm_signals function**

```python
async def confirm_signals(
    db: AsyncSession,
    *,
    job: JobPosting,
    actor_id: UUID,
    correlation_id: str,
) -> JobPostingSignalSnapshot:
    """Confirm the latest snapshot — sets confirmed_by/at and transitions
    the job to signals_confirmed."""
    # Get latest snapshot
    result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(JobPostingSignalSnapshot.job_posting_id == job.id)
        .order_by(desc(JobPostingSignalSnapshot.version))
        .limit(1)
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        raise ValueError("No snapshot to confirm")

    snapshot.confirmed_by = actor_id
    snapshot.confirmed_at = datetime.now(UTC)

    await transition(
        db, job,
        to_state="signals_confirmed",
        actor_id=actor_id,
        correlation_id=correlation_id,
    )

    return snapshot
```

Add import at top:

```python
from datetime import UTC, date, datetime
```

- [ ] **Step 3: Add trigger_reenrichment function**

```python
async def trigger_reenrichment(
    db: AsyncSession,
    *,
    job: JobPosting,
) -> None:
    """Set enrichment_status to streaming. Caller is responsible for
    enqueuing the Dramatiq actor after commit."""
    if job.enrichment_status == "streaming":
        from app.modules.jd.errors import IllegalTransitionError
        raise IllegalTransitionError(
            from_state="enrichment:streaming",
            to_state="enrichment:streaming",
        )
    job.enrichment_status = "streaming"
    job.enrichment_error = None
```

- [ ] **Step 4: Update get_job_status to include new fields**

In `get_job_status()`, update the return to include enrichment_status and is_confirmed:

```python
async def get_job_status(db: AsyncSession, job_id: UUID) -> JobStatusEvent | None:
    job, snapshot = await get_job_posting_with_latest_snapshot(db, job_id)
    if job is None:
        return None
    return JobStatusEvent(
        job_id=job.id,
        status=job.status,
        error=job.status_error,
        signal_snapshot_version=snapshot.version if snapshot else None,
        enrichment_status=job.enrichment_status,
        is_confirmed=snapshot.confirmed_at is not None if snapshot else False,
    )
```

- [ ] **Step 5: Commit**

```bash
git add app/modules/jd/service.py
git commit -m "feat(jd): add save_signals, confirm_signals, trigger_reenrichment service functions"
```

---

## Task 5: Router — Three New Endpoints

**Files:**
- Modify: `backend/nexus/app/modules/jd/router.py`

- [ ] **Step 1: Add imports**

Add to existing imports:

```python
from app.modules.jd.schemas import (
    JobPostingCreate,
    JobPostingSummary,
    JobPostingWithSnapshot,
    SaveSignalsRequest,
    SignalItemResponse,
    SignalSnapshotResponse,
)
from app.modules.jd.service import (
    confirm_signals,
    create_job_posting,
    get_job_posting_with_latest_snapshot,
    list_job_postings,
    retry_failed_extraction,
    save_signals,
    trigger_reenrichment,
)
```

- [ ] **Step 2: Add PATCH /api/jobs/{job_id}/signals**

```python
@router.patch("/{job_id}/signals", response_model=SignalSnapshotResponse)
async def update_signals(
    job_id: UUID,
    body: SaveSignalsRequest,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> SignalSnapshotResponse:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status not in ("signals_extracted", "signals_confirmed"):
        raise HTTPException(
            status_code=409,
            detail="Signals can only be edited after extraction completes",
        )
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    snapshot = await save_signals(
        db, job=job, data=body, actor_id=user.user.id, correlation_id=correlation_id,
    )
    return _snapshot_to_response(snapshot)
```

- [ ] **Step 3: Add POST /api/jobs/{job_id}/signals/confirm**

```python
@router.post("/{job_id}/signals/confirm", response_model=JobPostingSummary)
async def confirm_job_signals(
    job_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> JobPostingSummary:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status != "signals_extracted":
        raise HTTPException(
            status_code=409,
            detail="Can only confirm signals when job is in signals_extracted state",
        )
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    await confirm_signals(
        db, job=job, actor_id=user.user.id, correlation_id=correlation_id,
    )
    return _job_to_summary(job)
```

- [ ] **Step 4: Add POST /api/jobs/{job_id}/enrich**

```python
@router.post("/{job_id}/enrich", status_code=202)
async def trigger_enrich(
    job_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> dict[str, str]:
    job = await require_job_access(db, job_id, user, "manage")
    if job.status not in ("signals_extracted", "signals_confirmed"):
        raise HTTPException(
            status_code=409, detail="Job must have extracted signals before re-enrichment",
        )
    correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
    await trigger_reenrichment(db, job=job)

    from app.modules.jd.actors import reenrich_jd

    background_tasks.add_task(
        _safe_dispatch_reenrichment,
        job_posting_id=str(job.id),
        tenant_id=str(user.user.tenant_id),
        correlation_id=correlation_id,
    )
    return {"status": "accepted"}
```

- [ ] **Step 5: Add _safe_dispatch_reenrichment helper**

Copy the pattern from `_safe_dispatch_extraction`, targeting the `reenrich_jd` actor and setting `enrichment_status = failed` on error:

```python
async def _safe_dispatch_reenrichment(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    """Enqueue the re-enrichment actor, failing gracefully on Redis error."""
    try:
        from app.modules.jd.actors import reenrich_jd
        reenrich_jd.send(
            job_posting_id=job_posting_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        _log.error("jd.reenrich_dispatch_failed", job_posting_id=job_posting_id, exc_info=exc)
        from sqlalchemy import select as sa_select
        from app.models import JobPosting

        async with get_tenant_session(tenant_id) as db:
            result = await db.execute(
                sa_select(JobPosting).where(JobPosting.id == UUID(job_posting_id))
            )
            job = result.scalar_one_or_none()
            if job and job.enrichment_status == "streaming":
                job.enrichment_status = "failed"
                job.enrichment_error = "Failed to dispatch re-enrichment job — please retry."
```

- [ ] **Step 6: Update _job_with_snapshot_to_response to include new fields**

```python
def _job_with_snapshot_to_response(job, snap) -> JobPostingWithSnapshot:
    return JobPostingWithSnapshot(
        id=job.id,
        title=job.title,
        org_unit_id=job.org_unit_id,
        description_raw=job.description_raw,
        project_scope_raw=job.project_scope_raw,
        description_enriched=job.description_enriched,
        status=job.status,
        status_error=job.status_error,
        target_headcount=job.target_headcount,
        deadline=job.deadline,
        created_at=job.created_at,
        updated_at=job.updated_at,
        latest_snapshot=_snapshot_to_response(snap),
        enrichment_status=job.enrichment_status,
        enrichment_error=job.enrichment_error,
        is_confirmed=snap.confirmed_at is not None if snap else False,
    )
```

- [ ] **Step 7: Commit**

```bash
git add app/modules/jd/router.py
git commit -m "feat(jd): add PATCH signals, POST confirm, POST enrich endpoints"
```

---

## Task 6: Call 2 Prompt & Re-enrichment Actor

**Files:**
- Create: `backend/nexus/prompts/v1/jd_reenrichment.txt`
- Modify: `backend/nexus/app/modules/jd/actors.py`
- Modify: `backend/nexus/app/worker.py`

- [ ] **Step 1: Write the re-enrichment prompt**

Create `prompts/v1/jd_reenrichment.txt`:

```
You are an enterprise hiring intelligence system that re-enriches job descriptions after a recruiter has edited the structured hiring signals.

# Your Task

You will receive a user message containing, IN THIS ORDER:
  1. The hiring company's profile (about, industry, company_stage, hiring_bar)
  2. The original raw job description
  3. The CURRENT enriched job description (produced by a prior enrichment pass)
  4. The UPDATED signal snapshot (the recruiter's edited version)
  5. A delta summary showing what changed between the previous and current signals

# Rules

1. **Preserve structure and tone** — keep the section headings, paragraph flow, and professional tone of the current enriched JD. Only modify sections affected by signal changes.
2. **Integrate new signals naturally** — if the recruiter added "Kubernetes" to required skills, weave it into the relevant section (e.g., "Deploy and manage services using Docker and Kubernetes"). Don't just append bullet points.
3. **Remove dropped signals** — if the recruiter removed a skill or must-have, remove the corresponding prose. Don't leave orphan references.
4. **Never invent signals** — only reflect what is in the updated snapshot. If a skill isn't listed, don't mention it.
5. **Respect the hiring bar** — the company profile's `hiring_bar` field describes what "strong" means. Use it to calibrate language (e.g., "deep expertise" vs "familiarity with").
6. **Output only the enriched JD** — no preamble, no commentary, no JSON wrapper. Plain prose with markdown section headings.

# Output Format

Return the complete re-enriched job description as plain text with markdown headings. Minimum 200 characters.
```

- [ ] **Step 2: Add ReEnrichmentOutput schema to ai/schemas.py**

```python
class ReEnrichmentOutput(BaseModel):
    enriched_jd: str = Field(min_length=200)
```

- [ ] **Step 3: Add reenrich_jd actor to actors.py**

After `extract_and_enhance_jd`, add:

```python
def _build_reenrich_user_message(
    job: JobPosting,
    profile: dict,
    snapshot: JobPostingSignalSnapshot,
) -> str:
    """Build the Call 2 user message: profile → raw JD → current enriched → signals → delta."""
    signal_sections = []
    for section in ("required_skills", "preferred_skills", "must_haves", "good_to_haves"):
        items = getattr(snapshot, section)
        if items:
            signal_sections.append(
                f"- {section}: " + ", ".join(item["value"] for item in items)
            )
    signal_sections.append(f"- min_experience_years: {snapshot.min_experience_years}")
    signal_sections.append(f"- seniority_level: {snapshot.seniority_level}")
    signal_sections.append(f"- role_summary: {snapshot.role_summary}")

    parts = [
        "## Company Profile\n"
        f"- About: {profile['about']}\n"
        f"- Industry: {profile['industry']}\n"
        f"- Company stage: {profile['company_stage']}\n"
        f"- Hiring bar: {profile['hiring_bar']}\n",
        f"## Original Raw Job Description\n\n{job.description_raw}\n",
        f"## Current Enriched Job Description\n\n{job.description_enriched or '(none)'}\n",
        "## Updated Signal Snapshot\n\n" + "\n".join(signal_sections) + "\n",
    ]
    return "\n".join(parts)


@observe(name="jd_reenrichment_call2")
async def _run_reenrichment(
    db: AsyncSession,
    *,
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
    retries_so_far: int,
) -> None:
    """Call 2 — re-enrich the JD prose based on updated signals."""
    from app.ai.schemas import ReEnrichmentOutput

    log = logger.bind(
        job_posting_id=job_posting_id,
        correlation_id=correlation_id,
        actor="reenrich_jd",
    )

    result = await db.execute(select(JobPosting).where(JobPosting.id == UUID(job_posting_id)))
    job = result.scalar_one_or_none()
    if job is None:
        log.warn("jd.reenrich.job_not_found")
        return

    if job.enrichment_status != "streaming":
        log.warn("jd.reenrich.skip_unexpected_status", enrichment_status=job.enrichment_status)
        return

    # Load latest snapshot
    snap_result = await db.execute(
        select(JobPostingSignalSnapshot)
        .where(JobPostingSignalSnapshot.job_posting_id == job.id)
        .order_by(JobPostingSignalSnapshot.version.desc())
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    if snapshot is None:
        job.enrichment_status = "failed"
        job.enrichment_error = "No signal snapshot found"
        return

    profile = await find_company_profile_in_ancestry(db, job.org_unit_id)
    if profile is None:
        job.enrichment_status = "failed"
        job.enrichment_error = "Company profile missing"
        return

    langfuse_context.update_current_trace(
        session_id=job_posting_id,
        tags=["jd_reenrichment"],
        metadata={
            "correlation_id": correlation_id,
            "job_posting_id": job_posting_id,
            "tenant_id": tenant_id,
            "prompt_name": "jd_reenrichment",
            "prompt_version": "v1",
            "snapshot_version": snapshot.version,
        },
    )

    try:
        client = get_openai_client()
        prompt = prompt_loader.get("jd_reenrichment")
        reenrich_result: ReEnrichmentOutput = await client.chat.completions.create(
            model=ai_config.extraction_model,
            response_model=ReEnrichmentOutput,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": _build_reenrich_user_message(job, profile, snapshot)},
            ],
            name="jd_reenrichment_call2",
            metadata={
                "correlation_id": correlation_id,
                "job_posting_id": job_posting_id,
                "tenant_id": tenant_id,
                "prompt_version": "v1",
            },
        )
    except Exception as exc:
        is_permanent = isinstance(exc, _PERMANENT_EXCEPTIONS)
        log.error("jd.reenrich.call2_failed", exc_info=exc, permanent=is_permanent)
        job.enrichment_status = "failed"
        job.enrichment_error = sanitize_error_for_user(exc)
        if not is_permanent and retries_so_far < 1:
            raise
        return

    job.description_enriched = reenrich_result.enriched_jd
    job.enriched_manually_edited = True
    job.enrichment_status = "completed"
    job.enrichment_error = None
    log.info("jd.reenrich.completed")


@dramatiq.actor(
    max_retries=1,
    min_backoff=2_000,
    max_backoff=30_000,
    queue_name="jd_reenrichment",
)
async def reenrich_jd(
    job_posting_id: str,
    tenant_id: str,
    correlation_id: str,
) -> None:
    """Dramatiq entry point for Call 2 re-enrichment."""
    current = CurrentMessage.get_current_message()
    retries_so_far = current.options.get("retries", 0) if current else 0

    async with get_bypass_session() as db:
        safe_tenant_id = str(UUID(tenant_id))
        await db.execute(
            text(f"SET LOCAL app.current_tenant = '{safe_tenant_id}'")
        )
        try:
            await _run_reenrichment(
                db,
                job_posting_id=job_posting_id,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                retries_so_far=retries_so_far,
            )
            await db.commit()
        except Exception:
            if retries_so_far >= 1:
                await db.commit()
            else:
                await db.rollback()
            raise
        finally:
            if langfuse_enabled():
                await asyncio.to_thread(flush_langfuse)
```

- [ ] **Step 4: Register actor in worker.py**

Add after the existing actor import:

```python
from app.modules.jd import actors as _jd_actors  # noqa: F401, E402
```

This already imports the entire `actors` module, so `reenrich_jd` is automatically registered. No change needed — verify by confirming `reenrich_jd` is defined in `actors.py`.

- [ ] **Step 5: Run tests**

```bash
docker compose run --rm nexus pytest -x -q
```

Expected: 122 passed (no new tests yet, but existing ones should not break).

- [ ] **Step 6: Commit**

```bash
git add prompts/v1/jd_reenrichment.txt app/ai/schemas.py app/modules/jd/actors.py
git commit -m "feat(jd): add Call 2 re-enrichment actor and prompt (Phase 2B)"
```

---

## Task 7: Backend Tests — Signal Editing & Confirmation

**Files:**
- Create: `backend/nexus/tests/test_jd_signals.py`

- [ ] **Step 1: Write tests for save_signals endpoint**

```python
"""Tests for signal editing, confirmation, and re-enrichment endpoints."""

import pytest
from httpx import AsyncClient

from tests.conftest import app  # or however the test app is accessed


# -- Helpers reused from test_jd_router.py patterns --
# Copy _setup_auth_overrides and _make_job_in_extracted_state helpers
# following the established patterns in test_jd_router.py.
# These create a tenant, user, org unit with company profile, and a job
# in signals_extracted state with a v1 snapshot.


@pytest.mark.asyncio
async def test_save_signals_creates_new_snapshot(client, extracted_job):
    """PATCH /api/jobs/{id}/signals creates a v2 snapshot."""
    job_id = extracted_job["job_id"]
    resp = await client.patch(
        f"/api/jobs/{job_id}/signals",
        json={
            "required_skills": [
                {"value": "Python", "source": "ai_extracted", "inference_basis": None},
                {"value": "Kubernetes", "source": "recruiter", "inference_basis": None},
            ],
            "preferred_skills": [],
            "must_haves": [{"value": "5+ years", "source": "ai_extracted", "inference_basis": None}],
            "good_to_haves": [],
            "min_experience_years": 5,
            "seniority_level": "senior",
            "role_summary": "Senior backend engineer with Kubernetes expertise.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 2
    assert len(data["required_skills"]) == 2
    assert data["required_skills"][1]["source"] == "recruiter"


@pytest.mark.asyncio
async def test_confirm_signals(client, extracted_job):
    """POST /api/jobs/{id}/signals/confirm transitions to signals_confirmed."""
    job_id = extracted_job["job_id"]
    resp = await client.post(f"/api/jobs/{job_id}/signals/confirm")
    assert resp.status_code == 200
    assert resp.json()["status"] == "signals_confirmed"


@pytest.mark.asyncio
async def test_confirm_requires_extracted_state(client, confirmed_job):
    """Confirm on an already-confirmed job returns 409."""
    job_id = confirmed_job["job_id"]
    resp = await client.post(f"/api/jobs/{job_id}/signals/confirm")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_save_after_confirm_clears_confirmation(client, confirmed_job):
    """Saving signals after confirmation auto-transitions back to signals_extracted."""
    job_id = confirmed_job["job_id"]
    resp = await client.patch(
        f"/api/jobs/{job_id}/signals",
        json={
            "required_skills": [{"value": "Go", "source": "recruiter", "inference_basis": None}],
            "preferred_skills": [],
            "must_haves": [],
            "good_to_haves": [],
            "min_experience_years": 3,
            "seniority_level": "mid",
            "role_summary": "Mid-level Go developer for API services.",
        },
    )
    assert resp.status_code == 200
    # Verify job went back to signals_extracted
    job_resp = await client.get(f"/api/jobs/{job_id}")
    assert job_resp.json()["status"] == "signals_extracted"
    assert job_resp.json()["is_confirmed"] is False
```

- [ ] **Step 2: Run tests**

```bash
docker compose run --rm nexus pytest tests/test_jd_signals.py -x -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_jd_signals.py
git commit -m "test(jd): add signal editing and confirmation endpoint tests"
```

---

## Task 8: Frontend — Types, API Client & Hooks

**Files:**
- Modify: `frontend/app/lib/api/jobs.ts`
- Create: `frontend/app/lib/hooks/use-save-signals.ts`
- Create: `frontend/app/lib/hooks/use-confirm-signals.ts`
- Create: `frontend/app/lib/hooks/use-trigger-enrich.ts`

- [ ] **Step 1: Update types in jobs.ts**

Add `signals_confirmed` to JobStatus:

```typescript
export type JobStatus =
  | 'draft'
  | 'signals_extracting'
  | 'signals_extraction_failed'
  | 'signals_extracted'
  | 'signals_confirmed'
```

Add fields to `JobPostingWithSnapshot`:

```typescript
export type JobPostingWithSnapshot = JobPostingSummary & {
  description_raw: string
  project_scope_raw: string | null
  description_enriched: string | null
  target_headcount: number | null
  deadline: string | null
  latest_snapshot: SignalSnapshot | null
  enrichment_status: 'idle' | 'streaming' | 'completed' | 'failed'
  enrichment_error: string | null
  is_confirmed: boolean
}
```

Add fields to `JobStatusEvent`:

```typescript
export type JobStatusEvent = {
  job_id: string
  status: JobStatus
  error: string | null
  signal_snapshot_version: number | null
  enrichment_status: 'idle' | 'streaming' | 'completed' | 'failed'
  is_confirmed: boolean
}
```

Add `SaveSignalsBody` type:

```typescript
export type SaveSignalsBody = {
  required_skills: SignalItem[]
  preferred_skills: SignalItem[]
  must_haves: SignalItem[]
  good_to_haves: SignalItem[]
  min_experience_years: number
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
}
```

- [ ] **Step 2: Add API methods**

Add to `jobsApi`:

```typescript
saveSignals: (token: string, id: string, body: SaveSignalsBody): Promise<SignalSnapshot> =>
  apiFetch<SignalSnapshot>(`/api/jobs/${id}/signals`, {
    token,
    method: 'PATCH',
    body: JSON.stringify(body),
  }),

confirmSignals: (token: string, id: string): Promise<JobPostingSummary> =>
  apiFetch<JobPostingSummary>(`/api/jobs/${id}/signals/confirm`, {
    token,
    method: 'POST',
  }),

triggerEnrich: (token: string, id: string): Promise<{ status: string }> =>
  apiFetch<{ status: string }>(`/api/jobs/${id}/enrich`, {
    token,
    method: 'POST',
  }),
```

- [ ] **Step 3: Create use-save-signals.ts**

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { type SaveSignalsBody, type SignalSnapshot, jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useSaveSignals(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<SignalSnapshot, Error, SaveSignalsBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.saveSignals(token, jobId, body)
    },
    onSuccess: () => {
      toast.success('Signals saved')
      queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
    onError: (err) => {
      toast.error(`Failed to save signals: ${err.message}`)
    },
  })
}
```

- [ ] **Step 4: Create use-confirm-signals.ts**

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { type JobPostingSummary, jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useConfirmSignals(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<JobPostingSummary, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.confirmSignals(token, jobId)
    },
    onSuccess: () => {
      toast.success('Signals confirmed')
      queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
    onError: (err) => {
      toast.error(`Failed to confirm: ${err.message}`)
    },
  })
}
```

- [ ] **Step 5: Create use-trigger-enrich.ts**

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useTriggerEnrich(jobId: string) {
  const queryClient = useQueryClient()

  return useMutation<{ status: string }, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.triggerEnrich(token, jobId)
    },
    onSuccess: () => {
      toast.success('Re-enrichment started')
      queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
    },
    onError: (err) => {
      toast.error(`Failed to start re-enrichment: ${err.message}`)
    },
  })
}
```

- [ ] **Step 6: Commit**

```bash
git add lib/api/jobs.ts lib/hooks/use-save-signals.ts lib/hooks/use-confirm-signals.ts lib/hooks/use-trigger-enrich.ts
git commit -m "feat(jd): add frontend API types and mutation hooks for signal editing"
```

---

## Task 9: Zustand Store

**Files:**
- Create: `frontend/app/stores/job-edit.ts`

- [ ] **Step 1: Install Zustand**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm install zustand
```

- [ ] **Step 2: Create the store**

```typescript
import { create } from 'zustand'

import type { SignalItem, SignalSnapshot } from '@/lib/api/jobs'

type DraftSignals = {
  required_skills: SignalItem[]
  preferred_skills: SignalItem[]
  must_haves: SignalItem[]
  good_to_haves: SignalItem[]
  min_experience_years: number
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
}

type JobEditState = {
  isEditing: boolean
  draft: DraftSignals | null
  isDirty: boolean

  startEditing: (snapshot: SignalSnapshot) => void
  stopEditing: () => void
  updateDraft: (updates: Partial<DraftSignals>) => void
  addChip: (section: keyof Pick<DraftSignals, 'required_skills' | 'preferred_skills' | 'must_haves' | 'good_to_haves'>, value: string) => void
  removeChip: (section: keyof Pick<DraftSignals, 'required_skills' | 'preferred_skills' | 'must_haves' | 'good_to_haves'>, index: number) => void
  markClean: () => void
}

export const useJobEditStore = create<JobEditState>((set) => ({
  isEditing: false,
  draft: null,
  isDirty: false,

  startEditing: (snapshot) =>
    set({
      isEditing: true,
      isDirty: false,
      draft: {
        required_skills: snapshot.required_skills,
        preferred_skills: snapshot.preferred_skills,
        must_haves: snapshot.must_haves,
        good_to_haves: snapshot.good_to_haves,
        min_experience_years: snapshot.min_experience_years,
        seniority_level: snapshot.seniority_level,
        role_summary: snapshot.role_summary,
      },
    }),

  stopEditing: () =>
    set({ isEditing: false, draft: null, isDirty: false }),

  updateDraft: (updates) =>
    set((state) => ({
      draft: state.draft ? { ...state.draft, ...updates } : null,
      isDirty: true,
    })),

  addChip: (section, value) =>
    set((state) => {
      if (!state.draft) return state
      const newItem: SignalItem = { value, source: 'recruiter', inference_basis: null }
      return {
        draft: {
          ...state.draft,
          [section]: [...state.draft[section], newItem],
        },
        isDirty: true,
      }
    }),

  removeChip: (section, index) =>
    set((state) => {
      if (!state.draft) return state
      return {
        draft: {
          ...state.draft,
          [section]: state.draft[section].filter((_, i) => i !== index),
        },
        isDirty: true,
      }
    }),

  markClean: () => set({ isDirty: false }),
}))
```

- [ ] **Step 3: Commit**

```bash
git add stores/job-edit.ts package.json package-lock.json
git commit -m "feat(jd): add Zustand store for signal editing buffer"
```

---

## Task 10: Frontend Components — EditableSignalsPanel & ConfirmBar

**Files:**
- Create: `frontend/app/components/dashboard/jd-panels/EditableSignalsPanel.tsx`
- Create: `frontend/app/components/dashboard/jd-panels/ConfirmBar.tsx`

- [ ] **Step 1: Create EditableSignalsPanel.tsx**

This is the edit-mode version of SignalsPanel. It reads from the Zustand draft and provides chip CRUD, add inputs, textarea for role summary, and number/select for experience/seniority. Full component code should follow the patterns in the existing SignalsPanel.tsx and SignalChip.tsx, using Tailwind classes and shadcn/ui primitives.

Key props: none (reads from Zustand `useJobEditStore`).

The component renders:
- Role summary textarea
- For each of required_skills, preferred_skills, must_haves, good_to_haves: chips with ✕ delete + input + "Add" button
- Experience number input + seniority select dropdown

- [ ] **Step 2: Create ConfirmBar.tsx**

```typescript
'use client'

type Props = {
  isEditing: boolean
  isConfirmed: boolean
  isSaving: boolean
  isConfirming: boolean
  onSave: () => void
  onConfirm: () => void
}

export function ConfirmBar({
  isEditing, isConfirmed, isSaving, isConfirming, onSave, onConfirm,
}: Props) {
  if (isEditing) {
    return (
      <div className="border-t border-zinc-100 pt-3 mt-4">
        <button
          onClick={onSave}
          disabled={isSaving}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {isSaving ? 'Saving…' : 'Save Signals'}
        </button>
      </div>
    )
  }

  if (isConfirmed) {
    return (
      <div className="border-t border-zinc-100 pt-3 mt-4">
        <div className="flex items-center justify-center gap-2 rounded-md bg-green-50 border border-green-200 px-4 py-2 text-sm font-medium text-green-700">
          <span>✓</span> Signals Confirmed
        </div>
      </div>
    )
  }

  return (
    <div className="border-t border-zinc-100 pt-3 mt-4">
      <button
        onClick={onConfirm}
        disabled={isConfirming}
        className="w-full rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
      >
        {isConfirming ? 'Confirming…' : 'Confirm Signals'}
      </button>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add components/dashboard/jd-panels/EditableSignalsPanel.tsx components/dashboard/jd-panels/ConfirmBar.tsx
git commit -m "feat(jd): add EditableSignalsPanel and ConfirmBar components"
```

---

## Task 11: Frontend Components — SignalsPanelWrapper & StaleBanner

**Files:**
- Create: `frontend/app/components/dashboard/jd-panels/SignalsPanelWrapper.tsx`
- Create: `frontend/app/components/dashboard/jd-panels/StaleBanner.tsx`

- [ ] **Step 1: Create SignalsPanelWrapper.tsx**

This is the orchestrator that renders either `SignalsPanel` (view) or `EditableSignalsPanel` (edit) based on the Zustand `isEditing` state. It also renders the `ConfirmBar` and handles the "Edit Signals" / "Done Editing" toggle.

Key props: `snapshot: SignalSnapshot`, `isConfirmed: boolean`, `canManage: boolean`, `jobId: string`

The toggle is only shown when `canManage` is true. "Done Editing" shows a browser confirm dialog if `isDirty`.

- [ ] **Step 2: Create StaleBanner.tsx**

```typescript
'use client'

type Props = {
  isStale: boolean
  isEnriching: boolean
  enrichmentError: string | null
  onReEnrich: () => void
  onRetry: () => void
}

export function StaleBanner({
  isStale, isEnriching, enrichmentError, onReEnrich, onRetry,
}: Props) {
  if (enrichmentError) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800 mb-4">
        <span>{enrichmentError}</span>
        <button
          onClick={onRetry}
          className="shrink-0 rounded-md bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-700"
        >
          Retry
        </button>
      </div>
    )
  }

  if (isEnriching) {
    return (
      <div className="inline-flex items-center gap-2 rounded-full border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs text-blue-700 mb-4">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
        Re-enriching based on updated signals…
      </div>
    )
  }

  if (!isStale) return null

  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800 mb-4">
      <span>Signals were updated since this JD was generated.</span>
      <button
        onClick={onReEnrich}
        className="shrink-0 rounded-md bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700"
      >
        Re-enrich JD
      </button>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add components/dashboard/jd-panels/SignalsPanelWrapper.tsx components/dashboard/jd-panels/StaleBanner.tsx
git commit -m "feat(jd): add SignalsPanelWrapper and StaleBanner components"
```

---

## Task 12: Update Job Review Page

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`
- Modify: `frontend/app/components/dashboard/jd-panels/EnrichedJdPanel.tsx`

- [ ] **Step 1: Update EnrichedJdPanel to accept children for banner slot**

```typescript
type Props = {
  enrichedJd: string
  banner?: React.ReactNode
}

export function EnrichedJdPanel({ enrichedJd, banner }: Props) {
  return (
    <section className="bg-white rounded-lg border border-zinc-200 p-6 col-span-1 3xl:col-span-1 overflow-y-auto">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 mb-4 pb-2 border-b border-zinc-100">
        Enriched JD
      </h3>
      {banner}
      <div className="whitespace-pre-wrap text-sm text-zinc-700 leading-relaxed">
        {enrichedJd}
      </div>
    </section>
  )
}
```

- [ ] **Step 2: Update job review page**

Wire up `SignalsPanelWrapper` in place of `SignalsPanel`, add `StaleBanner` to the enriched panel, and connect the enrich trigger. The page needs to:

1. Import `SignalsPanelWrapper` instead of `SignalsPanel`
2. Import `StaleBanner` and `useTriggerEnrich`
3. Determine `isStale` by comparing snapshot version or checking if `enrichment_status != 'completed'` after a save
4. Determine `canManage` from user permissions (initially always true for super admin; refine with permission check)
5. Pass all props through

Update `showPanels` condition to also accept `signals_confirmed`:

```typescript
const showPanels =
  (job.status === 'signals_extracted' || job.status === 'signals_confirmed') &&
  job.latest_snapshot &&
  job.description_enriched
```

- [ ] **Step 3: Run type-check**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add app/\(dashboard\)/jobs/\[jobId\]/page.tsx components/dashboard/jd-panels/EnrichedJdPanel.tsx
git commit -m "feat(jd): wire up signal editing, confirmation, and re-enrichment in review page"
```

---

## Task 13: Vitest Setup

**Files:**
- Create: `frontend/app/vitest.config.ts`
- Modify: `frontend/app/package.json`

- [ ] **Step 1: Install dependencies**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm install -D vitest @testing-library/react @testing-library/user-event @testing-library/jest-dom jsdom
```

- [ ] **Step 2: Create vitest.config.ts**

```typescript
import { defineConfig } from 'vitest/config'
import path from 'path'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
})
```

- [ ] **Step 3: Create test setup file**

Create `frontend/app/tests/setup.ts`:

```typescript
import '@testing-library/jest-dom/vitest'
```

- [ ] **Step 4: Add test script to package.json**

Add to the `"scripts"` section:

```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 5: Verify setup**

```bash
npx vitest run --passWithNoTests
```

Expected: "No test files found" or passes with 0 tests.

- [ ] **Step 6: Commit**

```bash
git add vitest.config.ts tests/setup.ts package.json package-lock.json
git commit -m "chore: set up Vitest with Testing Library and jsdom"
```

---

## Task 14: Frontend Component Tests

**Files:**
- Create: `frontend/app/tests/components/SignalsPanelWrapper.test.tsx`

- [ ] **Step 1: Write component tests**

Test the core behaviors: toggle view/edit, save calls mutation, confirm calls mutation, dirty state warning.

```typescript
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

// Tests for SignalsPanelWrapper toggle behavior, ConfirmBar states,
// and StaleBanner rendering conditions.
// Mock useJobEditStore, useSaveSignals, useConfirmSignals, useTriggerEnrich.
```

- [ ] **Step 2: Run tests**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run test
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add tests/components/
git commit -m "test(jd): add component tests for signal editing panel"
```

---

## Task 15: Final Integration Verification

- [ ] **Step 1: Run backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest -x -q
```

Expected: all pass (122 existing + new signal/reenrich tests).

- [ ] **Step 2: Run frontend type-check and lint**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
```

Expected: no errors.

- [ ] **Step 3: Run frontend tests**

```bash
npm run test
```

Expected: all pass.

- [ ] **Step 4: Build check**

```bash
npm run build
```

Expected: production build succeeds.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(jd): Phase 2B — signal editing, re-enrichment, and confirmation"
```
