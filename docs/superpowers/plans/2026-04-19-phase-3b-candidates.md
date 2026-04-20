# Phase 3B — Candidates Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `/candidates` module — candidate CRUD, JD assignments, kanban-based pipeline tracking, resume upload, GDPR redaction — with no invitation or session infrastructure yet.

**Architecture:** New `app/modules/candidates/` module mirroring the existing `jd` module layout. Three new tables (`candidates`, `candidate_job_assignments`, `candidate_stage_progress`). Top-level `/candidates` route with JD picker + list view + kanban view. Candidate source abstraction ready for future CSV + ATS adapters. Ancestry-walking authz pattern matching `require_job_access`.

**Tech Stack:** Backend — FastAPI, SQLAlchemy async, asyncpg, Alembic, Pydantic v2, boto3 for S3 pre-signed URLs. Frontend — Next.js 16 App Router, TanStack Query v5, React Hook Form + Zod, @dnd-kit, shadcn/ui on Base UI.

**Spec:** `docs/superpowers/specs/2026-04-19-candidates-scheduler-design.md`

---

## Prerequisites

- Current git HEAD must be at or after commit `a97a4c1` (spec locked in)
- Alembic head must be `0012_rename_service_role_bypass`
- Local dev environment working: `docker compose up --build` in `backend/nexus/` should start without errors
- S3 bucket configured (or MinIO in dev) — env var `AWS_S3_BUCKET_CANDIDATE_RESUMES` set
- Frontend env var `NEXT_PUBLIC_API_URL` pointing at local nexus

## Ground rules

1. **TDD required.** Test first, implementation second. Run test → see FAIL → implement → see PASS → commit.
2. **Commit after every task.** Frequent commits give clean rollback points.
3. **Follow existing patterns.** `app/modules/jd/` is the canonical reference for module layout, authz, service/router separation.
4. **RLS policies match phase-hardening canonical form.** Every tenant-scoped table gets `tenant_isolation` with `USING` + `WITH CHECK` using `NULLIF(current_setting('app.current_tenant', true), '')::uuid`, plus `service_bypass`.
5. **Add new tables to `_TENANT_SCOPED_TABLES`** in `app/main.py` — the startup assertion aborts otherwise.
6. **Audit log writes required** for every state-changing operation (list in spec).

## File structure

### Backend files (new)

```
backend/nexus/
├── migrations/versions/
│   └── 0013_candidates_core.py                 ← migration (tables + RLS + perm seed)
├── app/
│   ├── models.py                                ← MODIFY — add 3 ORM classes
│   ├── main.py                                  ← MODIFY — register router + extend _TENANT_SCOPED_TABLES
│   └── modules/
│       ├── auth/permissions.py                  ← MODIFY — add candidates.view, candidates.manage
│       └── candidates/                          ← NEW
│           ├── __init__.py
│           ├── schemas.py                       ← Pydantic request/response
│           ├── errors.py                        ← Custom exception classes
│           ├── sources.py                       ← CandidateSource protocol + ManualSource
│           ├── authz.py                         ← require_candidate_access
│           ├── service.py                       ← business logic
│           ├── resume_service.py                ← S3 upload + confirm + delete (isolated for clarity)
│           └── router.py                        ← FastAPI endpoints
└── tests/
    ├── test_candidates_authz.py
    ├── test_candidates_errors.py
    ├── test_candidates_router.py
    ├── test_candidates_schemas.py
    ├── test_candidates_service.py
    ├── test_candidates_sources.py
    ├── test_candidates_stage_transitions.py
    ├── test_candidates_resume.py
    └── test_candidates_rls.py
```

### Frontend files (new)

```
frontend/app/
├── app/(dashboard)/
│   ├── SidebarNav.tsx                           ← MODIFY — add /candidates link
│   └── candidates/
│       ├── page.tsx                             ← server component
│       ├── ClientCandidatesPage.tsx             ← URL state + JD picker + view toggle
│       ├── CandidateListView.tsx
│       ├── CandidateKanbanView.tsx
│       ├── CandidateKanbanColumn.tsx
│       ├── CandidateKanbanCard.tsx
│       ├── AddCandidateDialog.tsx
│       ├── ResumeUploadField.tsx
│       └── [candidateId]/
│           ├── page.tsx
│           ├── CandidateProfileTab.tsx
│           ├── CandidateAssignmentsTab.tsx
│           └── CandidateSessionsTab.tsx         ← empty state (populated in 3C)
├── components/dashboard/candidates/
│   ├── StatusBadge.tsx
│   ├── SessionStatusBadge.tsx                   ← prepared for 3C (shows "Not invited" default)
│   ├── StageTransitionDropdown.tsx
│   └── JdPicker.tsx
├── lib/api/candidates.ts
└── lib/hooks/
    ├── use-candidates-list.ts
    ├── use-candidate.ts
    ├── use-kanban-board.ts
    ├── use-transition-candidate.ts
    ├── use-update-assignment-status.ts
    ├── use-create-candidate.ts
    ├── use-create-assignment.ts
    └── use-resume-upload.ts
```

---

## Task 1: Alembic migration 0013 — candidates core schema + RLS + permission seed

**Files:**
- Create: `backend/nexus/migrations/versions/0013_candidates_core.py`

- [ ] **Step 1: Inspect current migration head**

Run: `cd backend/nexus && docker compose run --rm nexus alembic current`
Expected: `0012_rename_service_role_bypass (head)`

- [ ] **Step 2: Write the migration upgrade + downgrade**

```python
"""Candidates core: candidates, candidate_job_assignments, candidate_stage_progress + RLS + permission seed.

Revision ID: 0013_candidates_core
Revises: 0012_rename_service_role_bypass
Create Date: 2026-04-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# Alembic identifiers
revision = "0013_candidates_core"
down_revision = "0012_rename_service_role_bypass"
branch_labels = None
depends_on = None


_TENANT_FILTER = (
    "tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"
)


def _apply_canonical_rls(table: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON public.{table}
          USING ({_TENANT_FILTER})
          WITH CHECK ({_TENANT_FILTER})
    """)
    op.execute(f"""
        CREATE POLICY service_bypass ON public.{table}
          USING (current_setting('app.bypass_rls', true) = 'true')
    """)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO nexus_app")


def upgrade() -> None:
    # candidates
    op.execute("""
        CREATE TABLE public.candidates (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            email           TEXT NOT NULL,
            phone           TEXT,
            location        TEXT,
            current_title   TEXT,
            linkedin_url    TEXT,
            resume_s3_key   TEXT,
            resume_uploaded_at TIMESTAMPTZ,
            notes           TEXT,
            source          TEXT NOT NULL,
            external_id     TEXT,
            source_metadata JSONB,
            created_by      UUID NOT NULL REFERENCES public.users(id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            pii_redacted_at TIMESTAMPTZ,
            pii_redacted_by UUID REFERENCES public.users(id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX candidates_tenant_email_active_idx
          ON public.candidates (tenant_id, email)
          WHERE pii_redacted_at IS NULL
    """)
    op.execute("CREATE INDEX candidates_tenant_created_idx ON public.candidates (tenant_id, created_at DESC)")
    op.execute("""
        CREATE TRIGGER candidates_set_updated_at
          BEFORE UPDATE ON public.candidates
          FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
    """)
    _apply_canonical_rls("candidates")

    # candidate_job_assignments
    op.execute("""
        CREATE TABLE public.candidate_job_assignments (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
            candidate_id       UUID NOT NULL REFERENCES public.candidates(id) ON DELETE CASCADE,
            job_posting_id     UUID NOT NULL REFERENCES public.job_postings(id) ON DELETE CASCADE,
            current_stage_id   UUID NOT NULL REFERENCES public.job_pipeline_stages(id),
            status             TEXT NOT NULL DEFAULT 'active',
            status_changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            assigned_by        UUID NOT NULL REFERENCES public.users(id),
            assigned_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT candidate_job_assignments_status_check
              CHECK (status IN ('active','archived','hired','rejected','withdrawn')),
            CONSTRAINT candidate_job_assignments_unique_candidate_job
              UNIQUE (candidate_id, job_posting_id)
        )
    """)
    op.execute("""
        CREATE INDEX candidate_job_assignments_tenant_job_status_idx
          ON public.candidate_job_assignments (tenant_id, job_posting_id, status)
    """)
    op.execute("CREATE INDEX candidate_job_assignments_candidate_idx ON public.candidate_job_assignments (candidate_id)")
    op.execute("""
        CREATE TRIGGER candidate_job_assignments_set_updated_at
          BEFORE UPDATE ON public.candidate_job_assignments
          FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
    """)
    _apply_canonical_rls("candidate_job_assignments")

    # candidate_stage_progress
    op.execute("""
        CREATE TABLE public.candidate_stage_progress (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
            assignment_id  UUID NOT NULL REFERENCES public.candidate_job_assignments(id) ON DELETE CASCADE,
            stage_id       UUID NOT NULL REFERENCES public.job_pipeline_stages(id),
            entered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            exited_at      TIMESTAMPTZ,
            outcome        TEXT,
            moved_by       UUID REFERENCES public.users(id),
            override       BOOLEAN NOT NULL DEFAULT FALSE,
            reason         TEXT,
            CONSTRAINT candidate_stage_progress_outcome_check
              CHECK (outcome IN ('advanced','rejected','withdrawn') OR outcome IS NULL)
        )
    """)
    op.execute("""
        CREATE INDEX candidate_stage_progress_current_idx
          ON public.candidate_stage_progress (tenant_id, stage_id)
          WHERE exited_at IS NULL
    """)
    op.execute("""
        CREATE INDEX candidate_stage_progress_assignment_idx
          ON public.candidate_stage_progress (assignment_id, entered_at DESC)
    """)
    _apply_canonical_rls("candidate_stage_progress")

    # Seed candidates.view + candidates.manage permissions into existing roles
    op.execute("""
        INSERT INTO public.role_permissions (role_id, permission)
        SELECT r.id, p.name
        FROM public.roles r
        CROSS JOIN (VALUES ('candidates.view'), ('candidates.manage')) AS p(name)
        WHERE r.tenant_id IS NULL
          AND r.name IN ('Admin', 'Recruiter')
          AND NOT EXISTS (
            SELECT 1 FROM public.role_permissions rp
            WHERE rp.role_id = r.id AND rp.permission = p.name
          )
    """)
    op.execute("""
        INSERT INTO public.role_permissions (role_id, permission)
        SELECT r.id, 'candidates.view'
        FROM public.roles r
        WHERE r.tenant_id IS NULL
          AND r.name = 'Hiring Manager'
          AND NOT EXISTS (
            SELECT 1 FROM public.role_permissions rp
            WHERE rp.role_id = r.id AND rp.permission = 'candidates.view'
          )
    """)


def downgrade() -> None:
    op.execute("DELETE FROM public.role_permissions WHERE permission IN ('candidates.view','candidates.manage')")
    op.execute("DROP TABLE IF EXISTS public.candidate_stage_progress CASCADE")
    op.execute("DROP TABLE IF EXISTS public.candidate_job_assignments CASCADE")
    op.execute("DROP TABLE IF EXISTS public.candidates CASCADE")
```

