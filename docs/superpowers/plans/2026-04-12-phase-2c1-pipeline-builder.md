# Phase 2C.1 — Pipeline Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship per-org-unit pipeline templates + per-job pipeline instances + starter pack + auto-apply hook on signal confirmation, with funnel UI for editing and a "Build Pipeline" button on the job review page.

**Architecture:** New top-level `app/modules/pipelines/` backend module (router, service, schemas, starter_pack, authz, errors). Four new DB tables via Alembic. Cross-module hook from `jd.confirm_signals` calls `pipelines.auto_apply_pipeline_on_confirmation` wrapped in try/except. Frontend adds `lib/api/pipelines.ts`, `components/dashboard/pipeline/*` components, new routes under `/settings/org-units/[unitId]/pipeline-templates/*` and `/jobs/[jobId]/pipeline`.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic + Pydantic v2 | Next.js 16 + TypeScript + shadcn/ui v4 (Base UI) + Tailwind v4 + TanStack Query v5 + Vitest

---

## File Map

### Backend — New Files
| File | Purpose |
|------|---------|
| `migrations/versions/0004_pipeline_builder.py` | 4 new tables + indexes + RLS policies |
| `app/modules/pipelines/__init__.py` | Module marker |
| `app/modules/pipelines/schemas.py` | Pydantic request/response models + all enum Literals |
| `app/modules/pipelines/starter_pack.py` | 6 hand-written templates + SYSTEM_FALLBACK_STARTER constant |
| `app/modules/pipelines/errors.py` | Custom exceptions |
| `app/modules/pipelines/authz.py` | require_template_access, require_instance_access |
| `app/modules/pipelines/service.py` | Template CRUD, instance mutation, auto-apply hook |
| `app/modules/pipelines/router.py` | All pipeline endpoints |
| `tests/test_pipelines_starter_pack.py` | Starter pack loading tests |
| `tests/test_pipelines_service.py` | Service layer tests |
| `tests/test_pipelines_router.py` | Router endpoint tests |
| `tests/test_pipelines_auto_apply.py` | Auto-apply hook tests |

### Backend — Modified Files
| File | Changes |
|------|---------|
| `app/models.py` | Add 4 new ORM classes after JobPostingSignalSnapshot |
| `app/main.py` | Register pipelines router |
| `app/modules/jd/service.py` | Call auto_apply_pipeline_on_confirmation from confirm_signals |
| `app/modules/auth/permissions.py` | (no change — reuses existing `jobs.view`, `jobs.manage`, `org_units.manage`) |

### Frontend — New Files
| File | Purpose |
|------|---------|
| `lib/api/pipelines.ts` | Types + pipelinesApi object |
| `lib/hooks/use-pipeline-templates.ts` | List templates for an org unit |
| `lib/hooks/use-starter-pack.ts` | Get starter pack (cached globally) |
| `lib/hooks/use-job-pipeline.ts` | Get a job's pipeline instance |
| `lib/hooks/use-save-pipeline-template.ts` | Mutation: create/update template |
| `lib/hooks/use-save-job-pipeline.ts` | Mutation: update job pipeline |
| `lib/hooks/use-create-job-pipeline.ts` | Mutation: create job pipeline |
| `components/dashboard/pipeline/PipelineFunnel.tsx` | Funnel layout primitive |
| `components/dashboard/pipeline/StageSlab.tsx` | Individual stage card |
| `components/dashboard/pipeline/StageConfigDrawer.tsx` | Right drawer for stage editing |
| `components/dashboard/pipeline/SignalFilterEditor.tsx` | Signal filter controls |
| `components/dashboard/pipeline/PassCriteriaEditor.tsx` | Pass criteria discriminated UI |
| `components/dashboard/pipeline/TemplatePickerDialog.tsx` | Pick from starter or library |
| `components/dashboard/pipeline/StarterPackBrowser.tsx` | Preview starter pack |
| `components/dashboard/pipeline/TemplateLibraryCard.tsx` | Card for library grid |
| `app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx` | Template library page |
| `app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/new/page.tsx` | Create template page |
| `app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/[templateId]/page.tsx` | Edit template page |
| `app/(dashboard)/jobs/[jobId]/pipeline/page.tsx` | Job pipeline page |
| `tests/components/PipelineFunnel.test.tsx` | Component test |

### Frontend — Modified Files
| File | Changes |
|------|---------|
| `app/(dashboard)/jobs/[jobId]/page.tsx` | Add "Build Pipeline" / "View Pipeline" button |
| `app/(dashboard)/settings/org-units/[unitId]/page.tsx` | Add "Pipeline Templates" section link |
| `lib/api/jobs.ts` | Add `can_manage` field if missing (sanity check) |

---

## Task 1: Alembic Migration + ORM Models

**Files:**
- Create: `backend/nexus/migrations/versions/0004_pipeline_builder.py`
- Modify: `backend/nexus/app/models.py`

- [ ] **Step 1: Add ORM classes to `models.py`**

Read the file first. After the `JobPostingSignalSnapshot` class ends (around line 191), add:

```python
class PipelineTemplate(Base):
    """Phase 2C.1 — reusable interview pipeline template per org unit.

    Templates are owned by an org unit and can be applied to jobs as
    a starting point. Editing a template does NOT affect existing job
    pipelines (jobs get snapshotted instances)."""

    __tablename__ = "pipeline_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    org_unit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    from_starter: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class PipelineTemplateStage(Base):
    """Ordered stage within a pipeline template."""

    __tablename__ = "pipeline_template_stages"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "position", name="uq_template_stage_position"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stage_type: Mapped[str] = mapped_column(String, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[str] = mapped_column(String, nullable=False)
    signal_filter: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pass_criteria: Mapped[dict] = mapped_column(JSONB, nullable=False)
    advance_behavior: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class JobPipelineInstance(Base):
    """Per-job pipeline instance — snapshotted from a template.

    Editing an instance does NOT propagate to the source template."""

    __tablename__ = "job_pipeline_instances"
    __table_args__ = (
        UniqueConstraint("job_posting_id", name="uq_job_pipeline_instance_job"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_templates.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )


class JobPipelineStage(Base):
    """Ordered stage within a job pipeline instance."""

    __tablename__ = "job_pipeline_stages"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "position", name="uq_job_pipeline_stage_position"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_pipeline_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stage_type: Mapped[str] = mapped_column(String, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[str] = mapped_column(String, nullable=False)
    signal_filter: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pass_criteria: Mapped[dict] = mapped_column(JSONB, nullable=False)
    advance_behavior: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 2: Create migration file**

Create `backend/nexus/migrations/versions/0004_pipeline_builder.py`:

```python
"""phase_2c1_pipeline_builder_tables

Revision ID: 0004_pipeline_builder
Revises: 0003_signal_schema_v2
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_pipeline_builder"
down_revision = "0003_signal_schema_v2"
branch_labels = None
depends_on = None

STAGE_TYPES = ("phone_screen", "ai_interview", "human_interview", "panel_interview", "take_home")
DIFFICULTIES = ("easy", "medium", "hard")
ADVANCE_BEHAVIORS = ("auto_advance", "manual_review")


def upgrade() -> None:
    # --- pipeline_templates ---
    op.create_table(
        "pipeline_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("org_unit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizational_units.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("from_starter", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_pipeline_templates_org_unit_default "
        "ON pipeline_templates (org_unit_id) WHERE is_default = true"
    )
    op.execute("ALTER TABLE pipeline_templates ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON pipeline_templates "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY service_role_bypass ON pipeline_templates "
        "USING (current_setting('app.bypass_rls', true) = 'true')"
    )

    # --- pipeline_template_stages ---
    op.create_table(
        "pipeline_template_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("stage_type", sa.String(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(), nullable=False),
        sa.Column("signal_filter", postgresql.JSONB(), nullable=False),
        sa.Column("pass_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("advance_behavior", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("template_id", "position", name="uq_template_stage_position"),
    )
    op.create_check_constraint(
        "ck_template_stages_stage_type", "pipeline_template_stages",
        f"stage_type IN {STAGE_TYPES}"
    )
    op.create_check_constraint(
        "ck_template_stages_difficulty", "pipeline_template_stages",
        f"difficulty IN {DIFFICULTIES}"
    )
    op.create_check_constraint(
        "ck_template_stages_advance_behavior", "pipeline_template_stages",
        f"advance_behavior IN {ADVANCE_BEHAVIORS}"
    )
    op.create_check_constraint(
        "ck_template_stages_duration", "pipeline_template_stages",
        "duration_minutes > 0 AND duration_minutes <= 240"
    )
    op.execute("ALTER TABLE pipeline_template_stages ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON pipeline_template_stages "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY service_role_bypass ON pipeline_template_stages "
        "USING (current_setting('app.bypass_rls', true) = 'true')"
    )

    # --- job_pipeline_instances ---
    op.create_table(
        "job_pipeline_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("job_posting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipeline_templates.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("job_posting_id", name="uq_job_pipeline_instance_job"),
    )
    op.execute("ALTER TABLE job_pipeline_instances ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON job_pipeline_instances "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY service_role_bypass ON job_pipeline_instances "
        "USING (current_setting('app.bypass_rls', true) = 'true')"
    )

    # --- job_pipeline_stages ---
    op.create_table(
        "job_pipeline_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_pipeline_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("stage_type", sa.String(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(), nullable=False),
        sa.Column("signal_filter", postgresql.JSONB(), nullable=False),
        sa.Column("pass_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("advance_behavior", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("instance_id", "position", name="uq_job_pipeline_stage_position"),
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_stage_type", "job_pipeline_stages",
        f"stage_type IN {STAGE_TYPES}"
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_difficulty", "job_pipeline_stages",
        f"difficulty IN {DIFFICULTIES}"
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_advance_behavior", "job_pipeline_stages",
        f"advance_behavior IN {ADVANCE_BEHAVIORS}"
    )
    op.create_check_constraint(
        "ck_job_pipeline_stages_duration", "job_pipeline_stages",
        "duration_minutes > 0 AND duration_minutes <= 240"
    )
    op.execute("ALTER TABLE job_pipeline_stages ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON job_pipeline_stages "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    op.execute(
        "CREATE POLICY service_role_bypass ON job_pipeline_stages "
        "USING (current_setting('app.bypass_rls', true) = 'true')"
    )


def downgrade() -> None:
    op.drop_table("job_pipeline_stages")
    op.drop_table("job_pipeline_instances")
    op.drop_table("pipeline_template_stages")
    op.execute("DROP INDEX IF EXISTS ix_pipeline_templates_org_unit_default")
    op.drop_table("pipeline_templates")
```

- [ ] **Step 3: Run migration**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus alembic upgrade head
```

Expected: migration applies cleanly.

- [ ] **Step 4: Verify imports + models**

```bash
docker compose run --rm nexus python -c "
from app.models import PipelineTemplate, PipelineTemplateStage, JobPipelineInstance, JobPipelineStage
print('tables:', PipelineTemplate.__tablename__, PipelineTemplateStage.__tablename__, JobPipelineInstance.__tablename__, JobPipelineStage.__tablename__)
print('OK')
"
```

- [ ] **Step 5: Run existing tests (should still pass)**

```bash
docker compose run --rm nexus pytest -x -q
```

Expected: 134 passed.

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0004_pipeline_builder.py app/models.py
git commit -m "feat(pipelines): add 4 tables + ORM models for pipeline builder (Phase 2C.1)"
```

---

## Task 2: Starter Pack + Schemas

**Files:**
- Create: `backend/nexus/app/modules/pipelines/__init__.py`
- Create: `backend/nexus/app/modules/pipelines/starter_pack.py`
- Create: `backend/nexus/app/modules/pipelines/schemas.py`
- Create: `backend/nexus/tests/test_pipelines_starter_pack.py`

- [ ] **Step 1: Create module marker**

Create `backend/nexus/app/modules/pipelines/__init__.py`:

```python
"""Phase 2C.1 — Pipeline Builder module.

