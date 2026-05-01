# Phase 4 — Modular Monolith Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split `app/models.py` into per-module `models.py` files, hoist late function-body imports to module-top, verify no inverted edges remain across `auth`/`audit`/`notifications`, and introduce module-public-API discipline backed by an AST-walking lint test.

**Architecture:** Single PR off `feat/phase-4-modular-monolith-refactor` (branched from `main` after PR #4 merged). Mechanical refactor — zero behavior change, lowest risk in the umbrella migration, highest line count. Splits land as ~7 sub-commits inside one PR: 4a (model split + transitional shim + `Base.registry.configure()` at startup), 4b (hoist late imports), 4c (verify inverted-edge cleanliness across the foundational trio), 4d-1 (`__init__.py` public-API exports), 4d-2 (cross-module import sweep + retire shim), 4d-3 (AST lint test), 4d-4 (CLAUDE.md "Module public API" section).

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async ORM, FastAPI, pytest, ruff, mypy. No new runtime deps.

**Spec reference:** `docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md` § Phase 4 (lines 520–615).

**Cycle pre-analysis (resolved during planning, 2026-05-01):**

| Late import | Apparent cycle | Actual cycle? | Plan |
|---|---|---|---|
| `jd/service.py:389` → `question_bank.service.recompute_and_persist_stale` | Comment claims `(jd ← question_bank ← jd)` | **No.** `qb/service.py` imports only `app.models` + `app.modules.audit.*`; `qb/__init__.py` is empty so it does not pull in `qb/refine.py` (which is the only qb file that imports `jd.authz`) | Hoist directly. The comment is stale from before qb's internal split. |
| `pipelines/service.py:845` → `question_bank.service.recompute_and_persist_stale` | Same shape as above | **No.** Same reasoning. | Hoist directly. |
| `jd/service.py:495` → `pipelines.categories` | jd is a peer of pipelines | **No.** `pipelines/categories.py` has zero imports from any sibling module. | Hoist directly. |
| `auth/router.py:229` → `org_units.service.create_org_unit` | auth importing "upward" into org_units | **No cycle.** `org_units/service.py` imports only `audit.*`; no path back to auth. The edge is "upward" but acyclic. Spec's 4c rule says foundational modules (auth/audit/notifications) should only import each other or non-modules. This is the only auth-→-domain edge and gets hoisted, not extracted. | Hoist; document the directional dependency in CLAUDE.md. |

**Escalation rule** (per spec § Phase 4b): if hoisting any import surfaces a true Python-level cycle that pytest catches, extract the shared symbol to a flat `app/shared/<topic>.py` namespace. Do not pre-create `app/shared/` — only on demand.

---

## Sub-commit map (target end-state)

| Sub-commit | Subject | Stage |
|---|---|---|
| 1 | `refactor(models): split app/models.py per module + transitional shim + Base.registry.configure()` | B |
| 2 | `refactor(imports): hoist late function-body imports to module top` | C |
| 3 | `chore(boundaries): verify foundational module trio (auth/audit/notifications) has no inverted edges` | D (may be no-op commit if 4c is purely doc-only) |
| 4 | `refactor(modules): add __init__.py public-API exports for every domain module` | E (4d-1) |
| 5 | `refactor(imports): sweep cross-module imports to use public API; retire app/models.py shim` | E (4d-2) |
| 6 | `test(boundaries): add AST-walking module-boundary lint test` | E (4d-3) |
| 7 | `docs(claude): add "Module public API" section to backend/nexus/CLAUDE.md` | E (4d-4) |

If 4c surfaces zero violations and produces no diff, that commit is skipped (not faked with an empty commit). The PR may have 6–7 sub-commits.

---

## Stage A — Pre-flight verification

### Task 1: Snapshot pytest baseline before any change

**Files:** none (reads only)

- [ ] **Step 1.1: Confirm git state**

Run:
```bash
cd /home/ishant/Projects/ProjectX
git status
git log --oneline -3
git rev-parse --abbrev-ref HEAD
```

Expected:
- Working tree clean
- HEAD commit `3985bed Merge pull request #4 from IshantPundir/feat/phase-3c2-interview-engine`
- Current branch `feat/phase-4-modular-monolith-refactor`

If any of those don't match, STOP and resync. The plan assumes an unmodified Phase 3 base.

- [ ] **Step 1.2: Snapshot pytest baseline**

Run:
```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase4_baseline_fails.txt
wc -l /tmp/phase4_baseline_fails.txt
cat /tmp/phase4_baseline_fails.txt
```

Expected: `642 passed, 9 failed` (per the handoff). The 9 failures are the post-Phase-3 baseline — environment-driven (missing OPENAI_API_KEY, S3 creds, etc.) and **not** introduced by this branch. Save the failure list for Stage F's diff.

If the count differs by more than ±2 tests, STOP. The plan's "preserve baseline" gate breaks if the starting line is off.

- [ ] **Step 1.3: Note ruff + mypy baseline**

Run:
```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus ruff check . 2>&1 | tail -3 > /tmp/phase4_baseline_ruff.txt
docker compose run --rm nexus mypy app/ 2>&1 | tail -3 > /tmp/phase4_baseline_mypy.txt
cat /tmp/phase4_baseline_ruff.txt
cat /tmp/phase4_baseline_mypy.txt
```

Expected: clean (zero violations). Phase 4 must keep both clean.

- [ ] **Step 1.4: Confirm `Base` already lives in `app/database.py`**

The spec text says "Base moves from `app/models.py` to `app/database.py`." That move has already been made in an earlier batch — verify, don't redo.

Run:
```bash
grep -n "class Base" /home/ishant/Projects/ProjectX/backend/nexus/app/database.py
grep -n "from app.database import Base" /home/ishant/Projects/ProjectX/backend/nexus/app/models.py
```

Expected:
- `app/database.py:51:class Base(DeclarativeBase):`
- `app/models.py:15:from app.database import Base`

If either grep returns nothing, the assumption is wrong — STOP and re-read the codebase before proceeding.

---

## Stage B — 4a: Per-module models split + transitional shim + `Base.registry.configure()` at startup

**Stage goal:** Each domain module owns its ORM classes. `app/models.py` becomes a thin re-export shim (deleted in Stage E). `Base.registry.configure()` runs at app startup so any string-FK resolution failure is loud at boot, not at first request.

**Critical invariant:** No Python-side cross-imports between model files. All FK references already use string form (e.g. `ForeignKey("clients.id")`). After the split, each `app/modules/<m>/models.py` imports only `from app.database import Base` plus stdlib + SQLAlchemy + dialect helpers.

**Source line ranges in current `app/models.py`** (consulted while extracting):

| Class | Lines |
|---|---|
| `Client` | 18–34 |
| `User` | 37–62 |
| `OrganizationalUnit` | 65–86 |
| `Role` | 89–102 |
| `UserRoleAssignment` | 105–118 |
| `UserInvite` | 121–135 |
| `AuditLog` | 138–164 |
| `JobPosting` | 167–201 |
| `JobPostingSignalSnapshot` | 204–223 |
| `PipelineTemplate` | 226–261 |
| `PipelineTemplateStage` | 264–298 |
| `JobPipelineInstance` | 301–334 |
| `JobPipelineStage` | 337–377 |
| `PipelineStageParticipant` | 380–412 |
| `StageQuestionBank` | 415–472 |
| `StageQuestion` | 475–528 |
| `Session` | 531–588 |
| `Candidate` | 591–647 |
| `CandidateJobAssignment` | 650–698 |
| `CandidateStageProgress` | 701–735 |
| `CandidateSessionToken` | 738–766 |
| _(no engine_dispatch_tokens / engine_token_uses — already retired in Phase 3)_ | — |

**Module-to-models mapping (per spec § Phase 4a):**

| Destination file | Models | Source lines |
|---|---|---|
| `app/modules/auth/models.py` | `User`, `UserRoleAssignment`, `UserInvite` | 37–62, 105–118, 121–135 |
| `app/modules/roles/models.py` | `Role` | 89–102 |
| `app/modules/org_units/models.py` | `Client`, `OrganizationalUnit` | 18–34, 65–86 |
| `app/modules/audit/models.py` | `AuditLog` | 138–164 |
| `app/modules/jd/models.py` | `JobPosting`, `JobPostingSignalSnapshot` | 167–201, 204–223 |
| `app/modules/pipelines/models.py` | `PipelineTemplate`, `PipelineTemplateStage`, `JobPipelineInstance`, `JobPipelineStage`, `PipelineStageParticipant` | 226–412 |
| `app/modules/question_bank/models.py` | `StageQuestionBank`, `StageQuestion` | 415–528 |
| `app/modules/candidates/models.py` | `Candidate`, `CandidateJobAssignment`, `CandidateStageProgress` | 591–735 |
| `app/modules/session/models.py` | `Session`, `CandidateSessionToken` | 531–588, 738–766 |

### Task 2: Create `app/modules/auth/models.py`

**Files:**
- Create: `backend/nexus/app/modules/auth/models.py`

- [ ] **Step 2.1: Read source ranges**

Read `backend/nexus/app/models.py` lines 37–62 (`User`), 105–118 (`UserRoleAssignment`), 121–135 (`UserInvite`). Confirm class bodies are intact and use only string FKs. (None of these classes uses `relationship()`.)

- [ ] **Step 2.2: Create `auth/models.py` with verbatim class bodies**

Write `backend/nexus/app/modules/auth/models.py`:

```python
"""Auth-owned ORM models.

Tables: users, user_role_assignments, user_invites.

FK references to other modules' tables (e.g. clients, organizational_units, roles)
are string-based — no Python-side cross-imports — so cross-module model files do
not need to be loaded for SQLAlchemy mapper configuration to succeed.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """Dashboard user — identity only. Roles live on user_role_assignments."""
    __tablename__ = "users"
    __table_args__ = (
        # Partial unique index: enforces auth_user_id uniqueness only among
        # non-soft-deleted rows. Lets a re-invitation of the same Supabase
        # Auth identity to a fresh tenant succeed after the prior tenant was
        # soft-deleted (and its users were cascade-soft-deleted). See
        # migration 0022 for the full rationale.
        Index(
            "users_auth_user_id_active_uniq",
            "auth_user_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    auth_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRoleAssignment(Base):
    """Junction: user assigned to org unit with a specific role."""
    __tablename__ = "user_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "org_unit_id", "role_id", name="unique_user_unit_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class UserInvite(Base):
    """Invite to join a tenant — no role info, just email + token."""
    __tablename__ = "user_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    projectx_admin_id: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("user_invites.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW() + INTERVAL '72 hours'"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 2.3: Verify the file imports cleanly in isolation**

Run:
```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus python -c "from app.modules.auth.models import User, UserRoleAssignment, UserInvite; print('ok:', [c.__tablename__ for c in (User, UserRoleAssignment, UserInvite)])"
```

Expected: `ok: ['users', 'user_role_assignments', 'user_invites']` — no errors.

If it errors: fix the import path / class body before moving on. **Do not commit yet.**

### Task 3: Create `app/modules/roles/models.py`

**Files:**
- Create: `backend/nexus/app/modules/roles/models.py`

- [ ] **Step 3.1: Create `roles/models.py`**

Write `backend/nexus/app/modules/roles/models.py`:

```python
"""Role definitions — system + tenant-custom."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Role(Base):
    """Role definition — system or tenant-custom."""
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="roles_unique_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default="''")
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 3.2: Verify**

```bash
docker compose run --rm nexus python -c "from app.modules.roles.models import Role; print('ok:', Role.__tablename__)"
```

Expected: `ok: roles`.

### Task 4: Create `app/modules/org_units/models.py`

**Files:**
- Create: `backend/nexus/app/modules/org_units/models.py`

- [ ] **Step 4.1: Create the file**

Write `backend/nexus/app/modules/org_units/models.py`:

```python
"""Tenant + org-unit ORM models.

`Client` is the tenant root. `OrganizationalUnit` is the hierarchical
container (company → division → region → team etc).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Client(Base):
    """Tenant root."""
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, server_default="''")
    industry: Mapped[str] = mapped_column(Text, server_default="''")
    size: Mapped[str] = mapped_column(Text, server_default="''")
    logo_url: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String, nullable=False, server_default="trial")
    onboarding_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    super_admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", deferrable=True, initially="DEFERRED"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationalUnit(Base):
    __tablename__ = "organizational_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    parent_unit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    unit_type: Mapped[str] = mapped_column(String, nullable=False)
    is_root: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    company_profile: Mapped[dict | None] = mapped_column(JSONB)
    company_profile_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company_profile_completed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # Per-unit-type metadata (region offices, division description, team focus,
    # etc). Mapped to DB column "metadata" but exposed on the ORM as
    # `unit_metadata` because SQLAlchemy reserves `metadata` on Base for the
    # MetaData registry. API layer re-aliases to `metadata` for clients.
    unit_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    deletable_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    admin_delete_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 4.2: Verify**