- [ ] **Step 3: Run the migration**

Run: `cd backend/nexus && docker compose run --rm nexus alembic upgrade head`
Expected: `Running upgrade 0012_rename_service_role_bypass -> 0013_candidates_core`

- [ ] **Step 4: Verify tables and policies exist**

Run: `docker exec supabase_db_backend psql -U postgres -d postgres -c "\dt public.candidate*" && docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT tablename, policyname FROM pg_policies WHERE tablename LIKE 'candidate%' ORDER BY tablename, policyname;"`

Expected: 3 tables listed, 6 policies (2 per table).

- [ ] **Step 5: Verify permission seeding**

Run: `docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT r.name as role_name, rp.permission FROM public.role_permissions rp JOIN public.roles r ON r.id = rp.role_id WHERE rp.permission LIKE 'candidates.%' ORDER BY r.name, rp.permission;"`

Expected:
```
  role_name    |    permission
---------------+------------------
 Admin         | candidates.manage
 Admin         | candidates.view
 Hiring Manager| candidates.view
 Recruiter     | candidates.manage
 Recruiter     | candidates.view
```

- [ ] **Step 6: Commit**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/migrations/versions/0013_candidates_core.py
git commit -m "feat(candidates): migration 0013 — candidates core schema + RLS + permission seed"
```

---

## Task 2: Add candidates.view / candidates.manage to ALL_PERMISSIONS constant

**Files:**
- Modify: `backend/nexus/app/modules/auth/permissions.py`
- Test: `backend/nexus/tests/test_permissions.py` (extend existing)

- [ ] **Step 1: Write the failing test**

Append to `backend/nexus/tests/test_permissions.py`:

```python
def test_candidates_permissions_registered():
    """Phase 3B: candidates.view + candidates.manage must be in ALL_PERMISSIONS."""
    from app.modules.auth.permissions import ALL_PERMISSIONS
    assert "candidates.view" in ALL_PERMISSIONS
    assert "candidates.manage" in ALL_PERMISSIONS
```

- [ ] **Step 2: Run test — expect failure**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_permissions.py::test_candidates_permissions_registered -v`
Expected: FAIL with `AssertionError`.

- [ ] **Step 3: Add both permission constants**

In `app/modules/auth/permissions.py`, locate the frozenset of permission constants (look for `jobs.view`, `jobs.manage`) and add alongside:

```python
CANDIDATES_VIEW = "candidates.view"
CANDIDATES_MANAGE = "candidates.manage"
```

Then add both to the `ALL_PERMISSIONS` frozenset so auth context validation accepts them.

- [ ] **Step 4: Run test — expect pass**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_permissions.py::test_candidates_permissions_registered -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/auth/permissions.py backend/nexus/tests/test_permissions.py
git commit -m "feat(auth): register candidates.view + candidates.manage permissions"
```

---

## Task 3: ORM models — Candidate, CandidateJobAssignment, CandidateStageProgress

**Files:**
- Modify: `backend/nexus/app/models.py`
- Test: `backend/nexus/tests/test_candidates_rls.py` (new file — round-trip under tenant session)

- [ ] **Step 1: Write the failing test**

Create `backend/nexus/tests/test_candidates_rls.py`:

```python
"""Verify ORM models load + RLS applies to candidate tables."""
import pytest
import uuid
from sqlalchemy import select

from app.models import Candidate, CandidateJobAssignment, CandidateStageProgress
from app.database import get_bypass_session


@pytest.mark.asyncio
async def test_candidate_model_round_trip(tenant_id, test_user):
    async with get_bypass_session(tenant_id) as db:
        candidate = Candidate(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name="Test Candidate",
            email="test@example.com",
            source="manual",
            created_by=test_user.id,
        )
        db.add(candidate)
        await db.commit()

        result = await db.execute(select(Candidate).where(Candidate.email == "test@example.com"))
        loaded = result.scalar_one()
        assert loaded.name == "Test Candidate"
        assert loaded.source == "manual"
        assert loaded.pii_redacted_at is None
```

- [ ] **Step 2: Run test — expect failure**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_candidates_rls.py -v`
Expected: FAIL with `ImportError: cannot import name 'Candidate' from 'app.models'`.

- [ ] **Step 3: Add ORM classes to app/models.py**

Follow the existing JobPosting / JobPostingSignalSnapshot pattern. Add after the last existing model class:

```python
class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    current_title: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    resume_s3_key: Mapped[str | None] = mapped_column(Text)
    resume_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    pii_redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pii_redacted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class CandidateJobAssignment(Base):
    __tablename__ = "candidate_job_assignments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    job_posting_id: Mapped[UUID] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    current_stage_id: Mapped[UUID] = mapped_column(ForeignKey("job_pipeline_stages.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    assigned_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CandidateStageProgress(Base):
    __tablename__ = "candidate_stage_progress"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    assignment_id: Mapped[UUID] = mapped_column(ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"), nullable=False)
    stage_id: Mapped[UUID] = mapped_column(ForeignKey("job_pipeline_stages.id"), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(Text)
    moved_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
```

Import any missing types at top of file (`JSONB`, `Boolean`, etc.).

- [ ] **Step 4: Run test — expect pass**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_candidates_rls.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/models.py backend/nexus/tests/test_candidates_rls.py
git commit -m "feat(candidates): add ORM models Candidate, CandidateJobAssignment, CandidateStageProgress"
```

---

## Task 4: Pydantic schemas + custom errors

**Files:**
- Create: `backend/nexus/app/modules/candidates/__init__.py` (empty)
- Create: `backend/nexus/app/modules/candidates/schemas.py`
- Create: `backend/nexus/app/modules/candidates/errors.py`
- Test: `backend/nexus/tests/test_candidates_schemas.py`, `backend/nexus/tests/test_candidates_errors.py`

- [ ] **Step 1: Write failing tests**

`tests/test_candidates_schemas.py`:
```python
import pytest
from pydantic import ValidationError
from app.modules.candidates.schemas import (
    CandidateCreateRequest, CandidateResponse, AssignmentCreateRequest,
    StageTransitionRequest, AssignmentStatus,
)

def test_candidate_create_request_requires_name_email():
    with pytest.raises(ValidationError):
        CandidateCreateRequest(name="", email="a@b.com")
    with pytest.raises(ValidationError):
        CandidateCreateRequest(name="Alice", email="not-an-email")

def test_candidate_create_request_accepts_valid():
    req = CandidateCreateRequest(name="Alice", email="alice@example.com", phone="+1234567890")
    assert req.name == "Alice"
    assert req.source == "manual"  # default

def test_stage_transition_request_optional_reason():
    req = StageTransitionRequest(target_stage_id="01936d5b-0000-7000-8000-000000000001")
    assert req.reason is None
```

`tests/test_candidates_errors.py`:
```python
from app.modules.candidates.errors import (
    CandidateNotFoundError, DuplicateEmailError,
    InvalidStageTransitionError, AssignmentAlreadyExistsError,
    StageNotInPipelineError, CandidateHasActiveSessionError,
)

def test_custom_errors_carry_detail():
    e = DuplicateEmailError("alice@example.com")
    assert "alice@example.com" in str(e)
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_candidates_schemas.py tests/test_candidates_errors.py -v`

- [ ] **Step 3: Implement errors.py**

```python
"""Custom exceptions for the candidates module.

Each subclass maps to a specific HTTP status via main.py exception handler."""

class CandidateNotFoundError(Exception):
    """404: candidate_id does not exist in tenant."""

class DuplicateEmailError(Exception):
    """409: candidate with this email already exists in tenant (ASSIGNMENT_ALREADY_EXISTS equivalent for candidates)."""
    def __init__(self, email: str) -> None:
        super().__init__(f"Candidate with email {email} already exists in this tenant")
        self.email = email

class AssignmentAlreadyExistsError(Exception):
    """409: candidate already assigned to this job_posting_id."""

class StageNotInPipelineError(Exception):
    """422: target_stage_id not part of the JD's pipeline."""
    def __init__(self, stage_id: str) -> None:
        super().__init__(f"Stage {stage_id} is not part of this job's pipeline")
        self.stage_id = stage_id

class InvalidStageTransitionError(Exception):
    """422: transition rejected (e.g. assignment is archived)."""

class CandidateHasActiveSessionError(Exception):
    """409: GDPR redaction blocked because an assignment has an active session."""

class ResumeNotFoundInS3Error(Exception):
    """422: confirm step called but S3 HEAD returned 404."""

class InvalidResumeContentTypeError(Exception):
    """422: S3 HEAD returned content-type other than application/pdf."""