Owns pipeline templates (per org unit) and job pipeline instances (per job).
Called from jd.confirm_signals() via auto_apply_pipeline_on_confirmation()."""
```

- [ ] **Step 2: Create starter pack**

Create `backend/nexus/app/modules/pipelines/starter_pack.py`. Copy the full content from the spec's "Starter Pack" section (Section with STARTER_TEMPLATES dict and SYSTEM_FALLBACK_STARTER constant). The file should define exactly 6 templates: `standard_technical`, `fast_track`, `screening_only`, `senior_leadership`, `sales_commercial`, `volume_hiring`.

At the top of the file:

```python
"""Starter pack — hand-written pipeline templates shipped with the product.

These are NOT stored in the database. When a recruiter clicks "Use this starter"
they get a COPY in their org unit's template library (which IS persisted).

The system fallback is used by auto_apply_pipeline_on_confirmation when neither
last-used nor org-unit-default exist."""

from typing import Any, Final
```

And at the bottom:

```python
SYSTEM_FALLBACK_STARTER: Final[str] = "standard_technical"
```

The `STARTER_TEMPLATES` dict goes in between. Refer to the spec for the full content.

- [ ] **Step 3: Write starter pack test**

Create `backend/nexus/tests/test_pipelines_starter_pack.py`:

```python
"""Tests for the starter pack — shape and integrity."""

import pytest

from app.modules.pipelines.starter_pack import STARTER_TEMPLATES, SYSTEM_FALLBACK_STARTER


EXPECTED_KEYS = {
    "standard_technical",
    "fast_track",
    "screening_only",
    "senior_leadership",
    "sales_commercial",
    "volume_hiring",
}


def test_starter_pack_has_six_templates():
    assert set(STARTER_TEMPLATES.keys()) == EXPECTED_KEYS


def test_system_fallback_is_in_pack():
    assert SYSTEM_FALLBACK_STARTER in STARTER_TEMPLATES


def test_every_template_has_required_fields():
    for key, tpl in STARTER_TEMPLATES.items():
        assert "name" in tpl, f"{key} missing name"
        assert "description" in tpl, f"{key} missing description"
        assert "stages" in tpl, f"{key} missing stages"
        assert len(tpl["stages"]) >= 1, f"{key} has no stages"


def test_every_stage_has_required_fields():
    required = {
        "position", "name", "stage_type", "duration_minutes",
        "difficulty", "signal_filter", "pass_criteria", "advance_behavior",
    }
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            missing = required - set(stage.keys())
            assert not missing, f"{key} stage {stage.get('position')} missing {missing}"


def test_stage_positions_are_sequential():
    for key, tpl in STARTER_TEMPLATES.items():
        positions = [s["position"] for s in tpl["stages"]]
        assert positions == list(range(len(positions))), f"{key} positions not sequential: {positions}"


def test_stage_types_are_valid():
    valid = {"phone_screen", "ai_interview", "human_interview", "panel_interview", "take_home"}
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            assert stage["stage_type"] in valid, f"{key} has invalid stage_type: {stage['stage_type']}"


def test_difficulties_are_valid():
    valid = {"easy", "medium", "hard"}
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            assert stage["difficulty"] in valid


def test_advance_behaviors_are_valid():
    valid = {"auto_advance", "manual_review"}
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            assert stage["advance_behavior"] in valid


def test_pass_criteria_discriminated_shape():
    valid_types = {"all_knockouts_pass", "score_threshold", "manual_review"}
    for key, tpl in STARTER_TEMPLATES.items():
        for stage in tpl["stages"]:
            pc = stage["pass_criteria"]
            assert pc["type"] in valid_types
            if pc["type"] == "score_threshold":
                assert "threshold" in pc
                assert isinstance(pc["threshold"], int)
                assert 0 <= pc["threshold"] <= 100
```

- [ ] **Step 4: Run starter pack tests**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_starter_pack.py -v
```

Expected: all pass.

- [ ] **Step 5: Create schemas file**

Create `backend/nexus/app/modules/pipelines/schemas.py`:

```python
"""Pipeline Builder Pydantic schemas.

All enum-style fields use Literal types for strict validation.
Signal filter and pass criteria are JSONB shapes validated via nested models."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Enums ---

StageType = Literal[
    "phone_screen",
    "ai_interview",
    "human_interview",
    "panel_interview",
    "take_home",
]

StageDifficulty = Literal["easy", "medium", "hard"]
AdvanceBehavior = Literal["auto_advance", "manual_review"]

# --- Signal filter ---

SignalFilterType = Literal["competency", "experience", "credential", "behavioral"]
SignalFilterStage = Literal["screen", "interview"]
SignalFilterPriority = Literal["required", "preferred"]


class SignalFilter(BaseModel):
    include_types: list[SignalFilterType]
    include_stages: list[SignalFilterStage]
    include_weights: list[Literal[1, 2, 3]]
    include_priority: list[SignalFilterPriority]


# --- Pass criteria (discriminated union) ---


class PassCriteriaKnockout(BaseModel):
    type: Literal["all_knockouts_pass"]


class PassCriteriaThreshold(BaseModel):
    type: Literal["score_threshold"]
    threshold: int = Field(ge=0, le=100)


class PassCriteriaManual(BaseModel):
    type: Literal["manual_review"]


PassCriteria = PassCriteriaKnockout | PassCriteriaThreshold | PassCriteriaManual


# --- Stage schemas ---


class PipelineStageBase(BaseModel):
    position: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=200)
    stage_type: StageType
    duration_minutes: int = Field(gt=0, le=240)
    difficulty: StageDifficulty
    signal_filter: SignalFilter
    pass_criteria: PassCriteria
    advance_behavior: AdvanceBehavior


class PipelineStageInput(PipelineStageBase):
    """Stage as sent by the frontend when creating/updating."""

    model_config = ConfigDict(extra="forbid")


class PipelineStageResponse(PipelineStageBase):
    """Stage as returned by the API."""

    id: UUID


# --- Template schemas ---


class PipelineTemplateResponse(BaseModel):
    id: UUID
    org_unit_id: UUID
    name: str
    description: str | None = None
    is_default: bool
    from_starter: str | None = None
    stages: list[PipelineStageResponse]
    created_at: datetime
    updated_at: datetime


class CreateTemplateFromScratch(BaseModel):
    source: Literal["scratch"]
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    is_default: bool = False
    stages: list[PipelineStageInput] = Field(min_length=1)

    @model_validator(mode="after")
    def check_positions_sequential(self) -> "CreateTemplateFromScratch":
        positions = sorted(s.position for s in self.stages)
        if positions != list(range(len(positions))):
            raise ValueError("stage positions must be sequential starting at 0")
        return self


class CreateTemplateFromStarter(BaseModel):
    source: Literal["starter"]
    starter_key: str
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    is_default: bool = False


CreateTemplateRequest = CreateTemplateFromScratch | CreateTemplateFromStarter


class UpdateTemplateRequest(BaseModel):
    """Partial update — all fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    stages: list[PipelineStageInput] | None = None

    @model_validator(mode="after")
    def check_positions_if_stages_provided(self) -> "UpdateTemplateRequest":
        if self.stages is not None:
            positions = sorted(s.position for s in self.stages)
            if positions != list(range(len(positions))):
                raise ValueError("stage positions must be sequential starting at 0")
        return self


# --- Starter pack response ---


class StarterTemplate(BaseModel):
    key: str
    name: str
    description: str
    stages: list[PipelineStageBase]


# --- Job pipeline instance ---


class JobPipelineInstanceResponse(BaseModel):
    id: UUID
    job_posting_id: UUID
    source_template_id: UUID | None = None
    source_template_name: str | None = None
    stages: list[PipelineStageResponse]
    created_at: datetime
    updated_at: datetime


class CreateJobPipelineFromTemplate(BaseModel):
    source: Literal["template"]
    template_id: UUID


class CreateJobPipelineFromStarter(BaseModel):
    source: Literal["starter"]
    starter_key: str


class CreateJobPipelineFromScratch(BaseModel):
    source: Literal["scratch"]
    stages: list[PipelineStageInput] = Field(min_length=1)

    @model_validator(mode="after")
    def check_positions_sequential(self) -> "CreateJobPipelineFromScratch":
        positions = sorted(s.position for s in self.stages)
        if positions != list(range(len(positions))):
            raise ValueError("stage positions must be sequential starting at 0")
        return self


CreateJobPipelineRequest = (
    CreateJobPipelineFromTemplate
    | CreateJobPipelineFromStarter
    | CreateJobPipelineFromScratch
)


class UpdateJobPipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[PipelineStageInput] = Field(min_length=1)

    @model_validator(mode="after")
    def check_positions_sequential(self) -> "UpdateJobPipelineRequest":
        positions = sorted(s.position for s in self.stages)
        if positions != list(range(len(positions))):
            raise ValueError("stage positions must be sequential starting at 0")
        return self


class SaveAsTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    is_default: bool = False
```

- [ ] **Step 6: Verify schemas import**

```bash
docker compose run --rm nexus python -c "
from app.modules.pipelines.schemas import (
    PipelineTemplateResponse, CreateTemplateRequest, PipelineStageInput,
    SignalFilter, PassCriteriaKnockout, JobPipelineInstanceResponse,
    StarterTemplate,
)
print('schemas OK')
"
```

- [ ] **Step 7: Commit**

```bash
git add app/modules/pipelines/__init__.py \
        app/modules/pipelines/starter_pack.py \
        app/modules/pipelines/schemas.py \
        tests/test_pipelines_starter_pack.py
git commit -m "feat(pipelines): starter pack + Pydantic schemas (Phase 2C.1)"
```

---

## Task 3: Errors + Authz

**Files:**
- Create: `backend/nexus/app/modules/pipelines/errors.py`
- Create: `backend/nexus/app/modules/pipelines/authz.py`

- [ ] **Step 1: Create errors.py**

```python
"""Custom exceptions for the pipelines module."""


class StarterKeyNotFoundError(Exception):
    """Raised when a request references an unknown starter_key."""

    def __init__(self, starter_key: str) -> None:
        self.starter_key = starter_key
        super().__init__(f"Unknown starter_key: {starter_key}")


class CannotDeleteDefaultError(Exception):
    """Raised when attempting to delete the default template."""

    def __init__(self) -> None:
        super().__init__(
            "Cannot delete the default template. Set another template as "
            "default first, then delete this one."
        )


class NoSourceTemplateError(Exception):
    """Raised when attempting to reset/update-source with no source."""

    def __init__(self) -> None:
        super().__init__(
            "This pipeline has no source template (built from scratch). "
            "Nothing to reset or update."
        )


class JobNotInConfirmedStateError(Exception):
    """Raised when attempting to create a pipeline for a job not in signals_confirmed."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"Pipelines can only be created for jobs in signals_confirmed state. "
            f"This job is in '{status}'."
        )