```bash
docker compose run --rm nexus python -c "from app.modules.org_units.models import Client, OrganizationalUnit; print('ok:', Client.__tablename__, OrganizationalUnit.__tablename__)"
```

Expected: `ok: clients organizational_units`.

### Task 5: Create `app/modules/audit/models.py`

**Files:**
- Create: `backend/nexus/app/modules/audit/models.py`

- [ ] **Step 5.1: Create the file**

Write `backend/nexus/app/modules/audit/models.py`:

```python
"""Audit-log ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    """Append-only audit trail for tenant-scoped mutations.

    NOTE: `tenant_id` and `actor_id` are intentionally PLAIN UUID
    columns, not ForeignKey references, so audit rows survive
    tenant/user hard-delete. See migration
    0023_tenant_hard_delete_cascade. Re-adding either FK would break
    `DELETE FROM clients` for any tenant with audit history (the
    hard-delete cascade would be blocked) and would also break
    user-deletion paths whose actor_id points at the row being
    removed. `actor_email` is denormalized so attribution queries
    keep working after the user row is gone.
    """
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    # Intentionally NOT a ForeignKey — see class docstring + migration 0023.
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Intentionally NOT a ForeignKey — see class docstring + migration 0023.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 5.2: Verify**

```bash
docker compose run --rm nexus python -c "from app.modules.audit.models import AuditLog; print('ok:', AuditLog.__tablename__)"
```

Expected: `ok: audit_log`.

### Task 6: Create `app/modules/jd/models.py`

**Files:**
- Create: `backend/nexus/app/modules/jd/models.py`

- [ ] **Step 6.1: Create the file**

Read the source class bodies at `backend/nexus/app/models.py` lines 167–201 (`JobPosting`) and 204–223 (`JobPostingSignalSnapshot`), then write `backend/nexus/app/modules/jd/models.py`:

```python
"""JD pipeline ORM models."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class JobPosting(Base):
    """Phase 2A — the raw-JD-to-enriched-JD-to-signals instrument.
    State machine states: draft, signals_extracting,
    signals_extraction_failed, signals_extracted. Mutations go through
    app.modules.jd.state_machine.transition()."""
    __tablename__ = "job_postings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    org_unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizational_units.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    project_scope_raw: Mapped[str | None] = mapped_column(Text)
    description_enriched: Mapped[str | None] = mapped_column(Text)
    enriched_manually_edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="'draft'")
    status_error: Mapped[str | None] = mapped_column(Text)
    enrichment_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="idle")
    enrichment_error: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default="'native'")
    external_id: Mapped[str | None] = mapped_column(Text)
    target_headcount: Mapped[int | None] = mapped_column(Integer)
    deadline: Mapped[date | None] = mapped_column(Date)
    employment_type: Mapped[str | None] = mapped_column(Text)
    work_arrangement: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    salary_range_min: Mapped[int | None] = mapped_column(Integer)
    salary_range_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(Text)
    travel_required: Mapped[str | None] = mapped_column(Text)
    start_date_pref: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))


class JobPostingSignalSnapshot(Base):
    """Phase 2A — immutable versioned snapshot of extracted+inferred signals
    for a job posting. Written by the Dramatiq actor after a successful
    Call 1. version=1 is the initial extraction. 2B+ will populate confirmed_by/at."""
    __tablename__ = "job_posting_signal_snapshots"
    __table_args__ = (
        UniqueConstraint("job_posting_id", "version", name="uq_snapshot_job_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    job_posting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    signals: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    seniority_level: Mapped[str] = mapped_column(String, nullable=False)
    role_summary: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))
```

- [ ] **Step 6.2: Verify**

```bash
docker compose run --rm nexus python -c "from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot; print('ok:', JobPosting.__tablename__, JobPostingSignalSnapshot.__tablename__)"
```

Expected: `ok: job_postings job_posting_signal_snapshots`.

### Task 7: Create `app/modules/pipelines/models.py`

**Files:**
- Create: `backend/nexus/app/modules/pipelines/models.py`

- [ ] **Step 7.1: Create the file by copying class bodies from `app/models.py` lines 226–412**

Write `backend/nexus/app/modules/pipelines/models.py`:

```python
"""Pipeline templates + per-job pipeline instances + stages + participants."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


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
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
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
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stage_type: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: intake / debrief stages have these fields FORBIDDEN by the
    # field-rules validator (migration 0019 relaxes the DB constraint to match).
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String, nullable=True)
    signal_filter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pass_criteria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    advance_behavior: Mapped[str | None] = mapped_column(String, nullable=True)
    sla_days: Mapped[int | None] = mapped_column(Integer)
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
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
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
    pipeline_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
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
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_pipeline_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    stage_type: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: intake / debrief stages have these fields FORBIDDEN by the
    # field-rules validator (migration 0019 relaxes the DB constraint to match).
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String, nullable=True)
    signal_filter: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pass_criteria: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    advance_behavior: Mapped[str | None] = mapped_column(String, nullable=True)
    sla_days: Mapped[int | None] = mapped_column(Integer)
    otp_required_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
    role: Mapped[str] = mapped_column(Text, nullable=False)  # CHECK enforced at DB (ck_stage_participants_role)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()")
    )
```

- [ ] **Step 7.2: Verify**

```bash
docker compose run --rm nexus python -c "from app.modules.pipelines.models import PipelineTemplate, PipelineTemplateStage, JobPipelineInstance, JobPipelineStage, PipelineStageParticipant; print('ok')"
```

Expected: `ok`.

### Task 8: Create `app/modules/question_bank/models.py`

**Files:**
- Create: `backend/nexus/app/modules/question_bank/models.py`

- [ ] **Step 8.1: Copy from `app/models.py` lines 415–528**

Write `backend/nexus/app/modules/question_bank/models.py` — copy the `StageQuestionBank` and `StageQuestion` class bodies verbatim from `app/models.py`. Carry over the `sql_text` alias for the `text` column case in `StageQuestion`:

```python
"""Question bank ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StageQuestionBank(Base):
    """Phase 2C.2 — per-stage question bank.

    1:1 with a job_pipeline_stages row (UNIQUE on stage_id). Pins the
    signal snapshot used at generation time so re-generation can detect
    drift."""

    __tablename__ = "stage_question_banks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_pipeline_stages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
    )
    signal_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_posting_signal_snapshots.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'draft'"))
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'v1'"))
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    pipeline_version_at_generation: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    stage_config_snapshot: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    is_stale: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )


class StageQuestion(Base):
    """Phase 2C.2 — single question within a stage question bank.

    Note: this class has a ``text`` column which would shadow the
    module-level ``text()`` SQL function within the class body, so
    server_default expressions here use the ``sql_text`` alias."""

    __tablename__ = "stage_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stage_question_banks.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    signal_values: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    estimated_minutes: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False)
    is_mandatory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    follow_ups: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    positive_evidence: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    red_flags: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'[]'::jsonb")
    )
    rubric: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evaluation_hint: Mapped[str] = mapped_column(Text, nullable=False)
    edited_by_recruiter: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    # onupdate uses clock_timestamp() — NOT NOW() — so updated_at reflects
    # the wall-clock moment of the UPDATE rather than the transaction start.
    # Matches the Postgres trigger in migration 0017 (defense-in-depth pair).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("NOW()"),
        onupdate=sql_text("clock_timestamp()"),
    )
```

- [ ] **Step 8.2: Verify**

```bash
docker compose run --rm nexus python -c "from app.modules.question_bank.models import StageQuestionBank, StageQuestion; print('ok')"
```

Expected: `ok`.

### Task 9: Create `app/modules/candidates/models.py`

**Files:**
- Create: `backend/nexus/app/modules/candidates/models.py`

- [ ] **Step 9.1: Copy from `app/models.py` lines 591–735**

Write `backend/nexus/app/modules/candidates/models.py`:

```python
"""Candidate identity + assignment + stage-progress ORM models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Candidate(Base):
    """Phase 3B — candidate identity. PII-bearing; scoped per tenant.

    ``source`` records the origin channel (``manual`` / ``ats_ceipal`` /
    ``ats_greenhouse`` / …). ``pii_redacted_at`` marks GDPR-compliant
    soft-erasure — service-layer enforcement decides which fields are
    nulled out on redaction."""

    __tablename__ = "candidates"
    __table_args__ = (
        # Partial unique index — matches migration 0013_candidates_core.
        # Declared on the ORM so Base.metadata.create_all builds it in the
        # test DB too (tests don't run alembic), keeping the duplicate-email
        # constraint enforceable in unit tests.
        Index(
            "candidates_tenant_email_active_idx",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=sql_text("pii_redacted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    # name/email are nullable so `redact_pii` can wipe them while preserving
    # the row for audit-trail linkage. Active-candidate uniqueness is guarded
    # by the partial unique index on (tenant_id, email) WHERE pii_redacted_at IS NULL.
    name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
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
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    pii_redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pii_redacted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )


class CandidateJobAssignment(Base):
    """Phase 3B — links a candidate to a job posting.

    One candidate can be assigned to multiple jobs but at most once per
    job (UNIQUE on (candidate_id, job_posting_id)). ``current_stage_id``
    points at the stage the candidate is sitting in right now."""

    __tablename__ = "candidate_job_assignments"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "job_posting_id",
            name="candidate_job_assignments_unique_candidate_job",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    job_posting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False
    )
    current_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_pipeline_stages.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'active'")
    )
    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    entered_at_pipeline_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )


class CandidateStageProgress(Base):
    """Phase 3B — per-stage trail for an assignment.

    One row per stage the candidate has been in for a given assignment.
    ``exited_at`` null = currently sitting in that stage. ``override=true``
    marks a manual stage move that skipped normal advance criteria."""

    __tablename__ = "candidate_stage_progress"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_pipeline_stages.id"), nullable=False
    )
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(Text)
    moved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    reason: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 9.2: Verify**

```bash
docker compose run --rm nexus python -c "from app.modules.candidates.models import Candidate, CandidateJobAssignment, CandidateStageProgress; print('ok')"
```

Expected: `ok`.

### Task 10: Create `app/modules/session/models.py`

**Files:**
- Create: `backend/nexus/app/modules/session/models.py`

- [ ] **Step 10.1: Copy from `app/models.py` lines 531–588 + 738–766**

Write `backend/nexus/app/modules/session/models.py`:

```python
"""Candidate interview session ORM models.

The historical EngineDispatchToken / EngineTokenUse classes were retired
in Phase 3 of the modular-monolith uplift (alembic 0025) — the engine
no longer mints a JWT or reaches over HTTP, so those tables and their
ORM mirrors are gone.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Session(Base):
    """Phase 3C: candidate interview session.

    Upgraded from the Phase 2A stub. Represents one invitation + pre-check +
    (future) LiveKit interview attempt against a specific candidate_job_assignment
    at a specific stage.
    """
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_job_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_pipeline_stages.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'created'"))
    state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    consent_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    otp_hash: Mapped[str | None] = mapped_column(Text)
    otp_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    otp_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    livekit_room_name: Mapped[str | None] = mapped_column(Text)
    recording_s3_key: Mapped[str | None] = mapped_column(Text)
    raw_result_json: Mapped[dict | None] = mapped_column(JSONB)
    transcript: Mapped[list | None] = mapped_column(JSONB)
    questions_asked: Mapped[int | None] = mapped_column(Integer)
    probes_fired: Mapped[int | None] = mapped_column(Integer)
    agent_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_status: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )


class CandidateSessionToken(Base):
    """Single-use candidate JWT tracking — atomic used_at UPDATE enforces single-use.

    One row minted per invite/resend. The JWT's `jti` claim is this row's PK.
    `used_at` is set exactly once by `POST /api/candidate-session/{token}/start`
    via an atomic `UPDATE … WHERE used_at IS NULL RETURNING`. Replay → zero rows → 409.
    """
    __tablename__ = "candidate_session_tokens"

    jti: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("NOW()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_ip: Mapped[str | None] = mapped_column(INET)
    used_user_agent: Mapped[str | None] = mapped_column(Text)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_session_tokens.jti")
    )
```

- [ ] **Step 10.2: Verify**

```bash
docker compose run --rm nexus python -c "from app.modules.session.models import Session, CandidateSessionToken; print('ok:', Session.__tablename__, CandidateSessionToken.__tablename__)"
```

Expected: `ok: sessions candidate_session_tokens`.

### Task 11: Convert `app/models.py` into a transitional re-export shim

**Files:**
- Modify: `backend/nexus/app/models.py` (replace contents)

The new shim must re-export every class name that was previously defined here, so existing `from app.models import X` callers still work. The shim is deleted in Stage E (Task 22) once those callers are rewritten.

- [ ] **Step 11.1: Replace `app/models.py` contents**

Overwrite `backend/nexus/app/models.py` with:

```python
"""Transitional re-export shim — Phase 4a of the modular-monolith refactor.

Each domain module now owns its ORM classes in `app/modules/<m>/models.py`.
This shim keeps `from app.models import X` working until Stage E (Task 22)
rewrites every cross-module import to use the per-module public API.

DO NOT add new model classes here. Add them to the owning module's
`models.py` and re-export through the module's `__init__.py`.

This file is deleted in Stage E (sub-commit 4d-2).
"""

# Re-exports — keep in alphabetical order per owning module so future
# diffs stay readable.

# auth
from app.modules.auth.models import User, UserInvite, UserRoleAssignment

# audit
from app.modules.audit.models import AuditLog

# candidates
from app.modules.candidates.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateStageProgress,
)

# jd
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot

# org_units
from app.modules.org_units.models import Client, OrganizationalUnit

# pipelines
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    PipelineTemplate,
    PipelineTemplateStage,
)

# question_bank
from app.modules.question_bank.models import StageQuestion, StageQuestionBank

# roles
from app.modules.roles.models import Role

# session
from app.modules.session.models import CandidateSessionToken, Session

__all__ = [
    "AuditLog",
    "Candidate",
    "CandidateJobAssignment",
    "CandidateSessionToken",
    "CandidateStageProgress",
    "Client",
    "JobPipelineInstance",
    "JobPipelineStage",
    "JobPosting",
    "JobPostingSignalSnapshot",
    "OrganizationalUnit",
    "PipelineStageParticipant",
    "PipelineTemplate",
    "PipelineTemplateStage",
    "Role",
    "Session",
    "StageQuestion",
    "StageQuestionBank",
    "User",
    "UserInvite",
    "UserRoleAssignment",
]
```

- [ ] **Step 11.2: Verify shim re-exports work**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus python -c "
from app.models import (
    AuditLog, Candidate, CandidateJobAssignment, CandidateSessionToken,
    CandidateStageProgress, Client, JobPipelineInstance, JobPipelineStage,
    JobPosting, JobPostingSignalSnapshot, OrganizationalUnit,
    PipelineStageParticipant, PipelineTemplate, PipelineTemplateStage,
    Role, Session, StageQuestion, StageQuestionBank, User, UserInvite,
    UserRoleAssignment,
)
print('ok')
"
```

Expected: `ok` — every class importable from the shim.

### Task 12: Wire `Base.registry.configure()` into app startup

**Files:**
- Modify: `backend/nexus/app/main.py` (add to `lifespan` after structlog setup, before `_assert_rls_completeness`)

`Base.registry.configure()` walks every mapper that's been registered and resolves all string-FK targets. After the model split, this is the loud failure boundary if any FK string typos a target table or any model file failed to load.

- [ ] **Step 12.1: Add the call in `lifespan`**

Edit `backend/nexus/app/main.py`. Find the lifespan body — between the `logger.info("nexus.startup", ...)` line and the `# OpenTelemetry bootstrap.` comment block. The current sequence is:

```python
    structlog.configure(...)
    logger.info("nexus.startup", environment=settings.environment)

    # OpenTelemetry bootstrap. ...
    from opentelemetry import trace
    from app.ai.otel import bootstrap_tracer_provider
```

Insert a new block right after `logger.info("nexus.startup", ...)` and before the OTel bootstrap:

```python
    logger.info("nexus.startup", environment=settings.environment)

    # Force every per-module models.py to load so Base.registry sees every
    # mapper before configure() runs. Without these imports, a module whose
    # router never references its own ORM classes (rare but possible) would
    # not register its tables, and the first cross-module query would fail
    # at runtime with "Could not resolve string FK".
    #
    # Phase 4 of the modular-monolith refactor split app/models.py per
    # module. Every model module is imported here so configure() resolves
    # every string FK at boot, not at first request.
    import app.modules.auth.models  # noqa: F401
    import app.modules.audit.models  # noqa: F401
    import app.modules.candidates.models  # noqa: F401
    import app.modules.jd.models  # noqa: F401
    import app.modules.org_units.models  # noqa: F401
    import app.modules.pipelines.models  # noqa: F401
    import app.modules.question_bank.models  # noqa: F401
    import app.modules.roles.models  # noqa: F401
    import app.modules.session.models  # noqa: F401

    from app.database import Base
    Base.registry.configure()

    # OpenTelemetry bootstrap. ...
```

- [ ] **Step 12.2: Verify the imports are added in alphabetical order**

Grep:
```bash
grep -n "import app.modules.*models" backend/nexus/app/main.py
```

Expected output (alphabetical):
```
NN:    import app.modules.auth.models  # noqa: F401
NN:    import app.modules.audit.models  # noqa: F401
NN:    import app.modules.candidates.models  # noqa: F401
NN:    import app.modules.jd.models  # noqa: F401
NN:    import app.modules.org_units.models  # noqa: F401
NN:    import app.modules.pipelines.models  # noqa: F401
NN:    import app.modules.question_bank.models  # noqa: F401
NN:    import app.modules.roles.models  # noqa: F401
NN:    import app.modules.session.models  # noqa: F401
```

### Task 13: Add `tests/test_startup_integrity.py` for mapper-config smoke

**Files:**
- Create: `backend/nexus/tests/test_startup_integrity.py`

This test asserts (1) every per-module models.py file loads cleanly and registers its tables on `Base.metadata`, and (2) `Base.registry.configure()` resolves without raising. It's a tiny, fast unit test — not a full app boot.

- [ ] **Step 13.1: Create the test file**

Write `backend/nexus/tests/test_startup_integrity.py`:

```python
"""Phase 4 — startup integrity tests.

Asserts that the per-module models.py split (Phase 4a) leaves Base
in a configurable state at boot. If a string-FK target is misspelled
or a model module fails to load, configure() raises here instead of
silently failing on the first cross-module query.
"""

from __future__ import annotations


def test_every_module_models_py_loads_and_registers_tables():
    """Importing each module's models.py must register its tables on
    Base.metadata. Catches typos / missing model migrations.
    """
    # Force load every per-module models file.
    import app.modules.auth.models  # noqa: F401
    import app.modules.audit.models  # noqa: F401
    import app.modules.candidates.models  # noqa: F401
    import app.modules.jd.models  # noqa: F401
    import app.modules.org_units.models  # noqa: F401
    import app.modules.pipelines.models  # noqa: F401
    import app.modules.question_bank.models  # noqa: F401
    import app.modules.roles.models  # noqa: F401
    import app.modules.session.models  # noqa: F401

    from app.database import Base

    # Every table that app/main.py's _TENANT_SCOPED_TABLES tracks must
    # appear in Base.metadata.tables after the per-module imports above.
    expected = {
        "users",
        "user_role_assignments",
        "user_invites",
        "audit_log",
        "candidates",
        "candidate_job_assignments",
        "candidate_stage_progress",
        "candidate_session_tokens",
        "job_postings",
        "job_posting_signal_snapshots",
        "clients",
        "organizational_units",
        "pipeline_templates",
        "pipeline_template_stages",
        "job_pipeline_instances",
        "job_pipeline_stages",
        "pipeline_stage_participants",
        "stage_question_banks",
        "stage_questions",
        "roles",
        "sessions",
    }
    actual = set(Base.metadata.tables.keys())
    missing = expected - actual
    assert not missing, f"Tables missing from Base.metadata: {missing}"


def test_base_registry_configure_resolves_all_string_fks():
    """Base.registry.configure() walks every mapper and resolves string
    FK targets. If any FK references a non-existent table name, this
    raises sqlalchemy.exc.InvalidRequestError.
    """
    # Same imports as above — needed in case this test runs in isolation.
    import app.modules.auth.models  # noqa: F401
    import app.modules.audit.models  # noqa: F401
    import app.modules.candidates.models  # noqa: F401
    import app.modules.jd.models  # noqa: F401
    import app.modules.org_units.models  # noqa: F401
    import app.modules.pipelines.models  # noqa: F401
    import app.modules.question_bank.models  # noqa: F401
    import app.modules.roles.models  # noqa: F401
    import app.modules.session.models  # noqa: F401

    from app.database import Base

    # No exception → all FK strings resolved.
    Base.registry.configure()
```

- [ ] **Step 13.2: Run the test**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest tests/test_startup_integrity.py -v
```

Expected: `2 passed`. If either fails, **fix the underlying model file** — do not weaken the assertion.

### Task 14: Run full pytest, verify baseline preserved, commit 4a

**Files:** none new — verification only

- [ ] **Step 14.1: Full pytest run**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase4_4a_fails.txt
diff /tmp/phase4_baseline_fails.txt /tmp/phase4_4a_fails.txt
```

Expected:
- `644 passed, 9 failed` (the +2 are the new `test_startup_integrity.py` tests).
- `diff` shows zero output — no test that was passing before is now failing.

If anything in the diff has a `>` line (new failure introduced by the model split), STOP and fix before committing.

- [ ] **Step 14.2: ruff + mypy**

```bash
docker compose run --rm nexus ruff check . 2>&1 | tail -3
docker compose run --rm nexus mypy app/ 2>&1 | tail -3
```

Expected: clean both — no new violations vs `/tmp/phase4_baseline_ruff.txt` / `/tmp/phase4_baseline_mypy.txt`.

