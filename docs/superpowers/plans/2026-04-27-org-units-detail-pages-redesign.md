# Org Unit Detail Pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the four org-unit detail components at `/settings/org-units/[unitId]/` to match the approved spec (`docs/superpowers/specs/2026-04-27-org-units-page-redesign-design.md`) and the design package (`Org Unit Configs.html`), introducing `default_role` on Team units and an inheritance pattern for locale + compliance.

**Architecture:** Frontend-led rewrite. Backend gets minimal additions: two helper functions that mirror `find_company_profile_in_ancestry`, plus `inherited_*` blocks on the GET response. The detail pages are summary "hubs" with a view ↔ edit mode toggle; deep editors continue to live at the existing sub-routes (`company-profile/`, `pipeline-templates/`).

**Tech Stack:** Next.js 16 App Router · React 19 · TypeScript strict · Tailwind v4 (already wired to `--px-*` tokens) · React Hook Form + Zod · TanStack Query v5 · Vitest + Testing Library · FastAPI + SQLAlchemy + asyncpg.

---

## File Structure

### Backend (additions only — no rewrites)

| File | Action | Purpose |
|------|--------|---------|
| `backend/nexus/app/modules/org_units/service.py` | Modify | Add `find_locale_defaults_in_ancestry()` and `find_compliance_flags_in_ancestry()` (mirror `find_company_profile_in_ancestry`). Surface `inherited_locale` + `inherited_compliance` in `_serialize_unit()` (the dict returned by `get_org_unit` and `list_org_units`). |
| `backend/nexus/app/modules/org_units/schemas.py` | Modify | Add `inherited_locale: dict \| None` and `inherited_compliance: dict \| None` to `OrgUnitResponse`. |
| `backend/nexus/app/modules/org_units/router.py` | Modify | Pass through the new fields to the response. |
| `backend/nexus/tests/modules/org_units/test_inheritance.py` | Create | Unit tests for the two new helpers (see Task B3). |

### Frontend — types & data layer

| File | Action | Purpose |
|------|--------|---------|
| `frontend/app/lib/api/org-units.ts` | Modify | Add `InheritedLocale`, `InheritedCompliance`, `LocaleDefaults`, `ComplianceFlags`, `TeamMetadata`, `CompanyMetadata`, `DivisionMetadata`, `RegionMetadata` types. Extend `OrgUnit` with `inherited_locale` and `inherited_compliance` optional fields. |

### Frontend — new shared sub-components

All under `frontend/app/components/dashboard/org-units/`:

| File | Action | Purpose |
|------|--------|---------|
| `DefaultRolePicker.tsx` | Create | 4-chip radio group for Team's `default_role`. View + edit modes. |
| `InheritedField.tsx` | Create | Wraps an input/select/checkbox with the inherited-or-override UX (used by Region + Client account locale & compliance). |
| `LocaleStrip.tsx` | Create | The 3-chip locale row (timezone, currency, locale). Source mode (Company root) and Inheritance mode (Region / Client account). Renders inline at the top of header per the design. |
| `ComplianceFlagsList.tsx` | Create | The 3-row compliance section (AIVIA / GDPR / CCPA). Source vs inheritance modes. |
| `SubUnitCard.tsx` | Create | The sub-unit card used in the grid (type pill, default-role pill for Team children, open-roles pill, member-count meta, "Open →" link). |
| `TemplateRow.tsx` | Create | Row for the Pipeline templates section: name + Default tag + stage flow chips + Edit link. |
| `MembersListCompact.tsx` | Create | Sidebar-friendly compact member list used by Division / Region / Company / Client (Direct members in sidebar). Wraps `useOrgUnitMembers` + `useAssignRole` + `useRemoveRole`. |
| `CascadingMembersTeam.tsx` | Create | Team-specific members section that renders three variants (regular, cascaded admin, manual admin) and supports the "Opt in to {default_role}" affordance. |
| `EditModeToggle.tsx` | Create | The view ↔ edit pill button. Returns `mode` and `onChange` (controlled). |

### Frontend — detail pages (full rewrites)

All under `frontend/app/app/(dashboard)/settings/org-units/[unitId]/`:

