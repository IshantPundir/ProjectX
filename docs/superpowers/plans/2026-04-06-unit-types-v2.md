# Org Unit Types v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace behaviourally-identical unit types with 5 semantically-distinct types (`company`, `division`, `client_account`, `region`, `team`) with enforced nesting rules, workspace modes, and auto-created root company units.

**Architecture:** Mutate the initial Supabase migration to add new columns and update the CHECK constraint, then `supabase db reset`. Backend service layer enforces all type rules, nesting constraints, and lifecycle invariants. Frontend onboarding becomes workspace-type selection + company profile. Org unit pages gate available types by workspace mode.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async (asyncpg), PostgreSQL 17, pytest-asyncio, Next.js 16, React 19, TypeScript, Tailwind CSS v4

**Spec:** `docs/superpowers/specs/2026-04-06-unit-types-v2-design.md`

---

## File Map

### Files to Create

| File | Responsibility |
|---|---|
| `backend/supabase/migrations/20260406000000_unit_types_v2.sql` | Unique partial index `one_root_per_tenant` |
| `backend/nexus/tests/test_org_unit_types.py` | 29 tests for all type rules, nesting, and migration verification |

### Files to Modify

| File | What Changes |
|---|---|
| `backend/supabase/migrations/20260405000000_initial_schema.sql` | New CHECK constraint, add `is_root`, `company_profile`, `workspace_mode` columns |
| `backend/nexus/app/models.py` | Add `is_root`, `company_profile` to OrganizationalUnit; `workspace_mode` to Client |
| `backend/nexus/app/modules/org_units/service.py` | New VALID_UNIT_TYPES, validation in create/update/delete, new params |
| `backend/nexus/app/modules/org_units/schemas.py` | Add fields to create/update/response schemas |
| `backend/nexus/app/modules/org_units/router.py` | Load workspace_mode, pass company_profile, update responses |
| `backend/nexus/app/modules/auth/context.py` | Add `workspace_mode` to UserContext |
| `backend/nexus/app/modules/auth/router.py` | Auto-create root unit in complete_invite, workspace_mode in /me |
| `backend/nexus/app/modules/auth/schemas.py` | Add `root_unit_id` to CompleteInviteResponse, `workspace_mode` to MeResponse |
| `backend/nexus/app/modules/settings/router.py` | Add PATCH /api/settings/workspace endpoint |
| `backend/nexus/app/modules/settings/schemas.py` | Add WorkspaceModeRequest |
| `backend/nexus/app/modules/audit/actions.py` | Add CLIENT_WORKSPACE_MODE_CHANGED |
| `backend/nexus/tests/conftest.py` | Update default unit_type from "department" to "division" |
| `backend/nexus/CLAUDE.md` | Add Organizational Unit Types section |
| `frontend/app/app/onboarding/page.tsx` | Replace with workspace type + company profile steps |
| `frontend/app/app/(dashboard)/settings/org-units/page.tsx` | New UNIT_TYPES, workspace_mode gating |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx` | Hide delete on root, hide type on company, company_profile section |
| `frontend/app/app/settings/org-units/new/page.tsx` | Add auth guard + deprecation comment |
| `frontend/app/app/(dashboard)/layout.tsx` | Update getMe type for workspace_mode |

---

### Task 1: Database Migrations + ORM Models

**Files:**
- Modify: `backend/supabase/migrations/20260405000000_initial_schema.sql`
- Create: `backend/supabase/migrations/20260406000000_unit_types_v2.sql`
- Modify: `backend/nexus/app/models.py`
- Modify: `backend/nexus/tests/conftest.py`

- [ ] **Step 1: Mutate the initial migration — organizational_units table**

In `backend/supabase/migrations/20260405000000_initial_schema.sql`, replace the `organizational_units` table CHECK constraint and add new columns. Find:

```sql
    unit_type       TEXT NOT NULL
                        CHECK (unit_type IN ('client_account', 'department', 'team', 'branch', 'region')),
    created_by      UUID REFERENCES public.users(id),
    deletable_by    UUID REFERENCES public.users(id),
    admin_delete_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```

Replace with:

```sql
    unit_type       TEXT NOT NULL
                        CHECK (unit_type IN ('company', 'division', 'client_account', 'region', 'team')),
    is_root         BOOLEAN NOT NULL DEFAULT FALSE,
    company_profile JSONB,
    created_by      UUID REFERENCES public.users(id),
    deletable_by    UUID REFERENCES public.users(id),
    admin_delete_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```

- [ ] **Step 2: Mutate the initial migration — clients table**

In the same file, add `workspace_mode` to the `clients` table. Find:

```sql
    onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
    super_admin_id UUID,                     -- FK added after users table
```

Replace with:

```sql
    onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
    workspace_mode TEXT NOT NULL DEFAULT 'enterprise'
                       CHECK (workspace_mode IN ('enterprise', 'agency')),
    super_admin_id UUID,                     -- FK added after users table
```

- [ ] **Step 3: Create the v2 migration — unique partial index**

Create `backend/supabase/migrations/20260406000000_unit_types_v2.sql`:

```sql
-- One root unit (parent_unit_id IS NULL) per tenant.
CREATE UNIQUE INDEX one_root_per_tenant
  ON public.organizational_units (client_id)
  WHERE parent_unit_id IS NULL;
```

- [ ] **Step 4: Run supabase db reset**

```bash
cd /home/ishant/Projects/ProjectX/backend && supabase db reset
```

Expected: clean reset, all migrations replay successfully.

- [ ] **Step 5: Update OrganizationalUnit ORM model**

In `backend/nexus/app/models.py`, add two columns to the `OrganizationalUnit` class after `unit_type`:

```python
    is_root: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    company_profile: Mapped[dict | None] = mapped_column(JSONB)
