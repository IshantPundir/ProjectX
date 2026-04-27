# Org Unit Detail Pages — Redesign

**Date:** 2026-04-27
**Status:** Approved (design phase)
**Owner:** Ishant Pundir
**Scope:** Redesign the per-unit detail pages at `app/(dashboard)/settings/org-units/[unitId]` — one page per unit type — to align with a sharper mental model and strip cosmetic metadata that no other code consumes. Introduces a `default_role` concept on Team units, an inheritance pattern for `company_profile` / locale defaults / compliance flags, and a hub-style layout with deep editors on existing sub-routes. Frontend-led; small backend additions for the new fields.

---

## 1. Goals

1. **Make the page reflect what each unit type actually IS.** Each detail page should foreground the load-bearing data and demote (or strip) the decorative metadata that nothing else reads.
2. **Eliminate the "info that does nothing" feel** of the current pages. ~70% of fields on the existing pages are stored as opaque JSONB and never read back.
3. **Introduce `default_role` on Team units** as an auto-fill convenience when adding members, simplifying the Members UX on team pages.
4. **Establish an inheritance pattern** for `company_profile`, locale defaults (timezone/currency/locale), and compliance flags (AIVIA/GDPR/CCPA) — values set at the Company / Client account level flow down through the tree, with optional per-region overrides.
5. **Lock a "hub pattern":** detail pages are summary surfaces with deep editors hanging off existing sub-routes (`[unitId]/company-profile/`, `[unitId]/pipeline-templates/`).
6. **Surface the real Pipeline Templates feature** on Division and Company pages, replacing the placeholder "Default interview panel" text inputs that exist today.

## 2. Non-Goals

- **No backend changes to the org-unit hierarchy model.** The 5 unit types, parent rules, and Admin cascade behavior all stay as-is.
- **No changes to JD anchoring, Copilot Call 1, or the company_profile schema.** The 4-field strict shape (`about` / `industry` / `company_stage` / `hiring_bar`) is unchanged.
- **No deprecation of the `[unitId]/company-profile/` deep editor route.** This redesign relies on it.
- **No replacement of the `[unitId]/pipeline-templates/` route.** Still the deep editor; the detail page links into it.
- **No new "Agency Contracts" feature.** The contract/SLA fields on `client_account` (contract_start, fee_model, guarantee_period, etc.) are stripped from this page; if/when an agency contract management feature ships, it gets its own surface.
- **No "members search across the whole subtree" view.** Detail pages show direct members only. Roll-up exploration belongs on a future people-search surface.
- **Default role on Region or Division** — not in scope. After implication analysis, only Team carries `default_role`.
- **Mobile layout** — dashboard is desktop-only per `frontend/app/CLAUDE.md`.

## 3. Mental Model

### 3.1 Unit type purposes

| Type            | Purpose                                                            | Carries `default_role`? | Has `company_profile`? |
|-----------------|--------------------------------------------------------------------|-------------------------|------------------------|
| **Company**     | Tenant root + tenant-wide source of truth for profile, locale, compliance | No                      | Yes (load-bearing)     |
| **Client account** | Alt-root for agency tenants. Same shape; overrides for JDs anchored under it | No                      | Yes (load-bearing)     |
| **Region**      | Geography. Inherits locale/compliance, may override per-region.    | No                      | No (inherits)          |
| **Division**    | BU container. Groups teams. Owns pipeline templates.               | No                      | No (inherits)          |
| **Team**        | Role-bucket leaf. JDs anchor here.                                 | **Yes**                 | No (inherits)          |