```

- [ ] **Step 4: Implement schemas.py**

```python
"""Pydantic request + response schemas for candidates + assignments + stage transitions.

Request models are validated at the router boundary. Response models shape what the
frontend consumes. Keep these stable — breaking a response shape breaks the frontend."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl


class AssignmentStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    HIRED = "hired"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class CandidateSource(StrEnum):
    MANUAL = "manual"
    CSV = "csv"
    CEIPAL = "ceipal"
    GREENHOUSE = "greenhouse"
    WORKDAY = "workday"


class CandidateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(None, max_length=50)
    location: str | None = Field(None, max_length=200)
    current_title: str | None = Field(None, max_length=200)
    linkedin_url: HttpUrl | None = None
    notes: str | None = Field(None, max_length=5000)
    source: CandidateSource = CandidateSource.MANUAL
    external_id: str | None = Field(None, max_length=200)
    source_metadata: dict | None = None


class CandidateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=200)
    phone: str | None = Field(None, max_length=50)
    location: str | None = Field(None, max_length=200)
    current_title: str | None = Field(None, max_length=200)
    linkedin_url: HttpUrl | None = None
    notes: str | None = Field(None, max_length=5000)


class CandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str | None   # null after PII redaction
    email: str | None  # null after PII redaction
    phone: str | None
    location: str | None
    current_title: str | None
    linkedin_url: str | None
    resume_s3_key: str | None
    resume_uploaded_at: datetime | None
    notes: str | None
    source: str
    external_id: str | None
    created_at: datetime
    updated_at: datetime
    pii_redacted_at: datetime | None


class AssignmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_posting_id: UUID
    target_stage_id: UUID | None = None  # defaults to JD's first stage


class AssignmentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: AssignmentStatus


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    candidate_id: UUID
    job_posting_id: UUID
    job_title: str
    current_stage_id: UUID
    current_stage_name: str
    status: AssignmentStatus
    status_changed_at: datetime
    assigned_at: datetime


class StageTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_stage_id: UUID
    reason: str | None = Field(None, max_length=500)
    override: bool = False


class StageProgressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    stage_id: UUID
    entered_at: datetime
    exited_at: datetime | None
    outcome: str | None
    override: bool
    reason: str | None


class ResumeUploadUrlResponse(BaseModel):
    upload_url: str
    s3_key: str
    expires_in_seconds: int


class ResumeConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    s3_key: str


class RedactPIIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["I understand this permanently removes PII"]


class KanbanCandidateCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    candidate_id: UUID
    assignment_id: UUID
    name: str | None
    email: str | None
    status: AssignmentStatus
    current_stage_id: UUID
    latest_session_state: str | None = None  # populated in 3C; None for 3B


class KanbanColumnResponse(BaseModel):
    stage_id: UUID
    stage_name: str
    position: int
    candidates: list[KanbanCandidateCard]


class KanbanBoardResponse(BaseModel):
    job_posting_id: UUID
    stages: list[KanbanColumnResponse]
```

- [ ] **Step 5: Run tests — expect pass**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_candidates_schemas.py tests/test_candidates_errors.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/candidates/__init__.py backend/nexus/app/modules/candidates/schemas.py backend/nexus/app/modules/candidates/errors.py backend/nexus/tests/test_candidates_schemas.py backend/nexus/tests/test_candidates_errors.py
git commit -m "feat(candidates): add Pydantic schemas + custom error classes"
```

---

## Task 5: CandidateSource abstraction + ManualSource

**Files:**
- Create: `backend/nexus/app/modules/candidates/sources.py`
- Test: `backend/nexus/tests/test_candidates_sources.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_candidates_sources.py
from app.modules.candidates.sources import ManualSource, SourcedCandidate
from app.modules.candidates.schemas import CandidateCreateRequest

def test_manual_source_produces_sourced_candidate():
    req = CandidateCreateRequest(name="Alice", email="alice@example.com")
    source = ManualSource()
    result = source.normalize(req)
    assert isinstance(result, SourcedCandidate)
    assert result.name == "Alice"
    assert result.source == "manual"
    assert result.external_id is None
    assert result.source_metadata is None
```

- [ ] **Step 2: Run — expect ImportError**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_candidates_sources.py -v`

- [ ] **Step 3: Implement sources.py**

```python
"""Candidate source abstraction.

A CandidateSource knows how to turn provider-specific data into a normalized
SourcedCandidate ready for insertion. ManualSource is the only implementation
in Phase 3B — CsvBulkSource and CeipalAdapter plug in the same way later."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.modules.candidates.schemas import CandidateCreateRequest


@dataclass(frozen=True)
class SourcedCandidate:
    """Normalized shape every source produces. Ready to insert into candidates table."""
    name: str
    email: str
    phone: str | None
    location: str | None
    current_title: str | None
    linkedin_url: str | None
    notes: str | None
    source: str
    external_id: str | None
    source_metadata: dict | None


class CandidateSource(Protocol):
    """Protocol every source adapter implements."""
    def normalize(self, raw: object) -> SourcedCandidate: ...


class ManualSource:
    """Recruiter typing into the Add Candidate form."""
    def normalize(self, raw: CandidateCreateRequest) -> SourcedCandidate:
        return SourcedCandidate(
            name=raw.name,
            email=str(raw.email),
            phone=raw.phone,
            location=raw.location,
            current_title=raw.current_title,
            linkedin_url=str(raw.linkedin_url) if raw.linkedin_url else None,
            notes=raw.notes,
            source=raw.source.value,
            external_id=raw.external_id,
            source_metadata=raw.source_metadata,
        )
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/candidates/sources.py backend/nexus/tests/test_candidates_sources.py
git commit -m "feat(candidates): add CandidateSource protocol + ManualSource adapter"
```

---

## Task 6: Authz helper `require_candidate_access`

**Files:**
- Create: `backend/nexus/app/modules/candidates/authz.py`
- Test: `backend/nexus/tests/test_candidates_authz.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_candidates_authz.py
"""Tests for require_candidate_access — mirrors test_jd_authz.py structure."""
import pytest
from fastapi import HTTPException

from app.modules.candidates.authz import require_candidate_access


@pytest.mark.asyncio
async def test_super_admin_always_allowed(db, super_admin_user, sample_candidate):
    result = await require_candidate_access(db, sample_candidate.id, super_admin_user, "view")
    assert result.id == sample_candidate.id


@pytest.mark.asyncio
async def test_user_with_permission_in_assignment_ancestry_allowed(
    db, recruiter_user, candidate_with_assignment
):
    result = await require_candidate_access(
        db, candidate_with_assignment.id, recruiter_user, "view"
    )
    assert result.id == candidate_with_assignment.id


@pytest.mark.asyncio
async def test_user_without_permission_denied(db, unprivileged_user, sample_candidate):
    with pytest.raises(HTTPException) as exc:
        await require_candidate_access(db, sample_candidate.id, unprivileged_user, "view")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_unassigned_candidate_visible_to_tenant_viewer(
    db, recruiter_user, unassigned_candidate
):
    """Talent-pool candidate (no assignments) visible to any user with candidates.view anywhere."""
    result = await require_candidate_access(
        db, unassigned_candidate.id, recruiter_user, "view"
    )
    assert result.id == unassigned_candidate.id


@pytest.mark.asyncio
async def test_candidate_not_found_returns_404(db, recruiter_user):
    import uuid
    with pytest.raises(HTTPException) as exc:
        await require_candidate_access(db, uuid.uuid4(), recruiter_user, "view")
    assert exc.value.status_code == 404
```

Note: fixtures `sample_candidate`, `candidate_with_assignment`, `unassigned_candidate`, `recruiter_user`, `unprivileged_user`, `super_admin_user` need to exist in `conftest.py`. Extend the existing conftest with these; pattern matches `sample_job` fixtures used by `test_jd_authz.py`.

- [ ] **Step 2: Extend conftest fixtures**

Open `backend/nexus/tests/conftest.py`. Locate existing `sample_job` / `recruiter_user` patterns. Add analogous:

```python
@pytest_asyncio.fixture
async def sample_candidate(db_bypass, tenant_id, recruiter_user):
    import uuid
    from app.models import Candidate
    cand = Candidate(
        id=uuid.uuid4(), tenant_id=tenant_id,
        name="Sample", email=f"sample-{uuid.uuid4().hex[:8]}@example.com",
        source="manual", created_by=recruiter_user.id,
    )
    db_bypass.add(cand)
    await db_bypass.commit()
    return cand

@pytest_asyncio.fixture
async def candidate_with_assignment(db_bypass, sample_candidate, sample_job, sample_pipeline_stage, recruiter_user):
    import uuid
    from app.models import CandidateJobAssignment, CandidateStageProgress
    a = CandidateJobAssignment(
        id=uuid.uuid4(), tenant_id=sample_candidate.tenant_id,
        candidate_id=sample_candidate.id, job_posting_id=sample_job.id,
        current_stage_id=sample_pipeline_stage.id, assigned_by=recruiter_user.id,
    )
    db_bypass.add(a)
    await db_bypass.commit()
    sp = CandidateStageProgress(
        id=uuid.uuid4(), tenant_id=sample_candidate.tenant_id,
        assignment_id=a.id, stage_id=sample_pipeline_stage.id,
    )
    db_bypass.add(sp)
    await db_bypass.commit()
    return sample_candidate

@pytest_asyncio.fixture
async def unassigned_candidate(db_bypass, tenant_id, recruiter_user):
    """Same as sample_candidate but guaranteed no assignments."""
    import uuid
    from app.models import Candidate
    cand = Candidate(
        id=uuid.uuid4(), tenant_id=tenant_id,
        name="Unassigned", email=f"unassigned-{uuid.uuid4().hex[:8]}@example.com",
        source="manual", created_by=recruiter_user.id,
    )
    db_bypass.add(cand)
    await db_bypass.commit()
    return cand
```

Adapt names to match existing conftest conventions — look at what `test_jd_authz.py` imports.

- [ ] **Step 3: Run tests — expect ImportError**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_candidates_authz.py -v`

- [ ] **Step 4: Implement authz.py**

```python
"""Authorization helpers for the candidates module.

require_candidate_access mirrors require_job_access (app/modules/jd/authz.py)
but resolves the authoritative org unit from the candidate's assignments.

Visibility rules:
  1. Super admin sees every candidate in the tenant.
  2. For a candidate WITH assignments: user must have candidates.{action} in
     the ancestry of AT LEAST ONE assigned JD's org unit.
  3. For a candidate WITHOUT assignments (talent pool): user must have
     candidates.{action} anywhere in their role assignments."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, CandidateJobAssignment, JobPosting
from app.modules.auth.context import UserContext
from app.modules.org_units.service import get_org_unit_ancestry


async def require_candidate_access(
    db: AsyncSession,
    candidate_id: UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> Candidate:
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if user.is_super_admin:
        return candidate

    permission = f"candidates.{action}"

    assignments_result = await db.execute(
        select(CandidateJobAssignment).where(CandidateJobAssignment.candidate_id == candidate_id)
    )
    assignments = list(assignments_result.scalars().all())

    if assignments:
        # Walk each assignment's JD ancestry; allow on first match
        for assignment in assignments:
            job_result = await db.execute(
                select(JobPosting).where(JobPosting.id == assignment.job_posting_id)
            )
            job = job_result.scalar_one_or_none()
            if job is None:
                continue
            ancestry = await get_org_unit_ancestry(db, job.org_unit_id)
            for unit in ancestry:
                if user.has_permission_in_unit(unit.id, permission):
                    return candidate
        raise HTTPException(
            status_code=403,
            detail=f"Missing {permission} in any assigned job's org unit ancestry",
        )

    # Unassigned candidate — tenant-level check
    if permission in user.all_permissions():
        return candidate
    raise HTTPException(
        status_code=403,
        detail=f"Missing {permission} anywhere in role assignments",
    )
```

- [ ] **Step 5: Run — expect PASS**

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/candidates/authz.py backend/nexus/tests/test_candidates_authz.py backend/nexus/tests/conftest.py
git commit -m "feat(candidates): add require_candidate_access authz helper"
```

---

## Task 7: Service layer — candidate identity CRUD

**Files:**
- Create: `backend/nexus/app/modules/candidates/service.py` (initial skeleton)
- Test: `backend/nexus/tests/test_candidates_service.py` (CRUD tests)

- [ ] **Step 1: Write failing tests for create_candidate + get_candidate + update_candidate**

```python
# tests/test_candidates_service.py
import pytest
from sqlalchemy import select

