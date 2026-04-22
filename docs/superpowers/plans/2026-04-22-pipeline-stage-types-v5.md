# Pipeline Stage Types v5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the stage-type taxonomy to 6 values, add per-stage participants (interviewers / observers / reviewers) gated by existing system roles, and make the pipeline builder UI reflect per-type behaviour.

**Architecture:** One Alembic migration carries the data + schema change. A new tenant-scoped `pipeline_stage_participants` table holds instance-level staffing (templates stay staffing-agnostic). A new router endpoint serves the role-gated picker pool. Frontend centralises per-type behaviour in `lib/pipelines/categories.ts` and introduces a `StageParticipantsEditor` component rendered only for job-pipeline instance edits.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / Alembic / asyncpg / pytest (backend). Next.js 16 / TypeScript strict / TanStack Query v5 / shadcn v4 Base UI / vitest + testing-library (frontend). Reference spec: `docs/superpowers/specs/2026-04-22-pipeline-stage-types-design.md`.

---

## File Map

### Backend

**Create:**
- `backend/nexus/migrations/versions/0016_stage_type_v5_and_participants.py` — schema + data migration
- `backend/nexus/migrations/rollback/0016_rollback.sql` — rollback script
- `backend/nexus/app/modules/pipelines/participants.py` — service helpers (replace_stage_participants, list_assignable_users, eligibility validator)
- `backend/nexus/tests/test_pipeline_participants.py` — participant round-trip, validation, cascade
- `backend/nexus/tests/test_pipeline_assignable_users.py` — pool endpoint
- `backend/nexus/tests/test_migration_0016.py` — migration data correctness

**Modify:**
- `backend/nexus/app/models.py` — new `PipelineStageParticipant` ORM model
- `backend/nexus/app/main.py` — add new table to `_TENANT_SCOPED_TABLES`
- `backend/nexus/app/modules/pipelines/schemas.py` — enum rewrite, participant schemas, per-type role validator
- `backend/nexus/app/modules/pipelines/service.py` — extend `get_job_pipeline_with_stages` + `update_job_pipeline_stages`
- `backend/nexus/app/modules/pipelines/router.py` — new picker endpoint + response helper pulls participants
- `backend/nexus/app/modules/pipelines/starter_pack.py` — rename legacy stage_type values
- `backend/nexus/app/modules/question_bank/actors.py` — update `STAGE_TYPE_TO_PROMPT`
- `backend/nexus/app/modules/question_bank/router.py` — filter intake/debrief from list_banks + get_bank
- `backend/nexus/CLAUDE.md` — pipelines module row refresh

**Rename:**
- `backend/nexus/prompts/v1/question_bank_ai_interview.txt` → `question_bank_ai_screening.txt`

**Delete:**
- `backend/nexus/prompts/v1/question_bank_panel_interview.txt`

### Frontend

**Create:**
- `frontend/app/lib/pipelines/categories.ts` — single source of truth for per-type UI behaviour
- `frontend/app/lib/pipelines/categories.test.ts` — exhaustive unit tests
- `frontend/app/lib/hooks/use-assignable-users.ts` — TanStack Query hook
- `frontend/app/components/dashboard/pipeline/StageParticipantsEditor.tsx` — picker component
- `frontend/app/components/dashboard/pipeline/StageParticipantsEditor.test.tsx` — component test

**Modify:**
- `frontend/app/lib/api/pipelines.ts` — `StageType` rewrite + participant types + new API method
- `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx` — new type picker + `jobId` prop + editor slot
- `frontend/app/components/dashboard/pipeline/StageConfigurationTab.tsx` — same pair of updates
- `frontend/app/components/dashboard/pipeline/JobPipelineFunnel.tsx` — label maps + stage-type options
- `frontend/app/components/dashboard/pipeline/StageFlowCard.tsx` — label/icon/accent maps + Unstaffed badge
- `frontend/app/components/dashboard/pipeline/StageSlab.tsx` — same maps + Unstaffed badge
- `frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx` — placeholder stage-type fix
- `frontend/app/components/dashboard/pipeline/StageActionsMenu.tsx` — clear participants on duplicate

---

## Task Sequence

Tasks are ordered so each one leaves the codebase compiling and the relevant test suite passing. Commits happen at the end of every task.

---

### Task 1: Alembic migration 0016 — schema + data migration + rollback script

**Files:**
- Create: `backend/nexus/migrations/versions/0016_stage_type_v5_and_participants.py`
- Create: `backend/nexus/migrations/rollback/0016_rollback.sql`

- [ ] **Step 1: Create the Alembic migration file**

Write `backend/nexus/migrations/versions/0016_stage_type_v5_and_participants.py`:

```python
"""stage_type v5 + pipeline_stage_participants

1. Create `pipeline_stage_participants` (instance-level staffing only) with
   canonical RLS pair + grants to nexus_app.
2. Drop old `ck_*_stages_stage_type` CHECK constraints (migration 0015
   enforced a 9-value allowlist).
3. Rename legacy rows: recruiter/panel_interview -> human_interview;
   ai_interview -> ai_screening. Deletes offer rows.
4. Re-sequence position per instance / per template (0..N-1) to avoid
   breaking UpdateJobPipelineRequest.check_positions_sequential on the
   next auto-save PATCH after offer deletion.
5. Re-create CHECK constraints with the 6-value allowlist.

Downgrade is lossy: deleted offer rows are NOT restored; rename-reverted
rows keep their new UUID identities.

Revision ID: 0016_stage_type_v5_and_participants
Revises: 0015_pipeline_stage_v4
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_stage_type_v5_and_participants"
down_revision = "0015_pipeline_stage_v4"
branch_labels = None
depends_on = None


STAGE_TYPES_V5 = (
    "intake",
    "phone_screen",
    "ai_screening",
    "human_interview",
    "debrief",
    "take_home",
)

STAGE_TYPES_V4 = (
    "phone_screen",
    "ai_interview",
    "human_interview",
    "panel_interview",
    "take_home",
    "intake",
    "recruiter",
    "debrief",
    "offer",
)


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # 1. Create participants table.
    op.execute(
        """
        CREATE TABLE pipeline_stage_participants (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            stage_id    uuid NOT NULL REFERENCES job_pipeline_stages(id) ON DELETE CASCADE,
            user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role        text NOT NULL
                          CHECK (role IN ('interviewer', 'observer', 'reviewer')),
            assigned_by uuid REFERENCES users(id) ON DELETE SET NULL,
            assigned_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (stage_id, user_id, role)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_stage_participants_stage ON pipeline_stage_participants (stage_id)"
    )
    op.execute(
        "CREATE INDEX ix_stage_participants_user ON pipeline_stage_participants (user_id)"
    )
    op.execute(
        "CREATE INDEX ix_stage_participants_tenant ON pipeline_stage_participants (tenant_id)"
    )

    # 2. RLS + policies + grants.
    op.execute("ALTER TABLE pipeline_stage_participants ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY "tenant_isolation" ON pipeline_stage_participants
          USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY "service_bypass" ON pipeline_stage_participants
          USING (current_setting('app.bypass_rls', true) = 'true')
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON pipeline_stage_participants TO nexus_app"
    )

    # 3. Drop old CHECK constraints BEFORE the UPDATE — otherwise renaming
    #    to ai_screening (not in the old allowlist) violates the constraint.
    op.drop_constraint(
        "ck_template_stages_stage_type", "pipeline_template_stages", type_="check"
    )
    op.drop_constraint(
        "ck_job_pipeline_stages_stage_type", "job_pipeline_stages", type_="check"
    )

    # 4. Rename legacy rows (both tables).
    for table in ("pipeline_template_stages", "job_pipeline_stages"):
        op.execute(
            f"""
            UPDATE {table} SET stage_type = CASE stage_type
                WHEN 'recruiter' THEN 'human_interview'
                WHEN 'panel_interview' THEN 'human_interview'
                WHEN 'ai_interview' THEN 'ai_screening'
                ELSE stage_type
            END
            """
        )

    # 5. Delete offer rows outright.
    op.execute("DELETE FROM pipeline_template_stages WHERE stage_type = 'offer'")
    op.execute("DELETE FROM job_pipeline_stages WHERE stage_type = 'offer'")

    # 6. Re-sequence positions per pipeline / per template. Required because
    #    UpdateJobPipelineRequest.check_positions_sequential rejects gaps on
    #    the next PATCH.
    op.execute(
        """
        WITH renumbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY instance_id ORDER BY position) - 1 AS new_pos
            FROM job_pipeline_stages
        )
        UPDATE job_pipeline_stages s
           SET position = r.new_pos
          FROM renumbered r
         WHERE s.id = r.id AND s.position <> r.new_pos
        """
    )
    op.execute(
        """
        WITH renumbered AS (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY template_id ORDER BY position) - 1 AS new_pos
            FROM pipeline_template_stages
        )
        UPDATE pipeline_template_stages s
           SET position = r.new_pos
          FROM renumbered r
         WHERE s.id = r.id AND s.position <> r.new_pos
        """
    )

    # 7. Re-create CHECK with the 6-value allowlist.
    op.create_check_constraint(
        "ck_template_stages_stage_type",
        "pipeline_template_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES_V5)}",
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_stage_type",
        "job_pipeline_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES_V5)}",
    )


def downgrade() -> None:
    # Restore v4 CHECK. Fails if any 'ai_screening' rows exist — by design,
    # since the rename is lossy. Operator must manually rename first.
    op.drop_constraint(
        "ck_template_stages_stage_type", "pipeline_template_stages", type_="check"
    )
    op.drop_constraint(
        "ck_job_pipeline_stages_stage_type", "job_pipeline_stages", type_="check"
    )
    op.create_check_constraint(
        "ck_template_stages_stage_type",
        "pipeline_template_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES_V4)}",
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_stage_type",
        "job_pipeline_stages",
        f"stage_type IN {_sql_in(STAGE_TYPES_V4)}",
    )
    op.execute("DROP TABLE IF EXISTS pipeline_stage_participants")
```

- [ ] **Step 2: Create the rollback script**