Rationale recap (from design discussion):
- Each unit type has ONE clear job. Dual-purpose units (a Division that's both container AND role pool) are explicitly avoided.
- Team is the only type where `default_role` makes sense — it's a leaf, can't contain other units, and JDs anchor here.
- Region is geography; making it a role-bucket overloads it (timezone/locale defaults conflict with role-pool semantics).
- Division is a BU container; recruiter / HM pools at division level are modeled as child teams (e.g. "Eng Recruiters" team with `default_role=Recruiter`).

### 3.2 Membership rules

| Unit              | Add members? | Role assignment                                                      |
|-------------------|--------------|-----------------------------------------------------------------------|
| Company (root)    | ✓            | Per-member role picker. Multiple roles per member allowed.           |
| Client account    | ✓            | Per-member role picker. Multiple roles per member allowed.           |
| Region            | ✓ (rare)     | Per-member role picker. Multiple roles per member allowed.           |
| Division          | ✓            | Per-member role picker. Multiple roles per member allowed.           |
| **Team**          | ✓            | **No role picker.** Members inherit the team's `default_role`. Admin is the only role that can coexist with `default_role` (via cascade or manual addition). |

A user's roles across the whole system come from being a member of multiple units. Multi-role users become multi-unit users — you stack assignments by joining different units, not by piling roles onto one assignment on a team.

### 3.3 The `default_role` concept (Team only)

- **Enum:** `Recruiter | Hiring Manager | Interviewer | Observer`. **`Admin` is intentionally excluded** — admins must be selected deliberately, never via membership of a "default = Admin" unit.
- **Semantics:** when a team has `default_role` set, every member added to that team automatically gets that role on the team. No per-row role picker on the team page Members section.
- **Single-step add** (Option A from design): click "+ Add member" → pick a person → they're added with the team's role applied. No role choice in the UI.
- **Pre-fill, not exclusive:** a team member can also hold the `Admin` role on the team (only Admin — no other role mixing inside a team).

### 3.4 Admin handling on teams

This is the only role that breaks the "one role per team membership" simplicity:

1. **Cascade on team creation** (existing backend behavior in `create_org_unit`): when a child team is created under a parent unit, anyone who's `Admin` on the parent gets `Admin` auto-assigned on the new team. They do NOT auto-get the team's `default_role`.
2. **Manual addition allowed.** A super admin or unit admin can manually add another Admin to a team without that admin being a member of the team's role-bucket.
3. **Cascaded admins may opt-in to `default_role`.** UI affordance: a cascaded admin row shows "+ Opt in to {default_role}" link they can click to also take the team's role.
4. **Two visual variants** in the team Members list:
   - Regular member → standard avatar + the team's `default_role` pill (shown for clarity even though implicit)
   - Cascaded admin → red-tinted avatar + `Admin` pill + optional `+ Opt in to {default_role}` link
   - Cascaded admin who opted in → red-tinted avatar + `Admin` pill + `default_role` pill

### 3.5 Inheritance pattern

Three things now flow up the tree using the same pattern that `find_company_profile_in_ancestry` already uses for `company_profile`:

1. **`company_profile`** — already implemented. Set at Company root; client_account overrides for JDs under it.
2. **Locale defaults** (NEW) — `timezone` / `currency` / `locale`. Set at Company; client_account / region may override.
3. **Compliance flags** (NEW) — `aivia_il` / `gdpr_eu` / `ccpa_ca` (boolean each, more can be added). Set at Company; region may override.

Lookup walks parent_unit_id chain; first non-null value wins. Mirrors the existing pattern.

### 3.6 Field philosophy (locked: Option B from design discussion)

For each field on the existing pages, decided per:

- **Load-bearing** — actually consumed by other code. Keep, surface prominently. (Examples: `company_profile`, `member_count`, governance fields, `short_name` for candidate-UI branding.)
- **Aspirational** — not consumed today, but with a clear near-term path to becoming real. Keep, label honestly. (Examples: team's `focus`, division's `description`, region's locale/compliance.)
- **Cosmetic** — stored, never read, no clear path to use. Strip. (Examples: division's `cost_center` and `hiring_budget`; region's `code` and `offices[]`; company's `panel_size` / `equity` / `bonus` / etc.)

### 3.7 Hub pattern

Detail pages are summary hubs, not mega-forms.

- The `[unitId]/company-profile/` deep editor is the canonical edit surface for the 4-field profile. The detail page shows a profile preview with an "Edit profile →" link.
- The `[unitId]/pipeline-templates/` deep editor is the canonical edit surface for templates. The detail page shows a list of templates with edit links.
- Inline-editable on the detail page: short-form identity fields (name, short_name, website) and all aspirational fields. Long-form narrative content (about, hiring_bar) is read-only on the detail page.

## 4. Decisions Locked

| ID  | Decision                                                                                          | Pick |
|-----|---------------------------------------------------------------------------------------------------|------|
| D1  | `default_role` scope — which unit types can carry one                                             | **Team only.** |
| D2  | `default_role` semantics — exclusive vs pre-fill                                                  | **Single-step add.** Members of a team get exactly the team's `default_role`. The only second role allowed is `Admin` (via cascade or manual). |
| D3  | Can `default_role` be Admin?                                                                      | **No.** Enum is `Recruiter / HM / Interviewer / Observer`. Admins are selected deliberately; no "Admin team" concept. |
| D4  | Admin behavior on team creation                                                                   | **Existing backend cascade preserved.** Cascaded admins get only `Admin`, not `default_role`. UI offers "Opt in to {default_role}" toggle. |
| D5  | `member_count` chrome stat                                                                        | **Kept** on every detail page header. |
| D6  | Inheritance for `company_profile` / locale / compliance                                          | **Walk-up-tree, first-non-null wins.** Mirrors `find_company_profile_in_ancestry`. |
| D7  | Where do locale defaults + compliance flags live?                                                | **Source: Company / Client account.** Region inherits with override toggles. Division/Team inherit only (no override). |
| D8  | Pipeline templates section — which detail pages surface it?                                       | **Company + Division** show templates section. Team and Region do not. (Team templates inherit; selection is a JD-creation concern.) |
| D9  | Field policy across all pages                                                                     | **Strip cosmetic, keep aspirational, keep load-bearing** (Option B from design). |
| D10 | Detail page edit pattern                                                                          | **Hub pattern.** Short-form fields inline-editable. Long-form profile narratives view-only on detail page; "Edit profile →" links to deep editor. |
| D11 | Sub-units section naming                                                                          | **"Sub-units"** on every page (not "Teams under X" or "Regions under X"). Cards show type pills + (where applicable) default-role pills. |
| D12 | "Lead" free-text fields (lead_name on division/team/region)                                       | **Stripped.** Admins ARE the leads — no separate free-text owner field. |
| D13 | Sidebar shape                                                                                      | **Hierarchy + Governance + Delete** on Team/Division/Region/Client account. Root Company has Hierarchy + Tenant info + delete-disabled note. |
| D14 | Header chrome                                                                                      | Type pill + (default-role pill on team) + breadcrumb + name + stats roll-up + actions (Save / Archive / Save changes). |
| D15 | Stats sidebar card (Members / Open / Sub-units)                                                   | **Stripped.** Stats live in the header chrome only. Single source of truth. |
| D16 | Client account differences from Company                                                           | Has parent → breadcrumb. Locale/compliance show inherited values from Company root with override toggles (same UX as Region). Has Delete button. **No** Contract/SLA section. |
| D17 | Company/Client_account: Identity grouping                                                         | Identity (name, short_name, website) lives **inside** the Company profile section as the top row. Not a separate top-level section. |
| D18 | Company/Client_account: About vs Hiring bar layout                                                | **Stacked vertically**, each at full width. About first, Hiring bar below. |

## 5. Per-Page Designs

### 5.1 Team page

**Purpose:** A role-bucket leaf where JDs anchor. Members inherit the team's `default_role`.

**Header chrome:**
- Type pill: `Team`
- Default-role pill: `Default · Interviewer` (or whichever)
- Name (large)
- Breadcrumb: `Acme · AMER · Engineering`
- Stats: `8 members · 3 open jobs`
- Actions: `Archive` · `Save changes`

**Body sections (left column, in order):**

1. **Default role** *(new — highlighted treatment, the headline change)*
   - Heading: "All members of this team hold the {role} role."
   - Picker: 4 chips for `Recruiter / Hiring Manager / Interviewer / Observer`. Active chip styled distinctly.
2. **Identity**
   - Name (input, editable)
   - Lead — *removed per D12*
3. **Focus** *(aspirational — Copilot context for JD enrichment)*
   - Subhead: "What this team focuses on"
   - Helper text: "Optional. Copilot uses this to tailor JD enrichment for roles anchored to this team."
   - Textarea
4. **Members** · `{N} people`
   - Helper text: "No role picker — every member inherits the team's default role. Admins shown separately and may opt in to also hold the default role."
   - Header row: Person · `+ Add member`
   - Per-row variants:
     - Regular member: avatar + name/email + `{default_role}` pill
     - Cascaded admin: red-tinted avatar + name/email "cascaded from {parent}" + `Admin` pill + `+ Opt in to {default_role}` link
     - Manual admin: red-tinted avatar + name/email "added manually" + `Admin` pill + `+ Opt in to {default_role}` link
     - Admin who opted in: red-tinted avatar + name/email + `Admin` pill + `{default_role}` pill
5. **Open jobs anchored here** · `{N}`
   - List of job titles linking to JD detail pages
   - "See all jobs scoped to {team}" link

**Sidebar (right column):**
- Hierarchy card (compact tree path)
- Governance card (created by/at, deletable_by, admin_delete_disabled)
- Delete team button (destructive variant)

**Removed from current `TeamDetail.tsx`:**
- `slug` (cosmetic)
- `lead_name` (D12)
- "Roster summary" duplicate table (showed same data as Members)
- `SmallStats` sidebar card (Members / Interviewers / Open / Pressure) — D15
- "Rolls up to" readonly field (replaced by hierarchy card)
- "Pipeline templates" sidebar link (templates are JD-creation concern, D8)

### 5.2 Division page

**Purpose:** A BU container. Owns pipeline templates. Members can be added directly with per-member roles.

**Header chrome:**
- Type pill: `Division`
- Name (large)
- Breadcrumb: `Acme · AMER`
- Stats: `4 teams · 7 direct members · 12 open jobs (rolled up)`
- Actions: `Archive` · `Save changes`

**Body sections (left column, in order):**

1. **Identity**
   - Name (input, editable)
   - Lead — *removed per D12*
2. **Description** *(aspirational — Copilot context)*
   - Subhead: "What this division does"
   - Helper text: "Optional. Copilot uses this as context when enriching JDs anchored to teams under this division."
   - Textarea
3. **Direct members** · `{N} people`
   - Helper text: "Per-member role picker (today's behavior). Default-role logic only applies to teams."
   - Header row: Person · Roles · `+ Add member`
   - Per-row: avatar + name/email + role chips + remove (×)
4. **Sub-units** · `{N}` (D11 naming)
   - Action: `+ New sub-unit`
   - Cards in 2-column grid; each card shows type pill, name, open-roles pill, default-role pill (for Team children), member-count meta, "Open →" link
5. **Pipeline templates** · `{N} templates` *(replaces fake "Default interview panel" textbox cluster)*
   - Helper text: "Reusable interview pipelines for jobs anchored under any team in this division. The default template auto-applies when creating a new JD."
   - Per-row: template name (with `Default` pill if applicable), stage summary, Edit link
   - "+ Manage templates →" link to `[unitId]/pipeline-templates/`

**Sidebar (right column):**
- Hierarchy card
- Governance card
- Delete division button

**Removed from current `DivisionDetail.tsx`:**
- `code` (cosmetic)
- `lead_name` (D12)
- `cost_center` (cosmetic)
- `hiring_budget` (cosmetic)
- "Default interview panel" placeholder block (`default_panel`, `default_takehome`, `default_tech_screen`, `bar_raiser_pool`) — replaced by real Pipeline templates section
- "Rolls up to" readonly field (replaced by hierarchy card)
- `SmallStats` sidebar card (D15)

### 5.3 Region page

**Purpose:** Geography. Carries locale defaults / compliance flags either inherited (default) or overridden per-region.

**Header chrome:**
- Type pill: `Region`
- Name (large)
- Breadcrumb: `Acme`
- Stats: `3 divisions · 2 direct members · 18 open jobs (rolled up)`
- Actions: `Archive` · `Save changes`

**Body sections (left column, in order):**

1. **Identity**
   - Name (input, editable)
   - Lead — *removed per D12*
2. **Direct members** · `{N} people`
   - Helper text: "Per-member role picker. Often empty — most members live at division/team level. Useful for regional HR partners, legal contacts, regional admins."
   - Same shape as Division Members
3. **Sub-units** · `{N}`
   - Same shape as Division Sub-units, except cards are typically Division-typed
4. *(divider: "Tenant defaults — inherited from {parent}")*
5. **Locale & defaults** *(inherited from parent, override per field)*
   - Three fields: `timezone` · `currency` · `locale`
   - Each field shows inherited value with an "Override for this region" toggle. When toggled on, the field becomes editable and a "Reset to inherited" affordance appears.
6. **Compliance flags** *(inherited from parent, override per flag)*
   - Three flag rows: AIVIA · GDPR · CCPA
   - Each flag shows inherited state (checked/unchecked from parent) with an override toggle. Same UX shape as locale fields.

**Sidebar (right column):**
- Hierarchy card
- Governance card
- Delete region button

**Removed from current `RegionDetail.tsx`:**
- `code` (cosmetic)
- `primary_city` (cosmetic — what does it do?)
- `lead_name` (D12)
- `offices[]` entire editable table (cosmetic, goes nowhere)
- "Rolls up to" readonly field (replaced by hierarchy card)
- `notes` (Regional notes section) — dropped per design discussion
- `SmallStats` sidebar card (D15)

### 5.4 Company page (root) and Client account

The root Company page is the source of truth for tenant-wide defaults. Client account follows the same shape with three differences (called out below).

**Header chrome:**
- Type pill: `Company · Root` (Company) or `Client account` (Client account)
- Name (large)
- Breadcrumb: none (Company root) or `Acme` (Client account)
- Stats: `3 regions · 12 divisions · 4 direct members · 42 open jobs (rolled up)`
- Actions: `Save changes` (Company root) — no Archive/Delete on root. `Archive` · `Save changes` plus Delete button on Client account.

**Body sections (left column, in order):**

1. **Company profile** *(load-bearing · feeds JD enrichment — highlighted treatment)*
   - **Identity row** at top (3-column grid): Company name · Short name · Website. Inline-editable.
   - **Profile divider**
   - **Meta row**: `INDUSTRY {value}` · `STAGE {value}` as inline label+pill pairs. Plus a small "Read by Copilot Call 1 · inherited by all sub-units" stamp.
   - **About** narrative block (read-only on detail page, char-count shown)
   - **Hiring bar** narrative block (read-only on detail page, char-count shown), stacked below About
   - **Action row**: `Edit profile →` button (links to `[unitId]/company-profile/`) + last-updated stamp
2. **Direct members** · `{N} people`
   - Same shape as Division/Region Members. Tenant-level admins live here.
3. **Sub-units** · `{N}`
   - Cards typically Region-typed (or Division-typed if the tenant skips regions). For agency tenants, may also include `Client account` typed cards.
4. **Pipeline templates** *(tenant-wide on Company root; "owned by this client" on Client account)* · `{N}`
   - Helper text: "Defaults for divisions that don't have their own. Each division can also define its own templates."
   - Same row layout as Division Pipeline templates section
   - "+ Manage tenant templates →" link
5. *(divider: "Tenant-wide defaults" on Company root; "Defaults — inherited from Acme" on Client account)*
6. **Locale & defaults**
   - Company root: source of truth, no inheritance UI. Three editable inputs (timezone, currency, locale).
   - Client account: shows inherited values with override toggles (same UX as Region).
7. **Compliance flags**
   - Company root: source of truth, no inheritance UI. AIVIA / GDPR / CCPA checkboxes.
   - Client account: shows inherited flags with override toggles.

**Sidebar (right column):**
- Hierarchy card
- Tenant info card on Company root: Created at, Tenant ID. Governance card on Client account.
- Note on Company root: "The root company unit cannot be deleted. To remove the tenant entirely, contact ProjectX support."
- Delete client button on Client account.

**Removed from current `CompanyProfileDetail.tsx`:**
- `legal_name`, `hq`, `size` (cosmetic)
- `interview_style`, `panel_size`, `takehome_policy`, `time_to_decision` (cosmetic — replaced by real Pipeline templates section)
- `values`, `base_philosophy`, `equity`, `bonus` (cosmetic)
- `locations[]`, `remote_policy`, `visa` (cosmetic)
- For client_account: `contract_start`, `renews`, `fee_model`, `guarantee_period`, `exclusive_roles`, `account_manager` — moved out of org-units entirely. If/when an Agency Contracts feature ships, it gets its own surface.
- Inline 4-textarea profile block (moved into preview-card + deep-editor route)
- `CopilotSignalsCard` aside (signals are a derived view, not edit state — recompute from saved profile)

## 6. Cross-Cutting Concerns

### 6.1 Naming and chrome consistency

- "Sub-units" everywhere (D11). Never "Teams under X" or "Regions under X".
- Type pills: dotted style with type-specific color. `Company`, `Client account`, `Region`, `Division`, `Team`.
- Default-role pills appear only on Team cards/headers. Format: `Default · {Role}`.
- Open-roles pill: appears when `open_roles > 0`. Pressure tiers retained from current implementation.

### 6.2 Members section rendering rules

| Page              | Role picker per row? | Multi-role rows? | Admin variant?                |
|-------------------|----------------------|------------------|-------------------------------|
| Company           | Yes                  | Yes              | Standard (red Admin pill)     |
| Client account    | Yes                  | Yes              | Standard                      |
| Region            | Yes                  | Yes              | Standard                      |
| Division          | Yes                  | Yes              | Standard                      |
| **Team**          | **No**               | No (except Admin) | Tinted avatar + opt-in link  |

### 6.3 Sidebar pattern

Every page's sidebar is consistent in shape:
1. **Hierarchy** card — compact tree path showing this unit and its position.
2. **Governance** card (or Tenant info on Company root) — created at, created by, deletable_by, admin_delete_disabled.
3. **Delete button** (full-width, destructive variant) — except Company root which has the no-deletion note.

### 6.4 Inheritance UX shape

Used on Region (locale + compliance) and Client account (locale + compliance). Same component pattern in both:

- **Default state** — input/checkbox shows the inherited value, dimmed/readonly, with a sub-label "Inherited from {ancestor name}".
- **Override toggle** — small "Override for this region" link beside the field.
- **Toggled state** — input/checkbox becomes editable with the inherited value pre-filled. Sub-label changes to "Override active. {Reset to inherited} link".

### 6.5 Hub pattern routing

| From                  | Sub-route                          | What lives there                              |
|-----------------------|-------------------------------------|----------------------------------------------|
| Detail page           | `[unitId]/company-profile/`        | 4-field profile deep editor (existing)       |
| Detail page           | `[unitId]/pipeline-templates/`     | Templates list + edit (existing)             |
| Detail page           | `[unitId]/pipeline-templates/new`  | New template (existing)                      |
| Detail page           | `[unitId]/pipeline-templates/:id`  | Edit template (existing)                     |

No new sub-routes introduced by this redesign.

## 7. Backend Implications

This is primarily a frontend redesign. Backend changes are minimal and non-breaking.

### 7.1 New fields needed

**On `team` units (in `unit_metadata` JSONB):**
- `default_role: 'Recruiter' | 'Hiring Manager' | 'Interviewer' | 'Observer'`. Optional initially for migration; eventually required on all team units. Validation: must match one of the 4 enum values; cannot be `Admin`.

**On `company` and `client_account` units (in `unit_metadata` JSONB or first-class columns):**
- `default_timezone: string` (IANA tz)
- `default_currency: string` (ISO 4217)
- `default_locale: string` (BCP 47)
- `compliance_aivia_il: boolean`
- `compliance_gdpr_eu: boolean`
- `compliance_ccpa_ca: boolean`

Implementation note: these can start as `unit_metadata` JSONB keys (no migration needed) and be promoted to first-class columns later if they need indexing.

### 7.2 New helper service functions

Mirror `find_company_profile_in_ancestry`:
- `find_locale_defaults_in_ancestry(db, org_unit_id) -> { timezone, currency, locale } | None`
- `find_compliance_flags_in_ancestry(db, org_unit_id) -> { aivia_il, gdpr_eu, ccpa_ca } | None`

### 7.3 Existing behavior preserved

- Admin cascade on `create_org_unit` — unchanged.
- `find_company_profile_in_ancestry` — unchanged.
- All hierarchy validation rules in `create_org_unit` — unchanged.
- RLS policies and the `nexus_app` role — unchanged.

### 7.4 What's deprecated (frontend-only)

These fields stop being read or written by the redesigned pages but stay in the JSONB for now (no destructive migration needed):

- Team: `slug`, `lead_name`
- Division: `code`, `lead_name`, `cost_center`, `hiring_budget`, `default_panel`, `default_takehome`, `default_tech_screen`, `bar_raiser_pool`
- Region: `code`, `primary_city`, `lead_name`, `offices[]`, `notes`
- Company / client_account: `legal_name`, `hq`, `size`, `interview_style`, `panel_size`, `takehome_policy`, `time_to_decision`, `values`, `base_philosophy`, `equity`, `bonus`, `locations[]`, `remote_policy`, `visa`, `contract_start`, `renews`, `fee_model`, `guarantee_period`, `exclusive_roles`, `account_manager`

A follow-up cleanup migration can drop these JSONB keys after the redesign ships and we're confident nothing reads them. Out of scope for this design.

## 8. Open Questions / Deferred

These are not blockers for implementation but are worth flagging.

1. **Team-level pipeline template overrides** — backend allows it (route `[unitId]/pipeline-templates/` works on any unit) but the team page doesn't surface a section for it. Decision: only specialized teams might need this, and they can hit the URL directly. We can add an "Override default template" affordance later if real demand emerges.
2. **Compliance flag set** — sketched 3 (AIVIA, GDPR, CCPA). Real Phase 3+ integration may require more flags or a different shape (per-flag config object instead of a boolean). Treat the 3 as a starting point, not an exhaustive list.
3. **Region locale inheritance from Client account** — when a Region is under a Client account (rather than directly under root Company), inheritance walks up to the Client account. Implementation just follows `parent_unit_id` chain; no special-casing needed.
4. **Members section pagination** — current TeamDetail has no pagination on the roster. Out of scope here; a future "huge teams" concern.
5. **`OrgUnitMember` schema** — no changes needed for this redesign. The existing `list_unit_members` API returns what we need.

## 9. Implementation Considerations

### 9.1 Component structure

Existing layout per `frontend/app/app/(dashboard)/settings/org-units/[unitId]/`:

- `page.tsx` — type dispatcher (renders the right detail component based on `unit.unit_type`)
- `CompanyProfileDetail.tsx` (handles both company + client_account today)
- `RegionDetail.tsx`
- `DivisionDetail.tsx`
- `TeamDetail.tsx`
- `MembersSection.tsx` (shared)
- `shared.tsx` (UnitPageHeader, Section, Field, etc.)

This redesign keeps this structure. Each detail component is rewritten internally; the dispatcher in `page.tsx` is unchanged. Shared chrome (`UnitPageHeader`, `Section`) gets touched but stays in `shared.tsx`.

### 9.2 New shared sub-components

Worth extracting:

- `<DefaultRolePicker>` — the 4-chip picker for Team's default_role
- `<InheritedField>` — wraps an input/checkbox with the inheritance + override-toggle UX (used by Region and Client account)
- `<ComplianceFlagsList>` — renders the 3 flags with descriptions; works in both source mode (Company) and inheritance mode (Region/Client account)
- `<SubUnitCard>` — the card used in the Sub-units section grid (handles type pill, default-role pill, open-roles pill, member-count meta)
- `<TemplateRow>` — the row used in Pipeline templates section

These should live in `components/dashboard/org-units/` per `frontend/app/CLAUDE.md` placement rules.

### 9.3 Forms & validation

Continues to use React Hook Form + Zod per project convention. Each detail component owns its form. Schemas are colocated.

New Zod constraints:
- `default_role`: `z.enum(['Recruiter', 'Hiring Manager', 'Interviewer', 'Observer'])`
- `default_timezone`: `z.string().regex(<IANA tz pattern>)` — keep loose for now
- `default_currency`: `z.string().length(3)`
- `default_locale`: `z.string().regex(<BCP 47 pattern>)` — keep loose for now
- Compliance flags: `z.boolean()` each

### 9.4 API surface

Existing `PATCH /api/org-units/{id}` with `{ metadata, set_metadata: true }` is sufficient. The frontend writes the new keys into `unit_metadata`. No new endpoints for v1.

## 10. References

- Brainstorm session: 2026-04-27 conversation, mockups archived under `.superpowers/brainstorm/3564947-1777277074/content/`:
  - `mental-model-current.html` — initial audit
  - `default-role-options.html` — option A/B/C tree comparison
  - `team-page-design.html` — Team wireframe
  - `division-page-design.html` — Division wireframe
  - `region-page-design.html` — Region wireframe (v1; v2 inheritance described in §5.3)
  - `company-page-design-v3.html` — Company wireframe (final)
- Existing related specs:
  - `2026-04-06-unit-types-v2-design.md` — original unit types model
  - `2026-04-05-roles-permissions-refactor-design.md` — role assignment model
  - `2026-04-05-unit-delete-auth-and-admin-inheritance-design.md` — Admin cascade rules preserved here
  - `2026-04-26-org-graph-radial-menu-design.md` — companion graph-editor work (the `settings/org-units` index page)
- Backend touchpoints:
  - `app/modules/org_units/service.py` — `create_org_unit`, `update_org_unit`, `find_company_profile_in_ancestry`
  - `app/modules/org_units/company_profile.py` — strict 4-field schema (unchanged)
  - `app/modules/pipelines/service.py` — pipeline templates service (unchanged)
- Relevant frontend conventions: `frontend/app/CLAUDE.md` (component placement, RHF+Zod, Base UI quirks).