- [ ] **Step 14.3: Commit sub-commit 1 (4a)**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/auth/models.py \
        backend/nexus/app/modules/audit/models.py \
        backend/nexus/app/modules/candidates/models.py \
        backend/nexus/app/modules/jd/models.py \
        backend/nexus/app/modules/org_units/models.py \
        backend/nexus/app/modules/pipelines/models.py \
        backend/nexus/app/modules/question_bank/models.py \
        backend/nexus/app/modules/roles/models.py \
        backend/nexus/app/modules/session/models.py \
        backend/nexus/app/models.py \
        backend/nexus/app/main.py \
        backend/nexus/tests/test_startup_integrity.py
git status
git diff --cached --stat
git commit -m "$(cat <<'EOF'
refactor(models): split app/models.py per module + Base.registry.configure()

Phase 4a of the modular-monolith refactor (umbrella spec
docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md).

- Moves every ORM class out of app/models.py into the owning module's
  app/modules/<m>/models.py file. 9 new files, 21 classes total.
- app/models.py becomes a transitional re-export shim. The shim is
  retired in 4d-2 once cross-module imports are migrated to public APIs.
- Adds explicit `Base.registry.configure()` to lifespan startup so any
  string-FK resolution failure is loud at boot, not at first request.
- Adds tests/test_startup_integrity.py covering the mapper-config smoke
  + per-module table registration.

No behavior change. pytest baseline preserved (642 passed -> 644 passed
with two new startup tests; same 9 environment-driven failures).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

Expected: clean commit, single line in `git log --oneline -1` showing the new commit message.

---

## Stage C — 4b: Hoist late function-body imports to module top

**Stage goal:** Eliminate hidden imports inside function bodies. Each one becomes a top-level import. If hoisting surfaces a true Python-level cycle (caught by pytest), extract the shared symbol to `app/shared/<topic>.py` per the spec's escalation rule.

**Late-import inventory (from `grep -rn "    from app\." app/modules/`):**

| File:line | Import | Tractability |
|---|---|---|
| `admin/router.py:210` | `from app.database import get_bypass_session` | trivial — `app.database` is non-module, no cycle risk |
| `admin/router.py:211` | `from app.modules.audit import actions as audit_actions` | downward edge — already top-level in many siblings |
| `admin/router.py:212` | `from app.modules.audit.service import log_event` | downward — trivial |
| `auth/router.py:65,120,410` | `from app.modules.auth.errors import AccountSuspendedError` | intra-module — trivial |
| `auth/router.py:88,318,447` | `from app.modules.auth.admin import ...` | intra-module — trivial (consolidate to single top-level import) |
| `auth/router.py:229` | `from app.modules.org_units.service import create_org_unit` | "upward" but acyclic — see pre-analysis. Hoist. |
| `auth/admin/__init__.py:39` | `from app.modules.auth.admin._factory import _get_provider_singleton` | intentionally lazy (singleton pattern). **Leave as-is** — see Step 16.1 below. |
| `jd/router.py:77` | `from app.modules.jd.schemas import default_evaluation_method` | intra-module — trivial |
| `jd/router.py:178,222,273` | `from app.models import ...` (JobPosting / OrganizationalUnit) | model imports — switch to per-module + hoist |
| `jd/router.py:207` | `from app.modules.jd.actors import reenrich_jd` | intra-module — trivial |
| `jd/router.py:235` | `from app.modules.audit.service import log_event` | downward — trivial |
| `jd/router.py:656` | `from app.modules.jd.service import delete_job_posting` | intra-module — trivial |
| `jd/service.py:389` | `from app.modules.question_bank.service import recompute_and_persist_stale` | apparent cycle — pre-analysis says **safe to hoist**. Cite stale comment in commit body. |
| `jd/service.py:400,493,516` | `from app.models import ...` (StageQuestionBank, JobPipelineStage, PipelineStageParticipant) | model imports — replace with `app.modules.{pipelines,question_bank}.models` + hoist |
| `jd/service.py:494` | `from app.modules.jd.errors import ActivationPredicateFailure` | intra-module — trivial |
| `jd/service.py:495-498` | `from app.modules.pipelines.categories import ...` | downward — trivial; categories.py has no imports |
| `jd/service.py:634` | `from app.modules.jd.errors import ActivationPredicatesFailed` | intra-module — trivial |
| `jd/service.py:677-678` | `from app.modules.audit import actions as audit_actions` + `from app.modules.audit.service import log_event` | downward — trivial |
| `jd/state_machine.py:72` | `from app.modules.audit.service import log_event` | downward — trivial |
| `pipelines/router.py:637` | `from app.modules.pipelines.participants import list_assignable_users` | intra-module — trivial |
| `pipelines/service.py:439` | `from app.models import PipelineStageParticipant, User` | model imports — replace + hoist |
| `pipelines/service.py:844-845` | `from app.models import StageQuestionBank` + `from app.modules.question_bank.service import recompute_and_persist_stale` | apparent cycle — pre-analysis says **safe to hoist**. |
| `scheduler/service.py:65` | `from app.modules.candidates.errors import CandidateNotFoundError` | downward — trivial |
| `scheduler/service.py:148,244` | `from app.modules.session.errors import SessionNotFoundError` | downward — trivial; consolidate to one top-level import |
| `settings/service.py:245` | `from app.modules.org_units.service import nullify_deletable_by_for_user` | downward — trivial |
| `settings/service.py:324` | `from app.modules.auth.admin import AuthProviderError, get_auth_provider` | downward — trivial |

### Task 15: Hoist trivial intra-module + downward late imports

**Files:**
- Modify: `backend/nexus/app/modules/admin/router.py`
- Modify: `backend/nexus/app/modules/auth/router.py`
- Modify: `backend/nexus/app/modules/jd/router.py`
- Modify: `backend/nexus/app/modules/jd/state_machine.py`
- Modify: `backend/nexus/app/modules/pipelines/router.py`
- Modify: `backend/nexus/app/modules/scheduler/service.py`
- Modify: `backend/nexus/app/modules/settings/service.py`

The intra-module hoists (auth/router → auth/errors etc.) are pure cleanup. Each function-body `from app.modules.<self>.foo import X` becomes a top-level import after the existing top-level imports.

- [ ] **Step 15.1: `admin/router.py:210-212`**

Read `app/modules/admin/router.py` lines 200–230. Move the three function-body imports to the top of the file (placed after existing `from app.modules....` imports, alphabetical within the existing block). Delete the inline lines.

After the change:
- Top-of-file gains:
  ```python
  from app.database import get_bypass_session
  from app.modules.audit import actions as audit_actions
  from app.modules.audit.service import log_event
  ```
- Lines 210–212 removed; the function body uses the now-top-level names directly.

If `from app.database import get_bypass_session` is already imported at top, do not duplicate.

- [ ] **Step 15.2: `auth/router.py` — consolidate**

Read `auth/router.py` 1–50 to see the existing top-level imports. The file has these late-import sites:

- Line 65: `from app.modules.auth.errors import AccountSuspendedError`
- Line 88: `from app.modules.auth.admin import (...)`  (multiple lines)
- Line 120: `from app.modules.auth.errors import AccountSuspendedError`  (duplicate of 65)
- Line 229: `from app.modules.org_units.service import create_org_unit as _create_root_unit`
- Line 318: `from app.modules.auth.admin import (...)`  (duplicate of 88)
- Line 410: `from app.modules.auth.errors import AccountSuspendedError`  (duplicate of 65)
- Line 447: `from app.modules.auth.admin import AuthProviderError`

Consolidate. Add at module top (after `from app.modules.audit.*` imports already there):

```python
from app.modules.auth.admin import (
    AuthProvider,
    AuthProviderError,
    InvalidCredentialsError,
    SessionTokens,
    UserAlreadyExistsError,
    UserIdentity,
    UserNotFoundError,
    get_auth_provider,
)
from app.modules.auth.errors import AccountSuspendedError
from app.modules.org_units.service import create_org_unit as _create_root_unit
```

Then delete the inline imports at lines 65, 88, 120, 229, 318, 410, 447. The function bodies now reference these names directly.

**Be careful with `auth/admin/__init__.py:39`:** that file's lazy import of `_get_provider_singleton` is a documented intentional pattern (avoids a circular config import at module load). **Do not hoist it.** Leave it inside `get_auth_provider()`. The grep flagged it, but the comment in that file explains the rationale and we honor it.

After the rewrite, run:
```bash
docker compose run --rm nexus python -c "import app.modules.auth.router; print('ok')"
```

Expected: `ok`. If a Python-level circular-import error fires, **revert the hoist of line 229** (the `org_units → auth` direction in router.py:9 may interact unfavorably; document and leave that one inline). All other hoists in this file are intra-module and safe.

- [ ] **Step 15.3: `jd/router.py`**

Hoist:
- Line 77: `from app.modules.jd.schemas import default_evaluation_method` → top of file (alphabetical with existing schemas import).
- Line 178, 222: `from app.models import JobPosting` → replace with `from app.modules.jd.models import JobPosting` at top (deduplicate; the file already has `from app.models import JobPostingSignalSnapshot` at line 17 — replace that with `from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot`).
- Line 207: `from app.modules.jd.actors import reenrich_jd` → top.
- Line 235: `from app.modules.audit.service import log_event` → top.
- Line 273: `from app.models import OrganizationalUnit` → replace with `from app.modules.org_units.models import OrganizationalUnit` at top.
- Line 656: `from app.modules.jd.service import delete_job_posting` → top.

Delete the inline duplicates after each hoist.

Run:
```bash
docker compose run --rm nexus python -c "import app.modules.jd.router; print('ok')"
```

- [ ] **Step 15.4: `jd/state_machine.py:72`**

Hoist `from app.modules.audit.service import log_event` to the top of `app/modules/jd/state_machine.py`. Delete the inline line.

Run:
```bash
docker compose run --rm nexus python -c "import app.modules.jd.state_machine; print('ok')"
```

- [ ] **Step 15.5: `pipelines/router.py:637`**

Hoist `from app.modules.pipelines.participants import list_assignable_users` to the top. Delete the inline line.

Run:
```bash
docker compose run --rm nexus python -c "import app.modules.pipelines.router; print('ok')"
```

- [ ] **Step 15.6: `scheduler/service.py` (lines 65, 148, 244)**

Hoist:
- Line 65: `from app.modules.candidates.errors import CandidateNotFoundError`
- Line 148, 244: `from app.modules.session.errors import SessionNotFoundError`

Consolidate to a single top-level block:
```python
from app.modules.candidates.errors import CandidateNotFoundError
from app.modules.session.errors import SessionNotFoundError
```

Delete the three inline imports.

Run:
```bash
docker compose run --rm nexus python -c "import app.modules.scheduler.service; print('ok')"
```

- [ ] **Step 15.7: `settings/service.py` (lines 245, 324)**

Hoist:
- Line 245: `from app.modules.org_units.service import nullify_deletable_by_for_user`
- Line 324: `from app.modules.auth.admin import AuthProviderError, get_auth_provider`

Add to top-level imports. Delete the inline ones.

Run:
```bash
docker compose run --rm nexus python -c "import app.modules.settings.service; print('ok')"
```

### Task 16: Hoist potentially-cyclic late imports (`jd/service.py`, `pipelines/service.py`)

**Files:**
- Modify: `backend/nexus/app/modules/jd/service.py`
- Modify: `backend/nexus/app/modules/pipelines/service.py`

These are the imports the pre-analysis flagged as "apparent cycle but actually safe to hoist." If the module import succeeds at the verification step, the cycle was a phantom and the hoist stands. If it fails with `ImportError: cannot import name`, fall back to the escalation rule (see Step 16.4).