| File | Action | Purpose |
|------|--------|---------|
| `shared.tsx` | Replace | Update `UnitPageHeader` to support the new chrome (locale strip slot, about narrative slot for company/client, stats roll-up, default-role pill on Team). Keep `Section`, `Field`, `TagChip`. Drop `SubUnitsList` (replaced by `SubUnitCard` grid in pages) and `SmallStats` (D15: stripped). |
| `Sidebar.tsx` | Create | Standard right-column sidebar: Hierarchy card + Governance card + Delete button (suppressed for Company root). Used by every detail page. |
| `TeamDetail.tsx` | Replace | New layout per spec §5.1: merged "Default role + Identity + Focus" top section, Members (cascading variants), Open jobs anchored here. |
| `DivisionDetail.tsx` | Replace | Per spec §5.2: merged Identity + Description, Sub-units grid, Pipeline templates. Direct members move to sidebar (per user iteration). |
| `RegionDetail.tsx` | Replace | Per spec §5.3: header carries inline LocaleStrip with inheritance + override. Body: Identity, Sub-units, Compliance flags. Direct members in sidebar. |
| `CompanyDetail.tsx` | Create | New file. Per spec §5.4. Header carries the rich Company chrome (name, website, About narrative, LocaleStrip in source mode). Body: Hiring bar, Sub-units, Pipeline templates, Compliance flags. Direct members in sidebar. Tenant info card (Company root) or Governance card + Delete (Client account) controlled by an `isClientAccount` prop. |
| `CompanyProfileDetail.tsx` | Delete | Replaced by `CompanyDetail.tsx`. The old file's deep-editor route under `[unitId]/company-profile/page.tsx` is unchanged — that route is the canonical edit surface for the 4-field profile per D10. |
| `MembersSection.tsx` | Delete | Replaced by `MembersListCompact.tsx` + `CascadingMembersTeam.tsx`. |
| `schema.ts` | Replace | Holds the metadata Zod schemas: `teamMetadataSchema`, `divisionMetadataSchema`, `regionMetadataSchema`, `companyMetadataSchema`. Each detail file imports its own. |
| `page.tsx` (dispatcher) | Modify | Switch the `client_account`/`company` branch to render `<CompanyDetail isClientAccount={...} />`. No other dispatcher changes. Keep all existing data loading. |

### Frontend — tests

| File | Action | Purpose |
|------|--------|---------|
| `frontend/app/tests/components/org-units/DefaultRolePicker.test.tsx` | Create | View/edit rendering, role change fires onChange, Admin not present in options. |
| `frontend/app/tests/components/org-units/InheritedField.test.tsx` | Create | Inherited state shows parent name + dimmed value. Override toggle flips to editable. Reset returns to inherited. |
| `frontend/app/tests/components/org-units/LocaleStrip.test.tsx` | Create | Source mode renders 3 editable chips. Inherit mode renders inherited values; toggling override per-chip works. |
| `frontend/app/tests/components/org-units/SubUnitCard.test.tsx` | Create | Shows type pill, default-role pill iff team, open-roles pill iff `>0`, links to detail page. |
| `frontend/app/tests/components/org-units/CascadingMembersTeam.test.tsx` | Create | Composition test: regular / cascaded admin / manual admin / opted-in admin variants render correctly. Uses MSW or fetch mocks. |
| `frontend/app/tests/settings/org-units/team-detail.test.tsx` | Create | Composition test: TeamDetail renders default role section + members + open jobs. Editing default_role + saving fires the API call. |
| `frontend/app/tests/settings/org-units/division-detail.test.tsx` | Create | Composition test: Division renders sub-units grid + pipeline templates section + sidebar members. |
| `frontend/app/tests/settings/org-units/region-detail.test.tsx` | Create | Composition test: Region renders inherited locale strip; flipping override + save sends the right payload. |
| `frontend/app/tests/settings/org-units/company-detail.test.tsx` | Create | Composition test: Company root renders source-mode locale strip; Client account renders inherited+override; tenant-info card shows on root only. |
| Existing tests under `tests/components/MembersSection.test.tsx`, `tests/components/org-units-client-account-flow.test.tsx`, etc. | Delete | Already deleted on the working branch (per `git status`). Confirm nothing else references them. |

---

## Decisions Locked (recap from spec, refined by user iteration in chat2)