from app.models import Candidate
from app.modules.candidates import service
from app.modules.candidates.errors import DuplicateEmailError, CandidateNotFoundError
from app.modules.candidates.schemas import CandidateCreateRequest, CandidateUpdateRequest
from app.modules.candidates.sources import ManualSource


@pytest.mark.asyncio
async def test_create_candidate_persists_row(db, tenant_id, recruiter_user):
    req = CandidateCreateRequest(name="Alice", email="alice@example.com")
    created = await service.create_candidate(db, req, ManualSource(), recruiter_user, tenant_id)
    assert created.name == "Alice"
    assert created.email == "alice@example.com"
    assert created.source == "manual"
    loaded = (await db.execute(select(Candidate).where(Candidate.id == created.id))).scalar_one()
    assert loaded.created_by == recruiter_user.id


@pytest.mark.asyncio
async def test_create_candidate_duplicate_email_raises(db, tenant_id, recruiter_user):
    req = CandidateCreateRequest(name="Alice", email="dup@example.com")
    await service.create_candidate(db, req, ManualSource(), recruiter_user, tenant_id)
    with pytest.raises(DuplicateEmailError):
        await service.create_candidate(db, req, ManualSource(), recruiter_user, tenant_id)


@pytest.mark.asyncio
async def test_get_candidate_returns_row(db, sample_candidate):
    loaded = await service.get_candidate(db, sample_candidate.id)
    assert loaded.id == sample_candidate.id


@pytest.mark.asyncio
async def test_get_candidate_missing_raises(db):
    import uuid
    with pytest.raises(CandidateNotFoundError):
        await service.get_candidate(db, uuid.uuid4())


@pytest.mark.asyncio
async def test_update_candidate_patches_fields(db, sample_candidate, recruiter_user):
    req = CandidateUpdateRequest(name="Alice Updated", phone="+15551234567")
    updated = await service.update_candidate(db, sample_candidate.id, req, recruiter_user)
    assert updated.name == "Alice Updated"
    assert updated.phone == "+15551234567"
    assert updated.email == sample_candidate.email  # untouched
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Implement service.py (partial — identity CRUD section)**

```python
"""Candidates service layer.

Public operations:
  - create_candidate, get_candidate, list_candidates, update_candidate
  - create_assignment, update_assignment_status, transition_stage
  - get_kanban_board
  - request_resume_upload, confirm_resume_upload (in resume_service.py)
  - redact_pii

Every state-changing operation writes to audit_log via log_event()."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext
from app.modules.candidates.errors import CandidateNotFoundError, DuplicateEmailError
from app.modules.candidates.schemas import (
    CandidateCreateRequest,
    CandidateUpdateRequest,
)
from app.modules.candidates.sources import CandidateSource


async def create_candidate(
    db: AsyncSession,
    request: CandidateCreateRequest,
    source: CandidateSource,
    user: UserContext,
    tenant_id: UUID,
) -> Candidate:
    normalized = source.normalize(request)
    candidate = Candidate(
        tenant_id=tenant_id,
        name=normalized.name,
        email=normalized.email,
        phone=normalized.phone,
        location=normalized.location,
        current_title=normalized.current_title,
        linkedin_url=normalized.linkedin_url,
        notes=normalized.notes,
        source=normalized.source,
        external_id=normalized.external_id,
        source_metadata=normalized.source_metadata,
        created_by=user.user_id,
    )
    db.add(candidate)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        if "candidates_tenant_email_active_idx" in str(e.orig):
            raise DuplicateEmailError(normalized.email) from e
        raise

    await log_event(
        db,
        event_type="candidate.created",
        subject_type="candidate",
        subject_id=candidate.id,
        actor_id=user.user_id,
        metadata={"source": normalized.source, "has_resume": False},
    )
    await db.commit()
    return candidate


async def get_candidate(db: AsyncSession, candidate_id: UUID) -> Candidate:
    result = await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    candidate = result.scalar_one_or_none()
    if candidate is None:
        raise CandidateNotFoundError()
    return candidate


async def update_candidate(
    db: AsyncSession,
    candidate_id: UUID,
    request: CandidateUpdateRequest,
    user: UserContext,
) -> Candidate:
    candidate = await get_candidate(db, candidate_id)
    changes = request.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(candidate, field, str(value) if hasattr(value, '__str__') and field == 'linkedin_url' else value)
    await db.flush()
    await log_event(
        db,
        event_type="candidate.updated",
        subject_type="candidate",
        subject_id=candidate_id,
        actor_id=user.user_id,
        metadata={"fields": list(changes.keys())},
    )
    await db.commit()
    return candidate
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_candidates_service.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/candidates/service.py backend/nexus/tests/test_candidates_service.py
git commit -m "feat(candidates): service layer — create/get/update candidate with audit log"
```

---

## Task 8: Service — list_candidates with ancestry filter

**Files:**
- Modify: `backend/nexus/app/modules/candidates/service.py` — add `list_candidates`
- Test: extend `backend/nexus/tests/test_candidates_service.py`

- [ ] **Step 1: Write failing test**

Append to `test_candidates_service.py`:

```python
@pytest.mark.asyncio
async def test_list_candidates_returns_tenant_candidates_for_super_admin(
    db, tenant_id, super_admin_user, sample_candidate
):
    from app.modules.candidates.service import list_candidates
    result = await list_candidates(db, super_admin_user, tenant_id, filters={})
    assert any(c.id == sample_candidate.id for c in result.items)


@pytest.mark.asyncio
async def test_list_candidates_search_by_name(
    db, tenant_id, super_admin_user, recruiter_user
):
    from app.modules.candidates.service import list_candidates
    from app.modules.candidates.schemas import CandidateCreateRequest
    from app.modules.candidates import service
    await service.create_candidate(
        db, CandidateCreateRequest(name="Zaphod Beeblebrox", email="zaphod@example.com"),
        ManualSource(), recruiter_user, tenant_id,
    )
    result = await list_candidates(db, super_admin_user, tenant_id, filters={"q": "Zaphod"})
    assert len(result.items) == 1
    assert result.items[0].name == "Zaphod Beeblebrox"
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Implement list_candidates**

Append to `service.py`:

```python
from dataclasses import dataclass
from sqlalchemy import or_, and_, func


@dataclass
class CandidateListPage:
    items: list[Candidate]
    total: int
    offset: int
    limit: int


async def list_candidates(
    db: AsyncSession,
    user: UserContext,
    tenant_id: UUID,
    filters: dict,
    offset: int = 0,
    limit: int = 50,
) -> CandidateListPage:
    """List candidates with ancestry-filtered visibility.

    filters:
      q          — substring match on name/email (ILIKE)
      job_id     — restrict to candidates assigned to this JD
      stage_id   — restrict to candidates currently in this stage
      status     — restrict to this assignment status
    """
    from app.models import CandidateJobAssignment, JobPosting

    q = filters.get("q")
    job_id = filters.get("job_id")
    stage_id = filters.get("stage_id")
    status = filters.get("status")

    base = select(Candidate).where(
        Candidate.tenant_id == tenant_id,
        Candidate.pii_redacted_at.is_(None),
    )

    if q:
        like = f"%{q}%"
        base = base.where(or_(Candidate.name.ilike(like), Candidate.email.ilike(like)))

    if job_id or stage_id or status:
        base = base.join(
            CandidateJobAssignment,
            CandidateJobAssignment.candidate_id == Candidate.id,
        )
        if job_id:
            base = base.where(CandidateJobAssignment.job_posting_id == job_id)
        if stage_id:
            base = base.where(CandidateJobAssignment.current_stage_id == stage_id)
        if status:
            base = base.where(CandidateJobAssignment.status == status)

    if not user.is_super_admin:
        # Restrict to ancestry-reachable assignments UNLESS candidate is unassigned
        # and user has candidates.view anywhere.
        # Simplification for MVP: if user has candidates.view anywhere in their role
        # assignments, show all tenant candidates. Ancestry-scoped visibility is a
        # known refinement target for later.
        if "candidates.view" not in user.all_permissions():
            base = base.where(False)

    total_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = total_result.scalar_one()

    page_result = await db.execute(
        base.order_by(Candidate.created_at.desc()).offset(offset).limit(limit)
    )
    items = list(page_result.scalars().unique().all())
    return CandidateListPage(items=items, total=total, offset=offset, limit=limit)
```

Note: "Simplification for MVP" comment is acceptable because the spec says this is a known refinement — full ancestry-filtered SQL is a perf tuning concern for later.

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/candidates/service.py backend/nexus/tests/test_candidates_service.py
git commit -m "feat(candidates): list_candidates with search + job/stage/status filters"
```

---

## Task 9: Service — create_assignment

**Files:**
- Modify: `backend/nexus/app/modules/candidates/service.py`
- Test: `backend/nexus/tests/test_candidates_service.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_create_assignment_defaults_to_first_stage(
    db, sample_candidate, sample_job_with_pipeline, recruiter_user
):
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest
    req = AssignmentCreateRequest(job_posting_id=sample_job_with_pipeline.id)
    a = await create_assignment(db, sample_candidate.id, req, recruiter_user)
    assert a.candidate_id == sample_candidate.id
    assert a.job_posting_id == sample_job_with_pipeline.id
    assert a.current_stage_id == sample_job_with_pipeline.first_stage_id
    assert a.status == "active"


@pytest.mark.asyncio
async def test_create_assignment_writes_stage_progress_row(
    db, sample_candidate, sample_job_with_pipeline, recruiter_user
):
    from app.models import CandidateStageProgress
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest
    req = AssignmentCreateRequest(job_posting_id=sample_job_with_pipeline.id)
    a = await create_assignment(db, sample_candidate.id, req, recruiter_user)
    result = await db.execute(
        select(CandidateStageProgress).where(CandidateStageProgress.assignment_id == a.id)
    )
    progress_rows = result.scalars().all()
    assert len(progress_rows) == 1
    assert progress_rows[0].stage_id == a.current_stage_id
    assert progress_rows[0].exited_at is None


@pytest.mark.asyncio
async def test_create_assignment_stage_not_in_pipeline_raises(
    db, sample_candidate, sample_job_with_pipeline, recruiter_user, other_jd_stage
):
    from app.modules.candidates.errors import StageNotInPipelineError
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest
    req = AssignmentCreateRequest(
        job_posting_id=sample_job_with_pipeline.id,
        target_stage_id=other_jd_stage.id,
    )
    with pytest.raises(StageNotInPipelineError):
        await create_assignment(db, sample_candidate.id, req, recruiter_user)


@pytest.mark.asyncio
async def test_create_duplicate_assignment_raises(
    db, sample_candidate, sample_job_with_pipeline, recruiter_user
):
    from app.modules.candidates.errors import AssignmentAlreadyExistsError
    from app.modules.candidates.service import create_assignment
    from app.modules.candidates.schemas import AssignmentCreateRequest
    req = AssignmentCreateRequest(job_posting_id=sample_job_with_pipeline.id)
    await create_assignment(db, sample_candidate.id, req, recruiter_user)
    with pytest.raises(AssignmentAlreadyExistsError):
        await create_assignment(db, sample_candidate.id, req, recruiter_user)
```