```

- [ ] **Step 6: Update Client ORM model**

In `backend/nexus/app/models.py`, add to the `Client` class after `onboarding_complete`:

```python
    workspace_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="'enterprise'")
```

- [ ] **Step 7: Update conftest.py default unit_type**

In `backend/nexus/tests/conftest.py`, the `create_test_org_unit` factory uses `"department"` as the default `unit_type`. Change it to `"division"`:

```python
    defaults = {
        "client_id": client_id,
        "name": f"Test Unit {n}",
        "unit_type": "division",
        "created_at": now,
        "updated_at": now,
    }
```

- [ ] **Step 8: Verify existing tests still pass**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests pass (the default type change is compatible).

- [ ] **Step 9: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add ../../backend/supabase/migrations/20260405000000_initial_schema.sql \
       ../../backend/supabase/migrations/20260406000000_unit_types_v2.sql \
       app/models.py tests/conftest.py
git commit -m "feat: add unit types v2 schema — is_root, company_profile, workspace_mode, one_root_per_tenant index"
```

---

### Task 2: Service Layer — create_org_unit Validation + Nesting Rules

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`

- [ ] **Step 1: Update VALID_UNIT_TYPES**

In `backend/nexus/app/modules/org_units/service.py`, replace line 15:

```python
VALID_UNIT_TYPES = {"company", "division", "client_account", "region", "team"}
```

- [ ] **Step 2: Add new params to create_org_unit**

Update the `create_org_unit` signature — add `workspace_mode` and `company_profile` after `ip_address`:

```python
async def create_org_unit(
    db: AsyncSession,
    client_id: uuid_mod.UUID,
    name: str,
    unit_type: str,
    parent_unit_id: uuid_mod.UUID | None = None,
    created_by: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
    workspace_mode: str = "enterprise",
    company_profile: dict | None = None,
) -> OrganizationalUnit:
```

- [ ] **Step 3: Add validation block**

After the existing `if unit_type not in VALID_UNIT_TYPES:` check, add the 5-rule validation block:

```python
    if unit_type not in VALID_UNIT_TYPES:
        raise ValueError(f"Invalid unit_type. Must be one of: {sorted(VALID_UNIT_TYPES)}")

    # Rule 1: company must be root (no parent)
    if unit_type == "company" and parent_unit_id is not None:
        raise ValueError("A company root unit cannot have a parent unit.")

    # Rule 2: only one company unit per tenant
    if unit_type == "company":
        existing_root = await db.execute(
            select(OrganizationalUnit).where(
                OrganizationalUnit.client_id == client_id,
                OrganizationalUnit.parent_unit_id == None,
            )
        )
        if existing_root.scalar_one_or_none():
            raise ValueError("A root company unit already exists for this tenant.")

    # Rule 3: company_profile required for company and client_account
    if unit_type in ("company", "client_account") and not company_profile:
        raise ValueError(
            f"A company_profile is required for units of type '{unit_type}'."
        )

    # Rule 4: client_account only in agency workspaces
    if unit_type == "client_account" and workspace_mode != "agency":
        raise ValueError("Client accounts are only available in agency workspaces.")

    # Rule 5: parent-based nesting enforcement
    if parent_unit_id is not None:
        parent_result = await db.execute(
            select(OrganizationalUnit).where(OrganizationalUnit.id == parent_unit_id)
        )
        parent_unit = parent_result.scalar_one_or_none()
        if not parent_unit:
            raise ValueError("Parent unit not found.")

        if parent_unit.unit_type == "team":
            raise ValueError("Teams are leaf nodes and cannot contain sub-units.")

        if unit_type == "client_account" and parent_unit.unit_type == "client_account":
            raise ValueError(
                "A client account cannot be nested under another client account."
            )
```

- [ ] **Step 4: Update unit instantiation**

Replace the `OrganizationalUnit(...)` constructor call:

```python
    unit = OrganizationalUnit(
        client_id=client_id,
        name=name,
        unit_type=unit_type,
        parent_unit_id=parent_unit_id,
        created_by=created_by,
        deletable_by=created_by,
        is_root=(unit_type == "company"),
        company_profile=company_profile,
    )
```

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/org_units/service.py
git commit -m "feat: add unit type validation and nesting rules to create_org_unit"
```

---

### Task 3: Service Layer — update_org_unit + delete_org_unit

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py`

- [ ] **Step 1: Add new params to update_org_unit**

Update the `update_org_unit` signature — add `company_profile` and `set_company_profile` after `ip_address`:

```python
async def update_org_unit(
    db: AsyncSession,
    unit: OrganizationalUnit,
    name: str | None,
    unit_type: str | None,
    deletable_by: str | None = None,
    set_deletable_by: bool = False,
    admin_delete_disabled: bool | None = None,
    actor_id: uuid_mod.UUID | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
    company_profile: dict | None = None,
    set_company_profile: bool = False,
) -> OrganizationalUnit:
```

- [ ] **Step 2: Add company_profile to before/after audit diff**

Update the `before` dict at the top of the function:

```python
    before = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
        "company_profile": str(unit.company_profile) if unit.company_profile else None,
    }
```

And the `after` dict:

```python
    after = {
        "name": unit.name,
        "unit_type": unit.unit_type,
        "deletable_by": str(unit.deletable_by) if unit.deletable_by else None,
        "admin_delete_disabled": unit.admin_delete_disabled,
        "company_profile": str(unit.company_profile) if unit.company_profile else None,
    }
