# Company Profile refactor — column-level fields, free-text industry, address replaces locale

**Date:** 2026-05-14
**Status:** Approved (design)
**Scope:** Backend (`app/modules/org_units`, `app/modules/ats`, JD ancestry consumers) + frontend (`/settings/org-units/[unitId]` detail page, onboarding, deletion of the deep editor route) + one alembic migration.

---

## Problem

Three problems compound on the current company / client_account profile surface:

1. **Saves are silently dropped.** The detail page's `buildProfilePayload` refuses to persist any of the strict 4-field `company_profile` JSONB (`about`, `industry`, `company_stage`, `hiring_bar`) unless ALL four validate. A recruiter editing `about` on the inline form gets a "Saved settings — open the deep editor to set Industry & Stage" toast and the typed text is silently dropped because Industry/Stage are unset. The strict block is gated all-or-nothing in code; this is a real bug masked by the "deep editor" escape hatch.

2. **Locale + compliance UX doesn't carry its weight.** Today the profile carries `default_timezone`, `default_currency`, `default_locale` plus `compliance_aivia_il` / `compliance_gdpr_eu` / `compliance_ccpa_ca`, all inheriting through ancestry with per-field overrides. None of these toggles are read by anything except the detail-page response — AIVIA / GDPR / CCPA enforcement lives in `session/` and `candidates/` and ignores them entirely. They're a recruiter-facing surface with no downstream consumer.

