# Signal Schema v2 + Job Metadata — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the signal extraction schema from 4 rigid JSONB columns to a single universal flat list with rich metadata (type, priority, weight, knockout, stage), add structured job metadata fields, and rewrite the Call 1 + Call 2 prompts for the new schema.

**Architecture:** Clean-slate migration (no existing data to preserve). Drop 4 old signal columns, add 1 `signals` JSONB column on `job_posting_signal_snapshots`, add job metadata columns on `job_postings`. Rewrite AI extraction schemas, prompts, and all frontend rendering to work with the flat list grouped by stage→type. `evaluation_method` is derived from type+stage defaults (not AI-set). `evaluation_hint` is null (populated later by Phase 2C Call 3).

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Pydantic v2, instructor, OpenAI | Next.js 16, TypeScript, Zustand, TanStack Query v5, shadcn/ui v4, Tailwind v4

---

## File Map

### Backend — Modified Files
| File | Changes |
|------|---------|
| `migrations/versions/0003_signal_schema_v2.py` | New migration: drop 4 signal columns + min_experience_years, add `signals` JSONB, add job metadata columns |
| `app/models.py` | Update `JobPostingSignalSnapshot` (drop 4+1 columns, add `signals`), update `JobPosting` (add metadata columns) |
| `app/ai/schemas.py` | Replace `ExtractedSignals` with flat `signals: list[SignalItemV2]`, add coverage validators |
| `app/modules/jd/schemas.py` | Rewrite all signal request/response schemas, add job metadata to `JobPostingCreate` and `JobPostingWithSnapshot` |
| `app/modules/jd/actors.py` | Update `_persist_enriched` and `_build_reenrich_user_message` for flat list |
| `app/modules/jd/service.py` | Update `save_signals` and `create_job_posting` for new schema |
| `app/modules/jd/router.py` | Update `_snapshot_to_response`, `_job_with_snapshot_to_response`, `create_job` for metadata |
| `prompts/v1/jd_enhancement.txt` | Full rewrite for new signal schema with type/priority/weight/knockout/stage |
| `prompts/v1/jd_reenrichment.txt` | Update for flat signal list |
| `tests/test_jd_actor.py` | Update `_fake_extraction_output` and assertions |
| `tests/test_jd_signals.py` | Update `_save_signals_body` and assertions |
| `tests/test_ai_schemas.py` | Rewrite for new schema + coverage validators |

### Frontend — Modified Files
| File | Changes |
|------|---------|
| `lib/api/jobs.ts` | Rewrite `SignalItem`, `SignalSnapshot`, `SaveSignalsBody`, add metadata types and enums |
| `stores/job-edit.ts` | Rewrite from 4-section to flat list with add/remove by filter |
| `components/dashboard/jd-panels/SignalChip.tsx` | Add weight indicator + knockout badge |
| `components/dashboard/jd-panels/SignalsPanel.tsx` | Group by stage→type instead of 4 hardcoded sections |
| `components/dashboard/jd-panels/EditableSignalsPanel.tsx` | Rewrite for flat list with type/weight/knockout/stage controls |
| `components/dashboard/jd-panels/SignalsPanelWrapper.tsx` | Update save payload to flat list |
| `app/(dashboard)/jobs/new/page.tsx` | Add metadata form fields |

---

## Task 1: Alembic Migration + ORM Model Updates

**Files:**
- Create: `backend/nexus/migrations/versions/0003_signal_schema_v2.py`
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 1: Update ORM models FIRST**

In `app/models.py`, update `JobPostingSignalSnapshot`:

Replace the 4 JSONB columns + `min_experience_years`:
```python
required_skills: Mapped[list] = mapped_column(JSONB, nullable=False)
preferred_skills: Mapped[list] = mapped_column(JSONB, nullable=False)
must_haves: Mapped[list] = mapped_column(JSONB, nullable=False)
good_to_haves: Mapped[list] = mapped_column(JSONB, nullable=False)
min_experience_years: Mapped[int] = mapped_column(Integer, nullable=False)
```

With:
```python
signals: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
```

Keep `seniority_level` and `role_summary` unchanged.