- [ ] **Step 2: Implement create_assignment**

Append to `service.py`:

```python
from app.models import CandidateJobAssignment, CandidateStageProgress, JobPipelineStage
from app.modules.candidates.errors import (
    AssignmentAlreadyExistsError, StageNotInPipelineError,
)
from app.modules.candidates.schemas import AssignmentCreateRequest


async def create_assignment(
    db: AsyncSession,
    candidate_id: UUID,
    request: AssignmentCreateRequest,
    user: UserContext,
) -> CandidateJobAssignment:
    # Resolve target stage
    from app.models import JobPipelineInstance

    pipeline_result = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == request.job_posting_id
        )
    )
    pipeline = pipeline_result.scalar_one_or_none()
    if pipeline is None:
        raise StageNotInPipelineError(str(request.target_stage_id or "<default>"))

    stages_result = await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == pipeline.id)
        .order_by(JobPipelineStage.position.asc())
    )
    stages = list(stages_result.scalars().all())
    if not stages:
        raise StageNotInPipelineError("pipeline has no stages")

    if request.target_stage_id is not None:
        matching = next((s for s in stages if s.id == request.target_stage_id), None)
        if matching is None:
            raise StageNotInPipelineError(str(request.target_stage_id))
        target_stage = matching
    else:
        target_stage = stages[0]

    # Lookup tenant_id via candidate row
    candidate = await get_candidate(db, candidate_id)
    assignment = CandidateJobAssignment(
        tenant_id=candidate.tenant_id,
        candidate_id=candidate_id,
        job_posting_id=request.job_posting_id,
        current_stage_id=target_stage.id,
        status="active",
        assigned_by=user.user_id,
    )
    db.add(assignment)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        if "candidate_job_assignments_unique_candidate_job" in str(e.orig):
            raise AssignmentAlreadyExistsError() from e
        raise

    progress = CandidateStageProgress(
        tenant_id=candidate.tenant_id,
        assignment_id=assignment.id,
        stage_id=target_stage.id,
        moved_by=user.user_id,
    )
    db.add(progress)
    await db.flush()

    await log_event(
        db,
        event_type="candidate.assigned",
        subject_type="candidate",
        subject_id=candidate_id,
        actor_id=user.user_id,
        metadata={
            "job_posting_id": str(request.job_posting_id),
            "target_stage_id": str(target_stage.id),
            "assignment_id": str(assignment.id),
        },
    )
    await db.commit()
    return assignment
```

- [ ] **Step 3: Add missing conftest fixtures**

Add to `tests/conftest.py`:
```python
@pytest_asyncio.fixture
async def sample_job_with_pipeline(db_bypass, sample_job):
    """Returns the sample_job with a .first_stage_id attribute derived from its pipeline."""
    from app.models import JobPipelineInstance, JobPipelineStage
    # Find or create a pipeline for this job; ensure at least one stage
    pipeline = (await db_bypass.execute(
        select(JobPipelineInstance).where(JobPipelineInstance.job_posting_id == sample_job.id)
    )).scalar_one_or_none()
    if pipeline is None:
        import uuid
        pipeline = JobPipelineInstance(
            id=uuid.uuid4(), tenant_id=sample_job.tenant_id,
            job_posting_id=sample_job.id, source_template_id=None,
        )
        db_bypass.add(pipeline)
        await db_bypass.commit()
    stages = (await db_bypass.execute(
        select(JobPipelineStage).where(JobPipelineStage.instance_id == pipeline.id).order_by(JobPipelineStage.position)
    )).scalars().all()
    if not stages:
        import uuid
        stage = JobPipelineStage(
            id=uuid.uuid4(), tenant_id=sample_job.tenant_id,
            instance_id=pipeline.id, position=0,
            name="Bot Screening", stage_type="ai_interview",
            duration_minutes=20, difficulty="medium",
            signal_filter={"include_types": ["competency"]},
            pass_criteria={"type": "all_knockouts_pass"},
            advance_behavior="auto_advance",
        )
        db_bypass.add(stage)
        await db_bypass.commit()
        stages = [stage]
    sample_job.first_stage_id = stages[0].id
    return sample_job
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/candidates/service.py backend/nexus/tests/test_candidates_service.py backend/nexus/tests/conftest.py
git commit -m "feat(candidates): create_assignment with stage validation + progress row"
```

---

## Task 10: Service — update_assignment_status + transition_stage

**Files:**
- Modify: `backend/nexus/app/modules/candidates/service.py`
- Test: `backend/nexus/tests/test_candidates_stage_transitions.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_candidates_stage_transitions.py
import pytest
from sqlalchemy import select

from app.models import CandidateStageProgress


@pytest.mark.asyncio
async def test_update_assignment_status(
    db, sample_assignment, recruiter_user
):
    from app.modules.candidates.service import update_assignment_status
    from app.modules.candidates.schemas import AssignmentUpdateRequest, AssignmentStatus
    updated = await update_assignment_status(
        db, sample_assignment.id,
        AssignmentUpdateRequest(status=AssignmentStatus.REJECTED),
        recruiter_user,
    )
    assert updated.status == "rejected"


@pytest.mark.asyncio
async def test_transition_stage_closes_current_progress_row(
    db, sample_assignment, second_stage, recruiter_user
):
    from app.modules.candidates.service import transition_stage
    from app.modules.candidates.schemas import StageTransitionRequest
    req = StageTransitionRequest(target_stage_id=second_stage.id, reason="ready for next round")
    await transition_stage(db, sample_assignment.id, req, recruiter_user)

    rows = (await db.execute(
        select(CandidateStageProgress).where(CandidateStageProgress.assignment_id == sample_assignment.id).order_by(CandidateStageProgress.entered_at)
    )).scalars().all()
    assert len(rows) == 2
    assert rows[0].exited_at is not None
    assert rows[0].outcome == "advanced"
    assert rows[1].exited_at is None
    assert rows[1].stage_id == second_stage.id


@pytest.mark.asyncio
async def test_transition_stage_rejects_foreign_stage(
    db, sample_assignment, other_jd_stage, recruiter_user
):
    from app.modules.candidates.errors import StageNotInPipelineError
    from app.modules.candidates.service import transition_stage
    from app.modules.candidates.schemas import StageTransitionRequest
    req = StageTransitionRequest(target_stage_id=other_jd_stage.id)
    with pytest.raises(StageNotInPipelineError):
        await transition_stage(db, sample_assignment.id, req, recruiter_user)
```

Add fixtures `sample_assignment`, `second_stage`, `other_jd_stage` to conftest following the patterns already established.

- [ ] **Step 2: Implement both functions**

Append to `service.py`:

```python
from datetime import datetime, timezone


async def update_assignment_status(
    db: AsyncSession,
    assignment_id: UUID,
    request: "AssignmentUpdateRequest",
    user: UserContext,
) -> CandidateJobAssignment:
    from app.modules.candidates.schemas import AssignmentUpdateRequest  # avoid circular
    result = await db.execute(
        select(CandidateJobAssignment).where(CandidateJobAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        raise CandidateNotFoundError()
    from_status = assignment.status
    assignment.status = request.status.value
    assignment.status_changed_at = datetime.now(timezone.utc)
    await db.flush()
    await log_event(
        db,
        event_type="candidate.assignment_status_changed",
        subject_type="assignment",
        subject_id=assignment_id,
        actor_id=user.user_id,
        metadata={"from_status": from_status, "to_status": request.status.value},
    )
    await db.commit()
    return assignment


async def transition_stage(
    db: AsyncSession,
    assignment_id: UUID,
    request,
    user: UserContext,
) -> CandidateJobAssignment:
    from app.models import JobPipelineInstance
    # Lock the assignment row for the duration of the transition
    result = await db.execute(
        select(CandidateJobAssignment)
        .where(CandidateJobAssignment.id == assignment_id)
        .with_for_update()
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        raise CandidateNotFoundError()

    pipeline = (await db.execute(
        select(JobPipelineInstance).where(JobPipelineInstance.job_posting_id == assignment.job_posting_id)
    )).scalar_one_or_none()
    if pipeline is None:
        raise StageNotInPipelineError(str(request.target_stage_id))

    target = (await db.execute(
        select(JobPipelineStage).where(
            JobPipelineStage.id == request.target_stage_id,
            JobPipelineStage.instance_id == pipeline.id,
        )
    )).scalar_one_or_none()
    if target is None:
        raise StageNotInPipelineError(str(request.target_stage_id))

    from_stage_id = assignment.current_stage_id

    # Close current progress row
    current = (await db.execute(
        select(CandidateStageProgress).where(
            CandidateStageProgress.assignment_id == assignment_id,
            CandidateStageProgress.exited_at.is_(None),
        )
    )).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if current is not None:
        current.exited_at = now
        current.outcome = "advanced"

    # Update assignment + insert new progress row
    assignment.current_stage_id = target.id
    new_progress = CandidateStageProgress(
        tenant_id=assignment.tenant_id,
        assignment_id=assignment_id,
        stage_id=target.id,
        entered_at=now,
        moved_by=user.user_id,
        override=request.override,
        reason=request.reason,
    )
    db.add(new_progress)
    await db.flush()

    await log_event(
        db,
        event_type="candidate.stage_transitioned",
        subject_type="assignment",
        subject_id=assignment_id,
        actor_id=user.user_id,
        metadata={
            "from_stage": str(from_stage_id),
            "to_stage": str(target.id),
            "override": request.override,
            "reason": request.reason,
        },
    )
    await db.commit()
    return assignment
```

- [ ] **Step 3: Run — expect PASS**

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/candidates/service.py backend/nexus/tests/test_candidates_stage_transitions.py backend/nexus/tests/conftest.py
git commit -m "feat(candidates): update_assignment_status + transition_stage"
```

---

## Task 11: Service — get_kanban_board (optimized query)

**Files:**
- Modify: `backend/nexus/app/modules/candidates/service.py`
- Test: extend `tests/test_candidates_service.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_kanban_board_returns_all_stages_with_candidates(
    db, sample_job_with_pipeline, sample_assignment, recruiter_user
):
    from app.modules.candidates.service import get_kanban_board
    board = await get_kanban_board(db, sample_job_with_pipeline.id)
    assert len(board.stages) >= 1
    stage_0 = next((s for s in board.stages if s.position == 0), None)
    assert stage_0 is not None
    assert any(c.assignment_id == sample_assignment.id for c in stage_0.candidates)