| ID | Decision | Implementation note |
|----|----------|---------------------|
| L1 | Detail pages have a **view ↔ edit** mode toggle in the header | Single `mode` state per page. View mode hides inputs in favor of read-only renderings. Edit mode unlocks all inline-editable fields. The "Save changes" button only appears in edit mode. |
| L2 | **Locale & compliance for Region / Client live IN the header**, not in body sections | Per the final HTML; user iteration moved them. Body keeps Identity + Sub-units + (for Region) Compliance flags as a small section. For Company/Client, the LocaleStrip lives in the unit-header alongside the About textarea. Compliance still gets a body section because it has 3 rows. |
| L3 | **Direct members live in the sidebar** for Division / Region / Company / Client | Per user iteration. Team is the exception — its members section stays in the body because it carries the cascading-admin UI which doesn't fit a narrow column. |
| L4 | **Team page top section merges Default role + Identity + Focus** | Per user iteration. One card with the role-picker headline at the top, then the name input, then the Focus textarea. |
| L5 | **Division Identity + Description** merged into a single section card | Per user iteration. |
| L6 | **`default_role` enum is `Recruiter \| Hiring Manager \| Interviewer \| Observer`** — `Admin` excluded | Validation enforced both client-side (Zod) and server-side (write into metadata as a free string but the frontend Zod schema rejects Admin). |
| L7 | **Inheritance is computed server-side** | The GET `/api/org-units/{id}` response includes `inherited_locale` and `inherited_compliance` blocks. No client-side ancestry walking. |
| L8 | **Hub pattern** — long-form profile edits live in `[unitId]/company-profile/`, the detail page only has a preview | The Hiring bar narrative on the Company page is the exception per the final HTML; About narrative lives in the Company header, Hiring bar in the body — both are inline-editable when in edit mode. |
| L9 | **No archive button** — user iteration replaced "Archive" with "Discard" (drops dirty form state) + "Save changes" | Confirmed in the final HTML. |

---

## Phase A — Backend additions

### Task A1: Add inheritance helpers

**Files:**
- Modify: `backend/nexus/app/modules/org_units/service.py` (after the existing `find_company_profile_in_ancestry`)
- Modify: `backend/nexus/app/modules/org_units/schemas.py`
- Modify: `backend/nexus/app/modules/org_units/router.py`

- [ ] **A1.1: Write the helpers**

In `service.py`, add after `find_company_profile_in_ancestry`:

```python
LOCALE_KEYS = ("default_timezone", "default_currency", "default_locale")
COMPLIANCE_KEYS = (
    "compliance_aivia_il",
    "compliance_gdpr_eu",
    "compliance_ccpa_ca",
)


async def find_locale_defaults_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> tuple[dict[str, str | None], UUID | None] | None:
    """
    Walk the parent chain. For each locale key (timezone/currency/locale), return
    the first non-null value encountered, plus the unit_id of the closest
    ancestor that contributed at least one key.

    Returns ({timezone, currency, locale}, source_unit_id) or None if nothing
    is set anywhere in the chain.
    """
    found: dict[str, str | None] = {k: None for k in LOCALE_KEYS}
    source_unit_id: UUID | None = None
    current_id: UUID | None = org_unit_id
    while current_id is not None:
        unit = await db.get(OrgUnit, current_id)
        if unit is None:
            break
        meta = unit.unit_metadata or {}
        for key in LOCALE_KEYS:
            if found[key] is None and meta.get(key):
                found[key] = meta[key]
                if source_unit_id is None:
                    source_unit_id = unit.id
        if all(v is not None for v in found.values()):
            break
        current_id = unit.parent_unit_id
    if all(v is None for v in found.values()):
        return None
    return (found, source_unit_id)


async def find_compliance_flags_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> tuple[dict[str, bool | None], UUID | None] | None:
    """Same shape as locale, but for compliance booleans."""
    found: dict[str, bool | None] = {k: None for k in COMPLIANCE_KEYS}
    source_unit_id: UUID | None = None
    current_id: UUID | None = org_unit_id
    while current_id is not None:
        unit = await db.get(OrgUnit, current_id)
        if unit is None:
            break
        meta = unit.unit_metadata or {}
        for key in COMPLIANCE_KEYS:
            if found[key] is None and key in meta and meta[key] is not None:
                found[key] = bool(meta[key])
                if source_unit_id is None:
                    source_unit_id = unit.id
        if all(v is not None for v in found.values()):
            break
        current_id = unit.parent_unit_id
    if all(v is None for v in found.values()):
        return None
    return (found, source_unit_id)
```

