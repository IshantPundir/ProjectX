# Unit Delete Authorization & Admin Inheritance — Design Spec

**Date:** 2026-04-05
**Status:** Approved
**Scope:** Backend schema changes, delete authorization logic, admin inheritance on sub-unit creation, frontend UI updates

---

## Problem

1. Any super admin can delete any unit. There is no concept of which admin is authorized to delete a specific unit.
2. When a sub-unit is created under a parent, parent admins are not automatically added to the sub-unit. Admins must be manually re-assigned.
3. There is no safeguard to lock deletion on sensitive units.

## Goals

1. Track who created each unit (`created_by` — immutable, audit trail).
2. Track which admin is authorized to delete a unit (`deletable_by` — mutable, defaults to creator).
3. Auto-nullify `deletable_by` when that user loses Admin role in the unit.
4. Allow super admin to reassign `deletable_by` to any current admin of the unit.
5. Allow super admin to lock deletion on a unit (`admin_delete_disabled`).
6. When creating a sub-unit with a parent, copy all Admin role assignments from the parent into the new unit.

---

## 1. Schema Changes

### `organizational_units` — 3 new columns

| Column | Type | Notes |
|---|---|---|
| `created_by` | UUID FK → users, NULLABLE | Set once at unit creation. Never modified, even if creator leaves the org. Immutable audit trail. NULL for units created before this migration. |
| `deletable_by` | UUID FK → users, NULLABLE | The one admin user authorized to delete this unit. Defaults to `created_by` at creation. Set to NULL when that user loses Admin role in the unit. Super admin can reassign to any current admin. |
| `admin_delete_disabled` | BOOLEAN NOT NULL DEFAULT FALSE | When TRUE, overrides `deletable_by` — only super admin can delete. |

### Migration

Add columns to existing `organizational_units` table:

```sql
ALTER TABLE public.organizational_units
  ADD COLUMN created_by UUID REFERENCES public.users(id),
  ADD COLUMN deletable_by UUID REFERENCES public.users(id),
  ADD COLUMN admin_delete_disabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX org_units_created_by_idx ON public.organizational_units (created_by);
```

Also update the clean-slate migration to include these columns for new deployments.

---

## 2. Delete Authorization Logic

```
Can user delete this unit?
  1. Super admin? → YES (always, regardless of any flags)
  2. admin_delete_disabled = TRUE? → NO
     Error: "Only a super admin can delete this unit"
  3. deletable_by is NULL? → NO
     Error: "No admin is authorized to delete this unit. Contact your super admin."
  4. User is Admin in unit AND user.id == deletable_by? → YES
  5. Otherwise → NO
     Error: "Only the super admin or {deletable_by_email} can delete this unit"
```

The `DELETE /api/org-units/{id}` endpoint changes from `dependencies=[require_super_admin()]` to custom authorization logic in the handler following the above flow.

Existing safeguards remain: unit must have no sub-units and no members before deletion.

---

## 3. Auto-nullification of `deletable_by`

When a role assignment is removed (`remove_role_from_user` or `remove_user_from_unit`), check:

1. Is the user being removed the `deletable_by` user for this org unit?
2. After removal, does the user still hold at least one Admin role in this unit?
3. If YES to (1) and NO to (2) → SET `deletable_by = NULL` on the org unit.

This runs inside the same transaction as the role removal — no separate query needed.

---

## 4. Super Admin Reassignment of `deletable_by`

`PUT /api/org-units/{id}` already accepts update fields. Add `deletable_by: string | null` to the request body.

**Authorization:** Super admin only can set `deletable_by`.

**Validation:** If `deletable_by` is not null, the target user must currently hold the Admin role in this unit. If not, return 400: "User must be an admin of this unit to be assigned as deletable_by."

Setting `deletable_by` to null explicitly is allowed (super admin revoking delete authority from all admins).

---

## 5. Admin Inheritance on Sub-unit Creation

When `create_org_unit` is called with a `parent_unit_id`:

1. Create the org unit (existing logic).
2. Set `created_by = caller.id` and `deletable_by = caller.id`.
3. Query all role assignments where `org_unit_id = parent_unit_id` AND role name is "Admin".
4. For each parent admin assignment, create a new `UserRoleAssignment` in the new unit with:
   - `user_id` = parent admin's user_id
   - `org_unit_id` = new unit's id
   - `role_id` = the Admin role id (same as parent)
   - `tenant_id` = same tenant
   - `assigned_by` = caller's user_id

**This is a one-time copy at creation.** If parent admins change later, sub-units are not affected. If the caller is already a parent admin, they won't get a duplicate (the unique constraint prevents it, but we should skip them to avoid an exception).

For top-level units (no parent): just set `created_by` and `deletable_by` to the caller. No inheritance.

---

## 6. API Changes

### `OrgUnitResponse` — new fields

```python
class OrgUnitResponse(BaseModel):
    id: str
    client_id: str
    parent_unit_id: str | None
    name: str
    unit_type: str
    member_count: int
    created_at: str
    created_by: str | None          # user ID of creator
    created_by_email: str | None    # for display
    deletable_by: str | None        # user ID authorized to delete
    deletable_by_email: str | None  # for display
    admin_delete_disabled: bool
```

### `UpdateOrgUnitRequest` — new optional fields

```python
class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    unit_type: str | None = None
    deletable_by: str | None = None          # super admin only
    admin_delete_disabled: bool | None = None  # super admin only
```

When `deletable_by` or `admin_delete_disabled` is in the request body, the handler checks the caller is super admin before applying. Non-super-admins sending these fields get a 403.

**Null handling for `deletable_by`:** The field is optional in the request. If omitted, no change. If explicitly set to `null`, the super admin is clearing delete authority (no admin can delete). If set to a user ID string, that user must be a current admin of the unit.

### `DELETE /api/org-units/{id}`

Remove `dependencies=[require_super_admin()]`. Authorization logic moves into the handler per Section 2.

### `POST /api/org-units`

Service function sets `created_by` and `deletable_by` to the caller. Performs admin inheritance if `parent_unit_id` is set.

---

## 7. Frontend Changes

### Unit Detail Page (`/settings/org-units/[unitId]`)

**Unit header area:**
- Show "Created by {email}" in the metadata line
- Show "Deletable by {email}" or "No admin can delete" or "Deletion locked by super admin"

**Delete button visibility:**
- Super admin: always visible
- `deletable_by` user who is Admin in unit: visible
- Everyone else: hidden

**Delete button state:**
- If `admin_delete_disabled` and user is not super admin: disabled with tooltip "Deletion locked by super admin"
- If `deletable_by` is null and user is not super admin: hidden

**Lock toggle** (super admin only):
- Toggle for `admin_delete_disabled` in the unit settings area
- When toggled ON: shows lock icon, "Only super admin can delete this unit"

**Reassign deletable_by** (super admin only):
- Dropdown to select from current admins of the unit
- Visible in unit settings area alongside the lock toggle

---

## 8. Affected Files

### Backend
- `backend/supabase/migrations/20260405000000_initial_schema.sql` — add 3 columns
- `backend/nexus/app/models.py` — add 3 fields to `OrganizationalUnit`
- `backend/nexus/app/modules/org_units/schemas.py` — update `OrgUnitResponse`, `UpdateOrgUnitRequest`
- `backend/nexus/app/modules/org_units/service.py` — update `create_org_unit` (set created_by, admin inheritance), `delete_org_unit` (new auth logic), `remove_role_from_user` (auto-nullify), `remove_user_from_unit` (auto-nullify)
- `backend/nexus/app/modules/org_units/router.py` — update delete endpoint auth, update response construction to include emails

### Frontend
- `frontend/app/app/(dashboard)/settings/org-units/[unitId]/page.tsx` — delete button logic, lock toggle, reassign dropdown, creator/deletable_by display
- `frontend/app/app/(dashboard)/settings/org-units/page.tsx` — update `OrgUnit` interface with new fields