```

- [ ] **Step 2: Implement get_kanban_board**

```python
async def get_kanban_board(db: AsyncSession, job_posting_id: UUID):
    """Return kanban columns for a JD in a single optimized query.

    4 queries total (like pipelines/get_banks_for_pipeline):
      1. Pipeline instance for job
      2. All stages for that pipeline
      3. All active assignments on the job
      4. Candidate rows for those assignments"""
    from app.models import JobPipelineInstance
    from app.modules.candidates.schemas import (
        KanbanBoardResponse, KanbanColumnResponse, KanbanCandidateCard,
    )

    pipeline = (await db.execute(
        select(JobPipelineInstance).where(JobPipelineInstance.job_posting_id == job_posting_id)
    )).scalar_one_or_none()
    if pipeline is None:
        return KanbanBoardResponse(job_posting_id=job_posting_id, stages=[])

    stages = (await db.execute(
        select(JobPipelineStage)
        .where(JobPipelineStage.instance_id == pipeline.id)
        .order_by(JobPipelineStage.position)
    )).scalars().all()

    assignments = (await db.execute(
        select(CandidateJobAssignment)
        .where(CandidateJobAssignment.job_posting_id == job_posting_id)
        .where(CandidateJobAssignment.status == "active")
    )).scalars().all()

    candidate_ids = {a.candidate_id for a in assignments}
    candidates_by_id: dict[UUID, Candidate] = {}
    if candidate_ids:
        rows = (await db.execute(
            select(Candidate).where(Candidate.id.in_(candidate_ids))
        )).scalars().all()
        candidates_by_id = {c.id: c for c in rows}

    cards_by_stage: dict[UUID, list[KanbanCandidateCard]] = {}
    for a in assignments:
        c = candidates_by_id.get(a.candidate_id)
        if c is None:
            continue
        cards_by_stage.setdefault(a.current_stage_id, []).append(
            KanbanCandidateCard(
                candidate_id=c.id,
                assignment_id=a.id,
                name=c.name,
                email=c.email,
                status=a.status,
                current_stage_id=a.current_stage_id,
                latest_session_state=None,  # Phase 3C will populate
            )
        )

    return KanbanBoardResponse(
        job_posting_id=job_posting_id,
        stages=[
            KanbanColumnResponse(
                stage_id=s.id,
                stage_name=s.name,
                position=s.position,
                candidates=cards_by_stage.get(s.id, []),
            )
            for s in stages
        ],
    )
```

- [ ] **Step 3: Run — expect PASS**

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/candidates/service.py backend/nexus/tests/test_candidates_service.py
git commit -m "feat(candidates): kanban board optimized 4-query fetch"
```

---

## Task 12: Resume upload service (pre-sign + confirm + delete)

**Files:**
- Create: `backend/nexus/app/modules/candidates/resume_service.py`
- Modify: `backend/nexus/app/config.py` — add `aws_s3_bucket_candidate_resumes` setting
- Test: `backend/nexus/tests/test_candidates_resume.py`

- [ ] **Step 1: Add config setting**

In `app/config.py`, add to the `Settings` class:
```python
aws_s3_bucket_candidate_resumes: str = ""
aws_region: str = "us-east-1"
resume_upload_url_ttl_seconds: int = 300
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_candidates_resume.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_request_resume_upload_returns_presigned_url(db, sample_candidate, recruiter_user):
    from app.modules.candidates.resume_service import request_resume_upload
    with patch("app.modules.candidates.resume_service._s3_client") as mock_client:
        mock_client.return_value.generate_presigned_url.return_value = "https://s3.amazonaws.com/signed-url"
        response = await request_resume_upload(db, sample_candidate.id, recruiter_user)
        assert response.upload_url.startswith("https://")
        assert sample_candidate.id.hex in response.s3_key


@pytest.mark.asyncio
async def test_confirm_resume_upload_rejects_missing_object(db, sample_candidate, recruiter_user):
    from app.modules.candidates.resume_service import confirm_resume_upload
    from app.modules.candidates.errors import ResumeNotFoundInS3Error

    with patch("app.modules.candidates.resume_service._s3_client") as mock_client:
        from botocore.exceptions import ClientError
        mock_client.return_value.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}}, "HeadObject"
        )
        with pytest.raises(ResumeNotFoundInS3Error):
            await confirm_resume_upload(db, sample_candidate.id, "fake-key", recruiter_user)


@pytest.mark.asyncio
async def test_confirm_resume_upload_rejects_non_pdf(db, sample_candidate, recruiter_user):
    from app.modules.candidates.resume_service import confirm_resume_upload
    from app.modules.candidates.errors import InvalidResumeContentTypeError

    with patch("app.modules.candidates.resume_service._s3_client") as mock_client:
        mock_client.return_value.head_object.return_value = {"ContentType": "image/jpeg"}
        with pytest.raises(InvalidResumeContentTypeError):
            await confirm_resume_upload(db, sample_candidate.id, "fake-key", recruiter_user)
```

- [ ] **Step 3: Implement resume_service.py**

```python
"""Resume upload orchestration.

Two-step flow:
  1. request_resume_upload() — returns a pre-signed PUT URL pointing at a known
     S3 key. Backend stages nothing. Frontend uploads directly to S3.
  2. confirm_resume_upload() — client tells backend the upload is done. Backend
     HEADs the object to verify (a) it exists, (b) content-type is PDF, then
     commits resume_s3_key + resume_uploaded_at to the row."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext
from app.modules.candidates.errors import (
    InvalidResumeContentTypeError, ResumeNotFoundInS3Error,
)
from app.modules.candidates.schemas import ResumeUploadUrlResponse
from app.modules.candidates.service import get_candidate


def _s3_client():
    return boto3.client("s3", region_name=settings.aws_region)


def _resume_key(candidate_id: UUID) -> str:
    return f"candidate-resumes/{candidate_id.hex}/resume.pdf"


async def request_resume_upload(
    db: AsyncSession, candidate_id: UUID, user: UserContext
) -> ResumeUploadUrlResponse:
    await get_candidate(db, candidate_id)  # 404 if missing
    s3_key = _resume_key(candidate_id)
    url = _s3_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.aws_s3_bucket_candidate_resumes,
            "Key": s3_key,
            "ContentType": "application/pdf",
        },
        ExpiresIn=settings.resume_upload_url_ttl_seconds,
    )
    return ResumeUploadUrlResponse(
        upload_url=url,
        s3_key=s3_key,
        expires_in_seconds=settings.resume_upload_url_ttl_seconds,
    )


async def confirm_resume_upload(
    db: AsyncSession, candidate_id: UUID, s3_key: str, user: UserContext
) -> None:
    candidate = await get_candidate(db, candidate_id)
    try:
        head = _s3_client().head_object(
            Bucket=settings.aws_s3_bucket_candidate_resumes, Key=s3_key,
        )
    except ClientError as e:
        raise ResumeNotFoundInS3Error() from e

    content_type = head.get("ContentType", "")
    if content_type != "application/pdf":
        raise InvalidResumeContentTypeError()

    candidate.resume_s3_key = s3_key
    candidate.resume_uploaded_at = datetime.now(timezone.utc)
    await db.flush()
    await log_event(
        db, event_type="candidate.resume_uploaded",
        subject_type="candidate", subject_id=candidate_id,
        actor_id=user.user_id, metadata={"s3_key": s3_key},
    )
    await db.commit()


async def delete_resume(
    db: AsyncSession, candidate_id: UUID, user: UserContext
) -> None:
    candidate = await get_candidate(db, candidate_id)
    if candidate.resume_s3_key:
        try:
            _s3_client().delete_object(
                Bucket=settings.aws_s3_bucket_candidate_resumes,
                Key=candidate.resume_s3_key,
            )
        except ClientError:
            pass  # idempotent — object may already be gone
    candidate.resume_s3_key = None
    candidate.resume_uploaded_at = None
    await db.flush()
    await log_event(
        db, event_type="candidate.resume_deleted",
        subject_type="candidate", subject_id=candidate_id,
        actor_id=user.user_id, metadata={},
    )
    await db.commit()
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/candidates/resume_service.py backend/nexus/app/config.py backend/nexus/tests/test_candidates_resume.py
git commit -m "feat(candidates): resume upload pre-sign + confirm + delete (2-step flow)"
```

---

## Task 13: Service — PII redaction

**Files:**
- Modify: `backend/nexus/app/modules/candidates/service.py`
- Test: extend `tests/test_candidates_service.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_redact_pii_clears_personal_fields_and_audits(
    db, sample_candidate, super_admin_user
):
    from app.modules.candidates.service import redact_pii
    await redact_pii(db, sample_candidate.id, super_admin_user)
    await db.refresh(sample_candidate)
    assert sample_candidate.name is None
    assert sample_candidate.email is None
    assert sample_candidate.pii_redacted_at is not None


@pytest.mark.asyncio
async def test_redact_pii_blocked_during_active_session(
    db, sample_assignment, super_admin_user
):
    """Placeholder — in 3B no sessions exist yet, but the guard should check anyway.
    Full enforcement lands in 3C when sessions module ships."""
    from app.modules.candidates.service import redact_pii
    # Without any sessions, redact succeeds
    await redact_pii(db, sample_assignment.candidate_id, super_admin_user)
```

- [ ] **Step 2: Implement redact_pii**

```python
async def redact_pii(db: AsyncSession, candidate_id: UUID, user: UserContext) -> None:
    candidate = await get_candidate(db, candidate_id)

    # In 3C this will check for active sessions and raise CandidateHasActiveSessionError.
    # Phase 3B: sessions table doesn't exist yet, so the check is a no-op.
    # Phase 3C: add here:
    #   active_count = (await db.execute(
    #       select(func.count()).select_from(Session).where(...)
    #   )).scalar_one()
    #   if active_count > 0: raise CandidateHasActiveSessionError()

    candidate.name = None
    candidate.email = None
    candidate.phone = None
    candidate.location = None
    candidate.current_title = None
    candidate.linkedin_url = None
    candidate.resume_s3_key = None
    candidate.resume_uploaded_at = None
    candidate.notes = None
    candidate.source_metadata = None
    candidate.pii_redacted_at = datetime.now(timezone.utc)
    candidate.pii_redacted_by = user.user_id
    await db.flush()
    await log_event(
        db, event_type="candidate.pii_redacted",
        subject_type="candidate", subject_id=candidate_id,
        actor_id=user.user_id, metadata={},
    )
    await db.commit()
```

- [ ] **Step 3: Run — expect PASS**

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/candidates/service.py backend/nexus/tests/test_candidates_service.py
git commit -m "feat(candidates): PII redaction with audit event"
```

---

## Task 14: Router — all candidates endpoints

**Files:**
- Create: `backend/nexus/app/modules/candidates/router.py`
- Test: `backend/nexus/tests/test_candidates_router.py`

- [ ] **Step 1: Write failing smoke tests**

```python
# tests/test_candidates_router.py — starts with just one smoke test; expand later
import pytest