class PipelineAlreadyExistsError(Exception):
    """Raised when trying to POST /pipeline for a job that already has one."""

    def __init__(self) -> None:
        super().__init__(
            "This job already has a pipeline instance. Use PATCH to update it."
        )
```

- [ ] **Step 2: Create authz.py**

```python
"""Pipeline authorization — ancestry-walking permission checks.

Follows the same pattern as app.modules.jd.authz.require_job_access:
super admin shortcut, ancestry walk, permission check on each ancestor."""

import uuid as uuid_mod
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    JobPipelineInstance,
    JobPosting,
    OrganizationalUnit,
    PipelineTemplate,
)
from app.modules.auth.context import UserContext
from app.modules.jd.authz import _get_org_unit_ancestry


async def require_template_access(
    db: AsyncSession,
    template_id: uuid_mod.UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> PipelineTemplate:
    """Load a template and verify the user has `org_units.{action}` in
    the template's org unit ancestry."""
    result = await db.execute(
        select(PipelineTemplate).where(PipelineTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail="Pipeline template not found")

    if user.is_super_admin:
        return template

    permission = f"org_units.{action}" if action == "manage" else "org_units.manage"
    ancestry = await _get_org_unit_ancestry(db, template.org_unit_id)
    for unit in ancestry:
        if user.has_permission_in_unit(unit.id, permission):
            return template

    raise HTTPException(
        status_code=403,
        detail=f"Missing {permission} in template's org unit ancestry",
    )


async def require_instance_access(
    db: AsyncSession,
    job_id: uuid_mod.UUID,
    user: UserContext,
    action: Literal["view", "manage"],
) -> tuple[JobPosting, JobPipelineInstance | None]:
    """Load a job + its pipeline instance. Verifies jobs.{action} in
    the job's org unit ancestry.

    Returns (job, instance). Instance may be None — callers handle missing
    instances (e.g. GET returns 404, POST creates fresh)."""
    job_result = await db.execute(
        select(JobPosting).where(JobPosting.id == job_id)
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if not user.is_super_admin:
        permission = f"jobs.{action}"
        ancestry = await _get_org_unit_ancestry(db, job.org_unit_id)
        if not any(
            user.has_permission_in_unit(unit.id, permission) for unit in ancestry
        ):
            raise HTTPException(
                status_code=403,
                detail=f"Missing {permission} in job's org unit ancestry",
            )

    instance_result = await db.execute(
        select(JobPipelineInstance).where(
            JobPipelineInstance.job_posting_id == job_id
        )
    )
    instance = instance_result.scalar_one_or_none()
    return job, instance
```

- [ ] **Step 3: Verify imports**

```bash
docker compose run --rm nexus python -c "
from app.modules.pipelines.errors import (
    StarterKeyNotFoundError, CannotDeleteDefaultError, NoSourceTemplateError,
    JobNotInConfirmedStateError, PipelineAlreadyExistsError,
)
from app.modules.pipelines.authz import require_template_access, require_instance_access
print('errors + authz OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/pipelines/errors.py app/modules/pipelines/authz.py
git commit -m "feat(pipelines): errors + ancestry-walking authz helpers"
```

---

## Task 4: Service Layer — Template CRUD

**Files:**
- Create: `backend/nexus/app/modules/pipelines/service.py`

- [ ] **Step 1: Create service.py with imports + template CRUD functions**

```python
"""Pipeline Builder service layer.

Template CRUD (per org unit), instance creation/mutation (per job),
and the auto_apply_pipeline_on_confirmation hook called from jd.confirm_signals.

All functions take an AsyncSession — transaction management is the caller's
responsibility (FastAPI dependency handles commit/rollback)."""

import uuid as uuid_mod
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
    PipelineStageInput,
)
from app.modules.pipelines.starter_pack import (
    STARTER_TEMPLATES,
    SYSTEM_FALLBACK_STARTER,
)

logger = structlog.get_logger()


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


def _stage_input_to_row_dict(
    stage: PipelineStageInput, tenant_id: UUID, template_id: UUID | None = None, instance_id: UUID | None = None
) -> dict:
    """Convert a PipelineStageInput into a dict suitable for a row constructor."""
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


async def _clear_existing_default(db: AsyncSession, org_unit_id: UUID) -> None:
    """Clear `is_default` on any existing default template in this org unit.
    Called before setting a new default to satisfy the partial unique index."""
    await db.execute(
        select(PipelineTemplate)
        .where(
            and_(
                PipelineTemplate.org_unit_id == org_unit_id,
                PipelineTemplate.is_default == True,
            )
        )
    )
    # Update via ORM for simplicity
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
    template.updated_at = datetime.now(tz=None)

    if stages is not None:
        # Delete existing stages
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
    template.updated_at = datetime.now(tz=None)
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
```

- [ ] **Step 2: Commit template CRUD section**

```bash
git add app/modules/pipelines/service.py
git commit -m "feat(pipelines): service layer — template CRUD"
```

---

## Task 5: Service Layer — Job Pipeline Instances + Auto-Apply

**Files:**
- Modify: `backend/nexus/app/modules/pipelines/service.py`

- [ ] **Step 1: Append job pipeline instance functions**

Add to the bottom of `backend/nexus/app/modules/pipelines/service.py`:

```python
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
    instance.updated_at = datetime.now(tz=None)
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
    instance.updated_at = datetime.now(tz=None)
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
    template.updated_at = datetime.now(tz=None)
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
```

- [ ] **Step 2: Verify service imports**

```bash
docker compose run --rm nexus python -c "
from app.modules.pipelines.service import (
    list_templates_for_org_unit, get_template_with_stages,
    create_template_from_scratch, create_template_from_starter,
    update_template, set_template_as_default, delete_template,
    get_job_pipeline_with_stages, create_job_pipeline_from_template,
    create_job_pipeline_from_starter, create_job_pipeline_from_scratch,
    update_job_pipeline_stages, reset_job_pipeline_to_source,
    save_job_pipeline_as_template, update_source_template_from_job,
    auto_apply_pipeline_on_confirmation,
)
print('service OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add app/modules/pipelines/service.py
git commit -m "feat(pipelines): service layer — job instances + auto-apply hook"
```

---

## Task 6: Router + Main Integration

**Files:**
- Create: `backend/nexus/app/modules/pipelines/router.py`
- Modify: `backend/nexus/app/main.py`

- [ ] **Step 1: Create router.py**

```python
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

from typing import Annotated
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
from app.modules.jd.authz import _get_org_unit_ancestry
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
    PipelineStageResponse,
    PipelineTemplateResponse,
    SaveAsTemplateRequest,
    SignalFilter,
    StarterTemplate,
    UpdateJobPipelineRequest,
    UpdateTemplateRequest,
    PipelineStageBase,
)
from app.modules.pipelines.service import (
    auto_apply_pipeline_on_confirmation,
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
    update_job_pipeline_stages,
    update_source_template_from_job,
    update_template,
    update_source_template_from_job as _update_source_template_from_job,
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
    ancestry = await _get_org_unit_ancestry(db, org_unit_id)
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
            stages=[PipelineStageBase(**{
                **stage,
                "signal_filter": SignalFilter(**stage["signal_filter"]),
                "pass_criteria": stage["pass_criteria"],
            }) for stage in tpl["stages"]],
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
            scratch = body  # type: CreateTemplateFromScratch
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
    return _template_to_response(pair[0], pair[1])  # type: ignore[index]


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
    return _template_to_response(pair[0], pair[1])  # type: ignore[index]


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
    instance, stages, source_template = result
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
            instance = await create_job_pipeline_from_template(
                db, job=job, template_id=body.template_id
            )
        elif isinstance(body, CreateJobPipelineFromStarter):
            instance = await create_job_pipeline_from_starter(
                db, job=job, starter_key=body.starter_key
            )
        else:
            scratch = body  # CreateJobPipelineFromScratch
            instance = await create_job_pipeline_from_scratch(
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
    instance, stages, source_template = result
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
    await update_job_pipeline_stages(db, instance=instance, stages=body.stages)
    result = await get_job_pipeline_with_stages(db, job_id)
    instance, stages, source_template = result  # type: ignore[misc]
    return _instance_to_response(instance, stages, source_template)


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
    instance, stages, source_template = result  # type: ignore[misc]
    return _instance_to_response(instance, stages, source_template)


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
    return _template_to_response(pair[0], pair[1])  # type: ignore[index]


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
    template = await require_template_access(db, instance.source_template_id, user, "manage")
    try:
        updated = await update_source_template_from_job(
            db, instance=instance, actor_id=user.user.id
        )
    except NoSourceTemplateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    pair = await get_template_with_stages(db, updated.id)
    return _template_to_response(pair[0], pair[1])  # type: ignore[index]
```

- [ ] **Step 2: Register router in main.py**

Read `app/main.py`. Find the router imports block (around line 74-87) and add after the existing jd import:

```python
from app.modules.pipelines.router import router as pipelines_router
```

Find the router inclusion block (around line 89-103) and add after `application.include_router(jd_router)`:

```python
application.include_router(pipelines_router)
```

- [ ] **Step 3: Run existing tests to verify no regressions**

```bash
docker compose run --rm nexus pytest -x -q
```

Expected: still 134 + new starter pack tests pass.

- [ ] **Step 4: Commit**

```bash
git add app/modules/pipelines/router.py app/main.py
git commit -m "feat(pipelines): HTTP router + main.py registration"
```

---

## Task 7: JD Service Integration — Auto-Apply Hook

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`

- [ ] **Step 1: Add the hook to confirm_signals**

Read `backend/nexus/app/modules/jd/service.py`. Find the `confirm_signals` function. At the very end, after the existing `logger.info("jd.service.signals_confirmed", ...)` call and before `return job`, add:

```python
    # Auto-apply pipeline on signal confirmation.
    # Failures here must NOT block the confirmation — the job is already
    # confirmed. Log the error and continue.
    try:
        from app.modules.pipelines.service import auto_apply_pipeline_on_confirmation

        await auto_apply_pipeline_on_confirmation(
            db, job=job, actor_id=actor_id,
        )
    except Exception as exc:
        logger.error(
            "jd.pipeline_auto_apply_failed",
            job_posting_id=str(job.id),
            exc_info=exc,
        )
        from app.modules.audit.service import log_event
        try:
            await log_event(
                db,
                tenant_id=job.tenant_id,
                actor_id=actor_id,
                actor_email=None,
                action="job_pipeline.auto_apply_failed",
                resource="job_posting",
                resource_id=job.id,
                payload={"error": str(exc)[:500]},
            )
        except Exception:
            pass  # audit log failure should never cascade
```

- [ ] **Step 2: Run JD tests to verify nothing broke**

```bash
docker compose run --rm nexus pytest tests/test_jd_signals.py -x -v
```

Expected: all confirm_signals tests still pass.

- [ ] **Step 3: Commit**

```bash
git add app/modules/jd/service.py
git commit -m "feat(jd): call auto_apply_pipeline_on_confirmation from confirm_signals"
```

---

## Task 8: Backend Tests — Service + Router + Auto-Apply

**Files:**
- Create: `backend/nexus/tests/test_pipelines_service.py`
- Create: `backend/nexus/tests/test_pipelines_router.py`
- Create: `backend/nexus/tests/test_pipelines_auto_apply.py`

- [ ] **Step 1: Create service tests**

Create `backend/nexus/tests/test_pipelines_service.py`. Read `tests/test_jd_signals.py` first to understand the test infrastructure (how `db`, `tenant_id`, `user_id`, test jobs are set up). Mirror that setup.

Write tests covering these scenarios (one test function per scenario):

- `test_create_template_from_scratch_persists_stages` — create a template with 2 stages, query back and verify ordering
- `test_create_template_from_starter_uses_starter_data` — copy `standard_technical`, verify 3 stages persisted with correct stage_type values
- `test_create_template_from_starter_unknown_key_raises` — raises `StarterKeyNotFoundError` for bogus key
- `test_setting_is_default_atomically_clears_existing` — create template A as default, create template B as default, verify A.is_default is now false
- `test_delete_default_template_raises` — raises `CannotDeleteDefaultError`
- `test_delete_non_default_template_succeeds` — deletes cleanly + cascades to stages
- `test_update_template_replaces_stages_atomically` — update template stages from 3 to 2, verify old stages gone and new ones present
- `test_create_job_pipeline_from_template_copies_stages` — verify the instance has identical stage content to the template
- `test_create_job_pipeline_from_starter_no_source_template` — `source_template_id` is None
- `test_create_job_pipeline_from_scratch_no_source_template` — same
- `test_create_job_pipeline_rejects_non_confirmed_job` — raises `JobNotInConfirmedStateError`
- `test_create_job_pipeline_rejects_duplicate` — raises `PipelineAlreadyExistsError`
- `test_update_job_pipeline_replaces_stages` — stages atomically replaced
- `test_reset_restores_from_source_template` — after editing, reset puts stages back to template content
- `test_reset_raises_if_no_source_template` — raises `NoSourceTemplateError`
- `test_save_as_template_creates_library_entry` — new template with copied stages
- `test_update_source_template_writes_back_to_library` — source template stages are updated

Each test should follow this pattern (copy from `test_jd_signals.py`):

```python
import pytest
from uuid import UUID
from app.modules.pipelines import service as pipelines_service
from app.modules.pipelines.schemas import (
    PipelineStageInput, SignalFilter, PassCriteriaKnockout,
)


def _make_stage(position: int = 0, name: str = "Phone Screen") -> PipelineStageInput:
    return PipelineStageInput(
        position=position,
        name=name,
        stage_type="phone_screen",
        duration_minutes=10,
        difficulty="easy",
        signal_filter=SignalFilter(
            include_types=["competency", "experience", "credential", "behavioral"],
            include_stages=["screen"],
            include_weights=[1, 2, 3],
            include_priority=["required", "preferred"],
        ),
        pass_criteria=PassCriteriaKnockout(type="all_knockouts_pass"),
        advance_behavior="auto_advance",
    )


@pytest.mark.asyncio
async def test_create_template_from_scratch_persists_stages(db, tenant_id, user_id, org_unit_id):
    template = await pipelines_service.create_template_from_scratch(
        db,
        tenant_id=tenant_id,
        org_unit_id=org_unit_id,
        created_by=user_id,
        name="Test Template",
        description=None,
        is_default=False,
        stages=[_make_stage(0), _make_stage(1, "AI Interview")],
    )
    pair = await pipelines_service.get_template_with_stages(db, template.id)
    assert pair is not None
    tpl, stages = pair
    assert len(stages) == 2
    assert stages[0].position == 0
    assert stages[1].position == 1
    assert stages[1].name == "AI Interview"
```

Copy this pattern for all 17 test functions listed above, adjusting the arrange/act/assert for each scenario.

- [ ] **Step 2: Run service tests**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_service.py -x -v
```

Expected: all pass.

- [ ] **Step 3: Create router tests**

Create `backend/nexus/tests/test_pipelines_router.py`. Follow the pattern from `tests/test_jd_router.py` (read that file first to see how auth/dependency overrides work).

Write tests for:

- `test_get_starter_pack_returns_six_templates`
- `test_list_templates_requires_auth` — returns 401/403 without auth
- `test_create_template_from_starter_happy_path` — POST with starter_key, returns 201 with stages
- `test_create_template_from_scratch_happy_path`
- `test_create_template_rejects_non_sequential_positions` — 422 Pydantic validation
- `test_set_default_clears_previous_default`
- `test_delete_default_returns_409`
- `test_delete_non_default_returns_204`
- `test_get_job_pipeline_returns_404_when_none`
- `test_create_job_pipeline_rejects_non_confirmed_job_returns_409`
- `test_update_job_pipeline_replaces_stages`
- `test_reset_returns_409_when_built_from_scratch`

Use the same auth/db override helpers from `test_jd_router.py`.

- [ ] **Step 4: Run router tests**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_router.py -x -v
```

Expected: all pass.

- [ ] **Step 5: Create auto-apply tests**

Create `backend/nexus/tests/test_pipelines_auto_apply.py`:

- `test_auto_apply_uses_last_used_template_when_available` — create first job with template X, confirm signals on a second job, verify second job's pipeline uses X
- `test_auto_apply_falls_back_to_org_default_when_no_last_used` — mark a template as default, confirm signals on a new job, verify it uses the default
- `test_auto_apply_falls_back_to_system_starter_when_no_templates` — fresh org unit, confirm signals, verify pipeline uses `standard_technical` stages with `source_template_id = None`
- `test_auto_apply_skipped_when_instance_exists` — create instance manually, call auto_apply, verify it doesn't create a second one
- `test_confirm_signals_succeeds_when_auto_apply_fails` — monkeypatch `auto_apply_pipeline_on_confirmation` to raise, verify confirm_signals still returns success and transitions status

- [ ] **Step 6: Run auto-apply tests**

```bash
docker compose run --rm nexus pytest tests/test_pipelines_auto_apply.py -x -v
```

Expected: all pass.

- [ ] **Step 7: Run full backend suite**

```bash
docker compose run --rm nexus pytest -x -q
```

Expected: 134 existing + all new pipeline tests pass.

- [ ] **Step 8: Commit**

```bash
git add tests/test_pipelines_service.py tests/test_pipelines_router.py tests/test_pipelines_auto_apply.py
git commit -m "test(pipelines): service + router + auto-apply hook tests"
```

---

## Task 9: Frontend API Client + Types

**Files:**
- Create: `frontend/app/lib/api/pipelines.ts`

- [ ] **Step 1: Create pipelines.ts**

```typescript
import { apiFetch } from './client'

// --- Enum types ---

export type StageType =
  | 'phone_screen'
  | 'ai_interview'
  | 'human_interview'
  | 'panel_interview'
  | 'take_home'

export type StageDifficulty = 'easy' | 'medium' | 'hard'
export type AdvanceBehavior = 'auto_advance' | 'manual_review'

// --- Signal filter ---

export type SignalFilter = {
  include_types: ('competency' | 'experience' | 'credential' | 'behavioral')[]
  include_stages: ('screen' | 'interview')[]
  include_weights: (1 | 2 | 3)[]
  include_priority: ('required' | 'preferred')[]
}

// --- Pass criteria discriminated union ---

export type PassCriteria =
  | { type: 'all_knockouts_pass' }
  | { type: 'score_threshold'; threshold: number }
  | { type: 'manual_review' }

// --- Stage ---

export type PipelineStageInput = {
  position: number
  name: string
  stage_type: StageType
  duration_minutes: number
  difficulty: StageDifficulty
  signal_filter: SignalFilter
  pass_criteria: PassCriteria
  advance_behavior: AdvanceBehavior
}

export type PipelineStageResponse = PipelineStageInput & {
  id: string
}

// --- Template ---

export type PipelineTemplate = {
  id: string
  org_unit_id: string
  name: string
  description: string | null
  is_default: boolean
  from_starter: string | null
  stages: PipelineStageResponse[]
  created_at: string
  updated_at: string
}

// --- Starter (no IDs on stages, no template ID) ---

export type StarterTemplate = {
  key: string
  name: string
  description: string
  stages: Omit<PipelineStageInput, never>[]
}

// --- Job pipeline instance ---

export type JobPipelineInstance = {
  id: string
  job_posting_id: string
  source_template_id: string | null
  source_template_name: string | null
  stages: PipelineStageResponse[]
  created_at: string
  updated_at: string
}

// --- Request bodies ---

export type CreateTemplateBody =
  | {
      source: 'scratch'
      name: string
      description: string | null
      is_default: boolean
      stages: PipelineStageInput[]
    }
  | {
      source: 'starter'
      starter_key: string
      name: string
      description: string | null
      is_default: boolean
    }

export type UpdateTemplateBody = {
  name?: string
  description?: string | null
  stages?: PipelineStageInput[]
}

export type CreateJobPipelineBody =
  | { source: 'template'; template_id: string }
  | { source: 'starter'; starter_key: string }
  | { source: 'scratch'; stages: PipelineStageInput[] }

export type UpdateJobPipelineBody = {
  stages: PipelineStageInput[]
}

export type SaveAsTemplateBody = {
  name: string
  description: string | null
  is_default: boolean
}

// --- API methods ---

export const pipelinesApi = {
  // Starter pack
  getStarterPack: (token: string): Promise<StarterTemplate[]> =>
    apiFetch<StarterTemplate[]>('/api/pipeline-templates/starter-pack', { token }),

  // Template library
  listTemplates: (token: string, unitId: string): Promise<PipelineTemplate[]> =>
    apiFetch<PipelineTemplate[]>(`/api/org-units/${unitId}/pipeline-templates`, { token }),

  createTemplate: (
    token: string,
    unitId: string,
    body: CreateTemplateBody,
  ): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/org-units/${unitId}/pipeline-templates`, {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateTemplate: (
    token: string,
    templateId: string,
    body: UpdateTemplateBody,
  ): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/pipeline-templates/${templateId}`, {
      token,
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  setDefault: (token: string, templateId: string): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/pipeline-templates/${templateId}/set-default`, {
      token,
      method: 'POST',
    }),

  deleteTemplate: (token: string, templateId: string): Promise<void> =>
    apiFetch<void>(`/api/pipeline-templates/${templateId}`, {
      token,
      method: 'DELETE',
    }),

  // Job pipeline
  getJobPipeline: (token: string, jobId: string): Promise<JobPipelineInstance | null> =>
    apiFetch<JobPipelineInstance>(`/api/jobs/${jobId}/pipeline`, { token }).catch(
      (err) => {
        if (err?.message?.includes('404') || err?.message?.includes('No pipeline')) {
          return null
        }
        throw err
      },
    ),

  createJobPipeline: (
    token: string,
    jobId: string,
    body: CreateJobPipelineBody,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(`/api/jobs/${jobId}/pipeline`, {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateJobPipeline: (
    token: string,
    jobId: string,
    body: UpdateJobPipelineBody,
  ): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(`/api/jobs/${jobId}/pipeline`, {
      token,
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  resetJobPipeline: (token: string, jobId: string): Promise<JobPipelineInstance> =>
    apiFetch<JobPipelineInstance>(`/api/jobs/${jobId}/pipeline/reset`, {
      token,
      method: 'POST',
    }),

  saveAsTemplate: (
    token: string,
    jobId: string,
    body: SaveAsTemplateBody,
  ): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/jobs/${jobId}/pipeline/save-as-template`, {
      token,
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateSourceTemplate: (token: string, jobId: string): Promise<PipelineTemplate> =>
    apiFetch<PipelineTemplate>(`/api/jobs/${jobId}/pipeline/update-source-template`, {
      token,
      method: 'POST',
    }),
}
```

- [ ] **Step 2: Verify tsc**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add lib/api/pipelines.ts
git commit -m "feat(pipelines): frontend API client + types"
```

---

## Task 10: Frontend Hooks

**Files:**
- Create: `frontend/app/lib/hooks/use-pipeline-templates.ts`
- Create: `frontend/app/lib/hooks/use-starter-pack.ts`
- Create: `frontend/app/lib/hooks/use-job-pipeline.ts`
- Create: `frontend/app/lib/hooks/use-save-pipeline-template.ts`
- Create: `frontend/app/lib/hooks/use-save-job-pipeline.ts`
- Create: `frontend/app/lib/hooks/use-create-job-pipeline.ts`

- [ ] **Step 1: Create use-starter-pack.ts**

```typescript
'use client'

import { useQuery } from '@tanstack/react-query'
import { pipelinesApi, type StarterTemplate } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useStarterPack() {
  return useQuery<StarterTemplate[]>({
    queryKey: ['pipeline-starter-pack'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.getStarterPack(token)
    },
    staleTime: Infinity, // starter pack is static
  })
}
```

- [ ] **Step 2: Create use-pipeline-templates.ts**

```typescript
'use client'

import { useQuery } from '@tanstack/react-query'
import { pipelinesApi, type PipelineTemplate } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function usePipelineTemplates(unitId: string) {
  return useQuery<PipelineTemplate[]>({
    queryKey: ['pipeline-templates', unitId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.listTemplates(token, unitId)
    },
    enabled: !!unitId,
  })
}
```

- [ ] **Step 3: Create use-job-pipeline.ts**

```typescript
'use client'

import { useQuery } from '@tanstack/react-query'
import { pipelinesApi, type JobPipelineInstance } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useJobPipeline(jobId: string) {
  return useQuery<JobPipelineInstance | null>({
    queryKey: ['job-pipeline', jobId],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.getJobPipeline(token, jobId)
    },
    enabled: !!jobId,
  })
}
```

- [ ] **Step 4: Create use-save-pipeline-template.ts**

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  pipelinesApi,
  type CreateTemplateBody,
  type PipelineTemplate,
  type UpdateTemplateBody,
} from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCreateTemplate(unitId: string) {
  const qc = useQueryClient()
  return useMutation<PipelineTemplate, Error, CreateTemplateBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.createTemplate(token, unitId, body)
    },
    onSuccess: () => {
      toast.success('Template created')
      qc.invalidateQueries({ queryKey: ['pipeline-templates', unitId] })
    },
    onError: (err) => toast.error(`Failed to create template: ${err.message}`),
  })
}

export function useUpdateTemplate(unitId: string, templateId: string) {
  const qc = useQueryClient()
  return useMutation<PipelineTemplate, Error, UpdateTemplateBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.updateTemplate(token, templateId, body)
    },
    onSuccess: () => {
      toast.success('Template saved')
      qc.invalidateQueries({ queryKey: ['pipeline-templates', unitId] })
    },
    onError: (err) => toast.error(`Failed to save: ${err.message}`),
  })
}

export function useSetDefault(unitId: string) {
  const qc = useQueryClient()
  return useMutation<PipelineTemplate, Error, string>({
    mutationFn: async (templateId) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.setDefault(token, templateId)
    },
    onSuccess: () => {
      toast.success('Default template updated')
      qc.invalidateQueries({ queryKey: ['pipeline-templates', unitId] })
    },
    onError: (err) => toast.error(`Failed to set default: ${err.message}`),
  })
}

export function useDeleteTemplate(unitId: string) {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: async (templateId) => {
      const token = await getFreshSupabaseToken()
      await pipelinesApi.deleteTemplate(token, templateId)
    },
    onSuccess: () => {
      toast.success('Template deleted')
      qc.invalidateQueries({ queryKey: ['pipeline-templates', unitId] })
    },
    onError: (err) => toast.error(`Failed to delete: ${err.message}`),
  })
}
```

- [ ] **Step 5: Create use-create-job-pipeline.ts**

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  pipelinesApi,
  type CreateJobPipelineBody,
  type JobPipelineInstance,
} from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useCreateJobPipeline(jobId: string) {
  const qc = useQueryClient()
  return useMutation<JobPipelineInstance, Error, CreateJobPipelineBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.createJobPipeline(token, jobId, body)
    },
    onSuccess: () => {
      toast.success('Pipeline created')
      qc.invalidateQueries({ queryKey: ['job-pipeline', jobId] })
    },
    onError: (err) => toast.error(`Failed to create pipeline: ${err.message}`),
  })
}
```

- [ ] **Step 6: Create use-save-job-pipeline.ts**

```typescript
'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  pipelinesApi,
  type JobPipelineInstance,
  type UpdateJobPipelineBody,
} from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useSaveJobPipeline(jobId: string) {
  const qc = useQueryClient()
  return useMutation<JobPipelineInstance, Error, UpdateJobPipelineBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.updateJobPipeline(token, jobId, body)
    },
    onSuccess: () => {
      toast.success('Pipeline saved')
      qc.invalidateQueries({ queryKey: ['job-pipeline', jobId] })
    },
    onError: (err) => toast.error(`Failed to save pipeline: ${err.message}`),
  })
}

export function useResetJobPipeline(jobId: string) {
  const qc = useQueryClient()
  return useMutation<JobPipelineInstance, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return pipelinesApi.resetJobPipeline(token, jobId)
    },
    onSuccess: () => {
      toast.success('Pipeline reset to source template')
      qc.invalidateQueries({ queryKey: ['job-pipeline', jobId] })
    },
    onError: (err) => toast.error(`Failed to reset: ${err.message}`),
  })
}
```

- [ ] **Step 7: tsc check + commit**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
git add lib/hooks/use-*.ts
git commit -m "feat(pipelines): frontend hooks for templates + instances"
```