In `app/models.py`, add metadata columns to `JobPosting` (after `deadline`):
```python
employment_type: Mapped[str | None] = mapped_column(Text)
work_arrangement: Mapped[str | None] = mapped_column(Text)
location: Mapped[str | None] = mapped_column(Text)
salary_range_min: Mapped[int | None] = mapped_column(Integer)
salary_range_max: Mapped[int | None] = mapped_column(Integer)
salary_currency: Mapped[str | None] = mapped_column(Text)
travel_required: Mapped[str | None] = mapped_column(Text)
start_date_pref: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 2: Create migration file**

Create `migrations/versions/0003_signal_schema_v2.py`:
```python
"""signal_schema_v2_flat_list_and_job_metadata

Revision ID: 0003_signal_schema_v2
Revises: 0002_add_updated_by
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_signal_schema_v2"
down_revision = "0002_add_updated_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Signal schema: 4 columns → 1 flat list --
    op.drop_column("job_posting_signal_snapshots", "required_skills")
    op.drop_column("job_posting_signal_snapshots", "preferred_skills")
    op.drop_column("job_posting_signal_snapshots", "must_haves")
    op.drop_column("job_posting_signal_snapshots", "good_to_haves")
    op.drop_column("job_posting_signal_snapshots", "min_experience_years")
    op.add_column(
        "job_posting_signal_snapshots",
        sa.Column("signals", sa.JSON(), nullable=False, server_default="[]"),
    )

    # -- Job metadata columns --
    op.add_column("job_postings", sa.Column("employment_type", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("work_arrangement", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("location", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("salary_range_min", sa.Integer(), nullable=True))
    op.add_column("job_postings", sa.Column("salary_range_max", sa.Integer(), nullable=True))
    op.add_column("job_postings", sa.Column("salary_currency", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("travel_required", sa.Text(), nullable=True))
    op.add_column("job_postings", sa.Column("start_date_pref", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_postings", "start_date_pref")
    op.drop_column("job_postings", "travel_required")
    op.drop_column("job_postings", "salary_currency")
    op.drop_column("job_postings", "salary_range_max")
    op.drop_column("job_postings", "salary_range_min")
    op.drop_column("job_postings", "location")
    op.drop_column("job_postings", "work_arrangement")
    op.drop_column("job_postings", "employment_type")
    op.drop_column("job_posting_signal_snapshots", "signals")
    op.add_column("job_posting_signal_snapshots", sa.Column("min_experience_years", sa.Integer(), nullable=False))
    op.add_column("job_posting_signal_snapshots", sa.Column("good_to_haves", sa.JSON(), nullable=False))
    op.add_column("job_posting_signal_snapshots", sa.Column("must_haves", sa.JSON(), nullable=False))
    op.add_column("job_posting_signal_snapshots", sa.Column("preferred_skills", sa.JSON(), nullable=False))
    op.add_column("job_posting_signal_snapshots", sa.Column("required_skills", sa.JSON(), nullable=False))
```

- [ ] **Step 3: Run migration**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus alembic upgrade head
```

- [ ] **Step 4: Run tests (expect failures — schema changed but code hasn't)**

```bash
docker compose run --rm nexus pytest -x -q 2>&1 | tail -5
```

Expected: failures in signal-related tests. This is correct — we'll fix them in subsequent tasks.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0003_signal_schema_v2.py app/models.py
git commit -m "feat(jd): signal schema v2 migration — flat list + job metadata columns"
```

---

## Task 2: AI Schemas — New Signal Item + Extraction Output

**Files:**
- Modify: `backend/nexus/app/ai/schemas.py`

- [ ] **Step 1: Rewrite the entire file**

```python
"""Call 1 structured output schemas — strict Pydantic models.

Signal Schema v2: universal flat list where each signal carries type,
priority, weight, knockout, and stage metadata. AI determines all fields
autonomously from JD context.

Validators enforce:
  - Provenance: ai_inferred requires inference_basis, ai_extracted requires null
  - Coverage: at least 5 signals, at least 1 screen + 1 interview, at least 1 competency
  - Knockout cap: max 5 knockout signals to prevent over-flagging"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SignalType = Literal["competency", "experience", "credential", "behavioral"]
SignalPriority = Literal["required", "preferred"]
SignalStage = Literal["screen", "interview"]
SignalSource = Literal["ai_extracted", "ai_inferred"]