- [ ] **Step 16.1: `jd/service.py:389` — qb stale recompute**

Hoist `from app.modules.question_bank.service import recompute_and_persist_stale` to the top of `jd/service.py` (alphabetical with the existing `from app.modules.org_units.service` line at 22).

Update the docstring on `_recompute_stale_for_job_banks`:

```python
async def _recompute_stale_for_job_banks(db: AsyncSession, job: JobPosting) -> None:
    """Recompute and persist is_stale for every bank on the job's pipeline.

    Fires after signal save so banks pinned to an older confirmed snapshot
    remain marked stale after the edit. The qb cycle this used to guard
    against was retired in Phase 4: qb/__init__.py is empty and qb/service.py
    has no jd imports, so a top-level edge from jd → qb is acyclic.
    """
```

Delete the inline import at the previous line 389.

- [ ] **Step 16.2: `jd/service.py:400, 493, 516` — model imports**

These currently look like:

```python
# line 400
    from app.models import StageQuestionBank, JobPipelineStage
# line 493
    from app.models import PipelineStageParticipant, StageQuestionBank
# line 495 (already hoisted in step 15? no — 15 only hoisted some jd/router.py items; jd/service.py is this task)
    from app.modules.pipelines.categories import (...)
# line 494
    from app.modules.jd.errors import ActivationPredicateFailure
# line 516
    from app.models import JobPipelineStage
# line 634
    from app.modules.jd.errors import ActivationPredicatesFailed
# lines 677-678
    from app.modules.audit import actions as audit_actions
    from app.modules.audit.service import log_event
```

Replace all of them with top-level imports:

```python
# Top-level (add to the existing import block, alphabetical order):
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event
from app.modules.jd.errors import ActivationPredicateFailure, ActivationPredicatesFailed
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot  # already at line 14, REPLACE that line
from app.modules.org_units.models import OrganizationalUnit, User  # already at line 14, REPLACE that line
from app.modules.pipelines.categories import (
    bank_eligible_stage_types,
    human_led_stage_types,
    is_paused,
    middle_stage_types_for_activation,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
)
from app.modules.question_bank.models import StageQuestionBank
from app.modules.question_bank.service import recompute_and_persist_stale
```

Note: `app/modules/jd/service.py:14` currently imports `JobPipelineInstance, JobPosting, JobPostingSignalSnapshot, OrganizationalUnit, User` from `app.models`. Split that into the per-module imports above.

Verify by reading lines 488–520 of `jd/service.py` to confirm exactly which categories functions are used (`is_paused`, etc.). Adjust the `from ... pipelines.categories import (...)` line so it matches the actually-used names — do not under-import or over-import.

Delete the inline imports at lines 400, 493, 494, 495, 516, 634, 677, 678.

Run:
```bash
docker compose run --rm nexus python -c "import app.modules.jd.service; print('ok')"
```

If that errors with `ImportError: cannot import name ... from partially initialized module`, you've hit a real cycle — go to Step 16.4.

- [ ] **Step 16.3: `pipelines/service.py:439, 844, 845`**

Lines 439, 844 import models from `app.models`. Line 845 imports `recompute_and_persist_stale`.

Top-level (already partially exists at line 16):
```python
# REPLACE the existing `from app.models import (...)` block at line 16 with the
# per-module split version. Identify which classes pipelines/service.py actually
# uses and import each from the right module.
```

Read `app/modules/pipelines/service.py` lines 1–40 to see the existing `from app.models import (...)` block. Identify which classes are imported from app.models. Likely set: `Client`, `JobPipelineInstance`, `JobPipelineStage`, `JobPosting`, `OrganizationalUnit`, `PipelineStageParticipant`, `PipelineTemplate`, `PipelineTemplateStage`, `Role`, `StageQuestion`, `StageQuestionBank`, `User` (verify by reading).

Replace with:
```python
from app.modules.auth.models import User
from app.modules.jd.models import JobPosting
from app.modules.org_units.models import Client, OrganizationalUnit
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    PipelineTemplate,
    PipelineTemplateStage,
)
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.question_bank.service import recompute_and_persist_stale
from app.modules.roles.models import Role
```

Adjust the set based on actual usage from the source file.

Delete the inline imports at lines 439, 844, 845.

Run:
```bash
docker compose run --rm nexus python -c "import app.modules.pipelines.service; print('ok')"
```

- [ ] **Step 16.4: Escalation — if a hoist fires a real cycle**

ONLY do this if Step 16.1 / 16.3 reports `ImportError: cannot import name ... from partially initialized module ...`.

Per the spec's escalation rule, extract the shared symbol to `app/shared/<topic>.py`. Concrete pattern for `recompute_and_persist_stale`:

1. Create `backend/nexus/app/shared/__init__.py` (empty).
2. Create `backend/nexus/app/shared/question_bank_staleness.py`:
   ```python
   """Bank-staleness recompute — extracted so jd/service.py and
   pipelines/service.py can call it without importing question_bank
   directly.

   Phase 4 of the modular-monolith refactor.
   """
   from sqlalchemy.ext.asyncio import AsyncSession

   from app.modules.question_bank.models import StageQuestionBank
   from app.modules.question_bank.service import (
       recompute_and_persist_stale as _recompute,
   )


   async def recompute_and_persist_stale(
       db: AsyncSession,
       bank: StageQuestionBank,
       *,
       current_stage_config: dict | None = None,
   ) -> bool:
       return await _recompute(db, bank, current_stage_config=current_stage_config)
   ```
3. Replace the `from app.modules.question_bank.service import recompute_and_persist_stale` lines in `jd/service.py` and `pipelines/service.py` with `from app.shared.question_bank_staleness import recompute_and_persist_stale`.

This is a **fallback only** — if Step 16.1/16.3 succeed without ImportError, do not create `app/shared/` (YAGNI per the spec).

### Task 17: Run pytest, verify baseline preserved, commit 4b

**Files:** none new — verification

- [ ] **Step 17.1: Full pytest**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase4_4b_fails.txt
diff /tmp/phase4_4a_fails.txt /tmp/phase4_4b_fails.txt
```

Expected: same `644 passed, 9 failed`. `diff` empty.

- [ ] **Step 17.2: Verify no late `from app.` imports remain**

Run:
```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
grep -rn "    from app\." app/modules/ --include="*.py" | grep -v test_
```

Expected: ONLY `auth/admin/__init__.py:39` (the documented lazy `_get_provider_singleton` import) and possibly the inline import in `app/modules/auth/admin/_factory.py:14` which is also intentional. Every other late import should be gone.

If anything else shows up, hoist it before committing.

- [ ] **Step 17.3: ruff + mypy**

```bash
docker compose run --rm nexus ruff check . 2>&1 | tail -3
docker compose run --rm nexus mypy app/ 2>&1 | tail -3
```

Expected: clean.

- [ ] **Step 17.4: Commit sub-commit 2 (4b)**

Stage the modified files:

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/admin/router.py \
        backend/nexus/app/modules/auth/router.py \
        backend/nexus/app/modules/jd/router.py \
        backend/nexus/app/modules/jd/service.py \
        backend/nexus/app/modules/jd/state_machine.py \
        backend/nexus/app/modules/pipelines/router.py \
        backend/nexus/app/modules/pipelines/service.py \
        backend/nexus/app/modules/scheduler/service.py \
        backend/nexus/app/modules/settings/service.py
# If Step 16.4 escalation fired:
# git add backend/nexus/app/shared/__init__.py backend/nexus/app/shared/question_bank_staleness.py
git status
git diff --cached --stat
git commit -m "$(cat <<'EOF'
refactor(imports): hoist late function-body imports to module top

Phase 4b of the modular-monolith refactor. Every function-body
`from app.*` import inside a domain module is now a top-level import,
so the dependency graph is visible at module load time.

Sites hoisted:
- admin/router.py x3
- auth/router.py x9 (consolidated to single top-level block;
  the auth.admin._factory lazy singleton import is preserved
  per its existing inline-comment rationale)
- jd/router.py x7 (model imports rewritten to per-module form)
- jd/service.py x9 (incl. the previously-lazy qb staleness recompute;
  the cycle that the lazy import guarded against was retired in
  Phase 4 once qb/__init__.py was confirmed empty and qb/service.py
  had no jd imports)
- jd/state_machine.py x1
- pipelines/router.py x1
- pipelines/service.py x3
- scheduler/service.py x3
- settings/service.py x2

No behavior change. pytest baseline preserved (644 passed, 9 failed).
ruff + mypy clean.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

If Step 16.4 fired, add a paragraph to the commit body explaining the `app/shared/question_bank_staleness.py` extraction and remove the "the cycle … was retired" line.

---

## Stage D — 4c: Verify foundational module trio (auth/audit/notifications) cleanliness

**Stage goal:** Confirm `auth`, `audit`, `notifications` only import from each other and from non-modules. Phase 3 already removed the explicit inverted edge (`auth → interview_runtime` via `EngineTokenInvalidError`). The hoist in Stage C also surfaced auth's outbound import to `org_units.service.create_org_unit` (in `auth/router.py:229`), which is "upward" but acyclic — see decision below.

### Task 18: Run the inverted-edge sweep grep

**Files:** none new — verification + decision

- [ ] **Step 18.1: Run the spec's grep**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
grep -rn "from app\.modules\." app/modules/auth/ app/modules/audit/ app/modules/notifications/ \
  --include="*.py" | grep -v test_ | grep -v "from app.modules.auth\." | grep -v "from app.modules.audit\." | grep -v "from app.modules.notifications\."
```