Walk semantics: **walk starts at the unit ITSELF** (so the unit's own metadata wins over parents) — this is identical to `find_company_profile_in_ancestry`. The frontend uses this same response for both source units (Company) and inheriting units (Region/Client) because for source units, the walk finds the value on the first iteration.

- [ ] **A1.2: Surface in the response**

Find `_serialize_unit` (or whichever helper builds the dict — search for the existing `"company_profile": unit.company_profile` line in `service.py`). Add after it:

```python
locale_result = await find_locale_defaults_in_ancestry(db, unit.id)
compliance_result = await find_compliance_flags_in_ancestry(db, unit.id)
```

And in the returned dict:

```python
"inherited_locale": (
    {
        "values": locale_result[0],
        "source_unit_id": str(locale_result[1]) if locale_result[1] else None,
    }
    if locale_result
    else None
),
"inherited_compliance": (
    {
        "values": compliance_result[0],
        "source_unit_id": str(compliance_result[1]) if compliance_result[1] else None,
    }
    if compliance_result
    else None
),
```

For the list endpoint (`list_org_units`), do the same but pre-fetch all units once and walk in-memory to avoid N+1 (most common shape: list endpoint already loads all units for the tenant).

- [ ] **A1.3: Schema update**

In `schemas.py`, on `OrgUnitResponse`, add:

```python
inherited_locale: dict | None = None
inherited_compliance: dict | None = None
```

Pass through in `router.py` where `OrgUnitResponse(...)` is constructed (two places — list and get).

- [ ] **A1.4: Run backend tests**

```bash
cd backend/nexus && docker compose run --rm nexus pytest tests/modules/org_units/ -v
```

Expected: all existing tests pass (no behavior change for callers that don't use the new fields).

- [ ] **A1.5: Commit**

```bash
git add backend/nexus/app/modules/org_units/
git commit -m "feat(org-units): surface inherited locale + compliance on GET response"
```

### Task A2: Inheritance helper tests

**Files:**
- Create: `backend/nexus/tests/modules/org_units/test_inheritance.py`

- [ ] **A2.1: Write tests covering**
  - Source unit (Company) sets all 3 locale keys → `find_locale_defaults_in_ancestry` returns its own values with its own id as source.
  - Region under Company: Region sets timezone, inherits currency + locale from Company → returns mixed dict, source = Region for the timezone, but the function returns a single source per call. Verify the source is the **closest** ancestor contributing.
  - Region under Company under root: Company sets all 3, Region sets nothing → returns Company's values with Company's id.
  - Nothing set anywhere → returns None.
  - Compliance: same shape, with booleans (including `False` which must NOT be treated as missing).

- [ ] **A2.2: Run + commit**

---

## Phase B — Frontend types & API surface

### Task B1: Extend `lib/api/org-units.ts`

**File:** `frontend/app/lib/api/org-units.ts`

- [ ] **B1.1: Add metadata types**

```typescript
export const TEAM_DEFAULT_ROLES = [
  "Recruiter",
  "Hiring Manager",
  "Interviewer",
  "Observer",
] as const;
export type TeamDefaultRole = (typeof TEAM_DEFAULT_ROLES)[number];

export interface TeamMetadata {
  default_role?: TeamDefaultRole;
  focus?: string;
}

export interface DivisionMetadata {
  description?: string;
}

export interface RegionMetadata {
  default_timezone?: string;
  default_currency?: string;
  default_locale?: string;
  compliance_aivia_il?: boolean;
  compliance_gdpr_eu?: boolean;
  compliance_ccpa_ca?: boolean;
}

export interface CompanyMetadata {
  short_name?: string;
  website?: string;
  default_timezone?: string;
  default_currency?: string;
  default_locale?: string;
  compliance_aivia_il?: boolean;
  compliance_gdpr_eu?: boolean;
  compliance_ccpa_ca?: boolean;
}

export interface InheritedLocale {
  values: {
    default_timezone: string | null;
    default_currency: string | null;
    default_locale: string | null;
  };
  source_unit_id: string | null;
}

export interface InheritedCompliance {
  values: {
    compliance_aivia_il: boolean | null;
    compliance_gdpr_eu: boolean | null;
    compliance_ccpa_ca: boolean | null;
  };
  source_unit_id: string | null;
}
```

- [ ] **B1.2: Extend `OrgUnit`**

```typescript
export interface OrgUnit {
  // ...existing fields
  inherited_locale: InheritedLocale | null;
  inherited_compliance: InheritedCompliance | null;
}
```

- [ ] **B1.3: Run type check**

```bash
cd frontend/app && npm run type-check
```

Expected: passes (the existing detail components don't reference the new fields yet).

---

## Phase C — New shared sub-components

For each sub-component below: create the file, write a Vitest composition test for it, run it, then commit. Each is tiny enough that you can write it in one go without intermediate steps.

### Task C1: `EditModeToggle.tsx`

```tsx
"use client";

interface Props {
  mode: "view" | "edit";
  onChange: (next: "view" | "edit") => void;
  disabled?: boolean;
}

export function EditModeToggle({ mode, onChange, disabled }: Props) {
  return (
    <button
      type="button"
      aria-pressed={mode === "edit"}
      disabled={disabled}
      onClick={() => onChange(mode === "edit" ? "view" : "edit")}
      className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] font-medium transition-colors"
      style={{
        background:
          mode === "edit" ? "var(--px-accent-tint)" : "var(--px-surface)",
        color: mode === "edit" ? "var(--px-accent)" : "var(--px-fg-3)",
        borderColor:
          mode === "edit" ? "var(--px-accent-line)" : "var(--px-hairline)",
      }}
    >
      <span
        className="h-[6px] w-[6px] rounded-full"
        style={{ background: "currentColor" }}
      />
      {mode === "edit" ? "Editing" : "View"}
    </button>
  );
}
```

### Task C2: `DefaultRolePicker.tsx`

Renders 4 chips. View mode shows the current role as a static pill. Edit mode shows the chip group with the active one highlighted in `--px-caution` (the team color).

Headline copy: "All members of this team hold the **{role}** role." (rendered above the picker by the parent — the picker only renders the chips).

Props: `{ value: TeamDefaultRole | undefined; onChange: (next: TeamDefaultRole) => void; mode: "view" | "edit"; }`

### Task C3: `InheritedField.tsx`

The wrapper. Renders one of these states:
- **inherited** (override === false, value is from inheritance): shows the inherited value as static dimmed text + small label "Inherited from {ancestorName}". When in edit mode, also shows an "Override" link.
- **override active** (value differs from inherited / explicit override): shows an editable input pre-filled with the value + sub-label "Override active. Reset to inherited." Reset link clears the override.
- **source mode** (no inheritance — this unit IS the source): shows an editable input. No override toggle. Sub-label "Tenant default" or similar.

Props:
```tsx
interface Props {
  label: string;
  mode: "view" | "edit";
  variant: "source" | "inherit";
  value: string | undefined; // explicit override on this unit
  inheritedValue: string | null;
  inheritedFromName: string | null;
  onChange: (next: string | undefined) => void;
  // children = the actual input/select (so the parent controls the input shape)
  children: (helpers: {
    isEditing: boolean;
    effectiveValue: string;
    isOverride: boolean;
  }) => React.ReactNode;
}
```

This avoids encoding any single input shape. Used by `LocaleStrip` (with `<select>` children) and `ComplianceFlagsList` (with checkbox children).

### Task C4: `LocaleStrip.tsx`

Three locale chips, horizontal flex row. Internally uses `InheritedField` per chip. In source mode (Company root), all three are direct editable selects. In inherit mode (Region / Client account), each shows the inherited value with a per-chip override toggle.

Selects: hardcode the option lists (timezone IANA list, ISO 4217 currencies, BCP 47 locales). Match the lists in the design HTML (lines 2094-2148).

### Task C5: `ComplianceFlagsList.tsx`

Three rows: AIVIA / GDPR / CCPA. Each row: name + short description. Source mode: simple checkbox + On/Off label. Inherit mode: shows inherited state with the same override toggle pattern as InheritedField.

### Task C6: `SubUnitCard.tsx`

Linked card. Shows:
- Top row: unit name + type pill (`UnitTypePill`).
- Default-role pill (only when child unit is Team and has default_role set).
- Bottom row: member count + (if `openRoles > 0`) open-roles pill + "Open →" arrow.

Used in a 2-column CSS grid by the Sub-units sections.

### Task C7: `TemplateRow.tsx`

One row of the Pipeline templates section. Shows:
- Template name (with "Default" tag if applicable).
- Stage flow chips with `→` arrows between them (`{stage1} → {stage2} → ...`).
- "Edit" link on the right.

Templates data comes from the existing `usePipelineTemplates(unitId)` hook. The row is presentational — no fetches inside.

### Task C8: `MembersListCompact.tsx`

Sidebar-friendly compact members list. Wraps `useOrgUnitMembers(unitId)`, `useTeamMembers()`, `useRoles()`, `useAssignRole()`, `useRemoveRole()`. Renders:
- Helper text per page (passed as a prop).
- One row per member: avatar + name + roles (Admin pill if applicable + other roles).
- "+ Add" inline form (collapsed by default — same UX as the existing `MembersSection.tsx`).

Width: fits a ~280px sidebar column.

### Task C9: `CascadingMembersTeam.tsx`

Team-only. The richest variant. Three row types:
- **Regular member**: avatar (default hue) + name/email + `default_role` pill. Remove button.
- **Cascaded admin**: avatar with red tint + name/email + "cascaded from {parentName}" sub-label + `Admin` pill + (if not opted in) `+ Opt in to {default_role}` link. The "cascaded from" text comes from a new field — we approximate by checking which parent unit they're admin on.
- **Manual admin**: same as cascaded but sub-label "added manually". Treated identically client-side; no backend distinction in v1. (Per spec §3.4.4, the design just shows a different sub-label; we render "added directly" when the member is admin on the team but not admin on any ancestor.)
- **Admin opted in**: cascaded/manual admin who also holds the default role. Same row as cascaded admin but with both pills.

To compute "cascaded from": fetch the ancestor admin lists once and compare. Use `useOrgUnits()` cache (already loaded by the dispatcher). For each admin on this team, check which ancestor lists this user as admin → that ancestor is the source. If none, show "added directly".

"Opt in to {default_role}": calls `useAssignRole({ user_id, role_id: <id of default_role> })`. The role_id mapping comes from `useRoles()`.

Add: existing `+ Add member` flow — single-step: pick person → that person is auto-added with the team's default_role. No role picker UI.

---

## Phase D — Update shared chrome

### Task D1: Replace `shared.tsx` chrome

**File:** `frontend/app/app/(dashboard)/settings/org-units/[unitId]/shared.tsx`

- [ ] **D1.1:** Update `UnitPageHeader` to accept new props:
  - `kicker?: string` (small mono text shown next to the type pill — e.g., "Default · Interviewer" for Team, or breadcrumb path for Client account)
  - `stats?: React.ReactNode` (the dot-separated stats roll-up: "8 members · 3 open jobs")
  - `defaultRolePill?: React.ReactNode` (rendered in the pills row next to UnitTypePill — Team only)
  - `onBack?: () => void` (existing)
  - Remove the existing `lead`, `people`, `openRoles` props in favor of the more flexible `stats` slot
  - Remove the parent path prop (it's now part of breadcrumb in the kicker)

- [ ] **D1.2:** Drop `SubUnitsList` (replaced by `SubUnitCard` grid in pages) and `SmallStats` (D15: stripped). Keep `Section`, `Field`, `TagChip`, `UnitTypePill`.

- [ ] **D1.3:** Add a new `Eyebrow` helper for section sub-headings (matches the design package — small caps label with hairline divider).

### Task D2: Create `Sidebar.tsx`

**File:** `frontend/app/app/(dashboard)/settings/org-units/[unitId]/Sidebar.tsx`

- [ ] **D2.1:** Single component that accepts:
  - `unit: OrgUnit`
  - `parentChain: OrgUnit[]` (for the hierarchy tree)
  - `extraTopCard?: React.ReactNode` (Direct members card slotted in by Division/Region/Company/Client)
  - `onDelete: () => void` (omitted on Company root)

- [ ] **D2.2:** Renders:
  1. (Optional) extra top card (Direct members)
  2. **Hierarchy** card — vertical tree showing the chain of ancestors with this unit highlighted, plus child units (use `parentChain` + sub-unit list)
  3. **Governance** card — created at, created by, deletable_by, admin_delete_disabled. Renamed to **Tenant info** when `unit.is_root` (just shows Created at + Tenant ID).
  4. (When not root) **Delete {unit_type}** button — uses existing `DangerConfirmDialog` pattern from the current detail components.

---

## Phase E — Rewrite each detail page

Order: Team → Division → Region → Company/Client. Each page is its own task with the same internal shape:

1. Set up `mode` state + form via React Hook Form with the right Zod schema.
2. Render the new header chrome (mode toggle, save/discard buttons in edit mode).
3. Render body sections per spec.
4. Render sidebar.
5. Wire `onSubmit` → `useUpdateOrgUnit`.
6. Write composition test.
7. Run `npm run type-check && npm run lint`.
8. Commit.

### Task E1: TeamDetail (spec §5.1)

**Files:**
- Replace: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/TeamDetail.tsx`
- Modify: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/schema.ts` — add `teamMetadataSchema`

Form values: `{ name: string; default_role: TeamDefaultRole; focus: string }`.

Body layout:
1. **Top section card** (merged Default role + Identity + Focus):
   - Eyebrow: "Default role" + small text "All members of this team hold the **{role}** role."
   - `<DefaultRolePicker>` (chips)
   - Below picker, a divider
   - Name input (full-width)
   - Focus textarea with the helper text from spec §5.1 (3)
2. **Members** section (uses `<CascadingMembersTeam unitId={unit.id} defaultRole={form.default_role} />`)
3. **Open jobs anchored here** — list pulled from existing jobs query, filtered by `org_unit_id === unit.id` and `status !== 'draft'`. If none, show empty-state.

Sidebar: `<Sidebar unit={unit} parentChain={parentPath} onDelete={...} />` (no extra top card).

### Task E2: DivisionDetail (spec §5.2)

**Files:**
- Replace: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/DivisionDetail.tsx`
- Modify: `schema.ts` — add `divisionMetadataSchema` (`{ description?: string }`)

Form values: `{ name: string; description: string }`.

Body layout:
1. **Identity + Description** (merged section card):
   - Name input
   - Eyebrow: "Description"
   - Helper text from spec §5.2 (2)
   - Description textarea
2. **Sub-units** — header with "+ New sub-unit" button (opens existing `OrgUnitCreateDialog`), then a 2-col grid of `<SubUnitCard>`s.
3. **Pipeline templates** — header with "+ Manage templates →" link to `[unitId]/pipeline-templates/`. Body uses `<TemplateRow>` per template loaded via `usePipelineTemplates(unitId)`. Empty state: "No templates yet. Inherits from {parent}."

Sidebar: `<Sidebar extraTopCard={<MembersListCompact unitId={unit.id} helperText="Per-member role picker." />} />`.

### Task E3: RegionDetail (spec §5.3)

**Files:**
- Replace: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/RegionDetail.tsx`
- Modify: `schema.ts` — add `regionMetadataSchema`

Form values: `{ name: string; default_timezone?: string; default_currency?: string; default_locale?: string; compliance_aivia_il?: boolean; compliance_gdpr_eu?: boolean; compliance_ccpa_ca?: boolean }`.

Header: include `<LocaleStrip variant="inherit" inherited={unit.inherited_locale} value={...} onChange={...} />` directly under the breadcrumb/name (slot via the new `kicker`/extra-header-content prop on `UnitPageHeader`).

Body layout:
1. **Identity** — name input
2. **Sub-units** — same shape as Division
3. **Compliance flags** section using `<ComplianceFlagsList variant="inherit" inherited={unit.inherited_compliance} value={...} onChange={...} />`

Sidebar: same as Division (extra top card = Direct members compact).

### Task E4: CompanyDetail (spec §5.4) — handles both Company root and Client account

**Files:**
- Create: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/CompanyDetail.tsx`
- Delete: `frontend/app/app/(dashboard)/settings/org-units/[unitId]/CompanyProfileDetail.tsx`
- Modify: `schema.ts` — add `companyMetadataSchema`
- Modify: `page.tsx` (dispatcher) — switch the company/client_account branch to render `<CompanyDetail isClientAccount={unit.unit_type === 'client_account'} />`

Form values: `{ name: string; short_name?: string; website?: string; about: string; hiring_bar: string; default_timezone?: string; default_currency?: string; default_locale?: string; compliance_aivia_il?: boolean; compliance_gdpr_eu?: boolean; compliance_ccpa_ca?: boolean }`.

`about` and `hiring_bar` come from `unit.company_profile` and persist via `set_company_profile: true` only when both are non-empty + the other 2 strict fields (industry, company_stage) are also present. If the user only edits about/hiring_bar but the profile is incomplete, save the metadata but do NOT save the profile (existing pattern from old `CompanyProfileDetail`).

Header chrome (per the final HTML lines 2068-2169):
- Type pill: "Company · Root" (root) or "Client account" (client) — pass via `kicker` slot
- Name (h1)
- Website input (small mono input under the name)
- About textarea (rendered in header — shows char count + Copilot Call 1 stamp)
- LocaleStrip — `variant="source"` for Company, `variant="inherit"` for Client account
- Stats roll-up

Body:
1. **Hiring bar** — narrative textarea with char count + last-updated stamp + Copilot Call 1 stamp. Per L8.
2. **Sub-units** grid
3. **Pipeline templates** — same shape as Division
4. **Compliance flags** — `variant="source"` for Company root, `variant="inherit"` for Client account

Sidebar (Company root):
- `extraTopCard` = MembersListCompact
- Hierarchy
- **Tenant info** card (Created at + Tenant ID)
- No delete button — instead a small text note: "The root company unit cannot be deleted. To remove the tenant entirely, contact ProjectX support."

Sidebar (Client account):
- Same but with regular Governance card and a Delete button.

Pass `isClientAccount: boolean` to `<CompanyDetail>` to switch:
- Type pill text + tone
- Crumbs: render breadcrumb only when `isClientAccount`
- LocaleStrip variant
- ComplianceFlagsList variant
- Sidebar bottom card variant

### Task E5: Update dispatcher (`page.tsx`)

- [ ] **E5.1:** Replace the company/client_account branch:

```tsx
if (unit.unit_type === "company" || unit.unit_type === "client_account") {
  return (
    <CompanyDetail
      unit={unit}
      isClientAccount={unit.unit_type === "client_account"}
      parentPath={parentPath}
      subUnits={subUnits}
      openRolesCount={openRolesCount}
      openRolesByChildId={openRolesByChildId}
      onSaved={handleSaved}
    />
  );
}
```

- [ ] **E5.2:** Drop the import of `CompanyProfileDetail`.

- [ ] **E5.3:** No other dispatcher changes.

---

## Phase F — Tests, cleanup, and verification

### Task F1: Composition tests

For each detail page, write a Vitest composition test that:
1. Mocks `getFreshSupabaseToken` and the org-units API calls.
2. Wraps in `QueryClientProvider` with retries off.
3. Renders the detail page with a known `OrgUnit` fixture.
4. Asserts the spec'd sections render.
5. For pages with mode toggle: clicks "Edit", changes a field, clicks "Save changes", asserts the API was called with the correct payload.

The `composition tests` rule from auto-memory: render parent + children together; mock at the API boundary (apiFetch, not the hooks); negative-control by reintroducing a removed field and verifying the test fails.

### Task F2: Delete dead files

- [ ] **F2.1:** Delete:
  - `frontend/app/app/(dashboard)/settings/org-units/[unitId]/CompanyProfileDetail.tsx` (replaced by CompanyDetail)
  - `frontend/app/app/(dashboard)/settings/org-units/[unitId]/MembersSection.tsx` (replaced by MembersListCompact + CascadingMembersTeam)
- [ ] **F2.2:** Confirm no other imports remain:

```bash
grep -rn "CompanyProfileDetail\|from.*MembersSection" frontend/app/app frontend/app/components frontend/app/tests
```

Expected: no matches.

### Task F3: Full type-check + lint + tests + build

- [ ] **F3.1:** From `frontend/app/`:

```bash
npm run type-check
npm run lint
npm run test
npm run build
```

All must pass with zero errors before declaring complete.

### Task F4: Manual UI verification

- [ ] **F4.1:** Run `npm run dev`. Walk through each unit type:
  - Team page: change default_role, save. Verify the headline copy updates and the "Add member" flow uses the new role.
  - Division page: edit description, save. Verify pipeline templates link works.
  - Region page: toggle locale override, change a value, reset to inherited. Verify save sends the right metadata.
  - Company root: edit About, edit a locale value, edit a compliance flag, save. Verify all three persist.
  - Client account: confirm locale + compliance show inherited state from Company root, override toggles work.

If any of these fail, do NOT claim the task complete. Per `verification-before-completion`: evidence before assertions.

### Task F5: Commit + push

- [ ] **F5.1:** Final commit:

```bash
git add -A
git commit -m "feat(org-units): redesign detail pages per 2026-04-27 spec"
```

- [ ] **F5.2:** Push (only if user explicitly asks).

---

## Self-Review Checklist

Before declaring the plan complete:

- [x] Every spec section §5.1–§5.4 maps to a task in Phase E
- [x] Every spec field policy decision §3.6 is reflected in the metadata schemas
- [x] Inheritance pattern §3.5 + §7.2 → Phase A
- [x] Default role concept §3.3 + §3.4 → DefaultRolePicker + CascadingMembersTeam
- [x] Hub pattern §3.7 → CompanyDetail keeps "Edit profile →" link to existing route; pipeline templates section links to existing route
- [x] Sidebar pattern §6.3 → Sidebar.tsx
- [x] Naming consistency §6.1 → "Sub-units" everywhere via SubUnitCard
- [x] No backend rewrites — only additions (Phase A is non-breaking)
- [x] Iterations from chat2 (locale/about in header, members in sidebar, merged top sections) → L1–L5

---

## Open Risks

1. **Inheritance N+1 risk on list endpoint.** Walking `parent_unit_id` chain inside `_serialize_unit` for every unit in `list_org_units` could be N². Mitigation: pre-fetch all units in the tenant once into a dict by id, walk in-memory. The list endpoint already loads everything for the tree view.
2. **Cascaded-admin detection is approximate.** "Cascaded from {parent}" requires checking which ancestor lists the user as admin. We can compute this client-side from `useOrgUnits()` data. Acceptance: if it gets it wrong (rare edge: user is admin on multiple ancestors), we just label "added directly" — no functional consequence.
3. **`set_metadata: true` is destructive** — sends the full new metadata blob. Forms must spread the existing metadata into form defaults so unsaved keys aren't dropped. Verify by snapshot-comparing `unit.metadata` before/after a save where only `default_role` changed.
4. **Vitest + Tailwind v4 with new tokens.** Components reference `var(--px-*)` which Vitest doesn't render. Tests should assert on text + role + structure, not on computed styles.