class SignalItemV2(BaseModel):
    """A single hiring signal extracted from a JD."""

    # What
    value: str = Field(min_length=1)
    type: SignalType

    # How important
    priority: SignalPriority
    weight: Literal[1, 2, 3] = 2
    knockout: bool = False

    # When
    stage: SignalStage

    # Provenance
    source: SignalSource
    inference_basis: str | None = Field(
        default=None,
        description="Required when source='ai_inferred', else null",
    )

    @model_validator(mode="after")
    def check_provenance(self) -> "SignalItemV2":
        if self.source == "ai_inferred" and not self.inference_basis:
            raise ValueError(
                "Signal with source='ai_inferred' must have an inference_basis"
            )
        if self.source == "ai_extracted" and self.inference_basis is not None:
            raise ValueError(
                "Signal with source='ai_extracted' must have inference_basis=null"
            )
        return self


class ExtractedSignals(BaseModel):
    """Flat signal list with coverage validators."""

    signals: list[SignalItemV2] = Field(min_length=5)
    seniority_level: Literal["junior", "mid", "senior", "lead", "principal"]
    role_summary: str = Field(min_length=10, max_length=2000)

    @model_validator(mode="after")
    def check_coverage(self) -> "ExtractedSignals":
        stages = {s.stage for s in self.signals}
        types = {s.type for s in self.signals}
        knockouts = [s for s in self.signals if s.knockout]

        if "screen" not in stages:
            raise ValueError("Must include at least one signal with stage='screen'")
        if "interview" not in stages:
            raise ValueError("Must include at least one signal with stage='interview'")
        if "competency" not in types:
            raise ValueError("Must include at least one competency signal")
        if len(knockouts) > 5:
            raise ValueError("Too many knockout signals (max 5) — knockouts should be reserved for truly non-negotiable requirements")
        return self


class ExtractionOutput(BaseModel):
    enriched_jd: str = Field(min_length=50)
    signals: ExtractedSignals


class ReEnrichmentOutput(BaseModel):
    enriched_jd: str = Field(min_length=200)