---

## Task 11: Frontend Components — PipelineFunnel, StageSlab, StageConfigDrawer

**Files:**
- Create: `frontend/app/components/dashboard/pipeline/PipelineFunnel.tsx`
- Create: `frontend/app/components/dashboard/pipeline/StageSlab.tsx`
- Create: `frontend/app/components/dashboard/pipeline/StageConfigDrawer.tsx`
- Create: `frontend/app/components/dashboard/pipeline/SignalFilterEditor.tsx`
- Create: `frontend/app/components/dashboard/pipeline/PassCriteriaEditor.tsx`

- [ ] **Step 1: Create PipelineFunnel.tsx**

```typescript
'use client'

import type { PipelineStageInput } from '@/lib/api/pipelines'
import { StageSlab } from './StageSlab'

type Props = {
  stages: PipelineStageInput[]
  onStageClick?: (index: number) => void
  selectedIndex?: number
}

export function PipelineFunnel({ stages, onStageClick, selectedIndex }: Props) {
  return (
    <div className="flex flex-col items-center gap-3 py-4">
      {stages.map((stage, i) => {
        // Funnel width narrows from top to bottom: 100% at top, 60% at bottom
        const width = 100 - (i * (40 / Math.max(stages.length - 1, 1)))
        return (
          <div
            key={`${i}-${stage.name}`}
            style={{ width: `${width}%`, maxWidth: '600px' }}
            className="relative"
          >
            <StageSlab
              stage={stage}
              selected={selectedIndex === i}
              onClick={() => onStageClick?.(i)}
            />
            {i < stages.length - 1 && (
              <div className="flex justify-center mt-1 text-zinc-400 text-lg">↓</div>
            )}
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Create StageSlab.tsx**

```typescript
'use client'