```

- [ ] **Step 3: Add type immutability and company_profile validation**

After the existing `if unit_type is not None and unit_type not in VALID_UNIT_TYPES:` check, add:

```python
    # Prevent changing unit_type of a root company unit
    if unit_type is not None and unit.unit_type == "company" and unit_type != "company":
        raise ValueError("The unit type of the root company unit cannot be changed.")
```

After the existing `set_deletable_by` block (before the `after` dict), add:

```python
    # Update company_profile if explicitly requested
    if set_company_profile:
        if unit.unit_type in ("company", "client_account") and not company_profile:
            raise ValueError(
                f"A company_profile is required for units of type '{unit.unit_type}'."
            )
        unit.company_profile = company_profile
```

- [ ] **Step 4: Add is_root hard block to delete_org_unit**

In `delete_org_unit`, add at the very top of the function body, after fetching the unit and the `if not unit:` check:

```python
    if unit.is_root:
        raise ValueError("The root company unit cannot be deleted.")
```

Place it before the `if not is_super_admin:` authorization block.

- [ ] **Step 5: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/org_units/service.py
git commit -m "feat: add type immutability, company_profile updates, and root delete block"
```

---

### Task 4: Schemas + Router + list_org_units Response

**Files:**
- Modify: `backend/nexus/app/modules/org_units/schemas.py`
- Modify: `backend/nexus/app/modules/org_units/router.py`

- [ ] **Step 1: Update schemas**

Replace the full contents of `backend/nexus/app/modules/org_units/schemas.py`:

```python
from pydantic import BaseModel


class CreateOrgUnitRequest(BaseModel):
    name: str
    unit_type: str
    parent_unit_id: str | None = None
    company_profile: dict | None = None


class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    deletable_by: str | None = None
    admin_delete_disabled: bool | None = None
    company_profile: dict | None = None
    set_company_profile: bool = False


class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    is_root: bool
    company_profile: dict | None
    created_at: str
    created_by: str | None
    created_by_email: str | None
    deletable_by: str | None
    deletable_by_email: str | None
    admin_delete_disabled: bool
    is_accessible: bool = True
    admin_emails: list[str] = []


class AssignRoleRequest(BaseModel):
    user_id: str
    role_id: str


class MemberRole(BaseModel):
    role_id: str
    role_name: str
    assigned_at: str


class OrgUnitMember(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    roles: list[MemberRole]
```

- [ ] **Step 2: Update _build_response in router**

In `backend/nexus/app/modules/org_units/router.py`, update the `_build_response` function to include `is_root` and `company_profile`:

```python
def _build_response(
    unit: OrganizationalUnit, member_count: int, email_map: dict
) -> OrgUnitResponse:
    return OrgUnitResponse(
        id=str(unit.id),
        client_id=str(unit.client_id),
        parent_unit_id=str(unit.parent_unit_id) if unit.parent_unit_id else None,
        name=unit.name,
        unit_type=unit.unit_type,
        member_count=member_count,
        is_root=unit.is_root,
        company_profile=unit.company_profile,
        created_at=unit.created_at.isoformat(),
        created_by=str(unit.created_by) if unit.created_by else None,
        created_by_email=email_map.get(unit.created_by) if unit.created_by else None,
        deletable_by=str(unit.deletable_by) if unit.deletable_by else None,
        deletable_by_email=email_map.get(unit.deletable_by) if unit.deletable_by else None,
        admin_delete_disabled=unit.admin_delete_disabled,
    )
```

- [ ] **Step 3: Update create endpoint to pass workspace_mode and company_profile**

In the `create_unit` function, add a `Client` import (already imported from `app.models`) and load the client before the service call:

```python
    # Load workspace_mode for the tenant
    client_result = await db.execute(
        select(Client).where(Client.id == ctx.user.tenant_id)
    )
    client = client_result.scalar_one()

    try:
        unit = await create_org_unit(
            db,
            ctx.user.tenant_id,
            data.name,
            data.unit_type,
            parent_id,
            created_by=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
            workspace_mode=client.workspace_mode,
            company_profile=data.company_profile,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

Add the `Client` import to the router's model imports if not already present:

```python
from app.models import Client, OrganizationalUnit, User
```

- [ ] **Step 4: Update update endpoint to pass company_profile**

In the `update_unit` function, update the service call to pass `company_profile` and `set_company_profile`:

```python
    try:
        unit = await update_org_unit(
            db,
            unit,
            data.name,
            data.unit_type,
            deletable_by=data.deletable_by,
            set_deletable_by=set_deletable_by,
            admin_delete_disabled=data.admin_delete_disabled,
            actor_id=ctx.user.id,
            actor_email=ctx.user.email,
            ip_address=request.client.host if request.client else None,
            company_profile=data.company_profile,
            set_company_profile=data.set_company_profile,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 5: Update list_org_units response dict**

In `backend/nexus/app/modules/org_units/service.py`, update the `list_org_units` return dict to include `is_root` and `company_profile`. Add these two lines to the dict comprehension:

```python
            "is_root": u.is_root,
            "company_profile": u.company_profile,
```

Add them after `"member_count"` and before `"created_at"`.

- [ ] **Step 6: Run tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All existing tests pass.

- [ ] **Step 7: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/org_units/schemas.py app/modules/org_units/router.py app/modules/org_units/service.py
git commit -m "feat: update schemas, router, and list response for unit types v2"
```

---

### Task 5: Workspace Mode Endpoint + UserContext + MeResponse

**Files:**
- Modify: `backend/nexus/app/modules/auth/context.py`
- Modify: `backend/nexus/app/modules/auth/schemas.py`
- Modify: `backend/nexus/app/modules/auth/router.py`
- Modify: `backend/nexus/app/modules/settings/router.py`
- Modify: `backend/nexus/app/modules/settings/schemas.py`
- Modify: `backend/nexus/app/modules/audit/actions.py`

- [ ] **Step 1: Add workspace_mode to UserContext**

In `backend/nexus/app/modules/auth/context.py`, add `workspace_mode` to the `UserContext` dataclass:

```python
@dataclass
class UserContext:
    user: User
    is_super_admin: bool
    workspace_mode: str = "enterprise"
    assignments: list[RoleAssignment] = field(default_factory=list)
```

In `get_current_user_roles`, populate it from the client row (which is already loaded):

```python
    return UserContext(
        user=user,
        is_super_admin=is_super_admin,
        workspace_mode=client.workspace_mode,
        assignments=assignments,
    )
```

- [ ] **Step 2: Add workspace_mode to MeResponse**

In `backend/nexus/app/modules/auth/schemas.py`, add to `MeResponse`:

```python
class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    tenant_id: str
    client_name: str
    is_super_admin: bool
    onboarding_complete: bool
    has_org_units: bool
    workspace_mode: str
    assignments: list[RoleAssignmentResponse]
```

- [ ] **Step 3: Update /me handler to include workspace_mode**

In `backend/nexus/app/modules/auth/router.py`, update the `get_current_user` function's `MeResponse` constructor to include `workspace_mode`:

```python
    return MeResponse(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        tenant_id=str(user.tenant_id),
        client_name=client.name,
        is_super_admin=ctx.is_super_admin,
        onboarding_complete=client.onboarding_complete,
        has_org_units=has_org_units,
        workspace_mode=ctx.workspace_mode,
        assignments=[
            RoleAssignmentResponse(
                org_unit_id=str(a.org_unit_id),
                org_unit_name=a.org_unit_name,
                role_name=a.role_name,
                permissions=a.permissions,
            )
            for a in ctx.assignments
        ],
    )
```

- [ ] **Step 4: Add audit action constant**

In `backend/nexus/app/modules/audit/actions.py`, add after `CLIENT_ONBOARDING_COMPLETED`:

```python
CLIENT_WORKSPACE_MODE_CHANGED = "client.workspace_mode_changed"
```

- [ ] **Step 5: Add WorkspaceModeRequest schema**

In `backend/nexus/app/modules/settings/schemas.py`, add at the end:

```python
class WorkspaceModeRequest(BaseModel):
    workspace_mode: str
```

- [ ] **Step 6: Add PATCH /api/settings/workspace endpoint**

In `backend/nexus/app/modules/settings/router.py`, add at the end of the file. First add needed imports near the top:

```python
from app.modules.audit import actions as audit_actions
from app.modules.audit.service import log_event
from app.modules.settings.schemas import (
    ResendInviteResponse,
    TeamInviteRequest,
    TeamInviteResponse,
    TeamMember,
    WorkspaceModeRequest,
)
```

Then add the endpoint:

```python
# --- Workspace settings ---

workspace_router = APIRouter(prefix="/api/settings", tags=["settings"])


@workspace_router.patch(
    "/workspace",
    dependencies=[require_super_admin()],
)
async def update_workspace_mode(
    data: WorkspaceModeRequest,
    request: Request,
    ctx: UserContext = Depends(get_current_user_roles),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict[str, str]:
    """Set workspace mode (enterprise or agency). Super admin only."""
    if data.workspace_mode not in ("enterprise", "agency"):
        raise HTTPException(status_code=400, detail="workspace_mode must be 'enterprise' or 'agency'")

    result = await db.execute(select(Client).where(Client.id == ctx.user.tenant_id))
    client = result.scalar_one()

    old_mode = client.workspace_mode
    client.workspace_mode = data.workspace_mode

    await log_event(
        db,
        tenant_id=ctx.user.tenant_id,
        actor_id=ctx.user.id,
        actor_email=ctx.user.email,
        action=audit_actions.CLIENT_WORKSPACE_MODE_CHANGED,
        resource="client",
        resource_id=ctx.user.tenant_id,
        payload={"from": old_mode, "to": data.workspace_mode},
        ip_address=request.client.host if request.client else None,
    )

    return {"status": "ok", "workspace_mode": data.workspace_mode}
```

- [ ] **Step 7: Register the workspace_router in main.py**

In `backend/nexus/app/main.py`, import and include the workspace router alongside the existing settings router. Find the settings router import and registration, then add:

```python
from app.modules.settings.router import router as settings_router, workspace_router
```

And register it:

```python
application.include_router(workspace_router)
```

Note: You'll need to update the import in `main.py` to import both `router` and `workspace_router` from the settings module.

- [ ] **Step 8: Run tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

- [ ] **Step 9: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/auth/context.py app/modules/auth/schemas.py app/modules/auth/router.py \
       app/modules/settings/router.py app/modules/settings/schemas.py \
       app/modules/audit/actions.py app/main.py
git commit -m "feat: add workspace mode endpoint, UserContext.workspace_mode, and MeResponse.workspace_mode"
```

---

### Task 6: Auto-Create Root Unit in complete_invite

**Files:**
- Modify: `backend/nexus/app/modules/auth/router.py`
- Modify: `backend/nexus/app/modules/auth/schemas.py`

- [ ] **Step 1: Update CompleteInviteResponse**

In `backend/nexus/app/modules/auth/schemas.py`, add `root_unit_id` to `CompleteInviteResponse`:

```python
class CompleteInviteResponse(BaseModel):
    redirect_to: str
    user_id: str
    tenant_id: str
    root_unit_id: str
```

- [ ] **Step 2: Add root unit auto-creation to complete_invite**

In `backend/nexus/app/modules/auth/router.py`, inside the `if is_super_admin:` block, after the `UPDATE clients SET super_admin_id` statement, add:

```python
    # If invited by projectx admin → this is the super admin
    is_super_admin = claimed_row.projectx_admin_id is not None
    root_unit_id = ""
    if is_super_admin:
        await db.execute(
            sqlalchemy.text(
                "UPDATE public.clients SET super_admin_id = :user_id WHERE id = :tenant_id"
            ),
            {"user_id": str(user.id), "tenant_id": str(claimed_row.tenant_id)},
        )

        # Auto-create root company unit with placeholder profile
        from app.modules.org_units.service import create_org_unit as _create_root_unit

        root_unit = await _create_root_unit(
            db=db,
            client_id=uuid_mod.UUID(str(claimed_row.tenant_id)),
            name="Company",
            unit_type="company",
            parent_unit_id=None,
            created_by=user.id,
            actor_email=oauth_email,
            workspace_mode="enterprise",
            company_profile={
                "display_name": "",
                "industry": "",
                "company_size": "",
                "culture_summary": "",
                "hiring_bar": "",
                "brand_voice": "professional",
                "what_good_looks_like": "",
            },
        )
        root_unit_id = str(root_unit.id)
```

Update the return statement:

```python
    return CompleteInviteResponse(
        redirect_to=redirect_to,
        user_id=str(user.id),
        tenant_id=str(claimed_row.tenant_id),
        root_unit_id=root_unit_id,
    )
```

- [ ] **Step 3: Run tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add app/modules/auth/router.py app/modules/auth/schemas.py
git commit -m "feat: auto-create root company unit during super admin invite claim"
```

---

### Task 7: Backend Tests (29 tests)

**Files:**
- Create: `backend/nexus/tests/test_org_unit_types.py`

- [ ] **Step 1: Write all 29 tests**

Create `backend/nexus/tests/test_org_unit_types.py`:

```python
"""Tests for unit type v2 — behavioural rules, nesting constraints, and migration verification."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrganizationalUnit
from app.modules.org_units.service import create_org_unit, delete_org_unit, update_org_unit
from tests.conftest import create_test_client, create_test_org_unit, create_test_user

PLACEHOLDER_PROFILE = {
    "display_name": "Test",
    "industry": "Tech",
    "company_size": "Startup",
    "culture_summary": "",
    "hiring_bar": "",
    "brand_voice": "professional",
    "what_good_looks_like": "",
}


# ---------------------------------------------------------------------------
# Helper: create a company root unit for a given client
# ---------------------------------------------------------------------------

async def _create_root(db: AsyncSession, client_id: uuid.UUID, user_id: uuid.UUID | None = None):
    return await create_org_unit(
        db=db,
        client_id=client_id,
        name="Root",
        unit_type="company",
        parent_unit_id=None,
        created_by=user_id,
        workspace_mode="enterprise",
        company_profile=PLACEHOLDER_PROFILE,
    )


# ===== Company type rules (5) =============================================


@pytest.mark.asyncio
async def test_company_with_parent_raises(db: AsyncSession):
    """Creating a company unit with a parent must raise ValueError."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)

    with pytest.raises(ValueError, match="cannot have a parent"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Bad",
            unit_type="company",
            parent_unit_id=root.id,
            workspace_mode="enterprise",
            company_profile=PLACEHOLDER_PROFILE,
        )


@pytest.mark.asyncio
async def test_second_company_in_same_tenant_raises(db: AsyncSession):
    """Only one company root per tenant."""
    client = await create_test_client(db)
    await _create_root(db, client.id)

    with pytest.raises(ValueError, match="already exists"):
        await _create_root(db, client.id)


@pytest.mark.asyncio
async def test_company_without_profile_raises(db: AsyncSession):
    """Company unit requires company_profile."""
    client = await create_test_client(db)

    with pytest.raises(ValueError, match="company_profile is required"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Root",
            unit_type="company",
            parent_unit_id=None,
            workspace_mode="enterprise",
            company_profile=None,
        )


@pytest.mark.asyncio
async def test_delete_root_unit_raises(db: AsyncSession):
    """Deleting a root unit (is_root=True) must raise ValueError."""
    client = await create_test_client(db)
    user = await create_test_user(db, client.id)
    root = await _create_root(db, client.id, user.id)

    with pytest.raises(ValueError, match="cannot be deleted"):
        await delete_org_unit(
            db=db,
            org_unit_id=root.id,
            caller_user_id=user.id,
            is_super_admin=True,
            caller_has_admin_role=True,
        )


@pytest.mark.asyncio
async def test_change_type_of_root_company_raises(db: AsyncSession):
    """Changing unit_type of a root company unit must raise ValueError."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)

    with pytest.raises(ValueError, match="cannot be changed"):
        await update_org_unit(db, root, name=None, unit_type="division")


# ===== Client account rules (7) ===========================================


@pytest.mark.asyncio
async def test_client_account_without_profile_raises(db: AsyncSession):
    """client_account requires company_profile."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)

    with pytest.raises(ValueError, match="company_profile is required"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Acme",
            unit_type="client_account",
            parent_unit_id=root.id,
            workspace_mode="agency",
            company_profile=None,
        )


@pytest.mark.asyncio
async def test_client_account_in_enterprise_raises(db: AsyncSession):
    """client_account only available in agency workspaces."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)

    with pytest.raises(ValueError, match="agency workspaces"):
        await create_org_unit(
            db=db,
            client_id=client.id,
            name="Acme",
            unit_type="client_account",
            parent_unit_id=root.id,
            workspace_mode="enterprise",
            company_profile=PLACEHOLDER_PROFILE,
        )


@pytest.mark.asyncio
async def test_client_account_under_client_account_raises(db: AsyncSession):
    """client_account cannot nest under another client_account."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca1 = await create_org_unit(
        db=db, client_id=client.id, name="CA1", unit_type="client_account",
        parent_unit_id=root.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE,
    )

    with pytest.raises(ValueError, match="cannot be nested under another client account"):
        await create_org_unit(
            db=db, client_id=client.id, name="CA2", unit_type="client_account",
            parent_unit_id=ca1.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE,
        )


@pytest.mark.asyncio
async def test_client_account_under_team_raises(db: AsyncSession):
    """client_account cannot nest under a team."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db, client_id=client.id, name="Team", unit_type="team",
        parent_unit_id=root.id, workspace_mode="agency",
    )

    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db, client_id=client.id, name="Acme", unit_type="client_account",
            parent_unit_id=team.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE,
        )


@pytest.mark.asyncio
async def test_client_account_under_company_success(db: AsyncSession):
    """client_account under company (agency) should succeed."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca = await create_org_unit(
        db=db, client_id=client.id, name="Acme", unit_type="client_account",
        parent_unit_id=root.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE,
    )
    assert ca.unit_type == "client_account"