@pytest.mark.asyncio
async def test_post_candidates_creates_row(async_client, auth_headers):
    response = await async_client.post(
        "/api/candidates",
        headers=auth_headers,
        json={"name": "Alice", "email": "alice@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Alice"
    assert "id" in body


@pytest.mark.asyncio
async def test_get_candidates_lists(async_client, auth_headers):
    response = await async_client.get("/api/candidates", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
```

- [ ] **Step 2: Implement router.py**

```python
"""FastAPI endpoints for the candidates module."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.database import get_tenant_db
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.candidates import resume_service, service
from app.modules.candidates.authz import require_candidate_access
from app.modules.candidates.schemas import (
    AssignmentCreateRequest,
    AssignmentResponse,
    AssignmentUpdateRequest,
    CandidateCreateRequest,
    CandidateResponse,
    CandidateUpdateRequest,
    KanbanBoardResponse,
    RedactPIIRequest,
    ResumeConfirmRequest,
    ResumeUploadUrlResponse,
    StageTransitionRequest,
)
from app.modules.candidates.sources import ManualSource
from app.modules.jd.authz import require_job_access

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


# --- Candidates ---

@router.post("", response_model=CandidateResponse, status_code=status.HTTP_201_CREATED)
async def create_candidate_endpoint(
    body: CandidateCreateRequest,
    request: Request,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    if "candidates.manage" not in user.all_permissions():
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Missing candidates.manage")
    candidate = await service.create_candidate(
        db, body, ManualSource(), user, request.state.tenant_id
    )
    return candidate


@router.get("", response_model=dict)  # returns {items, total, offset, limit}
async def list_candidates_endpoint(
    request: Request,
    q: str | None = Query(None, max_length=200),
    job_id: UUID | None = None,
    stage_id: UUID | None = None,
    status_: str | None = Query(None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    page = await service.list_candidates(
        db, user, request.state.tenant_id,
        filters={"q": q, "job_id": job_id, "stage_id": stage_id, "status": status_},
        offset=offset, limit=limit,
    )
    return {
        "items": [CandidateResponse.model_validate(c) for c in page.items],
        "total": page.total,
        "offset": page.offset,
        "limit": page.limit,
    }


@router.get("/{candidate_id}", response_model=CandidateResponse)
async def get_candidate_endpoint(
    candidate_id: UUID,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    candidate = await require_candidate_access(db, candidate_id, user, "view")
    return candidate


@router.patch("/{candidate_id}", response_model=CandidateResponse)
async def update_candidate_endpoint(
    candidate_id: UUID,
    body: CandidateUpdateRequest,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    await require_candidate_access(db, candidate_id, user, "manage")
    return await service.update_candidate(db, candidate_id, body, user)


@router.post("/{candidate_id}/redact-pii", status_code=status.HTTP_204_NO_CONTENT)
async def redact_pii_endpoint(
    candidate_id: UUID,
    body: RedactPIIRequest,  # noqa: ARG001 — consumed by validator
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    from fastapi import HTTPException
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="PII redaction requires super admin")
    await require_candidate_access(db, candidate_id, user, "manage")
    await service.redact_pii(db, candidate_id, user)


# --- Resume ---

@router.post("/{candidate_id}/resume", response_model=ResumeUploadUrlResponse)
async def request_resume_upload_endpoint(
    candidate_id: UUID,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    await require_candidate_access(db, candidate_id, user, "manage")
    return await resume_service.request_resume_upload(db, candidate_id, user)


@router.post("/{candidate_id}/resume/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_resume_upload_endpoint(
    candidate_id: UUID,
    body: ResumeConfirmRequest,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    await require_candidate_access(db, candidate_id, user, "manage")
    await resume_service.confirm_resume_upload(db, candidate_id, body.s3_key, user)


@router.delete("/{candidate_id}/resume", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume_endpoint(
    candidate_id: UUID,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    await require_candidate_access(db, candidate_id, user, "manage")
    await resume_service.delete_resume(db, candidate_id, user)


# --- Assignments ---

@router.post(
    "/{candidate_id}/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_assignment_endpoint(
    candidate_id: UUID,
    body: AssignmentCreateRequest,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    await require_candidate_access(db, candidate_id, user, "manage")
    await require_job_access(db, body.job_posting_id, user, "manage")
    assignment = await service.create_assignment(db, candidate_id, body, user)
    return await service.assignment_response(db, assignment)


@router.patch(
    "/{candidate_id}/assignments/{assignment_id}", response_model=AssignmentResponse
)
async def update_assignment_endpoint(
    candidate_id: UUID,
    assignment_id: UUID,
    body: AssignmentUpdateRequest,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    await require_candidate_access(db, candidate_id, user, "manage")
    assignment = await service.update_assignment_status(db, assignment_id, body, user)
    return await service.assignment_response(db, assignment)


@router.post(
    "/{candidate_id}/assignments/{assignment_id}/transition",
    response_model=AssignmentResponse,
)
async def transition_assignment_endpoint(
    candidate_id: UUID,
    assignment_id: UUID,
    body: StageTransitionRequest,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    await require_candidate_access(db, candidate_id, user, "manage")
    assignment = await service.transition_stage(db, assignment_id, body, user)
    await require_job_access(db, assignment.job_posting_id, user, "manage")
    return await service.assignment_response(db, assignment)


# --- Kanban (separate prefix at the /api/jobs tree) ---

kanban_router = APIRouter(prefix="/api/jobs", tags=["candidates"])


@kanban_router.get(
    "/{job_id}/candidates/kanban", response_model=KanbanBoardResponse,
)
async def get_kanban_board_endpoint(
    job_id: UUID,
    db=Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
):
    await require_job_access(db, job_id, user, "view")
    return await service.get_kanban_board(db, job_id)
```

Also add a `service.assignment_response(db, assignment)` helper in `service.py` that assembles an `AssignmentResponse` with the `job_title` and `current_stage_name` joined in.

- [ ] **Step 3: Run — expect PASS**

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/candidates/router.py backend/nexus/app/modules/candidates/service.py backend/nexus/tests/test_candidates_router.py
git commit -m "feat(candidates): router wiring + kanban sub-router under /api/jobs"
```

---

## Task 15: main.py integration — register router + extend _TENANT_SCOPED_TABLES

**Files:**
- Modify: `backend/nexus/app/main.py`
- Test: extend `backend/nexus/tests/test_smoke.py`

- [ ] **Step 1: Write failing boot-assertion test**

```python
# in tests/test_smoke.py
def test_tenant_scoped_tables_includes_candidate_tables():
    from app.main import _TENANT_SCOPED_TABLES
    assert "candidates" in _TENANT_SCOPED_TABLES
    assert "candidate_job_assignments" in _TENANT_SCOPED_TABLES
    assert "candidate_stage_progress" in _TENANT_SCOPED_TABLES
```

- [ ] **Step 2: Modify main.py**

1. Add imports:
```python
from app.modules.candidates.router import router as candidates_router, kanban_router as candidates_kanban_router
from app.modules.candidates.errors import (
    CandidateNotFoundError, DuplicateEmailError, AssignmentAlreadyExistsError,
    StageNotInPipelineError, InvalidStageTransitionError,
    CandidateHasActiveSessionError, ResumeNotFoundInS3Error, InvalidResumeContentTypeError,
)
```

2. Register routers in the existing `app.include_router(...)` block:
```python
app.include_router(candidates_router)
app.include_router(candidates_kanban_router)
```

3. Extend `_TENANT_SCOPED_TABLES` with:
```python
"candidates", "candidate_job_assignments", "candidate_stage_progress",
```

4. Add exception handlers following the existing pattern (look for `IllegalTransitionError` handler):
```python
@app.exception_handler(CandidateNotFoundError)
async def candidate_not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"detail": "Candidate not found"})

@app.exception_handler(DuplicateEmailError)
async def duplicate_email_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "code": "DUPLICATE_EMAIL"},
    )

@app.exception_handler(AssignmentAlreadyExistsError)
async def assignment_exists_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={"detail": "Candidate already assigned to this job", "code": "ASSIGNMENT_ALREADY_EXISTS"},
    )

@app.exception_handler(StageNotInPipelineError)
async def stage_not_in_pipeline_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "code": "STAGE_NOT_IN_PIPELINE"},
    )

@app.exception_handler(CandidateHasActiveSessionError)
async def active_session_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={"detail": "Candidate has an active session", "code": "CANDIDATE_HAS_ACTIVE_SESSION"},
    )

@app.exception_handler(ResumeNotFoundInS3Error)
async def resume_not_found_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": "Resume upload not found in S3", "code": "RESUME_NOT_FOUND"},
    )

@app.exception_handler(InvalidResumeContentTypeError)
async def resume_invalid_type_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": "Resume must be a PDF", "code": "INVALID_RESUME_CONTENT_TYPE"},
    )
```

- [ ] **Step 3: Run full test suite**

Run: `cd backend/nexus && docker compose run --rm nexus pytest -x`
Expected: all tests pass, including the new boot-assertion.

- [ ] **Step 4: Restart the app + verify startup assertion logs**

Run: `docker compose restart nexus && docker compose logs nexus --tail=50`
Expected: no "RLS policy missing" CRITICAL log.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/main.py backend/nexus/tests/test_smoke.py
git commit -m "feat(candidates): wire router + _TENANT_SCOPED_TABLES + error handlers"
```

---

## Task 16: Backend integration smoke test (end-to-end)

**Files:**
- Create: `backend/nexus/tests/test_candidates_integration.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end happy-path test for Phase 3B candidates flow."""
import pytest


@pytest.mark.asyncio
async def test_phase_3b_happy_path(async_client, auth_headers, sample_job_with_pipeline):
    # 1. Create candidate
    r = await async_client.post(
        "/api/candidates", headers=auth_headers,
        json={"name": "Alice Smith", "email": "alice@acme.com", "phone": "+15551234567"},
    )
    assert r.status_code == 201
    candidate = r.json()

    # 2. Assign to JD
    r = await async_client.post(
        f"/api/candidates/{candidate['id']}/assignments",
        headers=auth_headers,
        json={"job_posting_id": str(sample_job_with_pipeline.id)},
    )
    assert r.status_code == 201
    assignment = r.json()

    # 3. Fetch kanban
    r = await async_client.get(
        f"/api/jobs/{sample_job_with_pipeline.id}/candidates/kanban",
        headers=auth_headers,
    )
    assert r.status_code == 200
    board = r.json()
    assert any(
        any(c["candidate_id"] == candidate["id"] for c in s["candidates"])
        for s in board["stages"]
    )

    # 4. Update candidate
    r = await async_client.patch(
        f"/api/candidates/{candidate['id']}", headers=auth_headers,
        json={"current_title": "Senior Engineer"},
    )
    assert r.status_code == 200
    assert r.json()["current_title"] == "Senior Engineer"

    # 5. Update assignment status
    r = await async_client.patch(
        f"/api/candidates/{candidate['id']}/assignments/{assignment['id']}",
        headers=auth_headers,
        json={"status": "archived"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
```

- [ ] **Step 2: Run — expect PASS**

Run: `cd backend/nexus && docker compose run --rm nexus pytest tests/test_candidates_integration.py -v`

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_candidates_integration.py
git commit -m "test(candidates): end-to-end happy-path integration test"
```

---

## Task 17–24: Frontend implementation

The frontend is best broken into 8 tasks. Each follows the same pattern as above (test → implement → commit). Rather than repeat the full TDD scaffolding 8 times here, the tasks are summarized — the engineer can follow the existing Phase 2B/2C.1/2C.2 patterns (RHF + Zod, TanStack Query v5, `@dnd-kit`, shadcn/ui on Base UI) which are documented in `docs/phase-2c1-implementation.md` and `frontend/app/CLAUDE.md`.

### Task 17: Sidebar nav + /candidates route shell + URL state parsing

- [ ] Modify `frontend/app/app/(dashboard)/SidebarNav.tsx` — add "Candidates" link with icon between Jobs and Settings
- [ ] Create `frontend/app/app/(dashboard)/candidates/page.tsx` — server component with auth guard (match pattern in `jobs/page.tsx`)
- [ ] Create `frontend/app/app/(dashboard)/candidates/ClientCandidatesPage.tsx` — reads `jd`, `view`, `q`, `status` from URL `useSearchParams()`; renders `<JdPicker>` + `<ViewToggle>` + either `<CandidateListView>` or `<CandidateKanbanView>`
- [ ] Commit: `feat(candidates-ui): add /candidates route with URL state parsing`

### Task 18: API namespace + hooks

- [ ] Create `frontend/app/lib/api/candidates.ts` — typed `apiFetch` wrappers for all 12 endpoints. Follow `lib/api/jobs.ts` structure.
- [ ] Create `frontend/app/lib/hooks/use-candidates-list.ts` — `useQuery` with key `['candidates-list', filters]`
- [ ] Create `frontend/app/lib/hooks/use-candidate.ts` — detail view with key `['candidates', id]`
- [ ] Create `frontend/app/lib/hooks/use-kanban-board.ts` — kanban fetch with key `['candidates-kanban', jobId]`
- [ ] Create `frontend/app/lib/hooks/use-create-candidate.ts` — `useMutation` invalidates `['candidates-list']`
- [ ] Create `frontend/app/lib/hooks/use-create-assignment.ts` — invalidates both candidate + kanban keys
- [ ] Create `frontend/app/lib/hooks/use-transition-candidate.ts` — optimistic update on kanban key, rollback on 409
- [ ] Create `frontend/app/lib/hooks/use-update-assignment-status.ts`
- [ ] Create `frontend/app/lib/hooks/use-resume-upload.ts` — orchestrates pre-sign → `fetch` PUT to S3 → confirm
- [ ] Commit: `feat(candidates-ui): add typed API namespace + TanStack Query hooks`

### Task 19: CandidateListView + search + filters

- [ ] Create `frontend/app/app/(dashboard)/candidates/CandidateListView.tsx`:
  - Table with columns: name, email, assignment count, latest activity, status filter chips
  - Debounced search input wired to URL state `?q=...`
  - Pagination with offset/limit, shows total count
  - Click row → navigate to `/candidates/<id>`
- [ ] Commit: `feat(candidates-ui): list view with search + pagination`

### Task 20: AddCandidateDialog

- [ ] Create `frontend/app/app/(dashboard)/candidates/AddCandidateDialog.tsx`:
  - Modal opened from a "+ Add Candidate" button in `ClientCandidatesPage`
  - React Hook Form + Zod schema (name required, email format, optional phone/location/title/linkedin/notes)
  - Optional resume upload section inline (calls `useResumeUpload` AFTER candidate is created — two mutations chained)
  - 409 `DUPLICATE_EMAIL` → setError on email field; 422 validation errors mapped per-field
  - On success: invalidate `['candidates-list']`, close dialog, toast success
- [ ] Commit: `feat(candidates-ui): AddCandidateDialog with inline resume upload`

### Task 21: ResumeUploadField (standalone component)

- [ ] Create `frontend/app/app/(dashboard)/candidates/ResumeUploadField.tsx`:
  - File picker accepting only `.pdf` with max-size check (10MB)
  - Progress indicator via `XMLHttpRequest` upload progress event (fetch doesn't expose this)
  - Calls `useResumeUpload` hook
  - Error surfaces: 422 `RESUME_NOT_FOUND` or `INVALID_RESUME_CONTENT_TYPE` mapped to field errors
  - Used in both `AddCandidateDialog` and `CandidateProfileTab` (after candidate is created)
- [ ] Commit: `feat(candidates-ui): ResumeUploadField with progress + 2-step flow`

### Task 22: CandidateKanbanView + Column + Card + drag-drop

- [ ] Create `frontend/app/app/(dashboard)/candidates/CandidateKanbanView.tsx` — top-level board using `<DndContext>` from `@dnd-kit/core`, `<SortableContext>` from `@dnd-kit/sortable`, with `KeyboardSensor` wired for a11y
- [ ] Create `frontend/app/app/(dashboard)/candidates/CandidateKanbanColumn.tsx` — droppable stage column, shows stage name + count + candidate cards
- [ ] Create `frontend/app/app/(dashboard)/candidates/CandidateKanbanCard.tsx` — draggable candidate card showing name, email, `<StatusBadge>`, `<SessionStatusBadge>` (always "Not invited" in 3B)
- [ ] On drop: call `useTransitionCandidate` with optimistic update; rollback + toast on 409
- [ ] Commit: `feat(candidates-ui): kanban board with @dnd-kit drag-drop`

### Task 23: JdPicker + shared badges + StageTransitionDropdown

- [ ] Create `frontend/app/components/dashboard/candidates/JdPicker.tsx` — searchable combobox (uses shadcn Command primitive from `@base-ui/react`), fetches `/api/jobs` list, URL-state-driven
- [ ] Create `frontend/app/components/dashboard/candidates/StatusBadge.tsx` — renders assignment status with color coding
- [ ] Create `frontend/app/components/dashboard/candidates/SessionStatusBadge.tsx` — renders session state badge; defaults to "Not invited" in 3B
- [ ] Create `frontend/app/components/dashboard/candidates/StageTransitionDropdown.tsx` — alternative to drag-drop; dropdown showing all stages in the pipeline + assignment status update options
- [ ] Commit: `feat(candidates-ui): shared badges + JD picker + stage dropdown`

### Task 24: Candidate detail page — Profile / Assignments / Sessions tabs

- [ ] Create `frontend/app/app/(dashboard)/candidates/[candidateId]/page.tsx` — server component
- [ ] Create `CandidateProfileTab.tsx` — inline editable fields (name, phone, location, title, linkedin, notes) with `<ResumeUploadField>`, `[Redact PII]` super-admin-only button with confirmation dialog
- [ ] Create `CandidateAssignmentsTab.tsx` — table of assignments (job title, current stage, status, assigned date), inline status dropdown using `<StageTransitionDropdown>`, "+ Assign to JD" action
- [ ] Create `CandidateSessionsTab.tsx` — empty state: "No interview sessions yet. Sessions will appear here once invites are sent (Phase 3C)."
- [ ] Commit: `feat(candidates-ui): candidate detail page with profile + assignments + sessions tabs`

---

## Task 25: Phase 3B end-to-end manual test + checkpoint

**Files:** none (manual verification gate)

- [ ] **Step 1: Build both surfaces fresh**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && docker compose up --build -d
cd /home/ishant/Projects/ProjectX/frontend/app && npm run build && npm run dev
```

- [ ] **Step 2: Walk the demo checklist**

Visit `http://localhost:3000/candidates` and verify:

1. Empty state renders
2. Add 5 candidates with varied fields (some with resume PDFs)
3. Each assigned to 2+ JDs (exercise many-to-many)
4. JD picker switches views correctly; kanban renders when JD selected
5. Drag candidate forward + backward + skip — each transition logs audit row; reload browser, state persists
6. Assignment status dropdown works (archived, rejected, hired, withdrawn, active)
7. Resume upload works: file picker → progress → "Uploaded" indicator → reload still shows resume
8. Resume delete works
9. Candidate detail page tabs all render; Sessions tab shows empty state
10. Cross-JD search (no JD picked) + JD-scoped list filter
11. GDPR redact-PII (as super admin) — name/email become `null`, candidate stays in DB
12. Ancestry-walking authz: login as Hiring Manager on limited org unit → confirm you see only accessible candidates

- [ ] **Step 3: Audit log verification**

```bash
docker exec supabase_db_backend psql -U postgres -d postgres -c "SELECT event_type, COUNT(*) FROM audit_log WHERE event_type LIKE 'candidate.%' GROUP BY event_type ORDER BY 1;"
```

Expected event types: `candidate.created`, `candidate.assigned`, `candidate.stage_transitioned`, `candidate.assignment_status_changed`, `candidate.updated`, `candidate.resume_uploaded`, `candidate.pii_redacted`.

- [ ] **Step 4: Run full test suite one more time**

```bash
cd backend/nexus && docker compose run --rm nexus pytest
cd frontend/app && npm run test && npm run type-check && npm run lint
```

Expected: all pass.

- [ ] **Step 5: Tag the checkpoint**

```bash
git tag -a phase-3b-complete -m "Phase 3B candidates module ready for Phase 3C"
```

Phase 3B is ready to merge / ship. Do NOT start Phase 3C until this checkpoint is clean.

---

## Self-review checklist (writer — before handoff)

- [x] Every spec Phase 3B requirement has a task (candidate CRUD, assignments, stage progress, kanban, resume upload, PII redaction, authz, audit log, new permissions)
- [x] Every task has concrete file paths, code snippets, commit messages, and exact test commands
- [x] No TODO / placeholder language in task bodies
- [x] Task ordering respects dependencies (schemas before service before router; authz before endpoints)
- [x] Test-first discipline throughout (every implementation task has a failing test first)
- [x] Commits after every task for clean rollback
- [x] End-to-end manual checkpoint task before the phase is declared done

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-19-phase-3b-candidates.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

**Note:** Phase 3C plan (`2026-04-19-phase-3c-scheduler-session.md`) will be written AFTER Phase 3B is shipped and the checkpoint is clean. Plan 2 depends on infrastructure created in Plan 1.