```

- [ ] **Step 2: Commit**

```bash
git add app/ai/schemas.py
git commit -m "feat(ai): signal schema v2 — flat list with type/priority/weight/knockout/stage"
```

---

## Task 3: JD Module Schemas — Request/Response + Job Metadata Enums

**Files:**
- Modify: `backend/nexus/app/modules/jd/schemas.py`

- [ ] **Step 1: Rewrite the entire file**

```python
"""Pydantic request / response schemas for the JD module.

These define the HTTP surface; internal ORM models live in app/models.py.
Conversions between them live in service.py and router.py."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Enums ---

JobStatus = Literal[
    "draft",
    "signals_extracting",
    "signals_extraction_failed",
    "signals_extracted",
    "signals_confirmed",
]

EnrichmentStatus = Literal["idle", "streaming", "completed", "failed"]

SignalType = Literal["competency", "experience", "credential", "behavioral"]
SignalPriority = Literal["required", "preferred"]
SignalStage = Literal["screen", "interview"]
EvaluationMethod = Literal["depth_probe", "verification", "situational", "case_study"]

EmploymentType = Literal["full_time", "part_time", "contract", "internship"]
WorkArrangement = Literal["remote", "hybrid", "onsite"]
SalaryCurrency = Literal["INR", "USD", "EUR"]
TravelRequired = Literal["none", "occasional", "frequent"]
StartDatePref = Literal["immediate", "within_30_days", "within_90_days", "flexible"]
SeniorityLevel = Literal["junior", "mid", "senior", "lead", "principal"]

# --- Evaluation method defaults ---

_EVAL_METHOD_DEFAULTS: dict[tuple[str, str], EvaluationMethod] = {
    ("competency", "screen"): "verification",
    ("competency", "interview"): "depth_probe",
    ("experience", "screen"): "verification",
    ("experience", "interview"): "situational",
    ("credential", "screen"): "verification",
    ("credential", "interview"): "verification",
    ("behavioral", "screen"): "verification",
    ("behavioral", "interview"): "situational",
}


def default_evaluation_method(signal_type: str, stage: str) -> EvaluationMethod:
    return _EVAL_METHOD_DEFAULTS.get((signal_type, stage), "verification")


# --- Signal item schemas ---

class SignalItemResponse(BaseModel):
    """Signal item as returned in API responses."""
    value: str
    type: SignalType
    priority: SignalPriority
    weight: Literal[1, 2, 3]
    knockout: bool
    stage: SignalStage
    evaluation_method: EvaluationMethod
    evaluation_hint: str | None = None
    source: Literal["ai_extracted", "ai_inferred", "recruiter"]
    inference_basis: str | None = None


class SignalItemInput(BaseModel):
    """Signal item as received in PATCH /signals request."""
    value: str = Field(min_length=1)
    type: SignalType
    priority: SignalPriority
    weight: Literal[1, 2, 3] = 2
    knockout: bool = False
    stage: SignalStage
    evaluation_method: EvaluationMethod | None = None  # null = use default
    evaluation_hint: str | None = None
    source: Literal["ai_extracted", "ai_inferred", "recruiter"]
    inference_basis: str | None = None

    @model_validator(mode="after")
    def check_provenance(self) -> "SignalItemInput":
        if self.source == "ai_inferred" and not self.inference_basis:
            raise ValueError("Signal with source='ai_inferred' must have an inference_basis")
        if self.source in ("ai_extracted", "recruiter") and self.inference_basis is not None:
            raise ValueError(f"Signal with source='{self.source}' must have inference_basis=null")
        return self


class SignalSnapshotResponse(BaseModel):
    """Snapshot as returned in API responses."""
    version: int
    signals: list[SignalItemResponse]
    seniority_level: SeniorityLevel
    role_summary: str
    confirmed_by: UUID | None = None
    confirmed_at: datetime | None = None


# --- Job posting schemas ---

class JobPostingCreate(BaseModel):
    """POST /api/jobs request body."""
    model_config = ConfigDict(extra="forbid")

    org_unit_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description_raw: str = Field(min_length=50, max_length=50_000)
    project_scope_raw: str | None = Field(default=None, max_length=20_000)
    target_headcount: int | None = Field(default=None, ge=1, le=10_000)
    deadline: date | None = None
    # Job metadata — all optional, recruiter fills what they know
    employment_type: EmploymentType | None = None
    work_arrangement: WorkArrangement | None = None
    location: str | None = Field(default=None, max_length=200)
    salary_range_min: int | None = Field(default=None, ge=0)
    salary_range_max: int | None = Field(default=None, ge=0)
    salary_currency: SalaryCurrency | None = None
    travel_required: TravelRequired | None = None
    start_date_pref: StartDatePref | None = None


class SaveSignalsRequest(BaseModel):
    """PATCH /api/jobs/{id}/signals request body."""
    signals: list[SignalItemInput]
    seniority_level: SeniorityLevel
    role_summary: str = Field(min_length=10, max_length=2000)


class JobPostingSummary(BaseModel):
    """Row shape for GET /api/jobs (list view)."""
    id: UUID
    title: str
    org_unit_id: UUID
    org_unit_name: str | None = None
    created_by_email: str | None = None
    updated_by_email: str | None = None
    status: JobStatus
    status_error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobPostingWithSnapshot(BaseModel):
    """Row shape for GET /api/jobs/{id} — full payload with latest snapshot."""
    id: UUID
    title: str
    org_unit_id: UUID
    description_raw: str
    project_scope_raw: str | None = None
    description_enriched: str | None = None
    status: JobStatus
    status_error: str | None = None
    target_headcount: int | None = None
    deadline: date | None = None
    # Job metadata
    employment_type: EmploymentType | None = None
    work_arrangement: WorkArrangement | None = None
    location: str | None = None
    salary_range_min: int | None = None
    salary_range_max: int | None = None
    salary_currency: SalaryCurrency | None = None
    travel_required: TravelRequired | None = None
    start_date_pref: StartDatePref | None = None
    # Timestamps
    created_at: datetime
    updated_at: datetime
    # Signals
    latest_snapshot: SignalSnapshotResponse | None = None
    enrichment_status: EnrichmentStatus = "idle"
    enrichment_error: str | None = None
    is_confirmed: bool = False
    can_manage: bool = False


class JobStatusEvent(BaseModel):
    """SSE event payload shape."""
    job_id: UUID
    status: JobStatus
    error: str | None = None
    signal_snapshot_version: int | None = None
    enrichment_status: EnrichmentStatus = "idle"
    is_confirmed: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            "signals_extracted",
            "signals_extraction_failed",
            "signals_confirmed",
        }
```

- [ ] **Step 2: Commit**

```bash
git add app/modules/jd/schemas.py
git commit -m "feat(jd): signal schema v2 request/response schemas + job metadata enums"
```

---

## Task 4: Service Layer + Router + Actors — Core Backend Updates

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`
- Modify: `backend/nexus/app/modules/jd/router.py`
- Modify: `backend/nexus/app/modules/jd/actors.py`

This task updates the 3 core backend files that read/write signals. These are tightly coupled and must be updated together.

- [ ] **Step 1: Update `service.py` — `create_job_posting` accepts metadata**

Add new parameters to `create_job_posting` after `deadline`:

```python
async def create_job_posting(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    created_by: UUID,
    org_unit_id: UUID,
    title: str,
    description_raw: str,
    project_scope_raw: str | None,
    target_headcount: int | None,
    deadline: date | None,
    employment_type: str | None = None,
    work_arrangement: str | None = None,
    location: str | None = None,
    salary_range_min: int | None = None,
    salary_range_max: int | None = None,
    salary_currency: str | None = None,
    travel_required: str | None = None,
    start_date_pref: str | None = None,
    correlation_id: str,
) -> JobPosting:
```

And in the `JobPosting(...)` constructor, add the new fields:

```python
job = JobPosting(
    ...
    deadline=deadline,
    employment_type=employment_type,
    work_arrangement=work_arrangement,
    location=location,
    salary_range_min=salary_range_min,
    salary_range_max=salary_range_max,
    salary_currency=salary_currency,
    travel_required=travel_required,
    start_date_pref=start_date_pref,
    status="draft",
    ...
)
```

- [ ] **Step 2: Update `service.py` — `save_signals` for flat list**

Replace the snapshot construction in `save_signals`:

```python
snapshot = JobPostingSignalSnapshot(
    tenant_id=job.tenant_id,
    job_posting_id=job.id,
    version=current_max + 1,
    signals=[item.model_dump() for item in body.signals],
    seniority_level=body.seniority_level,
    role_summary=body.role_summary,
    confirmed_by=None,
    confirmed_at=None,
)
```

- [ ] **Step 3: Update `actors.py` — `_persist_enriched` for flat list**

Replace the snapshot construction in `_persist_enriched`:

```python
snapshot = JobPostingSignalSnapshot(
    tenant_id=job.tenant_id,
    job_posting_id=job.id,
    version=current_max + 1,
    signals=[item.model_dump() for item in result.signals.signals],
    seniority_level=result.signals.seniority_level,
    role_summary=result.signals.role_summary,
    prompt_version="v1",
)
```

- [ ] **Step 4: Update `actors.py` — `_build_reenrich_user_message` for flat list**

Replace the `snapshot_data` dict:

```python
snapshot_data = {
    "signals": snapshot.signals,
    "seniority_level": snapshot.seniority_level,
    "role_summary": snapshot.role_summary,
}
```

- [ ] **Step 5: Update `router.py` — `_snapshot_to_response` for flat list**

```python
def _snapshot_to_response(
    snap: JobPostingSignalSnapshot | None,
) -> SignalSnapshotResponse | None:
    if snap is None:
        return None

    from app.modules.jd.schemas import SignalItemResponse, default_evaluation_method

    response_signals = []
    for item in snap.signals:
        eval_method = item.get("evaluation_method") or default_evaluation_method(
            item["type"], item["stage"]
        )
        response_signals.append(
            SignalItemResponse(
                value=item["value"],
                type=item["type"],
                priority=item["priority"],
                weight=item.get("weight", 2),
                knockout=item.get("knockout", False),
                stage=item["stage"],
                evaluation_method=eval_method,
                evaluation_hint=item.get("evaluation_hint"),
                source=item["source"],
                inference_basis=item.get("inference_basis"),
            )
        )

    return SignalSnapshotResponse(
        version=snap.version,
        signals=response_signals,
        seniority_level=snap.seniority_level,
        role_summary=snap.role_summary,
        confirmed_by=snap.confirmed_by,
        confirmed_at=snap.confirmed_at,
    )
```

- [ ] **Step 6: Update `router.py` — `_job_with_snapshot_to_response` for metadata**

Add all metadata fields:

```python
def _job_with_snapshot_to_response(
    job, snap, *, can_manage: bool = False,
) -> JobPostingWithSnapshot:
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
        employment_type=job.employment_type,
        work_arrangement=job.work_arrangement,
        location=job.location,
        salary_range_min=job.salary_range_min,
        salary_range_max=job.salary_range_max,
        salary_currency=job.salary_currency,
        travel_required=job.travel_required,
        start_date_pref=job.start_date_pref,
        created_at=job.created_at,
        updated_at=job.updated_at,
        latest_snapshot=_snapshot_to_response(snap),
        enrichment_status=job.enrichment_status,
        enrichment_error=job.enrichment_error,
        is_confirmed=snap.confirmed_at is not None if snap else False,
        can_manage=can_manage,
    )
```

- [ ] **Step 7: Update `router.py` — `create_job` endpoint passes metadata**

In `create_job`, pass the new fields from `body` to `create_job_posting`:

```python
job = await create_job_posting(
    db,
    tenant_id=user.user.tenant_id,
    created_by=user.user.id,
    org_unit_id=body.org_unit_id,
    title=body.title,
    description_raw=body.description_raw,
    project_scope_raw=body.project_scope_raw,
    target_headcount=body.target_headcount,
    deadline=body.deadline,
    employment_type=body.employment_type,
    work_arrangement=body.work_arrangement,
    location=body.location,
    salary_range_min=body.salary_range_min,
    salary_range_max=body.salary_range_max,
    salary_currency=body.salary_currency,
    travel_required=body.travel_required,
    start_date_pref=body.start_date_pref,
    correlation_id=correlation_id,
)
```

- [ ] **Step 8: Run tests**

```bash
docker compose run --rm nexus pytest -x -q
```

Some tests may still fail due to test fixtures using old schema. Fix in Task 5.

- [ ] **Step 9: Commit**

```bash
git add app/modules/jd/service.py app/modules/jd/router.py app/modules/jd/actors.py
git commit -m "feat(jd): update service/router/actors for signal schema v2 + job metadata"
```

---

## Task 5: Backend Tests — Update for v2 Schema

**Files:**
- Modify: `backend/nexus/tests/test_jd_actor.py`
- Modify: `backend/nexus/tests/test_jd_signals.py`
- Modify: `backend/nexus/tests/test_ai_schemas.py`

- [ ] **Step 1: Update `test_ai_schemas.py`**

Rewrite tests for the new `SignalItemV2` and `ExtractedSignals` with coverage validators. Test: valid signal, provenance rules, coverage minimums, knockout cap.

- [ ] **Step 2: Update `test_jd_actor.py` — `_fake_extraction_output`**

```python
def _fake_extraction_output() -> ExtractionOutput:
    return ExtractionOutput(
        enriched_jd="A" * 80,
        signals=ExtractedSignals(
            signals=[
                SignalItemV2(value="Python", type="competency", priority="required", weight=2, knockout=False, stage="interview", source="ai_extracted", inference_basis=None),
                SignalItemV2(value="5+ years backend", type="experience", priority="required", weight=2, knockout=True, stage="screen", source="ai_extracted", inference_basis=None),
                SignalItemV2(value="CS degree", type="credential", priority="preferred", weight=1, knockout=False, stage="screen", source="ai_extracted", inference_basis=None),
                SignalItemV2(value="System Design", type="competency", priority="required", weight=3, knockout=False, stage="interview", source="ai_inferred", inference_basis="Senior role implies architectural ownership"),
                SignalItemV2(value="Mentoring", type="behavioral", priority="preferred", weight=1, knockout=False, stage="interview", source="ai_inferred", inference_basis="Senior role at growth-stage company"),
            ],
            seniority_level="senior",
            role_summary="A senior backend engineer at a Series A fintech. Owns end-to-end.",
        ),
    )
```

Update assertions: `snap.signals` instead of `snap.required_skills`. Check `len(snap.signals) == 5`.

- [ ] **Step 3: Update `test_jd_signals.py` — `_save_signals_body`**

```python
def _save_signals_body(**overrides) -> dict:
    base = {
        "signals": [
            {"value": "Python", "type": "competency", "priority": "required", "weight": 2, "knockout": False, "stage": "interview", "source": "ai_extracted", "inference_basis": None},
            {"value": "FastAPI", "type": "competency", "priority": "required", "weight": 2, "knockout": False, "stage": "interview", "source": "ai_extracted", "inference_basis": None},
            {"value": "Docker", "type": "competency", "priority": "preferred", "weight": 1, "knockout": False, "stage": "interview", "source": "ai_extracted", "inference_basis": None},
            {"value": "5+ years backend", "type": "experience", "priority": "required", "weight": 2, "knockout": True, "stage": "screen", "source": "ai_extracted", "inference_basis": None},
            {"value": "Kubernetes experience", "type": "competency", "priority": "preferred", "weight": 1, "knockout": False, "stage": "interview", "source": "recruiter", "inference_basis": None},
        ],
        "seniority_level": "senior",
        "role_summary": "A senior backend engineer owning the platform end-to-end.",
    }
    base.update(overrides)
    return base
```

Update all assertions to use `data["signals"]` instead of `data["required_skills"]`.

- [ ] **Step 4: Run full test suite**

```bash
docker compose run --rm nexus pytest -x -v
```

Expected: ALL tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test(jd): update all tests for signal schema v2"
```

---

## Task 6: Prompt Rewrite — Call 1 + Call 2

**Files:**
- Modify: `backend/nexus/prompts/v1/jd_enhancement.txt`
- Modify: `backend/nexus/prompts/v1/jd_reenrichment.txt`

- [ ] **Step 1: Rewrite Call 1 prompt**

Rewrite `prompts/v1/jd_enhancement.txt` with the full new signal schema instructions. The prompt must include:
- Signal type definitions (competency, experience, credential, behavioral) with examples
- Priority, weight, knockout assignment heuristics
- Stage assignment heuristics (screen vs interview)
- Provenance rules (unchanged from current)
- Soft skills rule (unchanged)
- Coverage requirements (min 5 signals, balanced stage/type)
- Output format: `{ enriched_jd: string, signals: { signals: [...], seniority_level: string, role_summary: string } }`

Read the current prompt first to preserve the enriched JD rules (dual-audience, canonical sections, preserve verbatim sections). Only the signal extraction output section changes.

- [ ] **Step 2: Update Call 2 prompt**

Update `prompts/v1/jd_reenrichment.txt` to reference the flat signal list format instead of 4 named arrays. The input section should describe `signals` as a flat list with `type`, `priority`, `stage` fields on each item.

- [ ] **Step 3: Commit**

```bash
git add prompts/v1/
git commit -m "feat(ai): rewrite prompts for signal schema v2 — flat list with type/priority/weight/knockout/stage"
```

---

## Task 7: Frontend Types + Zustand Store

**Files:**
- Modify: `frontend/app/lib/api/jobs.ts`
- Modify: `frontend/app/stores/job-edit.ts`

- [ ] **Step 1: Rewrite `lib/api/jobs.ts` types**

Replace `SignalItem`, `SignalSnapshot`, `SaveSignalsBody` with v2 types. Add job metadata types and enums. Add metadata fields to `JobPostingWithSnapshot` and `CreateJobBody`. Update the `saveSignals` API method body shape.

Key type:
```typescript
export type SignalType = 'competency' | 'experience' | 'credential' | 'behavioral'
export type SignalPriority = 'required' | 'preferred'
export type SignalStage = 'screen' | 'interview'
export type EvaluationMethod = 'depth_probe' | 'verification' | 'situational' | 'case_study'

export type SignalItem = {
  value: string
  type: SignalType
  priority: SignalPriority
  weight: 1 | 2 | 3
  knockout: boolean
  stage: SignalStage
  evaluation_method: EvaluationMethod
  evaluation_hint: string | null
  source: 'ai_extracted' | 'ai_inferred' | 'recruiter'
  inference_basis: string | null
}

export type SignalSnapshot = {
  version: number
  signals: SignalItem[]
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
  confirmed_by: string | null
  confirmed_at: string | null
}
```

- [ ] **Step 2: Rewrite `stores/job-edit.ts`**

Change from 4-section model to flat list:

```typescript
type DraftSignals = {
  signals: SignalItem[]
  seniority_level: 'junior' | 'mid' | 'senior' | 'lead' | 'principal'
  role_summary: string
}
```

Update `addChip` to accept full signal metadata (type, stage, etc.), `removeChip` to work by index on the flat list, `startEditing` to copy the flat list.

- [ ] **Step 3: Commit**

```bash
git add lib/api/jobs.ts stores/job-edit.ts
git commit -m "feat(jd): frontend types + Zustand store for signal schema v2"
```

---

## Task 8: Frontend Components — SignalsPanel + EditableSignalsPanel + SignalChip

**Files:**
- Modify: `frontend/app/components/dashboard/jd-panels/SignalChip.tsx`
- Modify: `frontend/app/components/dashboard/jd-panels/SignalsPanel.tsx`
- Modify: `frontend/app/components/dashboard/jd-panels/EditableSignalsPanel.tsx`
- Modify: `frontend/app/components/dashboard/jd-panels/SignalsPanelWrapper.tsx`

- [ ] **Step 1: Update `SignalChip.tsx`**

Add weight indicator (dots/stars) and knockout badge (small red icon). The chip still uses provenance color (blue/amber/green) as before, but now also shows weight visually and knockout status.

- [ ] **Step 2: Rewrite `SignalsPanel.tsx`**

Group signals by `stage` (Screen / Interview), then by `type` within each stage. Filter `snapshot.signals` to render sections:

```typescript
const screenSignals = snapshot.signals.filter(s => s.stage === 'screen')
const interviewSignals = snapshot.signals.filter(s => s.stage === 'interview')
```

Within each stage, group by type: knockouts first (if screen), then competencies, experiences, credentials, behavioral.

- [ ] **Step 3: Rewrite `EditableSignalsPanel.tsx`**

Same stage→type grouping as read-only panel, but with:
- Type selector dropdown per chip
- Weight selector (1/2/3) per chip
- Knockout toggle per chip
- Stage selector (screen/interview) per chip
- Add chip with type/stage pre-filled based on which section the user clicks "Add" in

- [ ] **Step 4: Update `SignalsPanelWrapper.tsx`**

Update `handleSave` to send the flat list:

```typescript
function handleSave() {
  if (!draft) return
  saveSignals.mutate({
    signals: draft.signals,
    seniority_level: draft.seniority_level,
    role_summary: draft.role_summary,
  }, { onSuccess: () => { markClean(); stopEditing() } })
}
```

- [ ] **Step 5: Run type-check + lint**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
```

- [ ] **Step 6: Commit**

```bash
git add components/dashboard/jd-panels/
git commit -m "feat(jd): frontend signal panels for schema v2 — stage→type grouping"
```

---

## Task 9: Job Creation Form — Metadata Fields

**Files:**
- Modify: `frontend/app/app/(dashboard)/jobs/new/page.tsx`

- [ ] **Step 1: Add metadata fields to the Zod schema and form**

Add optional fields to the existing Zod schema:

```typescript
employment_type: z.enum(['full_time', 'part_time', 'contract', 'internship']).optional().nullable(),
work_arrangement: z.enum(['remote', 'hybrid', 'onsite']).optional().nullable(),
location: z.string().max(200).optional().nullable(),
salary_range_min: z.coerce.number().int().min(0).optional().nullable(),
salary_range_max: z.coerce.number().int().min(0).optional().nullable(),
salary_currency: z.enum(['INR', 'USD', 'EUR']).optional().nullable(),
travel_required: z.enum(['none', 'occasional', 'frequent']).optional().nullable(),
start_date_pref: z.enum(['immediate', 'within_30_days', 'within_90_days', 'flexible']).optional().nullable(),
```

Add form inputs: select dropdowns for enums, text input for location (conditionally shown when hybrid/onsite), two number inputs + currency select for salary range.

Group the new fields under an "Additional Details" section below the existing form fields with a subtle divider.

- [ ] **Step 2: Update the mutation body**

Pass the new fields to `jobsApi.create()`.

- [ ] **Step 3: Run type-check + build**

```bash
npx tsc --noEmit
npm run build
```

- [ ] **Step 4: Commit**

```bash
git add app/\(dashboard\)/jobs/new/page.tsx
git commit -m "feat(jd): job creation form with metadata fields (employment type, salary, location, etc.)"
```

---

## Task 10: Final Integration Verification

- [ ] **Step 1: Run backend tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest -x -q
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend checks**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
npm run test
npm run build
```

Expected: all pass.

- [ ] **Step 3: Manual smoke test**

1. Create a new job with metadata (employment type, salary, etc.)
2. Verify Call 1 extracts signals in flat list format with type/priority/weight/knockout/stage
3. Verify signals panel displays grouped by stage→type
4. Edit signals — add/remove chips, change weight, toggle knockout
5. Save signals, verify re-enrich stale banner appears
6. Confirm signals, verify confirmation flow
7. Verify jobs list still works

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(jd): Signal Schema v2 — universal flat list with type/priority/weight/knockout/stage + job metadata"
```