Write `backend/nexus/migrations/rollback/0016_rollback.sql` (human-readable PSQL form for operator-executable rollback, in case `alembic downgrade` can't run):

```sql
-- Rollback for 0016_stage_type_v5_and_participants.
-- LOSSY: deleted offer rows are not restored; ai_screening -> ai_interview
-- only works if no rows carry the new value.

BEGIN;

-- 1. If any rows carry ai_screening, rename them back before the CHECK swap.
UPDATE pipeline_template_stages SET stage_type = 'ai_interview'
  WHERE stage_type = 'ai_screening';
UPDATE job_pipeline_stages SET stage_type = 'ai_interview'
  WHERE stage_type = 'ai_screening';

-- 2. Swap CHECK constraints back to v4 allowlist.
ALTER TABLE pipeline_template_stages
  DROP CONSTRAINT ck_template_stages_stage_type;
ALTER TABLE pipeline_template_stages
  ADD CONSTRAINT ck_template_stages_stage_type CHECK (stage_type IN (
    'phone_screen','ai_interview','human_interview','panel_interview',
    'take_home','intake','recruiter','debrief','offer'
  ));

ALTER TABLE job_pipeline_stages
  DROP CONSTRAINT ck_job_pipeline_stages_stage_type;
ALTER TABLE job_pipeline_stages
  ADD CONSTRAINT ck_job_pipeline_stages_stage_type CHECK (stage_type IN (
    'phone_screen','ai_interview','human_interview','panel_interview',
    'take_home','intake','recruiter','debrief','offer'
  ));

-- 3. Drop the participants table.
DROP TABLE IF EXISTS pipeline_stage_participants;

COMMIT;
```

- [ ] **Step 3: Run the migration against a dev DB**

Run:
```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus alembic upgrade head
```

Expected: output ends with `INFO  [alembic.runtime.migration] Running upgrade 0015_pipeline_stage_v4 -> 0016_stage_type_v5_and_participants`.

- [ ] **Step 4: Verify the new table exists with policies**

Run (using the supabase local container):
```bash
docker compose -f backend/supabase/docker-compose.yml exec db psql -U postgres -c "\d pipeline_stage_participants"
docker compose -f backend/supabase/docker-compose.yml exec db psql -U postgres -c "SELECT policyname FROM pg_policies WHERE tablename='pipeline_stage_participants'"
```

Expected: table definition printed; policies listed are `tenant_isolation` and `service_bypass`.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/migrations/versions/0016_stage_type_v5_and_participants.py \
        backend/nexus/migrations/rollback/0016_rollback.sql
git commit -m "feat(pipelines): alembic 0016 — stage_type v5 + participants table"
```

---

### Task 2: Add `PipelineStageParticipant` ORM model + RLS enumeration entry

**Files:**
- Modify: `backend/nexus/app/models.py` (append new model at end of file, near other pipeline models)
- Modify: `backend/nexus/app/main.py:30-41` (`_TENANT_SCOPED_TABLES` tuple)

- [ ] **Step 1: Add the ORM model**

Append to `backend/nexus/app/models.py` (after the existing `JobPipelineStage` class):

```python
class PipelineStageParticipant(Base):
    """Instance-level staffing for a pipeline stage.

    Only attached to job_pipeline_stages (instance rows) — templates are
    staffing-agnostic. Cascades on stage delete and user delete.
    """

    __tablename__ = "pipeline_stage_participants"
    __table_args__ = (
        UniqueConstraint("stage_id", "user_id", "role", name="uq_stage_user_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_pipeline_stages.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # CHECK enforced at DB
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 2: Add the new table to the RLS completeness assertion list**

In `backend/nexus/app/main.py`, find the `_TENANT_SCOPED_TABLES` tuple (starts around line 30) and add `"pipeline_stage_participants"` to the tuple, preserving alphabetical-ish grouping next to the other pipeline rows.

Example — the existing block looks like (actual names may vary slightly):

```python
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    ...,
    "pipeline_template_stages",
    "pipeline_templates",
    "job_pipeline_stages",
    "job_pipeline_instances",
    ...,
)
```

After edit:

```python
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    ...,
    "pipeline_template_stages",
    "pipeline_templates",
    "job_pipeline_stages",
    "job_pipeline_instances",
    "pipeline_stage_participants",
    ...,
)
```

- [ ] **Step 3: Boot the app and verify startup assertion passes**

Run:
```bash
docker compose -f backend/nexus/docker-compose.yml up --build nexus
```

Expected: log line `rls_completeness_check_passed` with `tables_verified=<N+1>` (one more than before). No `rls_completeness_check_failed` entries. Kill the process after confirming.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/models.py backend/nexus/app/main.py
git commit -m "feat(pipelines): ORM model for stage participants + RLS enumerate"
```

---

### Task 3: Rewrite `StageType` literal + participant schemas + per-type role validator

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/schemas.py`

- [ ] **Step 1: Rewrite the `StageType` literal and add participant schemas**

Replace the top of `backend/nexus/app/modules/pipelines/schemas.py` (lines ~12–27) to the new taxonomy. Add `StageParticipantInput` / `StageParticipantResponse` schemas and `ParticipantRole` literal.

Replace this block:

```python
StageType = Literal[
    # Phase 2C.1 set — interview-oriented
    "phone_screen",
    "ai_interview",
    "human_interview",
    "panel_interview",
    "take_home",
    # Phase 4 additions — bookend + role-specific stages from the v4 design.
    # The DB CHECK in migration 0015_pipeline_stage_v4 covers these.
    "intake",
    "recruiter",
    "debrief",
    "offer",
]
```

With:

```python
# Stage type v5 — see migration 0016 and
# docs/superpowers/specs/2026-04-22-pipeline-stage-types-design.md.
StageType = Literal[
    "intake",
    "phone_screen",
    "ai_screening",
    "human_interview",
    "debrief",
    "take_home",
]

ParticipantRole = Literal["interviewer", "observer", "reviewer"]


class StageParticipantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    role: ParticipantRole


class StageParticipantResponse(StageParticipantInput):
    """Adds display fields so the UI doesn't round-trip separately."""
    full_name: str
    email: str
```

- [ ] **Step 2: Extend stage input/response schemas with participants**

In the same file, extend `PipelineStageInput`, `PipelineStageUpdateInput`, `PipelineStageResponse`:

```python
class PipelineStageInput(PipelineStageBase):
    """Stage as sent by the frontend when creating/updating."""

    model_config = ConfigDict(extra="forbid")
    participants: list[StageParticipantInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_participants_for_stage_type(self) -> "PipelineStageInput":
        _validate_participants_role_for_type(self.stage_type, self.participants)
        return self


class PipelineStageUpdateInput(PipelineStageInput):
    """Stage input used on UPDATE — id-carrying, participants=None means untouched."""

    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    # override: allow None to mean "don't touch participants for this stage".
    participants: list[StageParticipantInput] | None = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _check_participants_for_stage_type(self) -> "PipelineStageUpdateInput":
        if self.participants is not None:
            _validate_participants_role_for_type(self.stage_type, self.participants)
        return self


class PipelineStageResponse(PipelineStageBase):
    """Stage as returned by the API."""

    id: UUID
    participants: list[StageParticipantResponse] = Field(default_factory=list)
```

- [ ] **Step 3: Add the per-type validator helper**

Add at module scope, above the stage schemas:

```python
_PARTICIPANT_ROLE_FOR_TYPE: dict[str, ParticipantRole | None] = {
    "intake":          None,
    "take_home":       None,
    "phone_screen":    "interviewer",
    "human_interview": "interviewer",
    "ai_screening":    "observer",
    "debrief":         "reviewer",
}


def _validate_participants_role_for_type(
    stage_type: StageType, participants: list[StageParticipantInput]
) -> None:
    allowed = _PARTICIPANT_ROLE_FOR_TYPE.get(stage_type)
    if allowed is None:
        if participants:
            raise ValueError(
                f"stage_type={stage_type!r} cannot carry participants"
            )
        return
    for p in participants:
        if p.role != allowed:
            raise ValueError(
                f"stage_type={stage_type!r} only accepts role={allowed!r}, "
                f"got {p.role!r} for user_id={p.user_id}"
            )
```

- [ ] **Step 4: Run schemas tests**

Run:
```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_pipelines_router.py tests/test_pipelines_service.py -x
```

Expected: tests still pass (nothing yet uses the new fields; the validator allows `participants=[]` default). If any pre-existing test instantiates a stage with a removed legacy type (`recruiter`, `panel_interview`, `ai_interview`, `offer`), fix it to the new value in the same commit.

- [ ] **Step 5: Commit**

```bash
git add backend/nexus/app/modules/pipelines/schemas.py
# plus any legacy-value fixes in existing tests
git commit -m "feat(pipelines): stage_type v5 literal + participant schemas + validator"
```

---

### Task 4: New `participants.py` module — service helpers

**Files:**
- Create: `backend/nexus/app/modules/pipelines/participants.py`

- [ ] **Step 1: Write the module**

Write `backend/nexus/app/modules/pipelines/participants.py`:

```python
"""Pipeline stage participants service helpers.

Three public helpers:

- replace_stage_participants(db, stage, participants, assigned_by)
    Diff-and-sync within a single stage. Preserves row identity for
    (stage_id, user_id, role) tuples that survive the edit; inserts
    missing; deletes removed.

- list_assignable_users(db, job, role)
    Returns the eligible-user pool for a picker slot. Filters by system
    role name gate (see spec §3) and the job's org unit ancestry.

- validate_participants_eligible(db, job, participants)
    Re-runs the pool query for every user_id supplied. Raises 422 if any
    user is outside the pool. Called by create-from-scratch and PATCH paths.
"""

from typing import Literal
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineStage,
    JobPosting,
    OrganizationalUnit,
    PipelineStageParticipant,
    Role,
    User,
    UserRoleAssignment,
)
from app.modules.org_units.service import get_org_unit_ancestry
from app.modules.pipelines.schemas import (
    ParticipantRole,
    StageParticipantInput,
)

logger = structlog.get_logger()


_ROLE_GATE: dict[str, tuple[str, ...]] = {
    "interviewer": ("Interviewer", "Hiring Manager"),
    "observer":    ("Observer", "Interviewer", "Hiring Manager", "Recruiter"),
    "reviewer":    ("Hiring Manager",),
}


async def _ancestor_unit_ids(
    db: AsyncSession, org_unit_id: UUID
) -> list[UUID]:
    ancestry = await get_org_unit_ancestry(db, org_unit_id)
    return [u.id for u in ancestry]


async def list_assignable_users(
    db: AsyncSession,
    *,
    job: JobPosting,
    role: Literal["interviewer", "observer", "reviewer"],
) -> list[dict]:
    gate_names = _ROLE_GATE[role]
    ancestor_ids = await _ancestor_unit_ids(db, job.org_unit_id)
    if not ancestor_ids:
        return []

    result = await db.execute(
        select(User, OrganizationalUnit, Role)
        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
        .join(OrganizationalUnit, OrganizationalUnit.id == UserRoleAssignment.org_unit_id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(
            and_(
                User.is_active == True,  # noqa: E712 — SQLAlchemy expects this form
                UserRoleAssignment.org_unit_id.in_(ancestor_ids),
                Role.name.in_(gate_names),
            )
        )
    )

    out: dict[UUID, dict] = {}
    for user, unit, role_row in result.all():
        entry = out.setdefault(
            user.id,
            {
                "user_id": user.id,
                "full_name": user.full_name or "",
                "email": user.email,
                "role_labels": set(),
                "org_unit_name": unit.name,
            },
        )
        entry["role_labels"].add(role_row.name)

    return [
        {**e, "role_labels": sorted(e["role_labels"])} for e in out.values()
    ]


async def validate_participants_eligible(
    db: AsyncSession,
    *,
    job: JobPosting,
    participants: list[StageParticipantInput],
) -> None:
    """Raise 422 if any participant is not in the eligibility pool for their slot."""
    if not participants:
        return

    # Group by slot to avoid N queries.
    by_role: dict[ParticipantRole, set[UUID]] = {}
    for p in participants:
        by_role.setdefault(p.role, set()).add(p.user_id)

    for role, user_ids in by_role.items():
        pool = await list_assignable_users(db, job=job, role=role)
        allowed_ids = {u["user_id"] for u in pool}
        missing = user_ids - allowed_ids
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"User(s) not eligible for role={role!r} on this job's "
                    f"org unit: {sorted(str(m) for m in missing)}"
                ),
            )


async def replace_stage_participants(
    db: AsyncSession,
    *,
    stage: JobPipelineStage,
    participants: list[StageParticipantInput],
    assigned_by: UUID,
) -> None:
    """Diff-and-sync participants for a single stage.

    Preserves rows whose (user_id, role) still appears in `participants`.
    Deletes rows whose tuple is absent. Inserts rows for new tuples.
    """
    existing_result = await db.execute(
        select(PipelineStageParticipant).where(
            PipelineStageParticipant.stage_id == stage.id
        )
    )
    existing = list(existing_result.scalars().all())

    incoming_keys = {(p.user_id, p.role) for p in participants}
    existing_keys = {(row.user_id, row.role) for row in existing}

    # Delete rows not in incoming.
    to_delete_ids = [r.id for r in existing if (r.user_id, r.role) not in incoming_keys]
    if to_delete_ids:
        await db.execute(
            delete(PipelineStageParticipant).where(
                PipelineStageParticipant.id.in_(to_delete_ids)
            )
        )

    # Insert rows that aren't already present.
    for p in participants:
        if (p.user_id, p.role) in existing_keys:
            continue
        db.add(
            PipelineStageParticipant(
                tenant_id=stage.tenant_id,
                stage_id=stage.id,
                user_id=p.user_id,
                role=p.role,
                assigned_by=assigned_by,
            )
        )

    await db.flush()
    logger.info(
        "pipelines.stage_participants_synced",
        stage_id=str(stage.id),
        kept=len(existing_keys & incoming_keys),
        inserted=len(incoming_keys - existing_keys),
        deleted=len(existing_keys - incoming_keys),
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/app/modules/pipelines/participants.py
git commit -m "feat(pipelines): service helpers for stage participants"
```

---

### Task 5: Extend `service.py` — load + save participants alongside stages

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/service.py` (extend `get_job_pipeline_with_stages` and `update_job_pipeline_stages`)

- [ ] **Step 1: Extend `get_job_pipeline_with_stages` to bulk-load participants**

In `backend/nexus/app/modules/pipelines/service.py`, modify the existing `get_job_pipeline_with_stages` function (around line 309) to also fetch participants and return them keyed by stage_id.

Replace the function body with:

```python
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
        from app.models import PipelineStageParticipant, User

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
```

- [ ] **Step 2: Extend `update_job_pipeline_stages` to sync participants per stage**

In the same file, modify `update_job_pipeline_stages` (around line 548). After the existing update/insert loop, add a participants sync pass.

Near the top of the file, add imports:

```python
from app.modules.pipelines.participants import (
    replace_stage_participants,
    validate_participants_eligible,
)
```

Then in the function, after the insert loop (before the final `instance.updated_at = ...`), add:

```python
    # Sync participants per stage. participants=None means "don't touch".
    # Collect all incoming participants to run eligibility in a single batch.
    all_eligible_checks: list[PipelineStageUpdateInput] = [
        s for s in stages
        if isinstance(s, PipelineStageUpdateInput) and s.participants is not None
    ]
    # Eligibility uses the job, not the stage — fetch once.
    if all_eligible_checks:
        from app.models import JobPosting

        job_result = await db.execute(
            select(JobPosting).where(JobPosting.id == instance.job_posting_id)
        )
        job = job_result.scalar_one()
        flat_participants: list[StageParticipantInput] = []
        for s in all_eligible_checks:
            flat_participants.extend(s.participants or [])
        await validate_participants_eligible(
            db, job=job, participants=flat_participants
        )

    # Reload stage rows by id so we can diff participants against the latest state.
    if all_eligible_checks:
        stage_id_list = [s.id for s in all_eligible_checks if s.id is not None]
        stages_reload = await db.execute(
            select(JobPipelineStage).where(JobPipelineStage.id.in_(stage_id_list))
        )
        stage_by_id = {row.id: row for row in stages_reload.scalars().all()}
        for incoming in all_eligible_checks:
            if incoming.id is None:
                continue  # brand-new stages don't carry participants on create
            row = stage_by_id.get(incoming.id)
            if row is None:
                continue  # defensive — should not happen
            await replace_stage_participants(
                db,
                stage=row,
                participants=incoming.participants or [],
                assigned_by=actor_id,
            )
```

Update the function signature to accept `actor_id: UUID`:

```python
async def update_job_pipeline_stages(
    db: AsyncSession,
    *,
    instance: JobPipelineInstance,
    stages: list[PipelineStageUpdateInput | PipelineStageInput],
    actor_id: UUID,
) -> JobPipelineInstance:
```

- [ ] **Step 3: Fix up all callers of `get_job_pipeline_with_stages` and `update_job_pipeline_stages`**

The return arity of `get_job_pipeline_with_stages` changed from 3-tuple to 4-tuple. Search:
```bash
grep -rn "get_job_pipeline_with_stages" backend/nexus/app/ backend/nexus/tests/
```

Update every caller to unpack 4 values (the router is the primary caller — Task 6 covers it).

`update_job_pipeline_stages` now requires `actor_id`. Search:
```bash
grep -rn "update_job_pipeline_stages" backend/nexus/app/ backend/nexus/tests/
```

Update callers to pass `actor_id=user.user.id`.

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/pipelines/service.py
git commit -m "feat(pipelines): load + diff-sync participants in service layer"
```

---

### Task 6: Router updates — response helper + new assignable-users endpoint

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/router.py`

- [ ] **Step 1: Update the response helper to emit participants**

In `backend/nexus/app/modules/pipelines/router.py`, modify `_stage_row_to_response` (around line 88) to accept optional participants:

```python
def _stage_row_to_response(
    row: PipelineTemplateStage | JobPipelineStage,
    participants: list[dict] | None = None,
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
        participants=[
            StageParticipantResponse(**p) for p in (participants or [])
        ],
    )
```

Add the import at the top of the file:
```python
from app.modules.pipelines.schemas import StageParticipantResponse
```

- [ ] **Step 2: Update `_instance_to_response` to pass participants through**

Modify `_instance_to_response` (around line 121):

```python
def _instance_to_response(
    instance: JobPipelineInstance,
    stages: list[JobPipelineStage],
    source_template: PipelineTemplate | None,
    participants_by_stage: dict[UUID, list[dict]] | None = None,
) -> JobPipelineInstanceResponse:
    participants_by_stage = participants_by_stage or {}
    return JobPipelineInstanceResponse(
        id=instance.id,
        job_posting_id=instance.job_posting_id,
        source_template_id=instance.source_template_id,
        source_template_name=source_template.name if source_template else None,
        stages=[
            _stage_row_to_response(s, participants_by_stage.get(s.id, []))
            for s in stages
        ],
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )
```

- [ ] **Step 3: Fix every call site to `get_job_pipeline_with_stages` in the router**

Every instance-endpoint unpacks `result` as a 4-tuple now. Example — the `get_job_pipeline` endpoint (around line 317) becomes:

```python
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
    instance, stages, source_template, participants_by_stage = result
    return _instance_to_response(instance, stages, source_template, participants_by_stage)
```

Apply the same 4-tuple unpack to `create_job_pipeline`, `update_job_pipeline`, `swap_job_pipeline_endpoint`, `reset_job_pipeline`.

The `update_job_pipeline` endpoint also now passes `actor_id`:

```python
    await update_job_pipeline_stages(
        db, instance=instance, stages=body.stages, actor_id=user.user.id
    )
```

- [ ] **Step 4: Add the assignable-users endpoint**

At the end of `router.py`, append:

```python
@router.get("/api/jobs/{job_id}/pipeline/assignable-users")
async def get_assignable_users(
    job_id: UUID,
    role: Literal["interviewer", "observer", "reviewer"],
    db: AsyncSession = Depends(get_tenant_db),
    user: UserContext = Depends(get_current_user_roles),
) -> list[dict]:
    job, _instance = await require_instance_access(db, job_id, user, "view")
    from app.modules.pipelines.participants import list_assignable_users
    return await list_assignable_users(db, job=job, role=role)
```

Add the `Literal` import to the top of the file:
```python
from typing import Literal
```

- [ ] **Step 5: Run the pipeline-router tests**

Run:
```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_pipelines_router.py -x
```

Expected: existing tests pass (response now carries `participants: []` on every stage by default; that's additive).

- [ ] **Step 6: Commit**

```bash
git add backend/nexus/app/modules/pipelines/router.py
git commit -m "feat(pipelines): router emits participants + assignable-users endpoint"
```

---

### Task 7: Starter pack stage-type rename

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/starter_pack.py`

- [ ] **Step 1: Rename legacy stage types in starters**

In `backend/nexus/app/modules/pipelines/starter_pack.py`:

- `standard_technical`: change `"stage_type": "ai_interview"` → `"ai_screening"`; change `"stage_type": "panel_interview"` → `"human_interview"`.
- `fast_track`: change `"stage_type": "ai_interview"` → `"ai_screening"`.
- `senior_leadership`: change `"stage_type": "ai_interview"` → `"ai_screening"`; change `"stage_type": "panel_interview"` → `"human_interview"`.

`screening_only`, `sales_commercial`, `volume_hiring` are already on allowed types (phone_screen / human_interview) — no change.

- [ ] **Step 2: Run starter pack tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_pipelines_starter_pack.py -x
```

Expected: pass (any test asserting the old strings needs fixing in the same commit).

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/app/modules/pipelines/starter_pack.py backend/nexus/tests/test_pipelines_starter_pack.py
git commit -m "feat(pipelines): rename legacy stage types in starter pack"
```

---

### Task 8: Question-bank prompt files + `STAGE_TYPE_TO_PROMPT` map

**Files:**
- Rename: `backend/nexus/prompts/v1/question_bank_ai_interview.txt` → `question_bank_ai_screening.txt`
- Delete: `backend/nexus/prompts/v1/question_bank_panel_interview.txt`
- Modify: `backend/nexus/app/modules/question_bank/actors.py:60-66`

- [ ] **Step 1: Rename and delete prompt files**

```bash
git mv backend/nexus/prompts/v1/question_bank_ai_interview.txt \
       backend/nexus/prompts/v1/question_bank_ai_screening.txt
git rm backend/nexus/prompts/v1/question_bank_panel_interview.txt
```

- [ ] **Step 2: Update `STAGE_TYPE_TO_PROMPT`**

In `backend/nexus/app/modules/question_bank/actors.py`, replace the map (around line 60):

```python
STAGE_TYPE_TO_PROMPT = {
    "phone_screen":    "question_bank_phone_screen",
    "ai_screening":    "question_bank_ai_screening",
    "human_interview": "question_bank_human_interview",
    "take_home":       "question_bank_take_home",
}
```

- [ ] **Step 3: Run question bank tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_question_banks_actors.py -x
```

Expected: tests pass (any test referencing `"ai_interview"` or `"panel_interview"` as stage_type needs updating in the same commit).

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/prompts/v1/ backend/nexus/app/modules/question_bank/actors.py \
        backend/nexus/tests/test_question_banks_actors.py
git commit -m "feat(question_bank): rename ai_screening prompt, drop panel_interview prompt"
```

---

### Task 9: Question-bank router — skip intake/debrief stages in `list_banks` + `get_bank`

**Files:**
- Modify: `backend/nexus/app/modules/question_bank/router.py` (`list_banks` around lines 270-306, `get_bank` around lines 309-339)

- [ ] **Step 1: Filter intake/debrief in `list_banks`**

Modify the `list_banks` loop (around line 291) to skip stages whose type is not in `STAGE_TYPE_TO_PROMPT`. Replace the loop body:

```python
    from app.modules.question_bank.actors import STAGE_TYPE_TO_PROMPT  # or top-level import
    banks: list[BankResponse | PlaceholderBankResponse] = []
    for stage in stages:
        if stage.stage_type not in STAGE_TYPE_TO_PROMPT:
            # intake / debrief / anything else that doesn't generate questions
            continue
        row = banks_by_stage.get(stage.id)
        if row is None:
            banks.append(PlaceholderBankResponse(stage_id=stage.id))
            continue
        bank, question_count, total_minutes, is_stale = row
        banks.append(
            _bank_to_response(
                bank,
                question_count=question_count,
                total_minutes=total_minutes,
                is_stale=is_stale,
            )
        )
    return BanksOverviewResponse(banks=banks)
```

- [ ] **Step 2: Reject non-generating stage types in `get_bank`**

Modify the `get_bank` endpoint (around line 313) to return 409 when the stage doesn't generate questions. Insert this check before the `ensure_bank_exists` call:

```python
    from app.modules.question_bank.actors import STAGE_TYPE_TO_PROMPT
    if stage.stage_type not in STAGE_TYPE_TO_PROMPT:
        raise HTTPException(
            status_code=409,
            detail="Stage type does not support question banks",
        )
```

- [ ] **Step 3: Run question-bank router tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_question_banks_router.py -x
```

Expected: pass. Add a targeted test in the same commit if none covers this case:

```python
async def test_list_banks_excludes_intake_and_debrief(
    client, factory_job_with_pipeline, auth_headers
):
    job = await factory_job_with_pipeline(stage_types=["intake", "phone_screen", "debrief"])
    response = await client.get(f"/api/jobs/{job.id}/pipeline/banks", headers=auth_headers)
    assert response.status_code == 200
    bank_stage_ids = {b["stage_id"] for b in response.json()["banks"]}
    # intake + debrief stages must NOT appear as entries
    assert len(bank_stage_ids) == 1
```

- [ ] **Step 4: Commit**

```bash
git add backend/nexus/app/modules/question_bank/router.py \
        backend/nexus/tests/test_question_banks_router.py
git commit -m "feat(question_bank): skip intake/debrief in list_banks + reject in get_bank"
```

---

### Task 10: Backend test — participants round-trip + validation

**Files:**
- Create: `backend/nexus/tests/test_pipeline_participants.py`

- [ ] **Step 1: Write the test file**

Write `backend/nexus/tests/test_pipeline_participants.py` (replace `TODO` markers with references to existing conftest fixtures — the existing `tests/conftest.py` provides `client`, bypass session, etc.):

```python
"""Participant round-trip, validation, cascade tests."""

import pytest
from uuid import UUID

# Assumes fixtures from conftest.py: client, factory_tenant, factory_user,
# factory_job_with_confirmed_signals, factory_pipeline_instance, auth_headers.


@pytest.mark.asyncio
async def test_patch_with_participants_none_leaves_existing_untouched(
    client, factory_pipeline_instance, factory_user, auth_headers
):
    instance = await factory_pipeline_instance(stage_types=["human_interview"])
    interviewer = await factory_user(role="Interviewer", org_unit_id=instance.org_unit_id)

    # First PATCH — set one participant.
    stage = instance.stages[0]
    r = await client.patch(
        f"/api/jobs/{instance.job_id}/pipeline",
        headers=auth_headers,
        json={
            "stages": [
                {
                    "id": str(stage.id),
                    "position": 0,
                    "name": stage.name,
                    "stage_type": "human_interview",
                    "duration_minutes": stage.duration_minutes,
                    "difficulty": stage.difficulty,
                    "signal_filter": stage.signal_filter,
                    "pass_criteria": stage.pass_criteria,
                    "advance_behavior": stage.advance_behavior,
                    "participants": [
                        {"user_id": str(interviewer.id), "role": "interviewer"}
                    ],
                }
            ]
        },
    )
    assert r.status_code == 200
    assert len(r.json()["stages"][0]["participants"]) == 1

    # Second PATCH — participants omitted (None). Change only name.
    r = await client.patch(
        f"/api/jobs/{instance.job_id}/pipeline",
        headers=auth_headers,
        json={
            "stages": [
                {
                    "id": str(stage.id),
                    "position": 0,
                    "name": "Renamed",
                    "stage_type": "human_interview",
                    "duration_minutes": stage.duration_minutes,
                    "difficulty": stage.difficulty,
                    "signal_filter": stage.signal_filter,
                    "pass_criteria": stage.pass_criteria,
                    "advance_behavior": stage.advance_behavior,
                    # participants omitted — treated as None.
                }
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["stages"][0]["name"] == "Renamed"
    assert len(r.json()["stages"][0]["participants"]) == 1, \
        "participants=None must not touch existing staffing"


@pytest.mark.asyncio
async def test_reviewer_role_rejected_on_human_interview_stage(
    client, factory_pipeline_instance, factory_user, auth_headers
):
    instance = await factory_pipeline_instance(stage_types=["human_interview"])
    hm = await factory_user(role="Hiring Manager", org_unit_id=instance.org_unit_id)
    stage = instance.stages[0]

    r = await client.patch(
        f"/api/jobs/{instance.job_id}/pipeline",
        headers=auth_headers,
        json={
            "stages": [
                {
                    "id": str(stage.id),
                    "position": 0,
                    "name": stage.name,
                    "stage_type": "human_interview",
                    "duration_minutes": stage.duration_minutes,
                    "difficulty": stage.difficulty,
                    "signal_filter": stage.signal_filter,
                    "pass_criteria": stage.pass_criteria,
                    "advance_behavior": stage.advance_behavior,
                    "participants": [
                        {"user_id": str(hm.id), "role": "reviewer"},
                    ],
                }
            ]
        },
    )
    assert r.status_code == 422
    assert "only accepts role='interviewer'" in r.text


@pytest.mark.asyncio
async def test_user_outside_org_unit_ancestry_rejected(
    client, factory_pipeline_instance, factory_user, factory_org_unit, auth_headers
):
    instance = await factory_pipeline_instance(stage_types=["human_interview"])
    sibling_unit = await factory_org_unit(under_company=True)
    outsider = await factory_user(role="Interviewer", org_unit_id=sibling_unit.id)

    stage = instance.stages[0]
    r = await client.patch(
        f"/api/jobs/{instance.job_id}/pipeline",
        headers=auth_headers,
        json={
            "stages": [
                {
                    "id": str(stage.id),
                    "position": 0,
                    "name": stage.name,
                    "stage_type": "human_interview",
                    "duration_minutes": stage.duration_minutes,
                    "difficulty": stage.difficulty,
                    "signal_filter": stage.signal_filter,
                    "pass_criteria": stage.pass_criteria,
                    "advance_behavior": stage.advance_behavior,
                    "participants": [
                        {"user_id": str(outsider.id), "role": "interviewer"},
                    ],
                }
            ]
        },
    )
    assert r.status_code == 422
    assert "not eligible" in r.text


@pytest.mark.asyncio
async def test_cascade_on_stage_delete(
    client, factory_pipeline_instance, factory_user, auth_headers, bypass_db
):
    instance = await factory_pipeline_instance(stage_types=["human_interview", "human_interview"])
    interviewer = await factory_user(role="Interviewer", org_unit_id=instance.org_unit_id)
    stage0, stage1 = instance.stages

    # Assign participants to both stages.
    # (body omitted — same pattern as above; assign one interviewer each.)

    # PATCH removes stage1.
    r = await client.patch(
        f"/api/jobs/{instance.job_id}/pipeline",
        headers=auth_headers,
        json={"stages": [stage0_patch_body]},
    )
    assert r.status_code == 200

    from sqlalchemy import select
    from app.models import PipelineStageParticipant
    remaining = (
        await bypass_db.execute(
            select(PipelineStageParticipant).where(
                PipelineStageParticipant.stage_id == stage1.id
            )
        )
    ).scalars().all()
    assert remaining == [], "ON DELETE CASCADE must wipe participants for removed stage"


@pytest.mark.asyncio
async def test_intake_stage_rejects_participants(
    client, factory_pipeline_instance, factory_user, auth_headers
):
    instance = await factory_pipeline_instance(stage_types=["intake"])
    any_user = await factory_user(role="Recruiter", org_unit_id=instance.org_unit_id)
    stage = instance.stages[0]

    r = await client.patch(
        f"/api/jobs/{instance.job_id}/pipeline",
        headers=auth_headers,
        json={
            "stages": [
                {
                    "id": str(stage.id),
                    "position": 0,
                    "name": stage.name,
                    "stage_type": "intake",
                    "duration_minutes": stage.duration_minutes,
                    "difficulty": stage.difficulty,
                    "signal_filter": stage.signal_filter,
                    "pass_criteria": stage.pass_criteria,
                    "advance_behavior": stage.advance_behavior,
                    "participants": [
                        {"user_id": str(any_user.id), "role": "interviewer"},
                    ],
                }
            ]
        },
    )
    assert r.status_code == 422
    assert "cannot carry participants" in r.text
```

Fill the `# body omitted` helper `stage0_patch_body` inline in the same test to keep the task self-contained.

- [ ] **Step 2: Run the new tests**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_pipeline_participants.py -x
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_pipeline_participants.py
git commit -m "test(pipelines): participants round-trip + validation"
```

---

### Task 11: Backend test — assignable-users endpoint

**Files:**
- Create: `backend/nexus/tests/test_pipeline_assignable_users.py`

- [ ] **Step 1: Write the test file**

```python
"""Assignable-users endpoint — role gating + ancestry + tenant isolation."""

import pytest


@pytest.mark.asyncio
async def test_interviewer_pool_includes_hiring_managers(
    client, factory_pipeline_instance, factory_user, auth_headers
):
    instance = await factory_pipeline_instance(stage_types=["human_interview"])
    iv = await factory_user(role="Interviewer", org_unit_id=instance.org_unit_id)
    hm = await factory_user(role="Hiring Manager", org_unit_id=instance.org_unit_id)
    rec = await factory_user(role="Recruiter", org_unit_id=instance.org_unit_id)

    r = await client.get(
        f"/api/jobs/{instance.job_id}/pipeline/assignable-users?role=interviewer",
        headers=auth_headers,
    )
    assert r.status_code == 200
    ids = {u["user_id"] for u in r.json()}
    assert str(iv.id) in ids
    assert str(hm.id) in ids
    assert str(rec.id) not in ids


@pytest.mark.asyncio
async def test_reviewer_pool_is_hiring_managers_only(
    client, factory_pipeline_instance, factory_user, auth_headers
):
    instance = await factory_pipeline_instance(stage_types=["debrief"])
    hm = await factory_user(role="Hiring Manager", org_unit_id=instance.org_unit_id)
    iv = await factory_user(role="Interviewer", org_unit_id=instance.org_unit_id)

    r = await client.get(
        f"/api/jobs/{instance.job_id}/pipeline/assignable-users?role=reviewer",
        headers=auth_headers,
    )
    assert r.status_code == 200
    ids = {u["user_id"] for u in r.json()}
    assert str(hm.id) in ids
    assert str(iv.id) not in ids


@pytest.mark.asyncio
async def test_sibling_unit_user_not_included(
    client, factory_pipeline_instance, factory_user, factory_org_unit, auth_headers
):
    instance = await factory_pipeline_instance(stage_types=["human_interview"])
    sibling = await factory_org_unit(under_company=True)
    outsider = await factory_user(role="Interviewer", org_unit_id=sibling.id)

    r = await client.get(
        f"/api/jobs/{instance.job_id}/pipeline/assignable-users?role=interviewer",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert str(outsider.id) not in {u["user_id"] for u in r.json()}


@pytest.mark.asyncio
async def test_inactive_users_excluded(
    client, factory_pipeline_instance, factory_user, auth_headers, bypass_db
):
    instance = await factory_pipeline_instance(stage_types=["human_interview"])
    iv = await factory_user(role="Interviewer", org_unit_id=instance.org_unit_id)

    # Deactivate.
    from app.models import User
    from sqlalchemy import update
    await bypass_db.execute(
        update(User).where(User.id == iv.id).values(is_active=False)
    )
    await bypass_db.commit()

    r = await client.get(
        f"/api/jobs/{instance.job_id}/pipeline/assignable-users?role=interviewer",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert str(iv.id) not in {u["user_id"] for u in r.json()}
```

- [ ] **Step 2: Run**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_pipeline_assignable_users.py -x
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_pipeline_assignable_users.py
git commit -m "test(pipelines): assignable-users role gating + tenant isolation"
```

---

### Task 12: Backend test — migration 0016 data correctness

**Files:**
- Create: `backend/nexus/tests/test_migration_0016.py` (follows the same pattern as `tests/test_migration_0014.py`)

- [ ] **Step 1: Write the test**

Read `backend/nexus/tests/test_migration_0014.py` first to see the existing pattern for migration tests (it uses `command.downgrade()` + `command.upgrade()` + bypass-RLS SQL). Mirror that pattern:

```python
"""Migration 0016: stage_type v5 + participants table.

Seeds rows with legacy stage_type values before upgrading and asserts that:
- recruiter / panel_interview rows become human_interview
- ai_interview rows become ai_screening
- offer rows are deleted
- position is resequenced per instance
- CHECK rejects legacy values after upgrade
- pipeline_stage_participants table exists with RLS enabled
"""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

# factory_legacy_stages creates rows via bypass SQL at the pre-0016 schema.
# downgrade_one / upgrade_one are thin wrappers around command.downgrade.


@pytest.mark.asyncio
async def test_legacy_stage_types_renamed(alembic_cfg, bypass_db, fresh_migration_fixture):
    # fresh_migration_fixture downgrades to 0015 first.
    ...
    # Insert legacy rows via bypass SQL — 3 stages with types
    # recruiter / panel_interview / ai_interview.
    # Insert an offer row in a separate instance.
    # Upgrade to 0016.
    # Assert SELECT results match expected.


@pytest.mark.asyncio
async def test_offer_rows_deleted(alembic_cfg, bypass_db, fresh_migration_fixture):
    ...


@pytest.mark.asyncio
async def test_positions_resequenced_after_offer_delete(
    alembic_cfg, bypass_db, fresh_migration_fixture
):
    # Insert 4 stages at positions 0,1,2,3 where position=2 is 'offer'.
    # Upgrade.
    # Assert remaining stages have positions 0,1,2.
    ...


@pytest.mark.asyncio
async def test_new_check_rejects_legacy_values(
    alembic_cfg, bypass_db, fresh_migration_fixture
):
    ...


@pytest.mark.asyncio
async def test_participants_table_has_rls_policies(
    alembic_cfg, bypass_db, fresh_migration_fixture
):
    # Query pg_policies; assert tenant_isolation + service_bypass both present.
    ...
```

Fill each `...` with the same pattern as `test_migration_0014.py`. If `fresh_migration_fixture` doesn't exist in conftest yet, either add it or use `alembic.command` directly per the 0014 test.

- [ ] **Step 2: Run**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest tests/test_migration_0016.py -x
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add backend/nexus/tests/test_migration_0016.py
git commit -m "test(migrations): 0016 data correctness + RLS + position resequence"
```

---

### Task 13: Frontend — rewrite `lib/api/pipelines.ts`

**Files:**
- Modify: `frontend/app/lib/api/pipelines.ts`

- [ ] **Step 1: Rewrite `StageType`, add participant types, add API method**

In `frontend/app/lib/api/pipelines.ts`, replace the `StageType` union (lines 5–16) with:

```ts
export type StageType =
  | 'intake'
  | 'phone_screen'
  | 'ai_screening'
  | 'human_interview'
  | 'debrief'
  | 'take_home'

export type ParticipantRole = 'interviewer' | 'observer' | 'reviewer'

export type StageParticipantInput = {
  user_id: string
  role: ParticipantRole
}

export type StageParticipantResponse = StageParticipantInput & {
  full_name: string
  email: string
}
```

Extend stage shapes (lines ~36–58):

```ts
export type PipelineStageInput = {
  position: number
  name: string
  stage_type: StageType
  duration_minutes: number
  difficulty: StageDifficulty
  signal_filter: SignalFilter
  pass_criteria: PassCriteria
  advance_behavior: AdvanceBehavior
  sla_days?: number | null
  participants?: StageParticipantInput[]
}

export type PipelineStageUpdateInput = Omit<PipelineStageInput, 'participants'> & {
  id?: string
  /** undefined/null = don't touch; [] = clear; [...] = replace. */
  participants?: StageParticipantInput[] | null
}

export type PipelineStageResponse = Omit<PipelineStageInput, 'participants'> & {
  id: string
  participants: StageParticipantResponse[]
}
```

Add the `AssignableUser` shape and the API method at the bottom of `pipelinesApi` (before the closing `}`):

```ts
export type AssignableUser = {
  user_id: string
  full_name: string
  email: string
  role_labels: string[]
  org_unit_name: string
}

// inside pipelinesApi:
  getAssignableUsers: (
    token: string,
    jobId: string,
    role: ParticipantRole,
  ): Promise<AssignableUser[]> =>
    apiFetch<AssignableUser[]>(
      `/api/jobs/${jobId}/pipeline/assignable-users?role=${role}`,
      { token },
    ),
```

- [ ] **Step 2: Type-check the whole frontend**

Run:
```bash
cd frontend/app && npm run type-check
```

Expected: a list of errors across components that reference removed `StageType` values (`recruiter`, `panel_interview`, `offer`, `ai_interview`). These will be fixed in Tasks 15–21. Leave them for now — this task intentionally only updates the API client.

**Don't commit yet** — Tasks 14 and subsequent tasks need to land before `type-check` passes cleanly.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/lib/api/pipelines.ts
git commit -m "feat(frontend): stage_type v5 + participant types in API client"
```

(Commit even though type-check is red — subsequent tasks clean it up. This keeps diffs reviewable.)

---

### Task 14: Frontend — `lib/pipelines/categories.ts` + test

**Files:**
- Create: `frontend/app/lib/pipelines/categories.ts`
- Create: `frontend/app/lib/pipelines/categories.test.ts`

- [ ] **Step 1: Write the failing test**

Write `frontend/app/lib/pipelines/categories.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { StageType, StageParticipantResponse } from '@/lib/api/pipelines'
import {
  stageCategory,
  participantSlotsFor,
  isStageUnstaffed,
} from './categories'

describe('stageCategory', () => {
  const cases: Array<[StageType, ReturnType<typeof stageCategory>]> = [
    ['intake', 'entry'],
    ['phone_screen', 'human_led'],
    ['human_interview', 'human_led'],
    ['ai_screening', 'ai_led'],
    ['debrief', 'review'],
    ['take_home', 'disabled'],
  ]
  it.each(cases)('maps %s to %s', (type, expected) => {
    expect(stageCategory(type)).toBe(expected)
  })
})

describe('participantSlotsFor', () => {
  it('returns [] for entry', () => {
    expect(participantSlotsFor('intake')).toEqual([])
  })
  it('returns interviewer slot for human_led', () => {
    expect(participantSlotsFor('human_interview')).toEqual([
      { role: 'interviewer', required: true, min: 1 },
    ])
    expect(participantSlotsFor('phone_screen')).toEqual([
      { role: 'interviewer', required: true, min: 1 },
    ])
  })
  it('returns optional observer slot for ai_led', () => {
    expect(participantSlotsFor('ai_screening')).toEqual([
      { role: 'observer', required: false },
    ])
  })
  it('returns reviewer slot for review', () => {
    expect(participantSlotsFor('debrief')).toEqual([
      { role: 'reviewer', required: true, min: 1 },
    ])
  })
  it('returns [] for disabled take_home', () => {
    expect(participantSlotsFor('take_home')).toEqual([])
  })
})

describe('isStageUnstaffed', () => {
  const p = (role: 'interviewer' | 'observer' | 'reviewer'): StageParticipantResponse => ({
    user_id: 'u',
    role,
    full_name: 'U',
    email: 'u@example.com',
  })
  it('true when human_interview has 0 interviewers', () => {
    expect(isStageUnstaffed({ stage_type: 'human_interview', participants: [] })).toBe(true)
  })
  it('false when human_interview has at least 1 interviewer', () => {
    expect(
      isStageUnstaffed({ stage_type: 'human_interview', participants: [p('interviewer')] }),
    ).toBe(false)
  })
  it('false for ai_screening (observers optional)', () => {
    expect(isStageUnstaffed({ stage_type: 'ai_screening', participants: [] })).toBe(false)
  })
  it('false for intake / take_home', () => {
    expect(isStageUnstaffed({ stage_type: 'intake', participants: [] })).toBe(false)
    expect(isStageUnstaffed({ stage_type: 'take_home', participants: [] })).toBe(false)
  })
  it('true when debrief has 0 reviewers', () => {
    expect(isStageUnstaffed({ stage_type: 'debrief', participants: [] })).toBe(true)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd frontend/app && npm run test -- categories.test
```

Expected: fails with "Cannot find module './categories'".

- [ ] **Step 3: Implement `categories.ts`**

Write `frontend/app/lib/pipelines/categories.ts`:

```ts
import type { StageType, StageParticipantResponse } from '@/lib/api/pipelines'

export type StageCategory = 'entry' | 'human_led' | 'ai_led' | 'review' | 'disabled'

export function stageCategory(type: StageType): StageCategory {
  switch (type) {
    case 'intake':
      return 'entry'
    case 'phone_screen':
    case 'human_interview':
      return 'human_led'
    case 'ai_screening':
      return 'ai_led'
    case 'debrief':
      return 'review'
    case 'take_home':
      return 'disabled'
  }
}

export type ParticipantSlotSpec =
  | { role: 'interviewer'; required: true; min: 1 }
  | { role: 'observer'; required: false }
  | { role: 'reviewer'; required: true; min: 1 }

export function participantSlotsFor(type: StageType): ParticipantSlotSpec[] {
  switch (stageCategory(type)) {
    case 'human_led':
      return [{ role: 'interviewer', required: true, min: 1 }]
    case 'ai_led':
      return [{ role: 'observer', required: false }]
    case 'review':
      return [{ role: 'reviewer', required: true, min: 1 }]
    default:
      return []
  }
}

export function isStageUnstaffed(stage: {
  stage_type: StageType
  participants: StageParticipantResponse[]
}): boolean {
  const required = participantSlotsFor(stage.stage_type).filter(
    (s): s is Extract<ParticipantSlotSpec, { required: true }> => s.required,
  )
  return required.some(
    (slot) => stage.participants.filter((p) => p.role === slot.role).length === 0,
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd frontend/app && npm run test -- categories.test
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/lib/pipelines/
git commit -m "feat(frontend): categories utility for per-type UI behaviour"
```

---

### Task 15: Frontend — `use-assignable-users` hook

**Files:**
- Create: `frontend/app/lib/hooks/use-assignable-users.ts`

- [ ] **Step 1: Write the hook**

```ts
'use client'

import { useQuery } from '@tanstack/react-query'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { pipelinesApi, type AssignableUser, type ParticipantRole } from '@/lib/api/pipelines'

export function useAssignableUsers(jobId: string, role: ParticipantRole | null) {
  return useQuery<AssignableUser[]>({
    queryKey: ['jobs', jobId, 'assignable-users', role],
    enabled: role !== null,
    staleTime: 60_000,
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.getAssignableUsers(token, jobId, role as ParticipantRole)
    },
  })
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/lib/hooks/use-assignable-users.ts
git commit -m "feat(frontend): use-assignable-users hook"
```

---

### Task 16: Frontend — `StageParticipantsEditor` component + test

**Files:**
- Create: `frontend/app/components/dashboard/pipeline/StageParticipantsEditor.tsx`
- Create: `frontend/app/components/dashboard/pipeline/StageParticipantsEditor.test.tsx`

- [ ] **Step 1: Write the failing test**

Write `frontend/app/components/dashboard/pipeline/StageParticipantsEditor.test.tsx`:

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StageParticipantsEditor } from './StageParticipantsEditor'

vi.mock('@/lib/hooks/use-assignable-users', () => ({
  useAssignableUsers: () => ({
    data: [
      { user_id: 'u1', full_name: 'Alice', email: 'a@ex.com', role_labels: ['Interviewer'], org_unit_name: 'Team' },
      { user_id: 'u2', full_name: 'Bob',   email: 'b@ex.com', role_labels: ['Interviewer'], org_unit_name: 'Team' },
    ],
    isLoading: false,
    isError: false,
  }),
}))

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient()
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

describe('StageParticipantsEditor', () => {
  it('renders the interviewer slot for human_interview', () => {
    render(wrap(
      <StageParticipantsEditor
        jobId="j1"
        stage={{ stage_type: 'human_interview', participants: [] }}
        onChange={() => {}}
      />,
    ))
    expect(screen.getByText(/interviewer/i)).toBeInTheDocument()
  })

  it('calls onChange with added participant when Add Alice is clicked', async () => {
    const onChange = vi.fn()
    render(wrap(
      <StageParticipantsEditor
        jobId="j1"
        stage={{ stage_type: 'human_interview', participants: [] }}
        onChange={onChange}
      />,
    ))
    fireEvent.click(screen.getByRole('button', { name: /add interviewer/i }))
    fireEvent.click(screen.getByText(/alice/i))
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith([{ user_id: 'u1', role: 'interviewer' }]),
    )
  })

  it('removes a participant when the chip × is clicked', () => {
    const onChange = vi.fn()
    render(wrap(
      <StageParticipantsEditor
        jobId="j1"
        stage={{
          stage_type: 'human_interview',
          participants: [{ user_id: 'u1', role: 'interviewer', full_name: 'Alice', email: 'a@ex.com' }],
        }}
        onChange={onChange}
      />,
    ))
    fireEvent.click(screen.getByRole('button', { name: /remove alice/i }))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('filters already-assigned users from the combobox options', () => {
    render(wrap(
      <StageParticipantsEditor
        jobId="j1"
        stage={{
          stage_type: 'human_interview',
          participants: [{ user_id: 'u1', role: 'interviewer', full_name: 'Alice', email: 'a@ex.com' }],
        }}
        onChange={() => {}}
      />,
    ))
    fireEvent.click(screen.getByRole('button', { name: /add interviewer/i }))
    expect(screen.queryByText(/alice/i)).not.toBeInTheDocument()  // hidden in chip list OR option list
    expect(screen.getByText(/bob/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify failure**

```bash
cd frontend/app && npm run test -- StageParticipantsEditor.test
```

Expected: fails with module-not-found on StageParticipantsEditor.

- [ ] **Step 3: Implement the component**

Write `frontend/app/components/dashboard/pipeline/StageParticipantsEditor.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useAssignableUsers } from '@/lib/hooks/use-assignable-users'
import {
  participantSlotsFor,
  type ParticipantSlotSpec,
} from '@/lib/pipelines/categories'
import type {
  StageParticipantInput,
  StageParticipantResponse,
  StageType,
} from '@/lib/api/pipelines'

type Props = {
  jobId: string
  stage: {
    stage_type: StageType
    participants: StageParticipantResponse[]
  }
  onChange: (next: StageParticipantInput[]) => void
}

export function StageParticipantsEditor({ jobId, stage, onChange }: Props) {
  const slots = participantSlotsFor(stage.stage_type)
  if (slots.length === 0) return null

  return (
    <div className="space-y-4">
      {slots.map((slot) => (
        <ParticipantSlotSection
          key={slot.role}
          jobId={jobId}
          slot={slot}
          participants={stage.participants.filter((p) => p.role === slot.role)}
          onChange={(next) => {
            const others = stage.participants.filter((p) => p.role !== slot.role)
            onChange([
              ...others.map(({ user_id, role }) => ({ user_id, role })),
              ...next,
            ])
          }}
        />
      ))}
    </div>
  )
}

function ParticipantSlotSection({
  jobId,
  slot,
  participants,
  onChange,
}: {
  jobId: string
  slot: ParticipantSlotSpec
  participants: StageParticipantResponse[]
  onChange: (next: StageParticipantInput[]) => void
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const { data: pool, isLoading, isError } = useAssignableUsers(
    jobId,
    pickerOpen ? slot.role : null,
  )

  const assignedIds = new Set(participants.map((p) => p.user_id))

  const label =
    slot.role === 'interviewer' ? 'Interviewer' :
    slot.role === 'observer'    ? 'Observer' :
                                   'Reviewer'

  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-xs font-medium text-zinc-700">
          {label}{slot.required ? '' : ' (optional)'}
        </div>
        <button
          type="button"
          onClick={() => setPickerOpen((v) => !v)}
          className="text-xs text-blue-600 hover:text-blue-800"
          aria-label={`Add ${label.toLowerCase()}`}
        >
          + Add
        </button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {participants.length === 0 && (
          <div className="text-xs text-zinc-400">
            {slot.required ? `No ${label.toLowerCase()}s assigned yet.`
                           : `No ${label.toLowerCase()}s yet (optional).`}
          </div>
        )}
        {participants.map((p) => (
          <span
            key={p.user_id}
            className="inline-flex items-center gap-1 rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs"
            title={p.email}
          >
            {p.full_name || p.email}
            <button
              type="button"
              aria-label={`Remove ${p.full_name || p.email}`}
              onClick={() =>
                onChange(
                  participants
                    .filter((x) => x.user_id !== p.user_id)
                    .map(({ user_id, role }) => ({ user_id, role })),
                )
              }
              className="text-zinc-400 hover:text-zinc-900"
            >
              ×
            </button>
          </span>
        ))}
      </div>

      {pickerOpen && (
        <div className="mt-2 border border-zinc-200 rounded-md p-2 max-h-48 overflow-y-auto">
          {isLoading && <div className="text-xs text-zinc-400">Loading…</div>}
          {isError && (
            <div className="text-xs text-red-500">
              Couldn't load the team roster.
            </div>
          )}
          {(pool ?? [])
            .filter((u) => !assignedIds.has(u.user_id))
            .map((u) => (
              <button
                type="button"
                key={u.user_id}
                onClick={() => {
                  onChange([
                    ...participants.map(({ user_id, role }) => ({ user_id, role })),
                    { user_id: u.user_id, role: slot.role },
                  ])
                  setPickerOpen(false)
                }}
                className="w-full text-left px-2 py-1 text-xs rounded hover:bg-zinc-50"
              >
                <div className="font-medium">{u.full_name || u.email}</div>
                <div className="text-zinc-400">{u.email}</div>
              </button>
            ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run tests**

```bash
cd frontend/app && npm run test -- StageParticipantsEditor.test
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/StageParticipantsEditor*
git commit -m "feat(frontend): StageParticipantsEditor component"
```

---

### Task 17: Frontend — update `StageConfigDrawer.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx`

- [ ] **Step 1: Rewrite `STAGE_TYPES` array + accept `jobId` prop + insert participants editor**

Replace the whole file with:

```tsx
'use client'

import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type {
  PipelineStageInput,
  PipelineStageUpdateInput,
  StageType,
  StageDifficulty,
  AdvanceBehavior,
  StageParticipantInput,
} from '@/lib/api/pipelines'
import { DifficultySlider } from './DifficultySlider'
import { SignalFilterEditor } from './SignalFilterEditor'
import { PassCriteriaEditor } from './PassCriteriaEditor'
import { StageParticipantsEditor } from './StageParticipantsEditor'
import { participantSlotsFor } from '@/lib/pipelines/categories'

type Props = {
  stage: PipelineStageUpdateInput
  /** When set, enables the participants editor. Omit for template editing. */
  jobId?: string
  onChange: (stage: PipelineStageUpdateInput) => void
  onClose: () => void
}

const STAGE_TYPES: { value: StageType; label: string; disabled?: boolean }[] = [
  { value: 'intake',          label: 'Intake' },
  { value: 'phone_screen',    label: 'Phone Screen' },
  { value: 'ai_screening',    label: 'AI Screening' },
  { value: 'human_interview', label: 'Human Interview' },
  { value: 'debrief',         label: 'Debrief' },
  { value: 'take_home',       label: 'Take-home (Coming soon)', disabled: true },
]

const ADVANCE_BEHAVIORS: { value: AdvanceBehavior; label: string }[] = [
  { value: 'auto_advance', label: 'Auto-advance on pass' },
  { value: 'manual_review', label: 'Manual review' },
]

export function StageConfigDrawer({ stage, jobId, onChange, onClose }: Props) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const nameInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameInputRef.current?.focus()
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  function update<K extends keyof PipelineStageInput>(key: K, value: PipelineStageInput[K]) {
    onChange({ ...stage, [key]: value })
  }

  function handleTypeChange(next: StageType) {
    // When type changes to a different category, strip participants that no
    // longer match. Keep only participants whose role is legal under next.
    const newSlot = participantSlotsFor(next)[0]?.role ?? null
    const currentParticipants = stage.participants ?? []
    const filtered = newSlot === null
      ? []
      : currentParticipants.filter((p) => p.role === newSlot)
    onChange({ ...stage, stage_type: next, participants: filtered })
  }

  const showParticipants =
    jobId !== undefined && participantSlotsFor(stage.stage_type).length > 0

  return (
    <div
      className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="stage-config-heading"
        className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <h3 id="stage-config-heading" className="text-base font-semibold text-zinc-900">
            Configure Stage
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="text-zinc-400 hover:text-zinc-900 text-xl leading-none p-1 rounded hover:bg-zinc-100 transition"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-5 overflow-y-auto">
          {/* Basic */}
          <div className="space-y-4">
            <div>
              <label htmlFor="stage-name" className="block text-xs font-medium text-zinc-700 mb-1.5">
                Name
              </label>
              <input
                ref={nameInputRef}
                id="stage-name"
                type="text"
                value={stage.name}
                onChange={(e) => update('name', e.target.value)}
                className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
              />
            </div>

            <div>
              <label htmlFor="stage-type" className="block text-xs font-medium text-zinc-700 mb-1.5">
                Stage type
              </label>
              <select
                id="stage-type"
                value={stage.stage_type}
                onChange={(e) => handleTypeChange(e.target.value as StageType)}
                className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
              >
                {STAGE_TYPES.map((t) => (
                  <option key={t.value} value={t.value} disabled={t.disabled}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="stage-duration" className="block text-xs font-medium text-zinc-700 mb-1.5">
                Duration
              </label>
              <div className="relative">
                <input
                  id="stage-duration"
                  type="number"
                  min={1}
                  max={240}
                  value={stage.duration_minutes}
                  onChange={(e) => update('duration_minutes', parseInt(e.target.value) || 1)}
                  className="w-full text-sm border border-zinc-300 rounded-lg px-3 py-2 pr-12 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400"
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-zinc-400 pointer-events-none">
                  min
                </span>
              </div>
            </div>

            <div>
              <label htmlFor="stage-difficulty" className="block text-xs font-medium text-zinc-700 mb-2">
                Difficulty
              </label>
              <DifficultySlider
                id="stage-difficulty"
                value={stage.difficulty}
                onChange={(v) => update('difficulty', v as StageDifficulty)}
              />
            </div>
          </div>

          {/* Participants (instance-only) */}
          {showParticipants && (
            <div className="border-t border-zinc-100 pt-4">
              <StageParticipantsEditor
                jobId={jobId!}
                stage={{
                  stage_type: stage.stage_type,
                  // Drawer holds input-shape (no full_name/email); editor only
                  // reads .role for rendering existing chips. Supply display
                  // fields from where the parent loads them, or fall back to
                  // empty strings — the combobox is what populates new adds.
                  participants: (stage.participants ?? []).map((p) => ({
                    ...p,
                    full_name: '',
                    email: '',
                  })),
                }}
                onChange={(next: StageParticipantInput[]) => update('participants', next)}
              />
            </div>
          )}

          {/* Advanced */}
          <div className="border-t border-zinc-100 pt-4">
            <button
              type="button"
              onClick={() => setAdvancedOpen((v) => !v)}
              aria-expanded={advancedOpen}
              aria-controls="advanced-section"
              className="w-full flex items-center justify-between text-xs font-medium text-zinc-700 hover:text-zinc-900 transition"
            >
              <span>Advanced settings</span>
              <ChevronDown className={`w-4 h-4 transition-transform duration-200 ${advancedOpen ? 'rotate-180' : ''}`} />
            </button>

            {advancedOpen && (
              <div id="advanced-section" className="mt-4 space-y-4">
                {/* SLA / Advance / Pass / Signal filters — unchanged */}
                {/* (copy the existing blocks from the previous version of this file verbatim) */}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
```

**NOTE:** the "Advanced settings" block body (SLA / advance / pass / signal filter) should be copied verbatim from the pre-edit file — the edit in this task doesn't change those controls. Don't paraphrase them; keep the existing code exactly.

- [ ] **Step 2: Persist participant display fields through the parent component's typing**

The drawer itself holds a `PipelineStageUpdateInput` (input shape — no full_name/email). The chip list rendered from `stage.participants` will show empty labels on existing stages. To show names for already-assigned users, the parent (JobPipelineFunnel) must map the server's `PipelineStageResponse.participants` (response shape — has full_name/email) into a local "view shape" that keeps the display fields. If the parent currently holds `PipelineStageUpdateInput[]`, extend its local type to preserve `full_name` and `email` for rendering — see Task 19 for the JobPipelineFunnel edit.

- [ ] **Step 3: Type-check**

```bash
cd frontend/app && npm run type-check
```

Expected: fewer errors than before; remaining errors will be in Tasks 18–21.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx
git commit -m "feat(frontend): stage type v5 picker + participants editor in drawer"
```

---

### Task 18: Frontend — update `StageConfigurationTab.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/StageConfigurationTab.tsx`

- [ ] **Step 1: Rewrite the `STAGE_TYPES` array**

Find the `STAGE_TYPES` array (around line 21) in `StageConfigurationTab.tsx` and replace with the same 6-value map used in `StageConfigDrawer` (including the `disabled: true` on `take_home`). Keep the `<select>` producing `<option disabled>` for those entries.

- [ ] **Step 2: Accept `jobId` prop and render the participants editor inline**

Mirror the changes from Task 17 here: add `jobId?: string` to the component props, render `<StageParticipantsEditor>` after the basic fields section when `jobId !== undefined && participantSlotsFor(stage.stage_type).length > 0`.

- [ ] **Step 3: Type-check + commit**

```bash
cd frontend/app && npm run type-check
git add frontend/app/components/dashboard/pipeline/StageConfigurationTab.tsx
git commit -m "feat(frontend): stage type v5 + participants editor in config tab"
```

---

### Task 19: Frontend — update `JobPipelineFunnel.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/JobPipelineFunnel.tsx`

- [ ] **Step 1: Rewrite `STAGE_TYPE_LABEL` map**

Find the `STAGE_TYPE_LABEL: Record<StageType, string>` (around line 64) and replace with:

```ts
const STAGE_TYPE_LABEL: Record<StageType, string> = {
  intake: 'Intake',
  phone_screen: 'Phone Screen',
  ai_screening: 'AI Screening',
  human_interview: 'Human Interview',
  debrief: 'Debrief',
  take_home: 'Take-home',
}
```

- [ ] **Step 2: Rewrite `stageTypeOptions` + `stageGate` + `lead` to use v5 types**

Find `stageTypeOptions` (around line 775) and rewrite to match the 6-value allowlist with `disabled` on `take_home`. Find the `lead` computation (around line 927) that keys on `panel_interview`/`human_interview` and replace with a single check for `human_interview`. Find `stageGate` (around line 76) and remove any reference to the deleted types.

- [ ] **Step 3: Pass `jobId` through to the drawer**

Every place this component renders `<StageConfigDrawer …>`, pass `jobId={jobId}` so the participants editor activates.

- [ ] **Step 4: Keep display fields on participants for the drawer**

The component loads `pipeline.stages` which come back from the server as `PipelineStageResponse[]` (response shape with full_name/email). When constructing the local drawer shape, preserve the participant display fields so the chip list labels render. Example:

```ts
const drawerStage: PipelineStageUpdateInput & { participants?: StageParticipantResponse[] } = {
  ...stageFromList,  // already has full participant response shape
}
```

- [ ] **Step 5: Type-check + commit**

```bash
cd frontend/app && npm run type-check
git add frontend/app/components/dashboard/pipeline/JobPipelineFunnel.tsx
git commit -m "feat(frontend): JobPipelineFunnel — stage type v5 labels + jobId passthrough"
```

---

### Task 20: Frontend — update `StageFlowCard.tsx`, `StageSlab.tsx`, `UnifiedPipelineView.tsx`, `StageActionsMenu.tsx`

**Files:**
- Modify: `frontend/app/components/dashboard/pipeline/StageFlowCard.tsx`
- Modify: `frontend/app/components/dashboard/pipeline/StageSlab.tsx`
- Modify: `frontend/app/components/dashboard/pipeline/UnifiedPipelineView.tsx`
- Modify: `frontend/app/components/dashboard/pipeline/StageActionsMenu.tsx`

- [ ] **Step 1: Rewrite the label/icon/accent/text maps in `StageFlowCard.tsx`**

Replace `STAGE_TYPE_LABELS`, `STAGE_TYPE_ICONS`, `STAGE_TYPE_ACCENT`, `STAGE_TYPE_TEXT` with 6-value maps using the icons from §6.7 of the spec:

```tsx
import { Inbox, Phone, Bot, Users, Gavel, FileText, type LucideIcon } from 'lucide-react'

const STAGE_TYPE_LABELS: Record<StageType, string> = {
  intake: 'Intake',
  phone_screen: 'Phone Screen',
  ai_screening: 'AI Screening',
  human_interview: 'Human Interview',
  debrief: 'Debrief',
  take_home: 'Take-home',
}

const STAGE_TYPE_ICONS: Record<StageType, LucideIcon> = {
  intake: Inbox,
  phone_screen: Phone,
  ai_screening: Bot,
  human_interview: Users,
  debrief: Gavel,
  take_home: FileText,
}

const STAGE_TYPE_ACCENT: Record<StageType, string> = {
  intake: 'bg-zinc-400',
  phone_screen: 'bg-blue-500',
  ai_screening: 'bg-violet-500',
  human_interview: 'bg-emerald-500',
  debrief: 'bg-amber-500',
  take_home: 'bg-zinc-300',
}

const STAGE_TYPE_TEXT: Record<StageType, string> = {
  intake: 'text-zinc-600',
  phone_screen: 'text-blue-600',
  ai_screening: 'text-violet-600',
  human_interview: 'text-emerald-600',
  debrief: 'text-amber-600',
  take_home: 'text-zinc-500',
}
```

- [ ] **Step 2: Add the Unstaffed badge**

Import the helper:
```tsx
import { isStageUnstaffed } from '@/lib/pipelines/categories'
```

Near the top of the card body rendering, add (adjust styling to match the existing card visuals):

```tsx
{isStageUnstaffed(stage) && (
  <span
    title="No interviewers/reviewers assigned"
    className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700"
  >
    Unstaffed
  </span>
)}
```

- [ ] **Step 3: Repeat Steps 1 and 2 for `StageSlab.tsx`**

Same three map rewrites and same badge insertion.

- [ ] **Step 4: Fix placeholder stage data in `UnifiedPipelineView.tsx`**

Find any fake/seed stage with an old type (around line 30) and swap to a v5-compliant type — e.g. `phone_screen` or `ai_screening`. Keep the rest of the placeholder intact.

- [ ] **Step 5: Clear participants on duplicate in `StageActionsMenu.tsx`**

Find the "Duplicate stage" handler. When building the duplicate stage payload, strip participants:

```ts
const duplicated = {
  ...originalStage,
  id: undefined,
  participants: [],  // staffing doesn't carry
}
```

- [ ] **Step 6: Final type-check + lint**

```bash
cd frontend/app && npm run type-check && npm run lint
```

Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/
git commit -m "feat(frontend): stage v5 labels/icons/accent + Unstaffed badge + clear on duplicate"
```

---

### Task 21: Frontend — StageConfigDrawer test (jobId / no-jobId branch)

**Files:**
- Create: `frontend/app/components/dashboard/pipeline/StageConfigDrawer.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StageConfigDrawer } from './StageConfigDrawer'

vi.mock('@/lib/hooks/use-assignable-users', () => ({
  useAssignableUsers: () => ({ data: [], isLoading: false, isError: false }),
}))

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient()
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

const baseStage = {
  position: 0,
  name: 'Test',
  stage_type: 'human_interview' as const,
  duration_minutes: 30,
  difficulty: 'medium' as const,
  signal_filter: { include_types: [] },
  pass_criteria: { type: 'manual_review' as const },
  advance_behavior: 'manual_review' as const,
  participants: [],
}

describe('StageConfigDrawer', () => {
  it('does NOT render participants editor when jobId is absent', () => {
    render(wrap(
      <StageConfigDrawer stage={baseStage} onChange={() => {}} onClose={() => {}} />,
    ))
    expect(screen.queryByText(/interviewer/i)).not.toBeInTheDocument()
  })

  it('renders participants editor when jobId is present and category has slots', () => {
    render(wrap(
      <StageConfigDrawer stage={baseStage} jobId="j1" onChange={() => {}} onClose={() => {}} />,
    ))
    expect(screen.getByText(/interviewer/i)).toBeInTheDocument()
  })

  it('strips mismatched participants when stage_type changes category', () => {
    const onChange = vi.fn()
    const stageWithInterviewer = {
      ...baseStage,
      participants: [{ user_id: 'u1', role: 'interviewer' as const }],
    }
    render(wrap(
      <StageConfigDrawer stage={stageWithInterviewer} jobId="j1" onChange={onChange} onClose={() => {}} />,
    ))
    fireEvent.change(screen.getByLabelText(/stage type/i), { target: { value: 'debrief' } })
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0]
    expect(lastCall.stage_type).toBe('debrief')
    expect(lastCall.participants).toEqual([])
  })
})
```

- [ ] **Step 2: Run**

```bash
cd frontend/app && npm run test -- StageConfigDrawer.test
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/dashboard/pipeline/StageConfigDrawer.test.tsx
git commit -m "test(frontend): StageConfigDrawer jobId branch + type-change strips participants"
```

---

### Task 22: Docs refresh — backend `CLAUDE.md`

**Files:**
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 1: Add a short note under the pipelines module row**

Find the "pipelines" row in the module table (around the "Phase 2C.1 — Implemented" section) and append:

```markdown
**v5 (2026-04-22):** stage types collapsed to 6 values (`intake`, `phone_screen`,
`ai_screening`, `human_interview`, `debrief`, `take_home`) — `recruiter`, `panel_interview`,
`offer`, `ai_interview` are hard-removed (no alias). Instance stages carry participants
(interviewers/observers/reviewers) via `pipeline_stage_participants`, gated by system-role
lookup at the job's org unit ancestry. Templates remain staffing-agnostic. New table is in
`_TENANT_SCOPED_TABLES` — startup RLS check covers it.
```

- [ ] **Step 2: Commit**

```bash
git add backend/nexus/CLAUDE.md
git commit -m "docs(nexus): document stage type v5 + participants"
```

---

### Task 23: Final integration smoke test

- [ ] **Step 1: Run the full backend suite**

```bash
docker compose -f backend/nexus/docker-compose.yml run --rm nexus pytest -x
```

Expected: all green.

- [ ] **Step 2: Run the full frontend suite + type-check + lint + build**

```bash
cd frontend/app && npm run test && npm run type-check && npm run lint && npm run build
```

Expected: all green.

- [ ] **Step 3: End-to-end manual verification**

Boot the stack:
```bash
docker compose -f backend/nexus/docker-compose.yml up &
cd frontend/app && npm run dev
```

In the browser at `http://localhost:3000`:

1. Log in as a super admin.
2. Open a job whose signals are confirmed: navigate to `/jobs/<jobId>/pipeline`.
3. Verify stages render with the new labels / icons / accent colours.
4. Click a `human_interview` stage → drawer opens → participants section shows "No interviewers assigned yet." with an "+ Add" button. Add a user from the combobox; the chip appears. Remove with the × button.
5. Switch the same stage's type to `debrief` → the interviewer chip disappears in the drawer's local state.
6. Close the drawer → kanban card renders with a yellow "Unstaffed" badge if no reviewer was added, else no badge.
7. Network tab: the PATCH body on auto-save contains `participants` only on stages that changed; other stages omit the field.
8. Navigate to Settings → Org Units → Templates → open a template → drawer does **not** render the participants section (templates are staffing-agnostic).

If all eight checkpoints pass, the change is complete.

- [ ] **Step 4: Final commit (if any manual-verification fixups)**

```bash
git status
# If clean, nothing to commit.
# If fixups, commit them with a clear scope.
```

---

## Self-Review Checklist (already run by the author)

- **Spec coverage:** every section 1–11 in the spec maps to at least one task above.
- **Placeholder scan:** no `TODO` or `TBD` in step content — "copy the existing blocks verbatim" for the drawer's Advanced section is a conscious preservation instruction, not a placeholder.
- **Type consistency:** `StageType` / `ParticipantRole` / `StageParticipantInput` / `StageParticipantResponse` identifiers match across backend schemas (Task 3), service (Tasks 4–5), router (Task 6), frontend API client (Task 13), utility (Task 14), hook (Task 15), editor (Task 16), drawer (Task 17), and all downstream components.
- **Signature consistency:** `update_job_pipeline_stages(db, *, instance, stages, actor_id)` is introduced in Task 5 and all call sites are updated in Task 6.
- **Migration correctness:** the CHECK-drop-before-UPDATE ordering and the position re-sequencing from the spec's §4.4 are both in Task 1.