3. **ATS-imported client_account stubs are too sparse.** Ceipal returns `country`, `state`, `city`, `website`, and `industry_type` at the client-detail level. The current importer drops country/state/city and reads `industry_exp` from the list endpoint (which the adapter wires through but the importer doesn't persist). Recruiters get a stub with name only and have to type the address by hand.

## Decision

Replace the locale + compliance system with a country/state/city address block. Promote the strict company_profile fields out of JSONB into typed columns on `organizational_units`. Switch `industry` to free-text (drop the 10-value enum). Drop `company_stage` entirely. Delete the deep editor route — everything edits inline on the detail page via the existing edit-mode toggle. Auto-fill `website`, `industry`, `country`, `state`, `city` from Ceipal on `_sync_clients` create; refresh those same fields on `_sync_clients` promote **only where currently NULL** (recruiter-edits win).

## Field-by-field

### `organizational_units` after refactor

| Column | Type | Source | Notes |
|---|---|---|---|
| `name` | TEXT | recruiter / Ceipal `name` | unchanged |
| `unit_type` | TEXT | recruiter | unchanged |
| `parent_unit_id`, `is_root`, `client_id` | UUID/BOOL | unchanged | |
| `about` | TEXT NULL | recruiter | NEW typed column. Free text; no length cap |
| `industry` | TEXT NULL | recruiter / Ceipal `industry_type` | NEW typed column. Free text — the 10-value enum is retired |
| `hiring_bar` | TEXT NULL | recruiter | NEW typed column |
| `website` | TEXT NULL | recruiter / Ceipal `website` | NEW typed column (was in `metadata.website`) |
| `country` | TEXT NULL | recruiter / Ceipal `country` | NEW typed column. Per-field inheritance via ancestry walk |
| `state` | TEXT NULL | recruiter / Ceipal `state` | NEW typed column. Same inheritance |
| `city` | TEXT NULL | recruiter / Ceipal `city` | NEW typed column. Same inheritance |
| `company_profile_completion_status` | TEXT | derived | unchanged. `'complete'` iff `about` AND `industry` AND `hiring_bar` are all non-empty (whitespace-trimmed). Drives the JD unblock cascade |
| `company_profile_completed_at` / `_completed_by` | TIMESTAMPTZ / UUID | derived | unchanged |
| `metadata` | JSONB | recruiter (per-unit-type) | kept for the non-profile keys that are unit-type-specific (`description` on division, `focus` + `default_role` on team). Locale + compliance + `website` + `short_name` keys are stripped on migration |

### Columns / data removed
- `company_profile` JSONB column — dropped.
- The enum modules `app/modules/org_units/company_profile.py` (`CompanyProfile` Pydantic model, `INDUSTRY_VALUES`, `COMPANY_STAGE_VALUES`) — deleted.
- `tests/fixtures/company_profile_enums.json` — deleted.
- `tests/test_company_profile_schema.py` — deleted.
- Frontend `INDUSTRY_OPTIONS` / `COMPANY_STAGE_OPTIONS` / `companyProfileSchema` constants — removed from `components/dashboard/company-profile-form.tsx`.
- The deep editor route `/settings/org-units/[unitId]/company-profile/page.tsx` and its sibling files — deleted. The URL returns 404; no redirect.
- Locale + compliance helpers in `shared.tsx`: `LOCALE_OPTIONS`, `TIMEZONE_OPTIONS`, `CURRENCY_OPTIONS`, `COMPLIANCE_FLAGS`, `LocaleChip`, `ComplianceRow`, `getLocaleCommonValues`, `getTimezoneCommonValues`, `CURRENCY_COMMON_VALUES`, `localeDefaults` — deleted (cross-check no remaining importers first).
- `find_locale_defaults_in_ancestry` and `find_compliance_flags_in_ancestry` in `app/modules/org_units/service.py` — replaced by a single `find_address_in_ancestry` that does the per-field walk for `country`/`state`/`city`.
- Typescript types: `InheritedLocale`, `InheritedCompliance`, locale/compliance keys on `CompanyMetadata` / `RegionMetadata`, `short_name` on `CompanyMetadata` — removed.

### Migration (`0034_company_profile_columns`)

Single alembic migration runs in one transaction:

1. `ALTER TABLE organizational_units ADD COLUMN about TEXT, industry TEXT, hiring_bar TEXT, website TEXT, country TEXT, state TEXT, city TEXT`.
2. Backfill from existing `company_profile` JSONB:
   ```sql
   UPDATE organizational_units SET
       about = company_profile->>'about',
       hiring_bar = company_profile->>'hiring_bar',
       industry = CASE company_profile->>'industry'
           WHEN 'fintech_financial_services'        THEN 'Fintech / Financial Services'
           WHEN 'healthcare_medtech'                THEN 'Healthcare / Medtech'
           WHEN 'ecommerce_retail'                  THEN 'E-commerce / Retail'
           WHEN 'ai_ml_products'                    THEN 'AI / ML Products'
           WHEN 'saas_enterprise_software'          THEN 'SaaS / Enterprise Software'
           WHEN 'developer_tools_infrastructure'    THEN 'Developer Tools / Infrastructure'
           WHEN 'agency_consulting_staffing'        THEN 'Agency / Consulting / Staffing'
           WHEN 'media_content'                     THEN 'Media / Content'
           WHEN 'logistics_supply_chain'            THEN 'Logistics / Supply Chain'
           WHEN 'other'                             THEN 'Other'
           ELSE company_profile->>'industry'
       END
   WHERE company_profile IS NOT NULL;
   ```
3. Backfill website from metadata:
   ```sql
   UPDATE organizational_units SET website = metadata->>'website'
   WHERE metadata ? 'website';
   ```
4. Strip locale / compliance / website / short_name keys from metadata:
   ```sql
   UPDATE organizational_units SET metadata =
       metadata - 'default_timezone' - 'default_currency' - 'default_locale'
                - 'compliance_aivia_il' - 'compliance_gdpr_eu' - 'compliance_ccpa_ca'
                - 'website' - 'short_name'
   WHERE metadata IS NOT NULL;
   ```
5. `ALTER TABLE organizational_units DROP COLUMN company_profile`.

**Downgrade** reverses 1+5 (recreate `company_profile` JSONB column, repopulate from the new columns using the inverse industry mapping). The stripped locale/compliance/short_name keys are LOST on downgrade — the migration docstring spells this out. `company_stage` was never carried forward into a new column, so on downgrade the rebuilt `company_profile` JSONB has no `company_stage` key (stays NULL).

---

## ATS auto-fill

### `_sync_clients` CREATE path

When the importer inserts a new `client_account` org_unit from a Ceipal `getClientsList` payload, populate the new columns at insert time alongside `name`:

```python
new_unit = OrganizationalUnit(
    client_id=tenant_id,
    parent_unit_id=root.id,
    name=payload.name,
    unit_type="client_account",
    is_root=False,
    company_profile_completion_status="pending",  # unchanged — about/hiring_bar still empty
    created_by=created_by,
    website=(payload.website or None),
    industry=(payload.industry or None),
    country=(payload.country or None),
    state=(payload.state or None),
    city=(payload.city or None),
)
```

`payload.industry` is the raw Ceipal string from `industry_exp` / `industry_type` — no normalization, no mapping table.

### `_sync_clients` PROMOTE path

When `_sync_clients` promotes a name-only stub (created earlier by `_sync_jobs`) to a real Ceipal-id-backed mapping, refresh the new columns **only where currently NULL** — recruiter edits between stub creation and promotion are preserved:

```python
if promotable is not None:
    # ... existing external_client_id rewrite + source_metadata refresh + last_synced_at ...
    unit = await db.get(OrganizationalUnit, promotable.org_unit_id)
    if unit.website is None and payload.website:
        unit.website = payload.website
    if unit.industry is None and payload.industry:
        unit.industry = payload.industry
    if unit.country is None and payload.country:
        unit.country = payload.country
    if unit.state is None and payload.state:
        unit.state = payload.state
    if unit.city is None and payload.city:
        unit.city = payload.city
    # about + hiring_bar are NEVER auto-filled — Ceipal has no equivalent.
```

### `_sync_jobs` stub creation

Unchanged. Job-phase stubs still land with `name` only; the new columns stay NULL until `_sync_clients` runs.

### Out of scope (deferred)
- Ceipal `contacts[]` and `accounts[]` — would warrant a related table (`client_account_contacts`) or a JSONB array; not a fit for org_unit columns.
- Ceipal `category` ("Premium"), `status` ("Active"), `ownership`, `primary_business_unit`, `accessible_business_units` — already persisted under `ats_client_mappings.source_metadata.raw` if needed.

---

## Backend: ancestry walks + completion gate

### `find_company_profile_in_ancestry` (rewrite)

Currently walks for a non-null `company_profile` JSONB. After refactor, returns the `{about, industry, hiring_bar}` triple from the **closest ancestor (or the unit itself)** where ALL THREE columns are non-empty (whitespace-trimmed). If no ancestor satisfies that, return `None`.

```python
async def find_company_profile_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict[str, str] | None:
    """Walk root → unit, return the closest ancestor (or self) with all
    three company-profile fields filled. Return None if no ancestor
    satisfies. The 'all three' rule matches today's behavior: an incomplete
    profile higher up is skipped so the AI receives a coherent triple."""
```

Used by JD enrichment + signal extraction prompts. Their interface (`profile["about"]` / `profile["industry"]` / `profile["hiring_bar"]`) is unchanged.

### `find_address_in_ancestry` (new)

Replaces `find_locale_defaults_in_ancestry` and `find_compliance_flags_in_ancestry`. Per-field walk:

```python
async def find_address_in_ancestry(
    db: AsyncSession, org_unit_id: UUID
) -> dict[str, dict[str, str | None] | str | None]:
    """Walk root → unit. For each of country/state/city, return the
    closest non-null value (whether on self or an ancestor). Return shape
    matches the previous inherited_locale / inherited_compliance shape so
    the frontend's chip-rendering code only changes its field names:

      {
        "values": {"country": str|None, "state": str|None, "city": str|None},
        "source_unit_id": "<closest ancestor that contributed at least one>",
      }
    """
```

The OrgUnitResponse model exposes this as `inherited_address: dict | None` (the `inherited_locale` / `inherited_compliance` fields are removed).

### Completion-gate auto-flip in `UpdateOrgUnit`

`UpdateOrgUnitRequest` is rewritten to accept each profile field as a `str | None` with sentinel semantics:

```python
class UpdateOrgUnitRequest(BaseModel):
    name: str | None = None
    deletable_by: str | None = None
    admin_delete_disabled: bool | None = None
    # NEW typed-column fields. Absent (None) means "don't touch".
    # Present (empty string is allowed) means "set this column".
    # Empty string clears the column to NULL after .strip().
    about: str | None = None
    industry: str | None = None
    hiring_bar: str | None = None
    website: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    set_about: bool = False
    set_industry: bool = False
    set_hiring_bar: bool = False
    set_website: bool = False
    set_country: bool = False
    set_state: bool = False
    set_city: bool = False
    # `metadata` set/replace stays for the OTHER unit-type-specific keys
    # that we still carry in JSONB (division description, team focus).
    metadata: dict | None = None
    set_metadata: bool = False
```

The `set_<field>` sentinels follow the existing `set_metadata` pattern: only fields with both `<field>` present AND `set_<field>=True` are written. This lets the frontend send partial payloads without ambiguity over "absent vs explicitly null".

After the column writes, the service re-evaluates completion status:

```python
def derive_completion_status(unit) -> str:
    if (
        unit.about and unit.about.strip()
        and unit.industry and unit.industry.strip()
        and unit.hiring_bar and unit.hiring_bar.strip()
    ):
        return "complete"
    return "pending"
```

The existing unblock-cascade logic (when status flips `pending` → `complete`, advance jobs out of `blocked_pending_client_setup`) is preserved — it now triggers off the derived check instead of the JSONB shape.

**One-way ratchet:** once a unit's status flips to `complete` and the cascade fires, a later edit that clears `about` back to NULL flips the status back to `pending` (UI shows the badge again), but the jobs that were already unblocked stay unblocked. Tested explicitly.

### Independent-field save (the user-flagged bug)

The current `buildProfilePayload` "all 4 fields must validate or nothing persists" gate is removed. Every column saves independently. The unit test `test_update_org_unit_persists_about_when_industry_is_blank` is the explicit regression check: PUT `{about: "Some text", set_about: true}` on a unit with NULL industry succeeds, `about` is `"Some text"`, `industry` stays NULL, `completion_status` stays `'pending'`.

---

## Frontend

### Deleted files
- `frontend/app/app/(dashboard)/settings/org-units/[unitId]/company-profile/page.tsx` — deep editor route.
- Any helpers in `shared.tsx` whose only consumers were the deep editor + the locale strip / compliance section (`LocaleChip`, `ComplianceRow`, the OPTION constants, the helper functions named above).
- `frontend/app/components/dashboard/company-profile-form.tsx` — deleted **after** onboarding is rewritten to use the new column-level API (so onboarding doesn't break on the same commit).

### `CompanyDetail.tsx` layout

Header (top to bottom):
- Pill row: `[Client account] [breadcrumb]` (only on client_account; root company unit shows `[Company · Root]`).
- Name (inline-editable in edit mode — same `contentEditable` pattern as today).
- **Website** row (always-editable text input in edit mode — already exists today).
- **Industry** row (NEW — inline-editable free-text input in edit mode, plain display in view mode).
- **About** textarea (existing position — but no length cap).
- **Address block** (NEW — replaces the locale strip): three chips for Country / State / City, each behaving like today's `LocaleChip`. Read-mode shows the value with an "Inherited from {ancestor name}" badge if the unit-local value is null and an ancestor contributed. Edit-mode renders as a free-text input.
- Stats line: `N regions · M divisions · K direct members · J open jobs`.
- HeaderActions: Edit / Save / Discard.

Body sections (unchanged in role; one section deleted):
- **Hiring bar** (highlighted section, textarea — no length cap).
- **Sub-units** — unchanged.
- **Pipeline templates** — unchanged.
- **(Compliance flags section — DELETED.)**

Sidebar (unchanged).

### Edit mode wiring
The whole-page edit-mode toggle is preserved. In edit mode, every editable field becomes an input/textarea. Save sends `PUT /api/org-units/{id}` with the explicit set of `set_<field>: true` sentinels for the fields the form recognizes (about, industry, hiring_bar, website, country, state, city, name, metadata). The Address chips, Industry chip, and About/Hiring-bar textareas all use the same form submission — one save round-trip.

### TypeScript shape changes

`OrgUnit` in `frontend/app/lib/api/org-units.ts`:
- Adds: `about`, `industry`, `hiring_bar`, `website`, `country`, `state`, `city` (all `string | null`).
- Adds: `inherited_address: InheritedAddress | null`.
- Removes: `company_profile`, `inherited_locale`, `inherited_compliance`.
- `CompanyMetadata` and `RegionMetadata` keep only their non-locale, non-compliance keys (and drop `short_name`/`website` since those moved to columns).

The `update()` function on `orgUnitsApi`:

```ts
update: (
  token: string,
  unitId: string,
  body: {
    name?: string
    about?: string
    set_about?: boolean
    industry?: string
    set_industry?: boolean
    hiring_bar?: string
    set_hiring_bar?: boolean
    website?: string
    set_website?: boolean
    country?: string
    set_country?: boolean
    state?: string
    set_state?: boolean
    city?: string
    set_city?: boolean
    metadata?: OrgUnitMetadata | null
    set_metadata?: boolean
  },
): Promise<OrgUnit>
```

### RegionDetail
Region also adopts country/state/city in the same chip style. Locale/compliance code in `RegionDetail.tsx` deleted.

### DivisionDetail
Unchanged — divisions don't surface locale/compliance today and won't surface address either (the columns exist on the unit but the UI doesn't render them for divisions).

### Onboarding
The 2-step onboarding wizard currently calls `companyProfileSchema` to validate the strict 4-field block. Rewrite to:
- Step 1: name, website (existing).
- Step 2: about, industry, hiring_bar (all free-text). Sends to `PUT /api/org-units/{id}` with the new column shape.
- `company_stage` field removed from the wizard entirely.

---

## Tests

### Backend tests (new + replacements for deleted)

1. `test_migration_upgrade_backfills_columns_from_jsonb` — pre-seed several org_units with the legacy `company_profile` JSONB shape (including each of the 10 enum values, one `null` JSONB row, one row with `metadata.website`). Run the upgrade. Assert: `about` / `hiring_bar` populated; `industry` mapped to human label; `website` populated; locale/compliance/short_name keys removed from metadata; `company_profile` column gone.

2. `test_migration_downgrade_round_trip` — upgrade then downgrade. Assert: data round-trips correctly for `about`/`industry` (human label → enum-like-string is lossy, document as known limitation in migration docstring); `metadata` does NOT regain the dropped locale/compliance keys.

3. `test_update_org_unit_persists_about_when_industry_is_blank` — pre-seed unit with all profile fields NULL. PUT `{about: "Some text", set_about: true}`. Assert: `about == "Some text"`, `industry IS NULL`, `completion_status == 'pending'`.

4. `test_update_completion_flips_pending_to_complete_when_all_three_filled` — pre-seed unit with `about` + `industry` set, `hiring_bar` NULL. PUT `{hiring_bar: "Strong eng", set_hiring_bar: true}`. Assert: `completion_status == 'complete'`; jobs linked to the unit with `status='blocked_pending_client_setup'` are advanced (unblock cascade fires).

5. `test_update_completion_flips_complete_to_pending_when_cleared` — pre-seed complete unit. PUT `{about: "", set_about: true}`. Assert: `about IS NULL`, `completion_status == 'pending'`. Jobs that were previously unblocked stay at their current status (no re-block).

6. `test_find_company_profile_in_ancestry_returns_first_complete_unit` — pre-seed grandchild with all 3 fields NULL, parent with `about`+`industry` only (incomplete), grandparent with all 3 filled. Assert: function returns grandparent's triple, skipping the incomplete parent.

7. `test_find_address_in_ancestry_per_field_walk` — pre-seed grandchild with `country=NULL, state='X', city=NULL`, parent with `country='US', state='Y', city='Z'`. Assert: returned `values = {country: 'US', state: 'X', city: 'Z'}`; `source_unit_id` points at the parent (closest contributor).

8. `test_sync_clients_create_populates_new_columns` — Ceipal payload with `website`, `industry` (`'Banking - Financial Services'`), `country`, `state`, `city`. Assert: all 5 columns populated on the new org_unit; `about` and `hiring_bar` NULL; `completion_status='pending'`.

9. `test_sync_clients_promote_does_not_overwrite_recruiter_edits` — pre-seed stub with recruiter-set `industry='Custom Industry'` and `website='https://recruiter.com'`, NULL `country`/`state`/`city`. Run `_sync_clients` with Ceipal returning the real id and different values for industry/website plus values for country/state/city. Assert: `industry`/`website` preserved (recruiter wins); `country`/`state`/`city` now hold Ceipal values.

10. `test_sync_clients_promote_fills_only_null_columns` — symmetric to #9: pre-seed stub with all fields NULL; promote with Ceipal returning all values. Assert: all columns hold Ceipal values.

### Frontend tests

- `OrgUnit` shape compilation: tsc must pass after type changes (catches stale consumers).
- `CompanyDetail` composition test: Industry chip renders when `unit.industry` is set; Address block renders inheritance badge when local fields null and ancestry returns value.
- Edit-mode save test: type into About → leave Industry blank → Save → mutation called with `{about: "...", set_about: true}` (no industry key); response shows About persisted, completion_status still pending.
- Onboarding: form posts the new column-level shape; no `company_stage`; no `company_profile` wrapper.

### Tests deleted

- `tests/test_company_profile_schema.py` (parity test for dropped enum).
- Deep editor route component tests.
- Locale / compliance ancestry tests.

---

## Out of scope (deferred)

- Adding country/state/city UI to `team` and `division` detail pages — columns exist on the table but the pages don't render them. Either of those detail pages could opt in later without schema changes.
- Persisting Ceipal `contacts[]` / `accounts[]`.
- Adding postal_code / address line 1 / address line 2 — Ceipal's `getClientDetails` doesn't return them at the client-account top level. If a flow needs them, they fit cleanly in the `metadata` JSONB.
- The earlier-discussed `source_metadata` JSONB on `job_postings` (preserving raw Ceipal job payloads). Separate refactor.
- A "promote on every sync, but only the changed Ceipal fields" diff-tracking layer. Today's "only fill NULLs" rule is good enough.

## Validation gates

- `docker compose run --rm nexus alembic upgrade head` clean on a freshly-seeded dev DB.
- `docker compose run --rm nexus alembic downgrade -1 && alembic upgrade head` round-trip clean.
- `docker compose run --rm nexus pytest tests/modules/org_units tests/modules/ats tests/modules/jd -q` — all green.
- `docker compose run --rm nexus ruff check app/modules/org_units app/modules/ats` — clean.
- `frontend/app && npm run lint && npm run type-check && npm run test` — clean.
- Manual: ATS-sync a fresh tenant against Ceipal sandbox, confirm the client_account shows up at `/settings/org-units` with website/industry/country/state/city populated and the "profile incomplete" badge (because `about` + `hiring_bar` still need recruiter input). Edit `about` only → save → confirm persisted, status still pending. Fill `hiring_bar` → save → confirm status flips to complete and any blocked jobs advance.