Expected output after Stage C: exactly one line —
```
app/modules/auth/router.py:NN:from app.modules.org_units.service import create_org_unit as _create_root_unit
```
(plus the per-module `models.py` imports inside `auth/audit/notifications` if any of those modules ever import other modules' models — none should.)

- [ ] **Step 18.2: Decision — keep or refactor the auth → org_units edge?**

The auth → org_units import comes from invite-acceptance: when a Super Admin claims an invite for the first time, the handler creates the root `company` org unit. This is a real cross-module call.

The spec's strict-modular rule for the foundational trio says auth should not reach upward into a domain module. Two resolution options:

1. **Keep the hoisted top-level import.** Acceptable because:
   - It's acyclic (org_units/service does not import auth).
   - It's only one site.
   - The alternative (post-invite-accept hook registry) adds complexity disproportionate to the boundary purity gain.
   - This decision is documented in CLAUDE.md as part of 4d-4.

2. **Hide it behind an orchestrator.** Move the invite-acceptance + root-unit creation flow into a higher-level module (e.g., a new `app/modules/onboarding/`) that imports both auth and org_units. Auth's router calls the orchestrator. This is a real refactor — out of Phase 4 scope.

**Decision: Option 1 — keep the import.** The CLAUDE.md note in Task 24 will document the rationale. If a reviewer pushes back, the orchestrator extraction is a follow-up PR.

If the grep at Step 18.1 returns ANY OTHER line, treat it as a violation and resolve it before Stage E. The known-acceptable line is the single `org_units.service.create_org_unit` import in `auth/router.py`.

- [ ] **Step 18.3: No commit**

This stage produces no diff (assuming Step 18.1 returns only the documented line and Step 18.2 chose Option 1). Skip the sub-commit. The next commit will be sub-commit 4 (start of Stage E).

If a reviewer changes their mind on Option 2 later, that becomes a follow-up PR — not Phase 4's job.

---

## Stage E — 4d: Module public-API discipline

**Stage goal:** Each domain module exports its public surface explicitly via `__init__.py`. Cross-module imports use `from app.modules.<m> import X`, never `from app.modules.<m>.<internal> import X`. The transitional shim at `app/models.py` is retired. An AST-walking lint test enforces the rule going forward. CLAUDE.md gains a Module Public API section.

**Per-module public surface inventory** (informed by what cross-module callers actually use):

| Module | Exports |
|---|---|
| `auth` | `verify_access_token`, `verify_candidate_token`, `TokenPayload`, `UserContext`, `get_current_user_roles`, `User`, `UserRoleAssignment`, `UserInvite` |
| `audit` | `log_event`, `actions` (the submodule of action constants), `AuditLog` |
| `notifications` | `send_email`, `send_sms` |
| `org_units` | `create_org_unit`, `find_company_profile_in_ancestry`, `get_org_unit_ancestry`, `nullify_deletable_by_for_user`, `Client`, `OrganizationalUnit` |
| `roles` | `Role` |
| `jd` | `transition` (state-machine API), `require_job_access`, `JobPosting`, `JobPostingSignalSnapshot`, `delete_job_posting` (only the public service entrypoints actually called from outside jd; verify list during Step 19.4) |
| `pipelines` | `auto_apply_pipeline_on_confirmation`, `bank_eligible_stage_types`, `human_led_stage_types`, `is_paused`, `middle_stage_types_for_activation`, `JobPipelineInstance`, `JobPipelineStage`, `PipelineStageParticipant`, `PipelineTemplate`, `PipelineTemplateStage` |
| `question_bank` | `recompute_and_persist_stale`, `StageQuestion`, `StageQuestionBank` |
| `candidates` | `Candidate`, `CandidateJobAssignment`, `CandidateStageProgress`, `CandidateNotFoundError` |
| `session` | `Session`, `CandidateSessionToken`, `SessionNotFoundError` |
| `scheduler`, `settings`, `admin`, `interview_runtime`, `interview_engine` | currently no cross-module callers reach into these — keep `__init__.py` as today |

**The discipline:**
- Every module gets an `__init__.py` with `__all__`.
- Routers + Dramatiq actors are NOT exported (they are wired in `app/main.py` and `app/worker.py` directly via deep import — that's the documented exception).
- `from app.modules.<m>.models import X` from OUTSIDE the module is forbidden (use `from app.modules.<m> import X`).
- `from app.modules.<m>.<service|errors|schemas|...> import X` from OUTSIDE the module is forbidden (same rule).
- INTRA-module deep imports (`from app.modules.jd.service import ...` inside `app/modules/jd/router.py`) are FINE — the rule only applies across module boundaries.

### Task 19: Add `__init__.py` exports for every domain module (sub-commit 4)

**Files:**
- Modify or replace: `backend/nexus/app/modules/<m>/__init__.py` for each module that needs it.

- [ ] **Step 19.1: `auth/__init__.py`**

Read current contents (already exists with partial exports). Replace with:

```python
"""Auth module — provider-agnostic JWT verification + RBAC context.

Public surface for cross-module callers.
"""
from app.modules.auth.context import UserContext, get_current_user_roles
from app.modules.auth.models import User, UserInvite, UserRoleAssignment
from app.modules.auth.schemas import TokenPayload
from app.modules.auth.service import verify_access_token, verify_candidate_token

__all__ = [
    "TokenPayload",
    "User",
    "UserContext",
    "UserInvite",
    "UserRoleAssignment",
    "get_current_user_roles",
    "verify_access_token",
    "verify_candidate_token",
]
```

- [ ] **Step 19.2: `audit/__init__.py`**

Replace:

```python
"""Audit module — append-only event log."""
from app.modules.audit import actions
from app.modules.audit.models import AuditLog
from app.modules.audit.service import log_event

__all__ = ["AuditLog", "actions", "log_event"]
```

- [ ] **Step 19.3: `notifications/__init__.py`**

Update (already partial):

```python
"""Notifications module — provider-agnostic email + SMS dispatch."""
from app.modules.notifications.service import send_email, send_sms

__all__ = ["send_email", "send_sms"]
```

(No models in this module — no model export.)

- [ ] **Step 19.4: `org_units/__init__.py`**

Replace empty file with:

```python
"""Org-units module — tenant + hierarchical unit modeling.

Public surface for cross-module callers.
"""
from app.modules.org_units.models import Client, OrganizationalUnit
from app.modules.org_units.service import (
    create_org_unit,
    find_company_profile_in_ancestry,
    get_org_unit_ancestry,
    nullify_deletable_by_for_user,
)

__all__ = [
    "Client",
    "OrganizationalUnit",
    "create_org_unit",
    "find_company_profile_in_ancestry",
    "get_org_unit_ancestry",
    "nullify_deletable_by_for_user",
]
```

If `nullify_deletable_by_for_user` is not actually defined in `org_units/service.py`, drop it (verify with `grep -n "def nullify_deletable_by_for_user" app/modules/org_units/service.py`).

- [ ] **Step 19.5: `roles/__init__.py`**

```python
"""Roles module — role definitions (system + tenant-custom)."""
from app.modules.roles.models import Role

__all__ = ["Role"]
```

- [ ] **Step 19.6: `jd/__init__.py`**

Identify the cross-module callers' surface by grepping:

```bash
grep -rn "from app\.modules\.jd" app/modules/ --include="*.py" | grep -v "from app.modules.jd" | grep -v "/jd/"
```

Expected names to export include at least: `JobPosting`, `JobPostingSignalSnapshot`, `require_job_access` (used by qb's authz), `transition` (used by pipelines/router as `jd_transition`).

Write:

```python
"""JD module — raw-JD-to-enriched-JD-to-signals pipeline.

Public surface for cross-module callers. Routers + Dramatiq actors
are NOT exported (per the modular-monolith public-API discipline).
"""
from app.modules.jd.authz import require_job_access
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.jd.service import delete_job_posting
from app.modules.jd.state_machine import transition

__all__ = [
    "JobPosting",
    "JobPostingSignalSnapshot",
    "delete_job_posting",
    "require_job_access",
    "transition",
]
```

If a name above isn't actually defined where it's described (file moved or renamed), grep to confirm and adjust the export list.

- [ ] **Step 19.7: `pipelines/__init__.py`**

Replace the existing docstring-only file with:

```python
"""Phase 2C.1 — Pipeline Builder module.

Owns pipeline templates (per org unit) and job pipeline instances (per job).
Called from jd.confirm_signals() via auto_apply_pipeline_on_confirmation().

Public surface for cross-module callers.
"""
from app.modules.pipelines.categories import (
    bank_eligible_stage_types,
    human_led_stage_types,
    is_paused,
    middle_stage_types_for_activation,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    PipelineTemplate,
    PipelineTemplateStage,
)
from app.modules.pipelines.service import auto_apply_pipeline_on_confirmation

__all__ = [
    "JobPipelineInstance",
    "JobPipelineStage",
    "PipelineStageParticipant",
    "PipelineTemplate",
    "PipelineTemplateStage",
    "auto_apply_pipeline_on_confirmation",
    "bank_eligible_stage_types",
    "human_led_stage_types",
    "is_paused",
    "middle_stage_types_for_activation",
]
```

- [ ] **Step 19.8: `question_bank/__init__.py`**

```python
"""Question bank module — per-stage AI-generated question banks."""
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.question_bank.service import recompute_and_persist_stale

__all__ = ["StageQuestion", "StageQuestionBank", "recompute_and_persist_stale"]
```

- [ ] **Step 19.9: `candidates/__init__.py`**

```python
"""Candidates module — candidate identity, assignments, kanban."""
from app.modules.candidates.errors import CandidateNotFoundError
from app.modules.candidates.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateStageProgress,
)

__all__ = [
    "Candidate",
    "CandidateJobAssignment",
    "CandidateNotFoundError",
    "CandidateStageProgress",
]
```

- [ ] **Step 19.10: `session/__init__.py`**

```python
"""Session module — candidate interview session lifecycle."""
from app.modules.session.errors import SessionNotFoundError
from app.modules.session.models import CandidateSessionToken, Session

__all__ = ["CandidateSessionToken", "Session", "SessionNotFoundError"]
```

- [ ] **Step 19.11: Verify each module's `__init__.py` imports cleanly**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
for m in auth audit notifications org_units roles jd pipelines question_bank candidates session; do
  docker compose run --rm nexus python -c "import app.modules.$m; print('$m: ok')"
done
```

Expected: every module prints `<m>: ok`. If any errors, fix the export list before moving on.

- [ ] **Step 19.12: Run pytest, baseline preserved**

```bash
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
```

Expected: `644 passed, 9 failed`.

- [ ] **Step 19.13: Commit sub-commit 4 (4d-1)**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/app/modules/auth/__init__.py \
        backend/nexus/app/modules/audit/__init__.py \
        backend/nexus/app/modules/notifications/__init__.py \
        backend/nexus/app/modules/org_units/__init__.py \
        backend/nexus/app/modules/roles/__init__.py \
        backend/nexus/app/modules/jd/__init__.py \
        backend/nexus/app/modules/pipelines/__init__.py \
        backend/nexus/app/modules/question_bank/__init__.py \
        backend/nexus/app/modules/candidates/__init__.py \
        backend/nexus/app/modules/session/__init__.py
git commit -m "$(cat <<'EOF'
refactor(modules): add __init__.py public-API exports for every domain module

Phase 4d-1 of the modular-monolith refactor. Each domain module now
declares its public surface via __init__.py + __all__. Routers and
Dramatiq actors are intentionally NOT exported — they are wired via
deep import from app/main.py and app/worker.py respectively (the
documented exception).

Models are exported through the module __init__ so cross-module
callers can use `from app.modules.<m> import X` instead of reaching
into the per-module models.py file.

The cross-module import sweep that retires app/models.py is the next
sub-commit (4d-2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 20: Sweep cross-module imports to use public API + retire `app/models.py` shim (sub-commit 5)

**Stage goal:** Every `from app.modules.<m>.<internal> import X` outside module `<m>` becomes `from app.modules.<m> import X`. Every `from app.models import X` becomes `from app.modules.<m> import X` for the right `<m>`. Then delete `app/models.py`.

**Mechanical pattern:**

1. For each external caller, identify the import statement.
2. Check whether the imported names are in the destination module's `__all__`.
3. Rewrite to `from app.modules.<m> import X` or `from app.modules.<m> import X1, X2, ...`.
4. After the sweep, all `from app.models import` statements should be gone.
5. Delete `app/models.py`.

**Files modified (cross-module callers):**

The deep-import / app.models call sites surface via:

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
echo "=== from app.models ==="
grep -rn "from app\.models import" app/ --include="*.py" | grep -v models.py
echo ""
echo "=== from app.modules.<m>.<internal> outside m ==="
# This is handled by the AST lint test in Task 21; the sweep here uses grep.
for m in auth audit notifications org_units roles jd pipelines question_bank candidates session scheduler settings admin interview_runtime interview_engine; do
  grep -rn "from app\.modules\.${m}\.[a-z_]\+ import" app/ --include="*.py" \
    | grep -v "app/modules/${m}/" \
    | grep -v "${m}/__init__.py" \
    | grep -v test_
done
```

The first grep enumerates all "shim users" — they switch to per-module imports. The second enumerates cross-module deep imports — they switch to public API.

- [ ] **Step 20.1: Sweep the `from app.models import` users**

The grep result lists exactly which files still pull from the shim. For each one, replace `from app.models import X1, X2, ...` with the per-module equivalent. Use the mapping from Stage B.

Concrete files (from earlier recon):

| File | Replace |
|---|---|
| `app/modules/admin/service.py:14` | `from app.modules.org_units.models import Client; from app.modules.auth.models import UserInvite` (or via public API: `from app.modules.org_units import Client; from app.modules.auth import UserInvite`) |
| `app/modules/audit/service.py:8` | `from app.modules.audit.models import AuditLog` (intra-module — deep import is fine, but using `from app.modules.audit import AuditLog` would also work) |
| `app/modules/auth/context.py:17` | `from app.modules.auth import User, UserRoleAssignment; from app.modules.org_units import Client, OrganizationalUnit; from app.modules.roles import Role` |
| `app/modules/candidates/authz.py:21` | `from app.modules.candidates.models import Candidate, CandidateJobAssignment` (intra) + `from app.modules.jd import JobPosting` (cross) |
| `app/modules/candidates/service.py:24` | per-module split |
| `app/modules/interview_runtime/service.py:27` | per-module split |
| `app/modules/jd/authz.py:19` | `from app.modules.jd.models import JobPosting` (intra) |
| `app/modules/jd/router.py:17` | already replaced in Stage C |
| `app/modules/jd/service.py:14` | already replaced in Stage C |
| `app/modules/org_units/router.py:8` | `from app.modules.org_units.models import OrganizationalUnit; from app.modules.auth import User` |
| `app/modules/org_units/service.py:12` | `from app.modules.org_units.models import OrganizationalUnit; from app.modules.roles import Role; from app.modules.auth import User, UserRoleAssignment` |
| `app/modules/pipelines/authz.py:13` | per-module split |
| `app/modules/pipelines/participants.py:28` | per-module split |
| `app/modules/pipelines/router.py:26` | per-module split |
| `app/modules/pipelines/service.py:16` | already replaced in Stage C (Step 16.3) |
| `app/modules/roles/router.py:6` | `from app.modules.roles import Role` |
| `app/modules/scheduler/authz.py:9` | `from app.modules.candidates import CandidateJobAssignment; from app.modules.pipelines import JobPipelineStage` |
| `app/modules/scheduler/service.py:21` | per-module split |
| `app/modules/session/router.py:26` | `from app.modules.candidates import Candidate, CandidateJobAssignment; from app.modules.session.models import Session` (intra-module gets the deep import, the cross-module ones go through public API) |
| `app/modules/session/service.py:26` | per-module split |
| `app/modules/settings/router.py:10` | `from app.modules.org_units import Client` |
| `app/modules/settings/service.py:15` | per-module split |

**Discipline:** intra-module imports may use the deep path (`from app.modules.<self>.models import X`) or the public API (`from app.modules.<self> import X`). The AST lint test in Task 21 only fires for cross-module deep imports. Pick whichever is more readable per file; default to deep for intra-module to make the boundary obvious.

For each file in the table, read the existing top-level imports, identify which classes/names actually come from `app.models`, and rewrite. Keep the import block alphabetical-by-source-module.

- [ ] **Step 20.2: Sweep cross-module deep imports**

Run:
```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
# A given module's deep import is forbidden from outside that module.
for m in auth audit notifications org_units roles jd pipelines question_bank candidates session; do
  echo "=== ${m} deep-import callers OUTSIDE ${m}/ ==="
  grep -rn "from app\.modules\.${m}\.[a-z_]\+ import" app/modules/ --include="*.py" \
    | grep -v "app/modules/${m}/" \
    | grep -v test_
done
```

Each line is a violation. Rewrite to use the module's public API (`from app.modules.<m> import X`). If an export is missing from the module's `__init__.py`, add it to `__all__`.

Common patterns:
- `from app.modules.auth.context import UserContext, get_current_user_roles` → `from app.modules.auth import UserContext, get_current_user_roles`
- `from app.modules.audit.service import log_event` + `from app.modules.audit import actions as audit_actions` → consolidate to `from app.modules.audit import actions, log_event` (or use `actions as audit_actions` if the local alias matters)
- `from app.modules.org_units.service import find_company_profile_in_ancestry` → `from app.modules.org_units import find_company_profile_in_ancestry`
- `from app.modules.jd.authz import require_job_access` → `from app.modules.jd import require_job_access`
- `from app.modules.jd.state_machine import transition as jd_transition` → `from app.modules.jd import transition as jd_transition`

Keep import-alphabetization sane.

If a module's `__init__.py` doesn't export the required name, add it. The test in Task 21 will fail for any deep import that didn't get rewritten.

- [ ] **Step 20.3: Verify no shim users remain**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
grep -rn "from app\.models import" app/ --include="*.py"
```

Expected: zero output (every caller has been migrated).

- [ ] **Step 20.4: Delete `app/models.py`**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
rm app/models.py
```

`Base.registry.configure()` already runs at startup (Task 12), and the per-module models imports are already explicit in `app/main.py`'s lifespan, so removing the shim does not break model registration.

- [ ] **Step 20.5: Run pytest, ruff, mypy**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus ruff check . 2>&1 | tail -3
docker compose run --rm nexus mypy app/ 2>&1 | tail -3
```

Expected: `644 passed, 9 failed`. Ruff + mypy clean.

If `mypy` reports `Cannot find module 'app.models'`, that means a caller still references the deleted module — go back to Step 20.1 and find the missed file.

- [ ] **Step 20.6: Commit sub-commit 5 (4d-2)**

```bash
cd /home/ishant/Projects/ProjectX
git add -A
git status
git diff --cached --stat
git commit -m "$(cat <<'EOF'
refactor(imports): sweep cross-module imports to public API; retire app/models.py

Phase 4d-2 of the modular-monolith refactor.

- Every `from app.models import X` rewritten to `from app.modules.<m> import X`
  (or `from app.modules.<m>.models import X` for intra-module callers,
   where deep imports are still allowed and remain more readable).
- Every cross-module `from app.modules.<m>.<internal> import X` rewritten
  to use the module's `__init__.py` public API.
- The transitional re-export shim at app/models.py is deleted; per-module
  registration runs via the explicit `import app.modules.<m>.models`
  block in app/main.py's lifespan, followed by `Base.registry.configure()`.

Routers and Dramatiq actors remain wired via deep import from
app/main.py and app/worker.py — the documented exception to the
public-API rule.

No behavior change. pytest baseline preserved (644 passed, 9 failed).
ruff + mypy clean.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

### Task 21: Add `tests/test_module_boundaries.py` AST-walking lint test (sub-commit 6)

**Files:**
- Create: `backend/nexus/tests/test_module_boundaries.py`

The test walks the AST of every `.py` file under `app/modules/` and asserts no cross-module deep imports. Routers + actor modules are explicitly allowed to be deep-imported by `app/main.py` and `app/worker.py`, but those callers live OUTSIDE `app/modules/` so they don't trip the test.

- [ ] **Step 21.1: Write the test**

Write `backend/nexus/tests/test_module_boundaries.py`:

```python
"""Phase 4d-3 — module boundary lint test.

Walks every .py file under `app/modules/` and asserts no cross-module
deep imports. A "cross-module deep import" means a statement of the
form `from app.modules.<m>.<internal> import X` that appears in a file
outside `app/modules/<m>/`.

The discipline:
- Cross-module callers must use the module's public API:
  `from app.modules.<m> import X`.
- Intra-module deep imports are allowed
  (`from app.modules.<self>.<internal> import X` inside `<self>/`).
- Routers + Dramatiq actors are deep-imported by `app/main.py` and
  `app/worker.py` — but those files live OUTSIDE `app/modules/`, so
  they don't trip this test.

If a new cross-module deep import lands and it's legitimate, add the
exported symbol to the destination module's `__init__.py` __all__
and rewrite the caller. Do NOT add the file to an exemption list —
the rule is the rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Modules whose public surface lives at app/modules/<m>/__init__.py.
# Add new modules here as they're created.
KNOWN_DOMAIN_MODULES = frozenset(
    {
        "admin",
        "ats",
        "analysis",
        "audit",
        "auth",
        "candidates",
        "interview_engine",
        "interview_runtime",
        "jd",
        "notifications",
        "org_units",
        "pipelines",
        "question_bank",
        "reporting",
        "roles",
        "scheduler",
        "session",
        "settings",
    }
)


def _module_root() -> Path:
    """Locate `app/modules/` from this test file's location.

    The test sits at `backend/nexus/tests/test_module_boundaries.py`;
    `app/modules/` is at `backend/nexus/app/modules/`.
    """
    here = Path(__file__).resolve().parent
    return here.parent / "app" / "modules"


def _module_owning_file(path: Path, modules_root: Path) -> str:
    """Return the domain-module name owning `path`.

    e.g. `app/modules/jd/service.py` -> `"jd"`.
    """
    rel = path.relative_to(modules_root)
    return rel.parts[0]


def _iter_python_files(root: Path):
    for p in root.rglob("*.py"):
        # Skip __pycache__ and similar — rglob already handles dotfiles.
        if "__pycache__" in p.parts:
            continue
        yield p


def test_no_cross_module_deep_imports():
    modules_root = _module_root()
    assert modules_root.is_dir(), f"expected app/modules at {modules_root}"

    violations: list[tuple[str, int, str]] = []

    for py_file in _iter_python_files(modules_root):
        owning_module = _module_owning_file(py_file, modules_root)
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError as exc:
            raise AssertionError(f"failed to parse {py_file}: {exc}") from exc

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module is None:
                continue
            if not node.module.startswith("app.modules."):
                continue

            # node.module is e.g. "app.modules.jd.service"
            # We only flag depth >= 4 components (app, modules, <m>, <internal>).
            parts = node.module.split(".")
            if len(parts) < 4:
                # `from app.modules.jd import X` — public API, allowed.
                continue
            target_module = parts[2]
            if target_module not in KNOWN_DOMAIN_MODULES:
                continue
            if target_module == owning_module:
                # Intra-module deep import — allowed.
                continue

            # Cross-module deep import — violation.
            violations.append(
                (
                    str(py_file.relative_to(modules_root.parent.parent)),
                    node.lineno,
                    node.module,
                )
            )

    assert not violations, (
        "Cross-module deep imports detected — every cross-module import "
        "must go through the destination module's `__init__.py` public API.\n\n"
        "Violations:\n"
        + "\n".join(f"  {path}:{lineno} -> {module}" for path, lineno, module in violations)
    )
```

- [ ] **Step 21.2: Run the test**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest tests/test_module_boundaries.py -v
```

Expected: `1 passed`. If it reports violations, each line in the failure tells you exactly which file:lineno → which deep import to rewrite. Go back to Task 20 and fix the identified imports, then re-run.

- [ ] **Step 21.3: Run full pytest**

```bash
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
```

Expected: `645 passed, 9 failed` (one new test added).

- [ ] **Step 21.4: Commit sub-commit 6 (4d-3)**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/tests/test_module_boundaries.py
git commit -m "$(cat <<'EOF'
test(boundaries): add AST-walking module-boundary lint test

Phase 4d-3 of the modular-monolith refactor. The test walks every
.py file under app/modules/ and asserts no cross-module deep
imports — every cross-module import must go through the
destination module's __init__.py public API.

Routers + Dramatiq actors are deep-imported by app/main.py and
app/worker.py respectively, but those callers live outside
app/modules/ so they don't trip this test.

The KNOWN_DOMAIN_MODULES set is the rule's input — add new modules
there as they're created.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

### Task 22: Add "Module Public API" section to `backend/nexus/CLAUDE.md` (sub-commit 7)

**Files:**
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 22.1: Insert the new section**

Open `backend/nexus/CLAUDE.md`. Find the "Code Standards" section near the bottom of the file (or the "Database & Connection Pool" subsection). Insert a new "## Module public API" section ABOVE the "## Dev Commands" section, with this content:

```markdown
## Module public API

Every domain module under `app/modules/<m>/` declares its public surface
via `__init__.py` + `__all__`. Cross-module callers MUST import through
the module's public API, never through internal files.

```python
# CORRECT — public API
from app.modules.org_units import OrganizationalUnit, get_org_unit_ancestry
from app.modules.audit import log_event, actions

# WRONG — cross-module deep import
from app.modules.org_units.service import get_org_unit_ancestry
from app.modules.audit.service import log_event
```

**Rule scope:**
- Applies to imports CROSSING module boundaries (`from app.modules.<other> import X`).
- INTRA-module deep imports are fine
  (`from app.modules.jd.service import X` inside any file under `app/modules/jd/`).
- Routers and Dramatiq actors are deep-imported by `app/main.py` and
  `app/worker.py` — those callers live outside `app/modules/` and do
  not trip the rule.

**Enforcement:** `tests/test_module_boundaries.py` walks the AST of
every `app/modules/<m>/*.py` file and asserts no cross-module deep
import remains. Adding a new domain module? Add its name to
`KNOWN_DOMAIN_MODULES` in that file.

**Why:** every module ends up depending on a small set of well-named
re-exports (`from app.modules.org_units import OrganizationalUnit`),
so refactoring inside a module (rename a service file, split a
schemas file) doesn't ripple across the codebase. The rule also
catches accidentally-deep imports introduced during PR review —
they're a flashing-red asymmetric trip wire, not a "clean it up
later" item.

**Documented exception — auth → org_units:** `auth/router.py`
imports `create_org_unit` from `org_units` to seed the root company
unit during invite acceptance. This is "upward" from a foundational
module into a domain module, which the spec's foundational-trio rule
nominally forbids. It's preserved because (a) it's acyclic
(`org_units/service.py` does not import auth), (b) it's a single
site, and (c) the alternative (post-invite-accept hook registry) is
disproportionate to the boundary purity gained. If the call site
list ever grows, extract an orchestrator module
(e.g. `onboarding/`) that depends on both auth and org_units.
```

(Note: the CLAUDE.md file uses GitHub-flavored markdown; the inner code fences use four backticks to avoid breaking out of the outer code block. Adjust accordingly when copying — if the existing CLAUDE.md doesn't use four-backtick fences, use the standard three-backtick form and rely on context to disambiguate. If unsure, examine an existing code block in CLAUDE.md to match the convention.)

- [ ] **Step 22.2: Commit sub-commit 7 (4d-4)**

```bash
cd /home/ishant/Projects/ProjectX
git add backend/nexus/CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude): add "Module public API" section to backend CLAUDE.md

Phase 4d-4 of the modular-monolith refactor. Documents the
module-boundary discipline that tests/test_module_boundaries.py
enforces, including the documented exception for auth -> org_units
during invite acceptance.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline -1
```

---

## Stage F — Final verification

### Task 23: Full pytest pass-fail diff vs baseline

**Files:** none — verification

- [ ] **Step 23.1: Full run**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus pytest --tb=no -q 2>&1 | tail -5
docker compose run --rm nexus pytest --tb=no -q 2>&1 | grep -E "^FAILED" | sort > /tmp/phase4_final_fails.txt
diff /tmp/phase4_baseline_fails.txt /tmp/phase4_final_fails.txt
```

Expected:
- `645 passed, 9 failed` (3 new tests across `test_startup_integrity.py` and `test_module_boundaries.py`).
- `diff` empty — no test that passed at baseline now fails.

If the diff has `>` lines, STOP. A new failure was introduced by the refactor and must be resolved before opening the PR.

### Task 24: Static checks

**Files:** none

- [ ] **Step 24.1: ruff + mypy**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose run --rm nexus ruff check . 2>&1 | tail -5
docker compose run --rm nexus mypy app/ 2>&1 | tail -5
```

Expected: clean both. Compare against `/tmp/phase4_baseline_ruff.txt` / `/tmp/phase4_baseline_mypy.txt`.

### Task 25: Image rebuild + boot smoke for nexus + nexus-worker + nexus-engine

**Files:** none

- [ ] **Step 25.1: Rebuild the image**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
docker compose build --no-cache nexus 2>&1 | tail -10
```

Expected: clean build. Phase 4 added zero new deps; cached image layers reuse heavily.

- [ ] **Step 25.2: Boot nexus + watch for startup errors**

```bash
docker compose up -d redis postgres
docker compose run --rm -d nexus uvicorn app.main:app --host 0.0.0.0 --port 8000
sleep 5
docker compose logs nexus | tail -40
```

Expected log lines:
- `nexus.startup environment=...`
- `rls.completeness_check_ok ...` (this is `_assert_rls_completeness` succeeding — it walks `_TENANT_SCOPED_TABLES` which still references the same 21 tables).
- No `RuntimeError`, no `sqlalchemy.exc.InvalidRequestError`, no `Could not resolve string FK ...`.

If `Base.registry.configure()` fails with a string-FK error, the per-module model split missed a class — re-grep for missing tables in `Base.metadata.tables` and verify all 9 model files were imported in lifespan.

Tear down:
```bash
docker compose down
```

- [ ] **Step 25.3: Boot nexus-worker — smoke**

```bash
docker compose up -d redis postgres
docker compose run --rm -d nexus-worker dramatiq app.worker --processes 1 --threads 2
sleep 5
docker compose logs nexus-worker | tail -20
docker compose down
```

Expected: clean dramatiq startup, no import errors, no `Base.registry.configure()` failure.

- [ ] **Step 25.4: Boot nexus-engine — smoke**

```bash
docker compose up -d redis postgres
docker compose run --rm -d nexus-engine python -m app.modules.interview_engine
sleep 5
docker compose logs nexus-engine | tail -20
docker compose down
```

Expected: LiveKit Agents worker connects (or fails on missing `LIVEKIT_URL` env, which is also acceptable — the goal here is "no Python-import-time error, no model-config error"). The smoke is at the app-import level, not at the LiveKit-registration level.

If any of the three boots produce an unexpected ImportError, fix it before opening the PR.

---

## Stage G — Open PR

### Task 26: Stage and review the full diff

**Files:** none

- [ ] **Step 26.1: Sub-commit summary**

```bash
cd /home/ishant/Projects/ProjectX
git log --oneline main..HEAD
```

Expected: 6 or 7 commits (5–7 sub-commits + the final docs commit), in this order:
1. `refactor(models): split app/models.py per module + Base.registry.configure()`
2. `refactor(imports): hoist late function-body imports to module top`
3. _(optional, only if Step 16.4 fired or Step 18.2 produced a diff)_
4. `refactor(modules): add __init__.py public-API exports for every domain module`
5. `refactor(imports): sweep cross-module imports to public API; retire app/models.py`
6. `test(boundaries): add AST-walking module-boundary lint test`
7. `docs(claude): add "Module public API" section to backend CLAUDE.md`

Sub-commit 3 is skipped if the auth → org_units edge in Stage D was kept as-is (most-likely outcome).

- [ ] **Step 26.2: Diff stat**

```bash
cd /home/ishant/Projects/ProjectX
git diff --stat main..HEAD | tail -5
git diff --stat main..HEAD | wc -l
```

Expected: roughly 30–50 files changed; large net negative line count from `app/models.py` deletion offset by new per-module `models.py` files. Roughly +2000 / −1500 net.

- [ ] **Step 26.3: Sanity-grep that nothing untoward landed**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
echo "=== app.models references (should be 0) ==="
grep -rn "from app\.models" app/ tests/ --include="*.py"
echo ""
echo "=== late from app.* imports (should be only documented exceptions) ==="
grep -rn "    from app\." app/modules/ --include="*.py" | grep -v test_
echo ""
echo "=== cross-module deep imports (should be 0) ==="
docker compose run --rm nexus pytest tests/test_module_boundaries.py -q
```

Expected: zero `app.models` references; only the documented `auth/admin/__init__.py` lazy singleton in late imports; module-boundaries test passes.

### Task 27: Open the PR

**Files:** none — uses gh

- [ ] **Step 27.1: Push the branch**

```bash
cd /home/ishant/Projects/ProjectX
git push -u origin feat/phase-4-modular-monolith-refactor
```

Expected: clean push, branch created on remote.

- [ ] **Step 27.2: Open PR with gh**

```bash
cd /home/ishant/Projects/ProjectX
gh pr create --base main --title "feat(architecture): Phase 4 — modular monolith refactor" --body "$(cat <<'EOF'
## Summary

Phase 4 of the umbrella modular-monolith spec
(`docs/superpowers/specs/2026-05-01-drop-langfuse-modular-monolith-design.md`):
mechanical refactor that brings the codebase to a defensible "true"
modular monolith. Every domain module owns its data + import surface.

- **4a — Model split.** `app/models.py` (771 lines, 21 ORM classes)
  splits into 9 per-module `models.py` files. Transitional re-export
  shim ships in this same commit; deleted in 4d-2 once all callers
  are migrated. `Base.registry.configure()` runs at lifespan startup
  so any string-FK resolution failure is loud at boot, not at first
  request.
- **4b — Hoist late imports.** Every `from app.*` import inside a
  function body is now a top-level import. The previously-lazy
  `recompute_and_persist_stale` import in jd / pipelines was
  guarded by an outdated cycle comment; the cycle was retired
  when qb's internal split landed (qb/__init__.py is empty;
  qb/service.py has no jd imports). The single intentional
  exception is `auth/admin/__init__.py`'s lazy
  `_get_provider_singleton` factory.
- **4c — Foundational-trio cleanliness.** `auth`, `audit`,
  `notifications` only import each other or non-modules. The single
  `auth → org_units` edge from invite acceptance is preserved as a
  documented exception (acyclic, single site, see CLAUDE.md).
- **4d — Public-API discipline.** Every domain module exports its
  public surface via `__init__.py` + `__all__`. Cross-module deep
  imports forbidden — `tests/test_module_boundaries.py` AST-walks
  every file and asserts the rule. Routers + Dramatiq actors are
  the documented exception (deep-imported from `app/main.py` /
  `app/worker.py`, both outside `app/modules/`).

Sub-commits land as 5-7 logical groups; see `git log` for the
breakdown.

## Test plan

- [x] `pytest --tb=no -q` matches baseline:
      642 passed → 645 passed (+3 new tests),
      same 9 environment-driven failures.
- [x] `ruff check .` clean.
- [x] `mypy app/` clean.
- [x] `tests/test_startup_integrity.py` — `Base.registry.configure()`
      smoke + per-module table registration assertions.
- [x] `tests/test_module_boundaries.py` — AST-walking lint test
      enforcing the public-API discipline.
- [x] `_assert_rls_completeness` still passes at app boot.
- [x] `nexus`, `nexus-worker`, `nexus-engine` all boot cleanly from
      the same image (no model-config errors).
- [ ] Optional: live LiveKit smoke (full candidate session) —
      run as part of the soak before the next phase.

## Hard-contract verification (per spec Q5)

This PR touches:
- ORM file layout
- Import statements
- One CLAUDE.md doc

It does NOT touch:
- Public API URLs / shapes / headers / SSE event names
- LiveKit room / token / participant attributes
- Database schema (no Alembic migration in this PR)
- Frontend code

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed. The eventual #5.

- [ ] **Step 27.3: Final sanity check**

```bash
cd /home/ishant/Projects/ProjectX
gh pr view --json url,title,baseRefName | tail -10
```

Expected: PR exists, title matches, `baseRefName: "main"`.

---

## Phase 4 done.

Phase 5 (package upgrade sweep — backend + frontend, patch/minor only) is the next branch off main; not part of this PR.
