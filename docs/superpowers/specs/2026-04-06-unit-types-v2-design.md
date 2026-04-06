# Org Unit Types v2 — Semantic Unit Types with Behavioural Rules

**Date:** 2026-04-06
**Status:** Approved
**Scope:** Backend (`backend/nexus/`, `backend/supabase/`) + Frontend (`frontend/app/`)

---

## Overview

Replace the current 5 behaviourally-identical unit types with 5 semantically-distinct types, each with enforced nesting rules, data requirements, and lifecycle constraints. Add workspace modes (enterprise vs agency) to gate agency-specific unit types.

### Customer Context

- **Enterprise:** Companies hiring for their own internal roles. No `client_account` units.
- **Agency:** Staffing/recruiting firms hiring on behalf of external clients. `client_account` units represent each external client.

---

## New Unit Type Model

| New type | Replaces | Notes |
|---|---|---|
| `company` | (new) | Root unit. Auto-created during onboarding. Non-deletable. |
| `division` | `department` + `branch` | General intermediate grouping. |
| `client_account` | `client_account` | Agency use only. Profile required. |
| `region` | `region` | Geographic grouping. |
| `team` | `team` | Leaf node. Cannot have children. |

Valid set: `{"company", "division", "client_account", "region", "team"}`

`branch` and `department` are dropped — both merge into `division`.

---

## Behavioural Rules Per Type

### `company`

- Exactly ONE per tenant. Enforced by unique partial index `one_root_per_tenant`.
- `parent_unit_id` MUST be NULL.
- `is_root = TRUE` — set at creation, never changed.
- CANNOT be deleted. Return 400: "The root company unit cannot be deleted."
- `unit_type` is IMMUTABLE once set to `company`. Reject any attempt to change it.
- `company_profile` (JSONB) is REQUIRED — cannot be NULL or empty.
- Auto-created during invite claiming (super admin branch only). Recruiters cannot create it manually.

### `client_account`

- `company_profile` (JSONB) is REQUIRED — cannot be NULL.
- Only creatable when `clients.workspace_mode = 'agency'`. Otherwise 400: "Client accounts are only available in agency workspaces."
- Cannot be nested under another `client_account` or under a `team`.
- CAN be nested under `company`, `division`, or `region`.

### `division`

- No required data fields. General intermediate grouping.
- Cannot be nested under a `team`.
- Can be nested under `company`, `client_account`, `division`, or `region`.

### `region`

- No required data fields. Geographic grouping.
- Cannot be nested under a `team`.
- Can be nested under `company`, `client_account`, `division`, or `region`.

### `team`

- LEAF NODE. Cannot have child units of any type.
- Enforced on the PARENT side: when any unit is created, if its parent is `team`, reject with 400: "Teams are leaf nodes and cannot contain sub-units."

---

## Nesting Rules — Forbidden Parent Types

| Unit type being created | Forbidden parent types |
|---|---|
| `company` | Any — must have NULL parent |
| `client_account` | `team`, `client_account` |
| `division` | `team` |
| `region` | `team` |
| `team` | `team` |

Two checks cover all cases:
1. `parent.unit_type == "team"` → reject (covers all child types)
2. `unit_type == "client_account" and parent.unit_type == "client_account"` → reject

---

## Database Changes

### Migration strategy

**Mutate the initial migration** (`20260405000000_initial_schema.sql`):
- Update the CHECK constraint on `organizational_units.unit_type` to `('company', 'division', 'client_account', 'region', 'team')`
- Add `is_root BOOLEAN NOT NULL DEFAULT FALSE` column to `organizational_units`
- Add `company_profile JSONB` column to `organizational_units`
- Add `workspace_mode TEXT NOT NULL DEFAULT 'enterprise' CHECK (workspace_mode IN ('enterprise', 'agency'))` column to `clients`

**Create new migration** (`20260406000000_unit_types_v2.sql`):
- Unique partial index: `CREATE UNIQUE INDEX one_root_per_tenant ON public.organizational_units (client_id) WHERE parent_unit_id IS NULL;`

After writing both files: `supabase db reset`. No live ALTER, no data fixup — clean slate.

### Why mutate the initial migration

We are in early development with a local Supabase instance and no real data. `supabase db reset` wipes the database and replays all migrations from scratch. There is no existing data to protect, no production deployment, and no need for additive ALTER statements.

### No DB-level constraint for `company_profile`

Do NOT add a DB-level NOT NULL or CHECK constraint for `company_profile`. It is only required for `company` and `client_account` types — enforce at the application layer.

---

## Backend Changes

### `app/models.py`

Add to `OrganizationalUnit`:
```python
is_root: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
company_profile: Mapped[dict | None] = mapped_column(JSONB)
```

Add to `Client`:
```python
workspace_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="'enterprise'")
```

### `app/modules/org_units/service.py`

**Update `VALID_UNIT_TYPES`:**
```python
VALID_UNIT_TYPES = {"company", "division", "client_account", "region", "team"}
```