import type { PipelineStageInput } from '@/lib/api/pipelines'

type Props = {
  stage: PipelineStageInput
  selected?: boolean
  onClick?: () => void
}

const STAGE_TYPE_LABELS: Record<string, string> = {
  phone_screen: '📞 Phone Screen',
  ai_interview: '🤖 AI Interview',
  human_interview: '👤 Human Interview',
  panel_interview: '👥 Panel',
  take_home: '📝 Take-home',
}

const DIFFICULTY_COLORS: Record<string, string> = {
  easy: 'bg-green-50 text-green-700 border-green-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  hard: 'bg-red-50 text-red-700 border-red-200',
}

export function StageSlab({ stage, selected, onClick }: Props) {
  const border = selected ? 'border-blue-500 ring-2 ring-blue-200' : 'border-zinc-200'
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left bg-white border ${border} rounded-lg px-5 py-3 hover:border-blue-400 transition`}
    >
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-zinc-900">{stage.name}</div>
          <div className="text-xs text-zinc-500 mt-0.5">
            {STAGE_TYPE_LABELS[stage.stage_type] ?? stage.stage_type} · {stage.duration_minutes} min
          </div>
        </div>
        <span
          className={`text-xs font-medium px-2 py-0.5 rounded-full border ${DIFFICULTY_COLORS[stage.difficulty] ?? ''}`}
        >
          {stage.difficulty}
        </span>
      </div>
    </button>
  )
}
```

- [ ] **Step 3: Create SignalFilterEditor.tsx**

```typescript
'use client'

import type { SignalFilter } from '@/lib/api/pipelines'

const TYPE_OPTIONS: ('competency' | 'experience' | 'credential' | 'behavioral')[] = [
  'competency',
  'experience',
  'credential',
  'behavioral',
]
const STAGE_OPTIONS: ('screen' | 'interview')[] = ['screen', 'interview']
const WEIGHT_OPTIONS: (1 | 2 | 3)[] = [1, 2, 3]
const PRIORITY_OPTIONS: ('required' | 'preferred')[] = ['required', 'preferred']

type Props = {
  value: SignalFilter
  onChange: (value: SignalFilter) => void
}

export function SignalFilterEditor({ value, onChange }: Props) {
  function toggle<T>(list: T[], item: T): T[] {
    return list.includes(item) ? list.filter((x) => x !== item) : [...list, item]
  }

  return (
    <div className="space-y-3">
      <div>
        <div className="text-xs font-medium text-zinc-700 mb-1">Signal types</div>
        <div className="flex flex-wrap gap-2">
          {TYPE_OPTIONS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() =>
                onChange({ ...value, include_types: toggle(value.include_types, t) })
              }
              className={`text-xs px-2 py-1 rounded border ${
                value.include_types.includes(t)
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-zinc-200 text-zinc-500'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="text-xs font-medium text-zinc-700 mb-1">Signal stages</div>
        <div className="flex flex-wrap gap-2">
          {STAGE_OPTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() =>
                onChange({ ...value, include_stages: toggle(value.include_stages, s) })
              }
              className={`text-xs px-2 py-1 rounded border ${
                value.include_stages.includes(s)
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-zinc-200 text-zinc-500'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="text-xs font-medium text-zinc-700 mb-1">Weights</div>
        <div className="flex flex-wrap gap-2">
          {WEIGHT_OPTIONS.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() =>
                onChange({ ...value, include_weights: toggle(value.include_weights, w) })
              }
              className={`text-xs px-2 py-1 rounded border ${
                value.include_weights.includes(w)
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-zinc-200 text-zinc-500'
              }`}
            >
              w{w}
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="text-xs font-medium text-zinc-700 mb-1">Priority</div>
        <div className="flex flex-wrap gap-2">
          {PRIORITY_OPTIONS.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() =>
                onChange({ ...value, include_priority: toggle(value.include_priority, p) })
              }
              className={`text-xs px-2 py-1 rounded border ${
                value.include_priority.includes(p)
                  ? 'bg-blue-50 border-blue-300 text-blue-700'
                  : 'bg-white border-zinc-200 text-zinc-500'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create PassCriteriaEditor.tsx**

```typescript
'use client'

import type { PassCriteria } from '@/lib/api/pipelines'

type Props = {
  value: PassCriteria
  onChange: (value: PassCriteria) => void
}

export function PassCriteriaEditor({ value, onChange }: Props) {
  return (
    <div className="space-y-2">
      <select
        value={value.type}
        onChange={(e) => {
          const t = e.target.value as PassCriteria['type']
          if (t === 'all_knockouts_pass') onChange({ type: 'all_knockouts_pass' })
          else if (t === 'manual_review') onChange({ type: 'manual_review' })
          else onChange({ type: 'score_threshold', threshold: 70 })
        }}
        className="w-full text-xs border border-zinc-200 rounded px-2 py-1.5"
      >
        <option value="all_knockouts_pass">All knockouts pass</option>
        <option value="score_threshold">Score threshold</option>
        <option value="manual_review">Manual review</option>
      </select>
      {value.type === 'score_threshold' && (
        <input
          type="number"
          min={0}
          max={100}
          value={value.threshold}
          onChange={(e) =>
            onChange({ type: 'score_threshold', threshold: parseInt(e.target.value) || 0 })
          }
          className="w-full text-xs border border-zinc-200 rounded px-2 py-1.5"
          placeholder="Threshold (0-100)"
        />
      )}
    </div>
  )
}
```

- [ ] **Step 5: Create StageConfigDrawer.tsx**

```typescript
'use client'

import type { PipelineStageInput, StageType, StageDifficulty, AdvanceBehavior } from '@/lib/api/pipelines'
import { Button } from '@/components/ui/button'
import { SignalFilterEditor } from './SignalFilterEditor'
import { PassCriteriaEditor } from './PassCriteriaEditor'

type Props = {
  stage: PipelineStageInput
  onChange: (stage: PipelineStageInput) => void
  onClose: () => void
  onDelete?: () => void
}

const STAGE_TYPES: StageType[] = [
  'phone_screen',
  'ai_interview',
  'human_interview',
  'panel_interview',
  'take_home',
]
const DIFFICULTIES: StageDifficulty[] = ['easy', 'medium', 'hard']
const ADVANCE_BEHAVIORS: AdvanceBehavior[] = ['auto_advance', 'manual_review']

export function StageConfigDrawer({ stage, onChange, onClose, onDelete }: Props) {
  function update<K extends keyof PipelineStageInput>(key: K, value: PipelineStageInput[K]) {
    onChange({ ...stage, [key]: value })
  }

  return (
    <aside className="fixed right-0 top-0 h-screen w-96 bg-white border-l border-zinc-200 shadow-xl z-50 overflow-y-auto">
      <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
        <h3 className="text-sm font-semibold">Configure Stage</h3>
        <button onClick={onClose} className="text-zinc-400 hover:text-zinc-900 text-xl leading-none">
          ×
        </button>
      </div>
      <div className="p-5 space-y-4">
        <div>
          <label className="text-xs font-medium text-zinc-700">Name</label>
          <input
            value={stage.name}
            onChange={(e) => update('name', e.target.value)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          />
        </div>

        <div>
          <label className="text-xs font-medium text-zinc-700">Stage type</label>
          <select
            value={stage.stage_type}
            onChange={(e) => update('stage_type', e.target.value as StageType)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          >
            {STAGE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs font-medium text-zinc-700">Duration (minutes)</label>
          <input
            type="number"
            min={1}
            max={240}
            value={stage.duration_minutes}
            onChange={(e) => update('duration_minutes', parseInt(e.target.value) || 1)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          />
        </div>

        <div>
          <label className="text-xs font-medium text-zinc-700">Difficulty</label>
          <select
            value={stage.difficulty}
            onChange={(e) => update('difficulty', e.target.value as StageDifficulty)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          >
            {DIFFICULTIES.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs font-medium text-zinc-700">Advance behavior</label>
          <select
            value={stage.advance_behavior}
            onChange={(e) => update('advance_behavior', e.target.value as AdvanceBehavior)}
            className="mt-1 w-full text-sm border border-zinc-200 rounded px-3 py-2"
          >
            {ADVANCE_BEHAVIORS.map((a) => (
              <option key={a} value={a}>
                {a === 'auto_advance' ? 'Auto-advance on pass' : 'Manual review'}
              </option>
            ))}
          </select>
        </div>

        <div>
          <div className="text-xs font-medium text-zinc-700 mb-1">Signal filter</div>
          <SignalFilterEditor
            value={stage.signal_filter}
            onChange={(sf) => update('signal_filter', sf)}
          />
        </div>

        <div>
          <div className="text-xs font-medium text-zinc-700 mb-1">Pass criteria</div>
          <PassCriteriaEditor
            value={stage.pass_criteria}
            onChange={(pc) => update('pass_criteria', pc)}
          />
        </div>

        {onDelete && (
          <div className="pt-3 border-t border-zinc-100">
            <Button variant="destructive" size="sm" onClick={onDelete}>
              Delete stage
            </Button>
          </div>
        )}
      </div>
    </aside>
  )
}
```

- [ ] **Step 6: tsc check**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
```

- [ ] **Step 7: Commit**

```bash
git add components/dashboard/pipeline/
git commit -m "feat(pipelines): funnel + slab + stage config drawer components"
```

---

## Task 12: Frontend Components — Template Picker + Library Card + Starter Browser

**Files:**
- Create: `frontend/app/components/dashboard/pipeline/TemplatePickerDialog.tsx`
- Create: `frontend/app/components/dashboard/pipeline/StarterPackBrowser.tsx`
- Create: `frontend/app/components/dashboard/pipeline/TemplateLibraryCard.tsx`

- [ ] **Step 1: Create TemplateLibraryCard.tsx**

```typescript
'use client'

import Link from 'next/link'
import type { PipelineTemplate } from '@/lib/api/pipelines'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

type Props = {
  template: PipelineTemplate
  editHref: string
  onSetDefault?: () => void
  onDelete?: () => void
  canManage?: boolean
}

export function TemplateLibraryCard({ template, editHref, onSetDefault, onDelete, canManage = true }: Props) {
  return (
    <div className="bg-white border border-zinc-200 rounded-lg p-5 hover:border-zinc-300 transition">
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-zinc-900">{template.name}</h3>
          {template.is_default && <Badge variant="default">Default</Badge>}
          {template.from_starter && <Badge variant="secondary">Starter</Badge>}
        </div>
      </div>
      {template.description && (
        <p className="text-xs text-zinc-500 mb-3">{template.description}</p>
      )}
      <div className="text-xs text-zinc-600 mb-4">
        {template.stages.length} stage{template.stages.length !== 1 ? 's' : ''} · {template.stages.map((s) => s.name).join(' → ')}
      </div>
      <div className="flex gap-2">
        <Link href={editHref}>
          <Button size="sm" variant="outline">
            Edit
          </Button>
        </Link>
        {canManage && !template.is_default && onSetDefault && (
          <Button size="sm" variant="outline" onClick={onSetDefault}>
            Set as default
          </Button>
        )}
        {canManage && !template.is_default && onDelete && (
          <Button size="sm" variant="destructive" onClick={onDelete}>
            Delete
          </Button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create StarterPackBrowser.tsx**

```typescript
'use client'

import type { StarterTemplate } from '@/lib/api/pipelines'
import { Button } from '@/components/ui/button'
import { useStarterPack } from '@/lib/hooks/use-starter-pack'

type Props = {
  onUse: (starter: StarterTemplate) => void
}

export function StarterPackBrowser({ onUse }: Props) {
  const { data: starters, isLoading } = useStarterPack()

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading starter pack…</div>
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {starters?.map((starter) => (
        <div key={starter.key} className="bg-white border border-zinc-200 rounded-lg p-5">
          <h3 className="text-sm font-semibold text-zinc-900 mb-1">{starter.name}</h3>
          <p className="text-xs text-zinc-500 mb-3">{starter.description}</p>
          <div className="text-xs text-zinc-600 mb-4">
            {starter.stages.length} stage{starter.stages.length !== 1 ? 's' : ''}:{' '}
            {starter.stages.map((s) => s.name).join(' → ')}
          </div>
          <Button size="sm" onClick={() => onUse(starter)}>
            Use this
          </Button>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Create TemplatePickerDialog.tsx**

```typescript
'use client'

import { useState } from 'react'
import type { PipelineTemplate, StarterTemplate } from '@/lib/api/pipelines'
import { Button } from '@/components/ui/button'
import { StarterPackBrowser } from './StarterPackBrowser'
import { usePipelineTemplates } from '@/lib/hooks/use-pipeline-templates'

type Props = {
  orgUnitId: string
  open: boolean
  onClose: () => void
  onPickTemplate: (template: PipelineTemplate) => void
  onPickStarter: (starter: StarterTemplate) => void
}

export function TemplatePickerDialog({
  orgUnitId,
  open,
  onClose,
  onPickTemplate,
  onPickStarter,
}: Props) {
  const [tab, setTab] = useState<'library' | 'starters'>('library')
  const { data: templates } = usePipelineTemplates(orgUnitId)

  if (!open) return null

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200">
          <h2 className="text-sm font-semibold">Pick a pipeline</h2>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-900 text-xl leading-none">
            ×
          </button>
        </div>
        <div className="px-5 pt-3">
          <div className="flex gap-1 border-b border-zinc-200">
            <button
              onClick={() => setTab('library')}
              className={`text-sm px-3 py-2 border-b-2 ${tab === 'library' ? 'border-blue-600 text-blue-600' : 'border-transparent text-zinc-500'}`}
            >
              Your library
            </button>
            <button
              onClick={() => setTab('starters')}
              className={`text-sm px-3 py-2 border-b-2 ${tab === 'starters' ? 'border-blue-600 text-blue-600' : 'border-transparent text-zinc-500'}`}
            >
              Starter pack
            </button>
          </div>
        </div>
        <div className="px-5 py-4 overflow-y-auto flex-1">
          {tab === 'library' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {templates?.length === 0 && (
                <div className="col-span-2 text-sm text-zinc-500">
                  No templates in your library yet. Try the starter pack tab.
                </div>
              )}
              {templates?.map((t) => (
                <div key={t.id} className="bg-zinc-50 border border-zinc-200 rounded-lg p-4">
                  <div className="text-sm font-semibold mb-1">{t.name}</div>
                  <div className="text-xs text-zinc-500 mb-3">
                    {t.stages.map((s) => s.name).join(' → ')}
                  </div>
                  <Button size="sm" onClick={() => onPickTemplate(t)}>
                    Use this
                  </Button>
                </div>
              ))}
            </div>
          )}
          {tab === 'starters' && <StarterPackBrowser onUse={onPickStarter} />}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: tsc + commit**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
git add components/dashboard/pipeline/
git commit -m "feat(pipelines): template picker + library card + starter browser"
```

---

## Task 13: Template Library Page + Template Editor Page

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx`
- Create: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/new/page.tsx`
- Create: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/[templateId]/page.tsx`

- [ ] **Step 1: Create template library page**

Create `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/page.tsx`:

```typescript
'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { TemplateLibraryCard } from '@/components/dashboard/pipeline/TemplateLibraryCard'
import { StarterPackBrowser } from '@/components/dashboard/pipeline/StarterPackBrowser'
import { usePipelineTemplates } from '@/lib/hooks/use-pipeline-templates'
import {
  useCreateTemplate,
  useSetDefault,
  useDeleteTemplate,
} from '@/lib/hooks/use-save-pipeline-template'
import type { StarterTemplate } from '@/lib/api/pipelines'

export default function PipelineTemplatesPage() {
  const params = useParams<{ unitId: string }>()
  const unitId = params.unitId
  const router = useRouter()

  const [showStarters, setShowStarters] = useState(false)
  const { data: templates, isLoading } = usePipelineTemplates(unitId)
  const createMutation = useCreateTemplate(unitId)
  const setDefaultMutation = useSetDefault(unitId)
  const deleteMutation = useDeleteTemplate(unitId)

  function handleUseStarter(starter: StarterTemplate) {
    createMutation.mutate(
      {
        source: 'starter',
        starter_key: starter.key,
        name: starter.name,
        description: starter.description,
        is_default: templates?.length === 0, // first template becomes default
      },
      {
        onSuccess: () => setShowStarters(false),
      },
    )
  }

  function handleDelete(templateId: string) {
    if (confirm('Delete this template? This cannot be undone.')) {
      deleteMutation.mutate(templateId)
    }
  }

  return (
    <div className="max-w-5xl">
      <div className="mb-6">
        <Link
          href={`/settings/org-units/${unitId}`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to org unit
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900">Pipeline Templates</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Reusable interview pipelines for jobs in this org unit.
        </p>
      </div>

      <div className="flex gap-2 mb-6">
        <Button onClick={() => setShowStarters(!showStarters)} variant="outline">
          {showStarters ? 'Hide starter pack' : 'Browse starter pack'}
        </Button>
        <Link href={`/settings/org-units/${unitId}/pipeline-templates/new`}>
          <Button>+ Create custom template</Button>
        </Link>
      </div>

      {showStarters && (
        <div className="mb-6 p-5 bg-zinc-50 rounded-lg border border-zinc-200">
          <h2 className="text-sm font-semibold mb-3">Starter Pack</h2>
          <StarterPackBrowser onUse={handleUseStarter} />
        </div>
      )}

      {isLoading && <div className="text-sm text-zinc-500">Loading templates…</div>}

      {templates && templates.length === 0 && !showStarters && (
        <div className="bg-white border border-dashed border-zinc-300 rounded-lg p-12 text-center">
          <h2 className="text-lg font-semibold text-zinc-900 mb-2">No templates yet</h2>
          <p className="text-sm text-zinc-500 mb-6">
            Browse the starter pack to get going quickly, or create a custom template from scratch.
          </p>
          <Button onClick={() => setShowStarters(true)}>Browse starter pack</Button>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {templates?.map((tpl) => (
          <TemplateLibraryCard
            key={tpl.id}
            template={tpl}
            editHref={`/settings/org-units/${unitId}/pipeline-templates/${tpl.id}`}
            onSetDefault={() => setDefaultMutation.mutate(tpl.id)}
            onDelete={() => handleDelete(tpl.id)}
          />
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create new template page**

Create `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/new/page.tsx`:

```typescript
'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import { StageConfigDrawer } from '@/components/dashboard/pipeline/StageConfigDrawer'
import { useCreateTemplate } from '@/lib/hooks/use-save-pipeline-template'
import type { PipelineStageInput } from '@/lib/api/pipelines'

function makeBlankStage(position: number): PipelineStageInput {
  return {
    position,
    name: 'New Stage',
    stage_type: 'phone_screen',
    duration_minutes: 10,
    difficulty: 'easy',
    signal_filter: {
      include_types: ['competency', 'experience', 'credential', 'behavioral'],
      include_stages: ['screen'],
      include_weights: [1, 2, 3],
      include_priority: ['required', 'preferred'],
    },
    pass_criteria: { type: 'all_knockouts_pass' },
    advance_behavior: 'auto_advance',
  }
}

export default function NewTemplatePage() {
  const params = useParams<{ unitId: string }>()
  const unitId = params.unitId
  const router = useRouter()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [stages, setStages] = useState<PipelineStageInput[]>([makeBlankStage(0)])
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)

  const createMutation = useCreateTemplate(unitId)

  function updateStage(index: number, updated: PipelineStageInput) {
    setStages(stages.map((s, i) => (i === index ? updated : s)))
  }

  function addStage() {
    setStages([...stages, makeBlankStage(stages.length)])
  }

  function deleteStage(index: number) {
    setStages(
      stages.filter((_, i) => i !== index).map((s, i) => ({ ...s, position: i })),
    )
    setSelectedIndex(null)
  }

  function handleSave() {
    if (!name.trim()) {
      toast.error('Template name is required')
      return
    }
    createMutation.mutate(
      {
        source: 'scratch',
        name: name.trim(),
        description: description.trim() || null,
        is_default: false,
        stages,
      },
      {
        onSuccess: () => router.push(`/settings/org-units/${unitId}/pipeline-templates`),
      },
    )
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <Link
          href={`/settings/org-units/${unitId}/pipeline-templates`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to templates
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900">New Template</h1>
      </div>

      <div className="space-y-4 mb-6">
        <div>
          <Label htmlFor="name">Name</Label>
          <Input
            id="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Engineering — Custom Pipeline"
            className="mt-1"
          />
        </div>
        <div>
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional"
            className="mt-1"
            rows={2}
          />
        </div>
      </div>

      <div className="bg-zinc-50 rounded-lg border border-zinc-200 p-6 mb-4">
        <h2 className="text-sm font-semibold mb-3">Stages</h2>
        <PipelineFunnel
          stages={stages}
          onStageClick={setSelectedIndex}
          selectedIndex={selectedIndex ?? undefined}
        />
        <div className="flex justify-center mt-4">
          <Button variant="outline" size="sm" onClick={addStage}>
            + Add stage
          </Button>
        </div>
      </div>

      <div className="flex gap-2">
        <Button onClick={handleSave} disabled={createMutation.isPending}>
          {createMutation.isPending ? 'Saving…' : 'Save template'}
        </Button>
        <Link href={`/settings/org-units/${unitId}/pipeline-templates`}>
          <Button variant="outline">Cancel</Button>
        </Link>
      </div>

      {selectedIndex !== null && (
        <StageConfigDrawer
          stage={stages[selectedIndex]}
          onChange={(updated) => updateStage(selectedIndex, updated)}
          onClose={() => setSelectedIndex(null)}
          onDelete={stages.length > 1 ? () => deleteStage(selectedIndex) : undefined}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 3: Create edit template page**

Create `frontend/app/app/(dashboard)/settings/org-units/[unitId]/pipeline-templates/[templateId]/page.tsx`:

```typescript
'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import { StageConfigDrawer } from '@/components/dashboard/pipeline/StageConfigDrawer'
import { usePipelineTemplates } from '@/lib/hooks/use-pipeline-templates'
import { useUpdateTemplate } from '@/lib/hooks/use-save-pipeline-template'
import type { PipelineStageInput } from '@/lib/api/pipelines'

function makeBlankStage(position: number): PipelineStageInput {
  return {
    position,
    name: 'New Stage',
    stage_type: 'phone_screen',
    duration_minutes: 10,
    difficulty: 'easy',
    signal_filter: {
      include_types: ['competency', 'experience', 'credential', 'behavioral'],
      include_stages: ['screen'],
      include_weights: [1, 2, 3],
      include_priority: ['required', 'preferred'],
    },
    pass_criteria: { type: 'all_knockouts_pass' },
    advance_behavior: 'auto_advance',
  }
}

export default function EditTemplatePage() {
  const params = useParams<{ unitId: string; templateId: string }>()
  const unitId = params.unitId
  const templateId = params.templateId
  const router = useRouter()

  const { data: templates } = usePipelineTemplates(unitId)
  const template = templates?.find((t) => t.id === templateId)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [stages, setStages] = useState<PipelineStageInput[]>([])
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)

  const updateMutation = useUpdateTemplate(unitId, templateId)

  useEffect(() => {
    if (template) {
      setName(template.name)
      setDescription(template.description ?? '')
      setStages(template.stages.map(({ id, ...rest }) => rest))
    }
  }, [template])

  if (!template) {
    return <div className="text-sm text-zinc-500">Loading template…</div>
  }

  function updateStage(index: number, updated: PipelineStageInput) {
    setStages(stages.map((s, i) => (i === index ? updated : s)))
  }
  function addStage() {
    setStages([...stages, makeBlankStage(stages.length)])
  }
  function deleteStage(index: number) {
    setStages(stages.filter((_, i) => i !== index).map((s, i) => ({ ...s, position: i })))
    setSelectedIndex(null)
  }
  function handleSave() {
    if (!name.trim()) {
      toast.error('Template name is required')
      return
    }
    updateMutation.mutate(
      { name: name.trim(), description: description.trim() || null, stages },
      { onSuccess: () => router.push(`/settings/org-units/${unitId}/pipeline-templates`) },
    )
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <Link
          href={`/settings/org-units/${unitId}/pipeline-templates`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to templates
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900">Edit Template</h1>
      </div>

      <div className="space-y-4 mb-6">
        <div>
          <Label htmlFor="name">Name</Label>
          <Input id="name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
        </div>
        <div>
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="mt-1"
            rows={2}
          />
        </div>
      </div>

      <div className="bg-zinc-50 rounded-lg border border-zinc-200 p-6 mb-4">
        <h2 className="text-sm font-semibold mb-3">Stages</h2>
        <PipelineFunnel
          stages={stages}
          onStageClick={setSelectedIndex}
          selectedIndex={selectedIndex ?? undefined}
        />
        <div className="flex justify-center mt-4">
          <Button variant="outline" size="sm" onClick={addStage}>
            + Add stage
          </Button>
        </div>
      </div>

      <div className="flex gap-2">
        <Button onClick={handleSave} disabled={updateMutation.isPending}>
          {updateMutation.isPending ? 'Saving…' : 'Save changes'}
        </Button>
        <Link href={`/settings/org-units/${unitId}/pipeline-templates`}>
          <Button variant="outline">Cancel</Button>
        </Link>
      </div>

      {selectedIndex !== null && (
        <StageConfigDrawer
          stage={stages[selectedIndex]}
          onChange={(updated) => updateStage(selectedIndex, updated)}
          onClose={() => setSelectedIndex(null)}
          onDelete={stages.length > 1 ? () => deleteStage(selectedIndex) : undefined}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 4: tsc check + commit**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
git add app/\(dashboard\)/settings/org-units/\[unitId\]/pipeline-templates/
git commit -m "feat(pipelines): template library + editor pages"
```

---

## Task 14: Job Pipeline Page + "Build Pipeline" Button

**Files:**
- Create: `frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx`
- Modify: `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`

- [ ] **Step 1: Create the job pipeline page**

Create `frontend/app/app/(dashboard)/jobs/[jobId]/pipeline/page.tsx`:

```typescript
'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import { StageConfigDrawer } from '@/components/dashboard/pipeline/StageConfigDrawer'
import { TemplatePickerDialog } from '@/components/dashboard/pipeline/TemplatePickerDialog'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useCreateJobPipeline } from '@/lib/hooks/use-create-job-pipeline'
import { useSaveJobPipeline, useResetJobPipeline } from '@/lib/hooks/use-save-job-pipeline'
import type { PipelineStageInput, PipelineTemplate, StarterTemplate } from '@/lib/api/pipelines'

function makeBlankStage(position: number): PipelineStageInput {
  return {
    position,
    name: 'New Stage',
    stage_type: 'phone_screen',
    duration_minutes: 10,
    difficulty: 'easy',
    signal_filter: {
      include_types: ['competency', 'experience', 'credential', 'behavioral'],
      include_stages: ['screen'],
      include_weights: [1, 2, 3],
      include_priority: ['required', 'preferred'],
    },
    pass_criteria: { type: 'all_knockouts_pass' },
    advance_behavior: 'auto_advance',
  }
}

export default function JobPipelinePage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId

  const { data: job } = useJob(jobId)
  const { data: pipeline, isLoading } = useJobPipeline(jobId)
  const saveMutation = useSaveJobPipeline(jobId)
  const resetMutation = useResetJobPipeline(jobId)
  const createMutation = useCreateJobPipeline(jobId)

  const [stages, setStages] = useState<PipelineStageInput[]>([])
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)

  useEffect(() => {
    if (pipeline) {
      setStages(pipeline.stages.map(({ id, ...rest }) => rest))
    }
  }, [pipeline])

  if (isLoading || !job) {
    return <div className="text-sm text-zinc-500">Loading pipeline…</div>
  }

  if (!pipeline) {
    return (
      <div className="max-w-4xl">
        <Link
          href={`/jobs/${jobId}`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to job
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900 mb-2">No pipeline yet</h1>
        <p className="text-sm text-zinc-500 mb-6">
          Pick a template from your library, the starter pack, or build from scratch.
        </p>
        <Button onClick={() => setPickerOpen(true)}>Pick a pipeline</Button>
        {pickerOpen && (
          <TemplatePickerDialog
            orgUnitId={job.org_unit_id}
            open={pickerOpen}
            onClose={() => setPickerOpen(false)}
            onPickTemplate={(t) =>
              createMutation.mutate(
                { source: 'template', template_id: t.id },
                { onSuccess: () => setPickerOpen(false) },
              )
            }
            onPickStarter={(s) =>
              createMutation.mutate(
                { source: 'starter', starter_key: s.key },
                { onSuccess: () => setPickerOpen(false) },
              )
            }
          />
        )}
      </div>
    )
  }

  function updateStage(index: number, updated: PipelineStageInput) {
    setStages(stages.map((s, i) => (i === index ? updated : s)))
  }
  function addStage() {
    setStages([...stages, makeBlankStage(stages.length)])
  }
  function deleteStage(index: number) {
    setStages(stages.filter((_, i) => i !== index).map((s, i) => ({ ...s, position: i })))
    setSelectedIndex(null)
  }
  function handleSave() {
    saveMutation.mutate({ stages })
  }
  function handleReset() {
    if (confirm('Discard your edits and reset to the source template?')) {
      resetMutation.mutate()
    }
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <Link
          href={`/jobs/${jobId}`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to job
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900">{job.title}</h1>
        <p className="text-sm text-zinc-500">
          Pipeline{pipeline.source_template_name && ` · from "${pipeline.source_template_name}"`}
        </p>
      </div>

      <div className="flex flex-wrap gap-2 mb-6">
        <Button onClick={handleSave} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? 'Saving…' : 'Save'}
        </Button>
        <Button variant="outline" onClick={() => setPickerOpen(true)}>
          Swap template
        </Button>
        {pipeline.source_template_id && (
          <Button variant="outline" onClick={handleReset} disabled={resetMutation.isPending}>
            Reset to source
          </Button>
        )}
      </div>

      <div className="bg-zinc-50 rounded-lg border border-zinc-200 p-6 mb-4">
        <h2 className="text-sm font-semibold mb-3">Stages</h2>
        <PipelineFunnel
          stages={stages}
          onStageClick={setSelectedIndex}
          selectedIndex={selectedIndex ?? undefined}
        />
        <div className="flex justify-center mt-4">
          <Button variant="outline" size="sm" onClick={addStage}>
            + Add stage
          </Button>
        </div>
      </div>

      {selectedIndex !== null && (
        <StageConfigDrawer
          stage={stages[selectedIndex]}
          onChange={(updated) => updateStage(selectedIndex, updated)}
          onClose={() => setSelectedIndex(null)}
          onDelete={stages.length > 1 ? () => deleteStage(selectedIndex) : undefined}
        />
      )}

      {pickerOpen && (
        <TemplatePickerDialog
          orgUnitId={job.org_unit_id}
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          onPickTemplate={(t) => {
            toast.error('Swapping template not yet implemented — delete and recreate')
            setPickerOpen(false)
          }}
          onPickStarter={(s) => {
            toast.error('Swapping template not yet implemented — delete and recreate')
            setPickerOpen(false)
          }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 2: Add "Build Pipeline" button to job review page**

Read `frontend/app/app/(dashboard)/jobs/[jobId]/page.tsx`. At the top of the component (after `useJob`, `useJobStatusStream`, etc.), add:

```typescript
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
// ... existing imports
```

Inside the component body, after the existing `useJob` call:

```typescript
const { data: pipeline } = useJobPipeline(jobId)
```

In the JSX, find the header section with the job title. After the title, add a conditional button section. The header currently looks like:

```tsx
<div className="mb-6">
  <Link href="/jobs" ...>← Job Descriptions</Link>
  <h1 className="text-2xl font-semibold text-zinc-900">{job.title}</h1>
</div>
```

Replace with:

```tsx
<div className="mb-6">
  <Link
    href="/jobs"
    className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
  >
    ← Job Descriptions
  </Link>
  <div className="flex items-center justify-between">
    <h1 className="text-2xl font-semibold text-zinc-900">{job.title}</h1>
    {job.status === 'signals_confirmed' && job.can_manage && (
      <Link href={`/jobs/${jobId}/pipeline`}>
        <Button variant={pipeline ? 'outline' : 'default'}>
          {pipeline ? 'View Pipeline' : 'Build Pipeline'}
        </Button>
      </Link>
    )}
  </div>
</div>
```

Make sure `Button` is imported from `@/components/ui/button`.

- [ ] **Step 3: tsc check + commit**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
git add app/\(dashboard\)/jobs/\[jobId\]/pipeline/page.tsx app/\(dashboard\)/jobs/\[jobId\]/page.tsx
git commit -m "feat(pipelines): job pipeline page + Build Pipeline button"
```

---

## Task 15: Org Unit Settings Integration + Frontend Test + Final Verification

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`
- Create: `frontend/app/tests/components/PipelineFunnel.test.tsx`

- [ ] **Step 1: Add "Pipeline Templates" section to org unit detail page**

Read the existing `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`. Find the section where members or sub-units are displayed (after the main content but before the footer/delete). Add a new section card:

```tsx
<div className="bg-white border border-zinc-200 rounded-lg p-5 mb-4">
  <div className="flex items-center justify-between">
    <div>
      <h3 className="text-sm font-semibold text-zinc-900">Pipeline Templates</h3>
      <p className="text-xs text-zinc-500 mt-1">
        Reusable interview pipelines for jobs in this org unit
      </p>
    </div>
    <Link href={`/settings/org-units/${unitId}/pipeline-templates`}>
      <Button variant="outline" size="sm">Manage templates</Button>
    </Link>
  </div>
</div>
```

Position this section near the other admin sections (between members and sub-units, or below both — follow the page's existing visual hierarchy).

- [ ] **Step 2: Create the PipelineFunnel test**

Create `frontend/app/tests/components/PipelineFunnel.test.tsx`:

```typescript
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'

import { PipelineFunnel } from '@/components/dashboard/pipeline/PipelineFunnel'
import type { PipelineStageInput } from '@/lib/api/pipelines'

function makeStage(position: number, name: string): PipelineStageInput {
  return {
    position,
    name,
    stage_type: 'phone_screen',
    duration_minutes: 10,
    difficulty: 'easy',
    signal_filter: {
      include_types: ['competency'],
      include_stages: ['screen'],
      include_weights: [1, 2, 3],
      include_priority: ['required'],
    },
    pass_criteria: { type: 'all_knockouts_pass' },
    advance_behavior: 'auto_advance',
  }
}

describe('PipelineFunnel', () => {
  it('renders all stages in order', () => {
    const stages = [
      makeStage(0, 'Phone Screen'),
      makeStage(1, 'AI Interview'),
      makeStage(2, 'Panel'),
    ]
    render(<PipelineFunnel stages={stages} />)
    expect(screen.getByText('Phone Screen')).toBeInTheDocument()
    expect(screen.getByText('AI Interview')).toBeInTheDocument()
    expect(screen.getByText('Panel')).toBeInTheDocument()
  })

  it('calls onStageClick with the index when a stage is clicked', async () => {
    const user = userEvent.setup()
    const stages = [makeStage(0, 'Phone Screen'), makeStage(1, 'AI Interview')]
    const onClick = vi.fn()
    render(<PipelineFunnel stages={stages} onStageClick={onClick} />)
    await user.click(screen.getByText('AI Interview'))
    expect(onClick).toHaveBeenCalledWith(1)
  })

  it('renders nothing when stages is empty', () => {
    const { container } = render(<PipelineFunnel stages={[]} />)
    expect(container.firstChild?.childNodes.length).toBe(0)
  })
})
```

- [ ] **Step 3: Run frontend tests**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npm run test
```

Expected: existing 10 + new 3 = 13 tests pass.

- [ ] **Step 4: Full backend test suite**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest -x -q
```

Expected: all pass (134 existing + all new pipeline tests).

- [ ] **Step 5: Full frontend type-check + lint + build**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
npx tsc --noEmit
npm run lint
npm run build
```

Expected: all clean.

- [ ] **Step 6: Final commit**

```bash
git add app/\(dashboard\)/settings/org-units/\[unitId\]/page.tsx tests/components/PipelineFunnel.test.tsx
git commit -m "feat(pipelines): org unit settings integration + PipelineFunnel component test"
```

- [ ] **Step 7: Manual smoke test (optional but recommended)**

1. Restart backend: `docker compose up --build nexus nexus-worker`
2. Open the frontend: `npm run dev` from `frontend/app`
3. Navigate to `Settings → Org Units → [your unit] → Pipeline Templates`
4. Click "Browse starter pack", pick "Standard Technical", verify it appears in the library
5. Create a new job, fill in metadata + JD, wait for extraction, confirm signals
6. Navigate back to the job — a "Build Pipeline" button should appear
7. Click "Build Pipeline" — if auto-apply succeeded, you see "View Pipeline" instead and the pipeline page loads with the template's stages
8. Click a stage — the config drawer opens
9. Edit a stage name, click "Save" on the page
10. Refresh — changes persist