@pytest.mark.asyncio
async def test_client_account_under_division_success(db: AsyncSession):
    """client_account under division (agency) should succeed."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    div = await create_org_unit(
        db=db, client_id=client.id, name="Div", unit_type="division",
        parent_unit_id=root.id, workspace_mode="agency",
    )
    ca = await create_org_unit(
        db=db, client_id=client.id, name="Acme", unit_type="client_account",
        parent_unit_id=div.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE,
    )
    assert ca.unit_type == "client_account"


@pytest.mark.asyncio
async def test_client_account_under_region_success(db: AsyncSession):
    """client_account under region (agency) should succeed."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    region = await create_org_unit(
        db=db, client_id=client.id, name="APAC", unit_type="region",
        parent_unit_id=root.id, workspace_mode="agency",
    )
    ca = await create_org_unit(
        db=db, client_id=client.id, name="Acme", unit_type="client_account",
        parent_unit_id=region.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE,
    )
    assert ca.unit_type == "client_account"


# ===== Team leaf node rules (4) ===========================================


@pytest.mark.asyncio
async def test_division_under_team_raises(db: AsyncSession):
    """division under team must raise."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db, client_id=client.id, name="Team", unit_type="team", parent_unit_id=root.id,
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db, client_id=client.id, name="Div", unit_type="division", parent_unit_id=team.id,
        )


@pytest.mark.asyncio
async def test_region_under_team_raises(db: AsyncSession):
    """region under team must raise."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db, client_id=client.id, name="Team", unit_type="team", parent_unit_id=root.id,
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db, client_id=client.id, name="APAC", unit_type="region", parent_unit_id=team.id,
        )