**`create_org_unit` — new params:**
```python
workspace_mode: str = "enterprise",
company_profile: dict | None = None,
```

**`create_org_unit` — validation block** (at top, before unit instantiation):

1. `company` must have NULL parent
2. Only one `company` per tenant (query `WHERE client_id = ? AND parent_unit_id IS NULL`)
3. `company_profile` required for `company` and `client_account` (check `not company_profile`)
4. `client_account` only when `workspace_mode == "agency"`
5. Parent nesting: fetch parent, reject if `parent.unit_type == "team"`, reject if `client_account` under `client_account`

**`create_org_unit` — unit instantiation:**
```python
unit = OrganizationalUnit(
    ...,
    is_root=(unit_type == "company"),
    company_profile=company_profile,
)
```

**`update_org_unit` — new params:**
```python
company_profile: dict | None = None,
set_company_profile: bool = False,
```

**`update_org_unit` — validation additions:**
- Block type change on root company: if `unit.unit_type == "company"` and `unit_type != "company"`, reject
- If `set_company_profile`: validate `company_profile` not empty for `company`/`client_account` types, then set `unit.company_profile = company_profile`
- Include `company_profile` in the before/after audit diff (stringify for comparison: `str(unit.company_profile)`)

**`delete_org_unit` — hard block at top:**
```python
if unit.is_root:
    raise ValueError("The root company unit cannot be deleted.")
```

### `app/modules/org_units/schemas.py`

**`CreateOrgUnitRequest`** — add `company_profile: dict | None = None`

**`UpdateOrgUnitRequest`** — add `company_profile: dict | None = None` and `set_company_profile: bool = False`

**`OrgUnitResponse`** — add `is_root: bool` and `company_profile: dict | None`

### `app/modules/org_units/router.py`

**Create endpoint** — load `Client` row to get `workspace_mode`, pass to `create_org_unit` along with `data.company_profile`.

**Update endpoint** — pass `company_profile=data.company_profile` and `set_company_profile="company_profile" in raw_body` to `update_org_unit`.

**`_build_response`** — add `is_root=unit.is_root` and `company_profile=unit.company_profile`.

**`list_org_units` response** — add `is_root` and `company_profile` to the dict returned per unit.

### New endpoint: `PATCH /api/settings/workspace`

In `app/modules/settings/router.py`:
- Body: `{ "workspace_mode": "enterprise" | "agency" }`
- Auth: `require_super_admin()`
- Updates `clients.workspace_mode` for the tenant
- Audit log: `CLIENT_WORKSPACE_MODE_CHANGED` (add to `audit/actions.py`)

New schema in `app/modules/settings/schemas.py`:
```python
class WorkspaceModeRequest(BaseModel):
    workspace_mode: str  # validated in handler: must be "enterprise" or "agency"
```

### `app/modules/auth/router.py` — `complete_invite`

**CRITICAL GUARD:** The root unit auto-creation MUST only fire inside the existing `if is_super_admin:` branch. Team member invites (`invited_by` is set, `projectx_admin_id` is NULL) must NOT trigger root unit creation.

After `UPDATE clients SET super_admin_id = ...` (inside the `is_super_admin` block):

```python
if is_super_admin:
    # ... existing super_admin_id update ...

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
```

The placeholder `company_profile` dict passes the required-field check (it is not None/falsy). The recruiter completes it during onboarding step 2.

**`CompleteInviteResponse`** — add `root_unit_id: str`. Return `str(root_unit.id)` for super admin, `""` for team members.

### `app/modules/auth/schemas.py` — `MeResponse`

Add `workspace_mode: str`. Populate from `client.workspace_mode` in the `/me` handler.

### `app/modules/auth/router.py` — `/me` handler

Add `workspace_mode=client.workspace_mode` to the `MeResponse` constructor.

---

## Frontend Changes

### Onboarding (`frontend/app/app/onboarding/page.tsx`)

Replace current "create first org unit" flow with two steps:

**Step 1 — Workspace type:**
Two cards:
- "We're hiring for our own company" → `enterprise`
- "We're a recruiting agency hiring for clients" → `agency`

On selection: `PATCH /api/settings/workspace` with chosen mode.

**Step 2 — Company profile:**
Form that updates the root company unit via `PUT /api/org-units/{root_unit_id}` with `set_company_profile: true`.

Fields:
- Company name (required) — updates `name` and `company_profile.display_name`
- Industry (text input)
- Company size (select: Startup / SMB / Enterprise)
- Culture summary (textarea, optional)
- What a strong hire looks like (textarea, optional)

The `root_unit_id` comes from the `CompleteInviteResponse` stored during invite claiming. Since the onboarding page loads after invite claiming redirects to `/onboarding`, the frontend needs to persist `root_unit_id` across this navigation. Options: URL query param, or fetch it from the `/me` endpoint (add `root_unit_id` to MeResponse), or fetch org units and find the one with `is_root === true`. The simplest: fetch org units list and find the root.

On completion: `POST /api/auth/onboarding/complete` → redirect to `/`.

### Org Units List (`settings/org-units/page.tsx`)