@pytest.mark.asyncio
async def test_team_under_team_raises(db: AsyncSession):
    """team under team must raise."""
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db, client_id=client.id, name="Team1", unit_type="team", parent_unit_id=root.id,
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db, client_id=client.id, name="Team2", unit_type="team", parent_unit_id=team.id,
        )


@pytest.mark.asyncio
async def test_client_account_under_team_via_team_parent_path(db: AsyncSession):
    """client_account under team rejected via the team-parent check, not client_account-specific."""
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    team = await create_org_unit(
        db=db, client_id=client.id, name="Team", unit_type="team", parent_unit_id=root.id,
        workspace_mode="agency",
    )
    with pytest.raises(ValueError, match="leaf nodes"):
        await create_org_unit(
            db=db, client_id=client.id, name="Acme", unit_type="client_account",
            parent_unit_id=team.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE,
        )


# ===== Valid nesting (12) ==================================================


@pytest.mark.asyncio
async def test_division_under_company(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    u = await create_org_unit(db=db, client_id=client.id, name="D", unit_type="division", parent_unit_id=root.id)
    assert u.unit_type == "division"


@pytest.mark.asyncio
async def test_division_under_client_account(db: AsyncSession):
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca = await create_org_unit(db=db, client_id=client.id, name="CA", unit_type="client_account", parent_unit_id=root.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE)
    u = await create_org_unit(db=db, client_id=client.id, name="D", unit_type="division", parent_unit_id=ca.id)
    assert u.unit_type == "division"


@pytest.mark.asyncio
async def test_division_under_division(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    d1 = await create_org_unit(db=db, client_id=client.id, name="D1", unit_type="division", parent_unit_id=root.id)
    d2 = await create_org_unit(db=db, client_id=client.id, name="D2", unit_type="division", parent_unit_id=d1.id)
    assert d2.unit_type == "division"


@pytest.mark.asyncio
async def test_division_under_region(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    r = await create_org_unit(db=db, client_id=client.id, name="R", unit_type="region", parent_unit_id=root.id)
    d = await create_org_unit(db=db, client_id=client.id, name="D", unit_type="division", parent_unit_id=r.id)
    assert d.unit_type == "division"


@pytest.mark.asyncio
async def test_region_under_company(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    u = await create_org_unit(db=db, client_id=client.id, name="R", unit_type="region", parent_unit_id=root.id)
    assert u.unit_type == "region"


@pytest.mark.asyncio
async def test_region_under_client_account(db: AsyncSession):
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca = await create_org_unit(db=db, client_id=client.id, name="CA", unit_type="client_account", parent_unit_id=root.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE)
    u = await create_org_unit(db=db, client_id=client.id, name="R", unit_type="region", parent_unit_id=ca.id)
    assert u.unit_type == "region"


@pytest.mark.asyncio
async def test_region_under_division(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    d = await create_org_unit(db=db, client_id=client.id, name="D", unit_type="division", parent_unit_id=root.id)
    r = await create_org_unit(db=db, client_id=client.id, name="R", unit_type="region", parent_unit_id=d.id)
    assert r.unit_type == "region"


@pytest.mark.asyncio
async def test_region_under_region(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    r1 = await create_org_unit(db=db, client_id=client.id, name="R1", unit_type="region", parent_unit_id=root.id)
    r2 = await create_org_unit(db=db, client_id=client.id, name="R2", unit_type="region", parent_unit_id=r1.id)
    assert r2.unit_type == "region"


@pytest.mark.asyncio
async def test_team_under_company(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    u = await create_org_unit(db=db, client_id=client.id, name="T", unit_type="team", parent_unit_id=root.id)
    assert u.unit_type == "team"


@pytest.mark.asyncio
async def test_team_under_division(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    d = await create_org_unit(db=db, client_id=client.id, name="D", unit_type="division", parent_unit_id=root.id)
    t = await create_org_unit(db=db, client_id=client.id, name="T", unit_type="team", parent_unit_id=d.id)
    assert t.unit_type == "team"


@pytest.mark.asyncio
async def test_team_under_client_account(db: AsyncSession):
    client = await create_test_client(db, workspace_mode="agency")
    root = await _create_root(db, client.id)
    ca = await create_org_unit(db=db, client_id=client.id, name="CA", unit_type="client_account", parent_unit_id=root.id, workspace_mode="agency", company_profile=PLACEHOLDER_PROFILE)
    t = await create_org_unit(db=db, client_id=client.id, name="T", unit_type="team", parent_unit_id=ca.id, workspace_mode="agency")
    assert t.unit_type == "team"


@pytest.mark.asyncio
async def test_team_under_region(db: AsyncSession):
    client = await create_test_client(db)
    root = await _create_root(db, client.id)
    r = await create_org_unit(db=db, client_id=client.id, name="R", unit_type="region", parent_unit_id=root.id)
    t = await create_org_unit(db=db, client_id=client.id, name="T", unit_type="team", parent_unit_id=r.id)
    assert t.unit_type == "team"


# ===== Migration verification (1) =========================================


@pytest.mark.asyncio
async def test_no_branch_or_department_rows_exist(db: AsyncSession):
    """After migration, no rows should have unit_type 'branch' or 'department'."""
    result = await db.execute(
        select(OrganizationalUnit).where(
            OrganizationalUnit.unit_type.in_(["branch", "department"])
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 0, f"Found {len(rows)} rows with deprecated unit_type values"
```

- [ ] **Step 2: Run the tests**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/test_org_unit_types.py -v
```

Expected: All 29 tests pass.

- [ ] **Step 3: Run full test suite**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add tests/test_org_unit_types.py
git commit -m "test: add 29 tests for unit type v2 rules, nesting, and migration verification"
```

---

### Task 8: Update CLAUDE.md

**Files:**
- Modify: `backend/nexus/CLAUDE.md`

- [ ] **Step 1: Add Organizational Unit Types section**

Add to `backend/nexus/CLAUDE.md` after the "Module Responsibilities" section and before "Absolute Rules":

```markdown
---

## Organizational Unit Types

Valid types (as of Phase 2): `company`, `division`, `client_account`, `region`, `team`

### Rules

**`company`**
- Auto-created during onboarding. Never manually created by recruiters.
- Non-deletable (`is_root = true`).
- Exactly one per tenant. `parent_unit_id` must be NULL.
- `unit_type` is immutable once set to `'company'`.
- `company_profile` (JSONB) is required — cannot be null.

**`client_account`**
- Only available when `clients.workspace_mode = 'agency'`.
- `company_profile` (JSONB) is required — cannot be null.
- Cannot be nested under another `client_account` or under a `team`.
- Can be nested under `company`, `division`, or `region`.

**`division`**
- No special data requirements. General intermediate grouping.
- Cannot be nested under a `team`.

**`region`**
- No special data requirements. Geographic grouping.
- Cannot be nested under a `team`.

**`team`**
- Leaf node. No child units of any type allowed under a team.
- Enforced on the parent side at `create_org_unit` time.

### Nesting Rule Enforcement

All nesting rules are enforced in `create_org_unit` in `app/modules/org_units/service.py`.
The single check `parent.unit_type == 'team' → reject` covers all child types.
The extra check `unit_type == 'client_account' and parent.unit_type == 'client_account' → reject` handles the client_account-under-client_account case.
```

- [ ] **Step 2: Commit**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus
git add CLAUDE.md
git commit -m "docs: add Organizational Unit Types section to CLAUDE.md"
```

---

### Task 9: Frontend — Onboarding Page

**Files:**
- Modify: `frontend/app/app/onboarding/page.tsx`

- [ ] **Step 1: Rewrite the onboarding page**

Replace the entire contents of `frontend/app/app/onboarding/page.tsx` with a new two-step flow:

**Step 1 — Workspace type selection:**
- Two cards side by side: Enterprise ("We're hiring for our own company") and Agency ("We're a recruiting agency hiring for clients")
- On selection: call `PATCH /api/settings/workspace` with `{ workspace_mode: "enterprise" | "agency" }`
- Advance to step 2

**Step 2 — Company profile:**
- Fetch org units via `GET /api/org-units`, find the root unit (`is_root === true`)
- Form fields: Company name (required, pre-filled from root unit name), Industry (text), Company size (select: Startup/SMB/Enterprise), Culture summary (textarea), What a strong hire looks like (textarea)
- On submit: call `PUT /api/org-units/{root_unit_id}` with `{ name, set_company_profile: true, company_profile: { display_name, industry, company_size, culture_summary, hiring_bar, brand_voice: "professional", what_good_looks_like } }`
- On completion: call `POST /api/auth/onboarding/complete` then `router.push("/")` + `router.refresh()`

The implementer should use the `/ui-ux-pro-max` skill for high-quality UI design. The page should match the existing design language (Geist fonts, zinc-50 bg, clean cards).

- [ ] **Step 2: Test manually in browser**

Start the frontend dev server and walk through the flow:
1. Login as super admin
2. Verify redirect to `/onboarding`
3. Select workspace type
4. Fill company profile
5. Complete onboarding
6. Verify redirect to dashboard

- [ ] **Step 3: Commit**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
git add app/onboarding/page.tsx
git commit -m "feat: redesign onboarding — workspace type selection + company profile"
```

---

### Task 10: Frontend — Org Units Pages

**Files:**
- Modify: `frontend/app/app/(dashboard)/settings/org-units/page.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`
- Modify: `frontend/app/app/(dashboard)/layout.tsx`

- [ ] **Step 1: Update dashboard layout getMe type**

In `frontend/app/app/(dashboard)/layout.tsx`, update the `getMe` return type to include `workspace_mode`:

```typescript
return res.json() as Promise<{
    is_super_admin: boolean;
    onboarding_complete: boolean;
    has_org_units: boolean;
    workspace_mode: string;
}>;
```

- [ ] **Step 2: Update org units list page**

In `frontend/app/app/(dashboard)/settings/org-units/page.tsx`:

Update `UNIT_TYPES` and `TYPE_LABELS`:

```typescript
const UNIT_TYPES = [
  { value: "company", label: "Company" },
  { value: "division", label: "Division" },
  { value: "client_account", label: "Client Account" },
  { value: "region", label: "Region" },
  { value: "team", label: "Team" },
] as const;

const TYPE_LABELS: Record<string, string> = {
  company: "Company",
  division: "Division",
  client_account: "Client Account",
  region: "Region",
  team: "Team",
};
```

Update the `OrgUnit` interface to add `is_root` and `company_profile`:

```typescript
interface OrgUnit {
    // ... existing fields ...
    is_root: boolean;
    company_profile: Record<string, string> | null;
}
```

Add `workspace_mode` to the `MeData` interface and fetch it from `/api/auth/me`.

Filter the create unit dropdown to hide `company` (always) and hide `client_account` when `workspace_mode === 'enterprise'`:

```typescript
const createableTypes = UNIT_TYPES.filter((t) => {
    if (t.value === "company") return false;
    if (t.value === "client_account" && me?.workspace_mode !== "agency") return false;
    return true;
});
```

Change the default `createType` from `"department"` to `"division"`.

- [ ] **Step 3: Update org unit detail page**

In `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx`:

Update `UNIT_TYPES` and `TYPE_LABELS` (same as list page).

Update the `OrgUnit` interface to add `is_root` and `company_profile`.

**Hide delete button on root:** In the `canDelete` useMemo, add:

```typescript
if (unit.is_root) return false;
```

**Hide type selector on company:** In the edit form, conditionally render the type dropdown:

```typescript
{unit.unit_type !== "company" && (
    // ... type select dropdown ...
)}
```

**Filter sub-unit create dropdown** — same filtering as the list page (hide `company`, hide `client_account` in enterprise).

Change default sub-unit type from `"department"` to `"division"`.

**Add company_profile display/edit section** for units where `unit.unit_type === 'company'` or `unit.unit_type === 'client_account'`:
- Display: read-only card showing Display name, Industry, Company size, Culture summary, What a strong hire looks like, Brand voice
- Edit: inline form with text inputs, selects, and textareas
- Save: `PUT /api/org-units/{unit.id}` with `{ set_company_profile: true, company_profile: { ... } }`

The implementer should use the `/ui-ux-pro-max` skill for high-quality profile section design.

- [ ] **Step 4: Commit**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
git add app/\(dashboard\)/layout.tsx app/\(dashboard\)/settings/org-units/page.tsx \
       app/\(dashboard\)/settings/org-units/\[unitId\]/page.tsx
git commit -m "feat: update org unit pages for unit types v2 — workspace gating, root protection, company profile"
```

---

### Task 11: Frontend — Legacy Page Auth Guard

**Files:**
- Modify: `frontend/app/app/settings/org-units/new/page.tsx`

- [ ] **Step 1: Add auth guard and deprecation comment**

At the top of `frontend/app/app/settings/org-units/new/page.tsx`, add a deprecation comment:

```typescript
// DEPRECATED: This page is superseded by the onboarding wizard and the
// (dashboard)/settings/org-units/page.tsx create form. It will be removed
// in a future cleanup pass. Auth guard added to prevent unauthenticated access.
```

Add a `useEffect` auth guard at the start of the component:

```typescript
useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data: { session } }) => {
        if (!session?.access_token) {
            window.location.href = "/login";
        }
    });
}, []);
```

Do NOT change the unit type list.

- [ ] **Step 2: Commit**

```bash
cd /home/ishant/Projects/ProjectX/frontend/app
git add app/settings/org-units/new/page.tsx
git commit -m "fix: add auth guard and deprecation comment to legacy org-units/new page"
```

---

### Task 12: Final Verification

- [ ] **Step 1: Run full backend test suite**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && python -m pytest tests/ -v --tb=short
```

Expected: All tests pass.

- [ ] **Step 2: Run ruff**

```bash
cd /home/ishant/Projects/ProjectX/backend/nexus && ruff check . && ruff format --check .
```

- [ ] **Step 3: Verify clean git status**

```bash
git status
```

Expected: Clean working tree.