- Update `UNIT_TYPES` and `TYPE_LABELS` to new set
- Hide `company` from create dropdown (auto-created only)
- Hide `client_account` from create dropdown when `workspace_mode === 'enterprise'` (read from `/me` response)
- Add `is_root` and `company_profile` to `OrgUnit` TypeScript interface

### Org Unit Detail (`settings/org-units/[unitId]/page.tsx`)

- Update `UNIT_TYPES` and `TYPE_LABELS`
- If `unit.is_root === true`: remove delete button from DOM entirely (not disabled — removed)
- If `unit.unit_type === 'company'`: hide unit type selector in edit form (type is immutable)
- Add `company_profile` display/edit section for `company` and `client_account` units
  - Display: read-only card showing all profile fields
  - Edit: inline form triggered by "Edit profile" button
  - Save: `PUT /api/org-units/{unit.id}` with `set_company_profile: true`
  - Fields: Display name, Industry, Company size, Culture summary, What a strong hire looks like, Brand voice (select: Professional / Conversational / Technical)
- Hide `company` from create sub-unit type dropdown
- Hide `client_account` from create sub-unit type dropdown when `workspace_mode === 'enterprise'`

### Legacy page (`settings/org-units/new/page.tsx`)

- Add auth guard (redirect to `/login` if no session)
- Add deprecation comment
- Do NOT update unit type list

### Dashboard layout

- Update the `getMe` return type to include `workspace_mode`

---

## `backend/nexus/CLAUDE.md` Update

Add an "Organizational Unit Types" section documenting all 5 types, their rules, and where nesting enforcement lives.

---

## Tests

28 test functions in `backend/nexus/tests/test_org_unit_types.py` (new file — separate from existing `test_org_units.py` which has HTTP auth guard tests):

**Company type rules (5):**
1. company with parent → ValueError
2. second company in same tenant → ValueError
3. company without company_profile → ValueError
4. delete root unit → ValueError
5. change type of root company → ValueError

**Client account rules (7):**
6. client_account without company_profile → ValueError
7. client_account in enterprise workspace → ValueError
8. client_account under client_account → ValueError
9. client_account under team → ValueError
10. client_account under company (agency) → success
11. client_account under division (agency) → success
12. client_account under region (agency) → success

**Team leaf node (4):**
13. division under team → ValueError
14. region under team → ValueError
15. team under team → ValueError
16. client_account under team → ValueError (via team-parent path)

**Valid nesting (12):**
17-20. division under company/client_account/division/region → success
21-24. region under company/client_account/division/region → success
25-28. team under company/division/client_account/region → success

All tests use existing conftest patterns: `create_test_client`, `create_test_user`, `create_test_org_unit`, `db` fixture with per-test rollback. Tests call service functions directly.

---

## Files Modified

| File | Changes |
|---|---|
| `backend/supabase/migrations/20260405000000_initial_schema.sql` | Mutate: new CHECK constraint, add `is_root`, `company_profile` columns, add `workspace_mode` to clients |
| `backend/nexus/app/models.py` | Add `is_root`, `company_profile` to OrganizationalUnit; `workspace_mode` to Client |
| `backend/nexus/app/modules/org_units/service.py` | New VALID_UNIT_TYPES, validation block in create, company_profile in update, is_root block in delete |
| `backend/nexus/app/modules/org_units/schemas.py` | Add company_profile to create/update requests, is_root + company_profile to response |
| `backend/nexus/app/modules/org_units/router.py` | Load workspace_mode, pass company_profile, update _build_response and list response |
| `backend/nexus/app/modules/settings/router.py` | Add PATCH /api/settings/workspace endpoint |
| `backend/nexus/app/modules/settings/schemas.py` | Add WorkspaceModeRequest |
| `backend/nexus/app/modules/auth/router.py` | Auto-create root unit in complete_invite (super admin only), add workspace_mode to /me |
| `backend/nexus/app/modules/auth/schemas.py` | Add root_unit_id to CompleteInviteResponse, workspace_mode to MeResponse |
| `backend/nexus/app/modules/audit/actions.py` | Add CLIENT_WORKSPACE_MODE_CHANGED |
| `backend/nexus/CLAUDE.md` | Add Organizational Unit Types section |
| `frontend/app/app/onboarding/page.tsx` | Replace with workspace type + company profile steps |
| `frontend/app/app/(dashboard)/settings/org-units/page.tsx` | New UNIT_TYPES, hide company/client_account from create, add workspace_mode gating |
| `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx` | Hide delete on root, hide type on company, company_profile section, workspace_mode gating |
| `frontend/app/app/settings/org-units/new/page.tsx` | Add auth guard + deprecation comment |
| `frontend/app/app/(dashboard)/layout.tsx` | Update getMe type for workspace_mode |

## Files Created

| File | Purpose |
|---|---|
| `backend/supabase/migrations/20260406000000_unit_types_v2.sql` | Unique partial index only |
| `backend/nexus/tests/test_org_unit_types.py` | 28 tests for all type rules and nesting combinations |
